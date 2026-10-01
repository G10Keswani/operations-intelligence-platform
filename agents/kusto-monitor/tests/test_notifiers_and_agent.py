"""Unit tests for alert formatting, webhooks, the simulator KQL and agent exit codes."""

import datetime
import json

import pytest

from kusto_monitor import agent, notifiers, simulate
from kusto_monitor.checks import Check, CheckResult


def alert(name="high-error-rate", rows=None, severity="critical"):
    rows = rows if rows is not None else [{"Service": "payments", "ErrorRatePct": 40.0}]
    return CheckResult(Check(name, "T", severity, "desc"), "alert", rows=rows)


def ok(name="fine"):
    return CheckResult(Check(name, "T"), "ok")


def test_alert_text_truncates_rows():
    text = notifiers.alert_text(alert(rows=[{"i": i} for i in range(8)]))
    assert "[CRITICAL] high-error-rate: 8 finding(s)" in text
    assert "i=4" in text and "i=5" not in text
    assert "... and 3 more" in text


@pytest.mark.parametrize("fmt", ["teams", "slack", "generic"])
def test_webhook_payload_formats_are_json(fmt):
    payload = notifiers.webhook_payload([alert(), ok()], fmt)
    blob = json.dumps(payload)
    assert "high-error-rate" in blob and "fine" not in blob


def test_teams_payload_is_adaptive_card():
    payload = notifiers.webhook_payload([alert()], "teams")
    card = payload["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["content"]["type"] == "AdaptiveCard"


def test_send_webhook_skips_when_no_alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(notifiers.requests, "post", lambda *a, **k: calls.append(a))
    notifiers.send_webhook("http://hook", [ok()])
    assert not calls


def test_record_alerts_command_is_one_json_line_per_alert():
    command = notifiers.record_alerts_command([alert(), ok(), alert("other", severity="warning")])
    header, *lines = command.split("\n")
    assert header.startswith(".ingest inline into table MonitorAlerts")
    assert [json.loads(line)["Check"] for line in lines] == ["high-error-rate", "other"]
    assert notifiers.record_alerts_command([ok()]) == ""


def test_record_alerts_escapes_newlines_and_quotes():
    command = notifiers.record_alerts_command([alert(rows=[{"msg": 'line1\nline2 "quoted"'}])])
    assert len(command.split("\n")) == 2  # header + exactly one record line


def test_simulator_command_validates_inputs():
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    end = start + datetime.timedelta(minutes=1)
    healthy = simulate.append_command(start, end)
    assert healthy.startswith(".set-or-append AppTelemetry <|")
    assert "datetime(2026-01-01T00:00:00.000000Z)" in healthy
    assert "Affected = false" in healthy
    assert "Affected, 0.40" in simulate.append_command(start, end, "error-spike", "auth")
    assert "where not(Affected)" in simulate.append_command(start, end, "outage")
    with pytest.raises(ValueError):
        simulate.append_command(start, end, service='x"; .drop table AppTelemetry')
    with pytest.raises(ValueError):
        simulate.append_command(start, end, incident="meteor")


class FakeKusto:
    """Stands in for KustoExecutor."""

    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.commands = []
        self.database = "MonitoringDb"

    def query(self, kql):
        if "Broken" in kql:
            raise RuntimeError("bad query")
        return self.rows_by_table.get(kql.split()[0], [])

    def command(self, cmd):
        self.commands.append(cmd)
        return []


def run(tmp_path, kusto, extra=None, checks_text=None):
    path = tmp_path / "checks.yaml"
    path.write_text(checks_text or "checks:\n  - name: errors\n    query: Errors | take 1\n", encoding="utf-8")
    args = agent.parse_args(["--checks", str(path)] + (extra or []))
    return agent.run_once(kusto, args)


def test_exit_codes(tmp_path, capsys):
    assert run(tmp_path, FakeKusto({})) == agent.EXIT_OK
    assert run(tmp_path, FakeKusto({"Errors": [{"x": 1}]})) == agent.EXIT_OK
    assert run(tmp_path, FakeKusto({"Errors": [{"x": 1}]}), ["--fail-on-alert"]) == agent.EXIT_ALERTS
    broken = "checks:\n  - name: b\n    query: Broken\n"
    assert run(tmp_path, FakeKusto({}), checks_text=broken) == agent.EXIT_ERRORS
    assert "ALERT" in capsys.readouterr().out


def test_record_alerts_flag_writes_to_kusto(tmp_path):
    kusto = FakeKusto({"Errors": [{"x": 1}]})
    run(tmp_path, kusto, ["--record-alerts"])
    assert len(kusto.commands) == 1 and "MonitorAlerts" in kusto.commands[0]


def test_webhook_failure_does_not_crash(tmp_path, monkeypatch, capsys):
    def fail(*_a, **_k):
        raise ConnectionError("down")

    monkeypatch.setattr(notifiers.requests, "post", fail)
    code = run(tmp_path, FakeKusto({"Errors": [{"x": 1}]}), ["--webhook-url", "http://hook"])
    assert code == agent.EXIT_OK
    assert "webhook delivery failed" in capsys.readouterr().err


def test_env_flag(monkeypatch):
    monkeypatch.setenv("RECORD_ALERTS", "true")
    assert agent.parse_args([]).record_alerts is True
    monkeypatch.setenv("RECORD_ALERTS", "no")
    assert agent.parse_args([]).record_alerts is False
