#!/usr/bin/env bash
# Scripted demo: healthy -> error spike -> latency -> outage, against whatever
# KUSTO_CLUSTER_URL points at (emulator by default). Press Enter between acts.
set -euo pipefail
cd "$(dirname "$0")/.."

export KUSTO_CLUSTER_URL="${KUSTO_CLUSTER_URL:-http://localhost:8080}"
export KUSTO_AUTH="${KUSTO_AUTH:-none}"
export KUSTO_DATABASE="${KUSTO_DATABASE:-MonitoringDb}"

pause() { echo; read -r -p "▶ Press Enter for: $1 " _; echo; }

python -m kusto_monitor.simulate reset 2>/dev/null || true   # fresh start (ignored if tables don't exist yet)
if [[ "$KUSTO_AUTH" == "none" ]]; then
  python -m kusto_monitor.setup_db --create-database
else
  python -m kusto_monitor.setup_db
fi

act() {  # act <title> [incident]
  pause "$1"
  python -m kusto_monitor.simulate reset --telemetry-only
  if [[ -n "${2:-}" ]]; then
    python -m kusto_monitor.simulate backfill --hours 3 --incident "$2" --service payments --incident-minutes 10
  else
    python -m kusto_monitor.simulate backfill --hours 3
  fi
  python -m kusto_monitor.agent --once --record-alerts || true
}

act "Act 1 - healthy system (expect all OK)"
act "Act 2 - payments error spike (expect high-error-rate + error-count-anomaly)" error-spike
act "Act 3 - payments slowdown (expect high-latency-p95)" latency
act "Act 4 - payments stops reporting (expect service-silent)" outage

pause "show alert history recorded in Kusto"
python - <<'PY'
from kusto_monitor.kusto import executor_from_env
for row in executor_from_env().query("MonitorAlerts | project Timestamp, Check, Severity, RowCount"):
    print(row)
PY
