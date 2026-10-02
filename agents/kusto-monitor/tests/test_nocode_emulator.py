"""End-to-end tests for the no-code (portal + Logic Apps) setup script in agents/kusto-monitor-nocode.

Runs the same KQL the portal guide tells you to paste, then replays what the two Logic Apps do.
    KUSTO_EMULATOR_URL=http://localhost:8080 pytest -m integration -v
"""

import os
import pathlib

import pytest

from kusto_monitor.kusto import KustoExecutor, build_client

EMULATOR = os.environ.get("KUSTO_EMULATOR_URL")
NOCODE_DIR = pathlib.Path(__file__).resolve().parents[2] / "kusto-monitor-nocode"
DATABASE = "NoCodeDbTest"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not EMULATOR, reason="set KUSTO_EMULATOR_URL to run emulator tests"),
]


@pytest.fixture(scope="module")
def kusto():
    client = build_client(EMULATOR, "none")
    admin = KustoExecutor(client, "NetDefaultDB")
    admin.command(f".drop database {DATABASE} ifexists")
    admin.command(f".create database {DATABASE} volatile")
    db = KustoExecutor(client, DATABASE)
    script = (NOCODE_DIR / "setup.kql").read_text(encoding="utf-8")
    db.command(script)
    db.command(script)  # the guide says re-running is safe
    return db


def set_incident(kusto, incident, service):
    kusto.command(f'.set-or-append SimulatorSettings <| print Incident = "{incident}", '
                  f'Service = "{service}", UpdatedAt = now()')


@pytest.mark.parametrize("incident,service,expected", [
    ("none", "payments", set()),
    ("error-spike", "payments", {"high-error-rate", "error-count-anomaly"}),
    ("latency", "checkout", {"high-latency-p95"}),
    ("outage", "auth", {"service-silent"}),
])
def test_run_all_checks(kusto, incident, service, expected):
    kusto.command(".clear table AppTelemetry data")
    kusto.command('.set-or-append AppTelemetry <| GenerateTraffic(ago(3h), ago(10m), "none", "payments")')
    set_incident(kusto, incident, service)
    kusto.command(f'.set-or-append AppTelemetry <| GenerateTraffic(ago(10m), ago(5s), "{incident}", "{service}")')
    kusto.command(".set-or-append AppTelemetry <| NextTrafficBatch()")  # what the generator Logic App runs

    rows = kusto.query("RunAllChecks()")  # what the monitor Logic App runs

    assert {r["Check"] for r in rows} == expected
    for row in rows:
        assert set(row) == {"Timestamp", "Severity", "Check", "Summary"}
        if row["Check"] != "error-count-anomaly":
            assert row["Summary"].startswith(service)


def test_alert_log_columns_line_up(kusto):
    kusto.command(".clear table AlertLog data")
    kusto.command(".set-or-append AlertLog <| RunAllChecks()")  # the monitor Logic App's record step
    rows = kusto.query("AlertLog")
    assert rows and rows[0]["Check"] == "service-silent" and rows[0]["Severity"] == "critical"


def test_next_batch_is_incremental(kusto):
    set_incident(kusto, "none", "payments")
    kusto.command(".clear table AppTelemetry data")
    kusto.command(".set-or-append AppTelemetry <| NextTrafficBatch()")  # empty table -> last minute
    first = kusto.query("AppTelemetry | count")[0]["Count"]
    assert 200 <= first <= 241
    kusto.command(".set-or-append AppTelemetry <| NextTrafficBatch()")
    dups = kusto.query("AppTelemetry | summarize d = count() - dcount(Timestamp)")[0]["d"]
    assert dups == 0
