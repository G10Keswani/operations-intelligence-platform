"""End-to-end tests against a real Kusto engine (the local Kusto emulator).

Start the emulator first:  ./scripts/start-emulator.sh
Then run:                  KUSTO_EMULATOR_URL=http://localhost:8080 pytest -m integration -v
"""

import os

import pytest

from kusto_monitor import setup_db, simulate
from kusto_monitor.checks import load_checks, run_checks
from kusto_monitor.kusto import KustoExecutor, build_client
from kusto_monitor.notifiers import record_alerts_command

EMULATOR = os.environ.get("KUSTO_EMULATOR_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not EMULATOR, reason="set KUSTO_EMULATOR_URL to run emulator tests"),
]

EXPECTED = {
    None: set(),
    "error-spike": {"high-error-rate", "error-count-anomaly"},
    "latency": {"high-latency-p95"},
    "outage": {"service-silent"},
}


@pytest.fixture(scope="module")
def kusto(monkeypatch_module):
    monkeypatch_module.setenv("KUSTO_CLUSTER_URL", EMULATOR)
    monkeypatch_module.setenv("KUSTO_AUTH", "none")
    monkeypatch_module.setenv("KUSTO_DATABASE", "MonitoringDbTest")
    setup_db.main(["--create-database"])
    return KustoExecutor(build_client(EMULATOR, "none"), "MonitoringDbTest")


@pytest.fixture(scope="module")
def monkeypatch_module():
    with pytest.MonkeyPatch.context() as patch:
        yield patch


@pytest.mark.parametrize("incident", [None, "error-spike", "latency", "outage"])
def test_incident_triggers_expected_checks(kusto, incident):
    kusto.command(".clear table AppTelemetry data")
    simulate.backfill(kusto, hours=3, incident=incident, service="payments", incident_minutes=10)

    results = run_checks(kusto.query, load_checks("checks.yaml"))

    assert [r for r in results if r.status == "error"] == []
    assert {r.check.name for r in results if r.is_alert} == EXPECTED[incident]
    for result in results:
        if result.is_alert and "Service" in result.rows[0]:
            assert result.rows[0]["Service"] == "payments"


def test_alerts_are_recorded(kusto):
    kusto.command(".clear table AppTelemetry data")
    kusto.command(".clear table MonitorAlerts data")
    simulate.backfill(kusto, hours=1, incident="latency", incident_minutes=10)
    results = run_checks(kusto.query, load_checks("checks.yaml"))
    kusto.command(record_alerts_command(results))
    rows = kusto.query("MonitorAlerts | project Check, Details")
    assert [r["Check"] for r in rows] == ["high-latency-p95"]
    assert rows[0]["Details"][0]["Service"] == "payments"
