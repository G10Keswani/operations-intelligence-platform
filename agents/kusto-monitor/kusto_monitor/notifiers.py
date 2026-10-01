"""Where alerts go: console, a chat webhook (Teams / Slack / generic) and a Kusto history table."""

import datetime
import json
from typing import Any, Dict, List

import requests

from .checks import CheckResult

MAX_SAMPLE_ROWS = 5
SEVERITY_ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}


def utc_now() -> str:
    """Current UTC time as ISO-8601."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def format_rows(rows: List[Dict[str, Any]], limit: int = MAX_SAMPLE_ROWS) -> str:
    """Render result rows as compact 'key=value' lines."""
    lines = [", ".join(f"{k}={v}" for k, v in row.items()) for row in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"... and {len(rows) - limit} more")
    return "\n".join(lines)


def alert_text(result: CheckResult) -> str:
    """Human-readable alert message for one check result."""
    check = result.check
    icon = SEVERITY_ICON.get(check.severity, "")
    header = f"{icon} [{check.severity.upper()}] {check.name}: {len(result.rows)} finding(s)"
    parts = [header]
    if check.description:
        parts.append(check.description)
    parts.append(format_rows(result.rows))
    return "\n".join(parts)


def print_report(results: List[CheckResult]) -> None:
    """Print a one-line-per-check summary plus details for alerts and errors."""
    print(f"\n=== Kusto Monitoring Agent run @ {utc_now()} ===")
    for result in results:
        label = {"ok": "OK   ", "alert": "ALERT", "error": "ERROR"}[result.status]
        print(f"[{label}] {result.check.name:<28} {result.check.severity:<8} "
              f"rows={len(result.rows):<3} {result.duration_ms:6.0f} ms")
    for result in results:
        if result.is_alert:
            print("\n" + alert_text(result))
        elif result.status == "error":
            print(f"\n❌ {result.check.name} failed to run: {result.error}")
    alerts = sum(r.is_alert for r in results)
    errors = sum(r.status == "error" for r in results)
    print(f"\nSummary: {len(results)} checks, {alerts} alert(s), {errors} error(s)\n")


def webhook_payload(results: List[CheckResult], fmt: str) -> Dict[str, Any]:
    """Build the webhook body for 'teams' (Workflows adaptive card), 'slack' or 'generic'."""
    alerts = [r for r in results if r.is_alert]
    title = f"Kusto Monitoring Agent: {len(alerts)} alert(s) @ {utc_now()}"
    text = "\n\n".join(alert_text(r) for r in alerts)

    if fmt == "teams":
        body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}]
        body += [{"type": "TextBlock", "text": alert_text(r), "wrap": True} for r in alerts]
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }],
        }
    if fmt == "slack":
        return {"text": f"*{title}*\n```{text}```"}
    return {
        "title": title,
        "alerts": [
            {"check": r.check.name, "severity": r.check.severity, "description": r.check.description,
             "rows": r.rows[:MAX_SAMPLE_ROWS], "row_count": len(r.rows)}
            for r in alerts
        ],
    }


def send_webhook(url: str, results: List[CheckResult], fmt: str = "generic", timeout: float = 10.0) -> None:
    """POST alerts to a webhook. Does nothing when there are no alerts."""
    if not any(r.is_alert for r in results):
        return
    response = requests.post(url, json=webhook_payload(results, fmt), timeout=timeout)
    response.raise_for_status()


def alert_records(results: List[CheckResult]) -> List[Dict[str, Any]]:
    """Rows for the MonitorAlerts table, one per alerting check."""
    now = utc_now()
    return [
        {
            "Timestamp": now,
            "Check": r.check.name,
            "Severity": r.check.severity,
            "Description": r.check.description,
            "RowCount": len(r.rows),
            "Details": r.rows[:MAX_SAMPLE_ROWS],
        }
        for r in results if r.is_alert
    ]


def record_alerts_command(results: List[CheckResult], table: str = "MonitorAlerts") -> str:
    """Build an '.ingest inline' command (one JSON record per line) or '' when nothing to record."""
    records = alert_records(results)
    if not records:
        return ""
    lines = "\n".join(json.dumps(record, ensure_ascii=True) for record in records)
    return f".ingest inline into table {table} with (format='multijson') <|\n{lines}"
