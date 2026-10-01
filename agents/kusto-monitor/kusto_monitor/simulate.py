"""Generate synthetic app telemetry in Kusto so the agent has something to watch.

Four services (checkout, payments, catalog, auth) send ~4 requests/second in total,
with ~1% errors and ~40-200 ms latency. You can inject an incident on one service:

    error-spike  40% of the service's requests fail with HTTP 500
    latency      the service gets ~1.5 s slower
    outage       the service stops sending telemetry entirely

Examples:
    python -m kusto_monitor.simulate backfill --hours 6
    python -m kusto_monitor.simulate backfill --hours 3 --incident error-spike --incident-minutes 10
    python -m kusto_monitor.simulate live
    python -m kusto_monitor.simulate live --incident latency --service checkout
    python -m kusto_monitor.simulate reset
"""

import argparse
import datetime
import time
from typing import Optional

from .kusto import KustoExecutor, executor_from_env

SERVICES = ("checkout", "payments", "catalog", "auth")
REGIONS = ("eastus", "westeurope")
INCIDENTS = ("error-spike", "latency", "outage")
STEP = "250ms"
MAX_GAP = datetime.timedelta(hours=6)


def _kql_datetime(value: datetime.datetime) -> str:
    return f"datetime({value.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')})"


def _kql_list(values) -> str:
    return "dynamic([" + ", ".join(f'"{v}"' for v in values) + "])"


def append_command(start: datetime.datetime, end: datetime.datetime,
                   incident: Optional[str] = None, service: str = "payments") -> str:
    """Build a '.set-or-append' command that generates telemetry for [start, end)."""
    if incident is not None and incident not in INCIDENTS:
        raise ValueError(f"Unknown incident {incident!r}; use one of {INCIDENTS}")
    if service not in SERVICES:
        raise ValueError(f"Unknown service {service!r}; use one of {SERVICES}")

    affected = "true" if incident else "false"
    error_rate = "iff(Affected, 0.40, 0.01)" if incident == "error-spike" else "0.01"
    extra_ms = "iff(Affected, 1500.0, 0.0)" if incident == "latency" else "0.0"
    outage_filter = "| where not(Affected)\n" if incident == "outage" else ""

    return (
        ".set-or-append AppTelemetry <|\n"
        f"range Timestamp from {_kql_datetime(start)} to {_kql_datetime(end)} step {STEP}\n"
        f"| where Timestamp < {_kql_datetime(end)}\n"
        f"| extend Service = tostring({_kql_list(SERVICES)}[toint(rand() * {len(SERVICES)})])\n"
        f"| extend Region = tostring({_kql_list(REGIONS)}[toint(rand() * {len(REGIONS)})])\n"
        f"| extend Affected = {affected} and Service == \"{service}\"\n"
        f"| extend StatusCode = toint(iff(rand() < {error_rate}, 500, 200))\n"
        f"| extend DurationMs = round(40.0 + rand() * 160.0 + iff(rand() < 0.02, rand() * 600.0, 0.0)"
        f" + {extra_ms}, 1)\n"
        f"{outage_filter}"
        "| project Timestamp, Service, Region, Endpoint = strcat(\"/api/\", Service), StatusCode, DurationMs"
    )


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _append(kusto: KustoExecutor, start, end, incident=None, service="payments") -> None:
    kusto.command(append_command(start, end, incident, service))


def last_timestamp(kusto: KustoExecutor) -> Optional[datetime.datetime]:
    """Newest Timestamp in AppTelemetry, or None if the table is empty."""
    rows = kusto.query("AppTelemetry | summarize Last = max(Timestamp)")
    value = rows[0]["Last"] if rows else None
    if not value:
        return None
    return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def backfill(kusto: KustoExecutor, hours: float, incident=None, service="payments", incident_minutes=10) -> None:
    """Write `hours` of history ending now, optionally with an incident in the last N minutes."""
    end = _now()
    start = end - datetime.timedelta(hours=hours)
    if incident:
        split = end - datetime.timedelta(minutes=incident_minutes)
        _append(kusto, start, split)
        _append(kusto, split, end, incident, service)
        print(f"Backfilled {start:%H:%M}..{end:%H:%M} UTC with '{incident}' on {service} "
              f"for the last {incident_minutes} min.")
    else:
        _append(kusto, start, end)
        print(f"Backfilled healthy traffic {start:%H:%M}..{end:%H:%M} UTC.")


def live(kusto: KustoExecutor, interval: int, incident=None, service="payments") -> None:
    """Keep appending fresh telemetry every `interval` seconds until Ctrl+C."""
    cursor = last_timestamp(kusto)
    now = _now()
    if cursor is None or now - cursor > MAX_GAP:
        print("No recent data found: backfilling 6h of healthy history first...")
        backfill(kusto, hours=6)
        cursor = now
    elif cursor < now:
        cursor += datetime.timedelta(milliseconds=250)

    label = f"INCIDENT '{incident}' on {service}" if incident else "healthy traffic"
    print(f"Streaming {label} every {interval}s. Press Ctrl+C to stop.")
    try:
        while True:
            end = _now()
            if end > cursor:
                _append(kusto, cursor, end, incident, service)
                print(f"  appended {cursor:%H:%M:%S}..{end:%H:%M:%S} UTC ({label})")
                cursor = end
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped simulator.")


def main(argv=None) -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    def incident_args(p):
        p.add_argument("--incident", choices=INCIDENTS, help="Inject an incident.")
        p.add_argument("--service", choices=SERVICES, default="payments", help="Service hit by the incident.")

    p_back = sub.add_parser("backfill", help="Write historical telemetry ending now.")
    p_back.add_argument("--hours", type=float, default=6.0)
    p_back.add_argument("--incident-minutes", type=int, default=10)
    incident_args(p_back)

    p_live = sub.add_parser("live", help="Stream telemetry continuously.")
    p_live.add_argument("--interval", type=int, default=30)
    incident_args(p_live)

    p_reset = sub.add_parser("reset", help="Delete all simulated telemetry and recorded alerts.")
    p_reset.add_argument("--telemetry-only", action="store_true", help="Keep the MonitorAlerts history.")

    args = parser.parse_args(argv)
    kusto = executor_from_env()

    if args.mode == "backfill":
        backfill(kusto, args.hours, args.incident, args.service, args.incident_minutes)
    elif args.mode == "live":
        live(kusto, args.interval, args.incident, args.service)
    elif args.mode == "reset":
        kusto.command(".clear table AppTelemetry data")
        if args.telemetry_only:
            print("Cleared AppTelemetry.")
        else:
            kusto.command(".clear table MonitorAlerts data")
            print("Cleared AppTelemetry and MonitorAlerts.")


if __name__ == "__main__":
    main()
