"""Kusto Monitoring Agent CLI.

Examples:
    python -m kusto_monitor.agent --once
    python -m kusto_monitor.agent --once --check high-error-rate --fail-on-alert
    python -m kusto_monitor.agent --interval 60          # loop forever

Environment:
    KUSTO_CLUSTER_URL   https://<cluster>.<region>.kusto.windows.net (or http://localhost:8080)
    KUSTO_DATABASE      default MonitoringDb
    KUSTO_AUTH          default | cli | none
    CHECKS_FILE         default checks.yaml
    WEBHOOK_URL         optional Teams Workflows / Slack / generic webhook
    WEBHOOK_FORMAT      teams | slack | generic (default generic)
    RECORD_ALERTS       true to write alerts to the MonitorAlerts table
"""

import argparse
import os
import sys
import time
from typing import List

from .checks import CheckResult, load_checks, run_checks
from .kusto import KustoExecutor, executor_from_env
from .notifiers import print_report, record_alerts_command, send_webhook

EXIT_OK = 0
EXIT_ALERTS = 2
EXIT_ERRORS = 3


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def dispatch(kusto: KustoExecutor, results: List[CheckResult], args) -> None:
    """Send alerts to every configured destination. A broken destination never stops the others."""
    print_report(results)

    if args.webhook_url:
        try:
            send_webhook(args.webhook_url, results, args.webhook_format)
            if any(r.is_alert for r in results):
                print(f"Webhook notified ({args.webhook_format}).")
        except Exception as exc:  # pylint: disable=broad-except
            print(f"WARNING: webhook delivery failed: {exc}", file=sys.stderr)

    if args.record_alerts:
        command = record_alerts_command(results)
        if command:
            try:
                kusto.command(command)
                print("Alerts recorded in table MonitorAlerts.")
            except Exception as exc:  # pylint: disable=broad-except
                print(f"WARNING: could not record alerts in Kusto: {exc}", file=sys.stderr)


def run_once(kusto: KustoExecutor, args) -> int:
    """Run all checks once and return the process exit code."""
    checks = load_checks(args.checks)
    results = run_checks(kusto.query, checks, only=args.check or None)
    dispatch(kusto, results, args)

    if any(r.status == "error" for r in results):
        return EXIT_ERRORS
    if args.fail_on_alert and any(r.is_alert for r in results):
        return EXIT_ALERTS
    return EXIT_OK


def parse_args(argv=None):
    """Parse CLI arguments (defaults come from environment variables)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run the checks once and exit (default).")
    mode.add_argument("--interval", type=int, metavar="SECONDS", help="Run forever, every N seconds.")
    parser.add_argument("--checks", default=os.environ.get("CHECKS_FILE", "checks.yaml"),
                        help="Path to the checks YAML file.")
    parser.add_argument("--check", action="append", metavar="NAME",
                        help="Only run this check (repeatable).")
    parser.add_argument("--fail-on-alert", action="store_true",
                        help=f"Exit with code {EXIT_ALERTS} when any check alerts (handy for tests/CI).")
    parser.add_argument("--webhook-url", default=os.environ.get("WEBHOOK_URL", ""))
    parser.add_argument("--webhook-format", default=os.environ.get("WEBHOOK_FORMAT", "generic"),
                        choices=("teams", "slack", "generic"))
    parser.add_argument("--record-alerts", action="store_true", default=_env_flag("RECORD_ALERTS"),
                        help="Write alerts to the MonitorAlerts table.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """Entry point."""
    args = parse_args(argv)
    kusto = executor_from_env()
    print(f"Connected target: {os.environ.get('KUSTO_CLUSTER_URL')} / database {kusto.database}")

    if not args.interval:
        return run_once(kusto, args)

    print(f"Running every {args.interval}s. Press Ctrl+C to stop.")
    try:
        while True:
            run_once(kusto, args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
