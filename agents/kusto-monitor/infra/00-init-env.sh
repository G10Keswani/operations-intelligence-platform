#!/usr/bin/env bash
# Creates infra/env.sh once, with globally-unique resource names. Edit it afterwards if you like.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f env.sh ]]; then
  echo "infra/env.sh already exists - keeping it:"; cat env.sh; exit 0
fi

SUFFIX="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)"
cat > env.sh <<VARS
# Settings shared by all infra scripts. Source it in your shell: source infra/env.sh
export LOCATION="eastus"
export RG="rg-kusto-monitor-demo"
export ADX_CLUSTER="adxmon${SUFFIX}"         # globally unique, lowercase letters+digits, 4-22 chars
export KUSTO_DATABASE="MonitoringDb"
export ACR_NAME="acrkmon${SUFFIX}"           # globally unique, letters+digits
export IDENTITY_NAME="id-kusto-monitor"
export ACA_ENV="cae-kusto-monitor"
export JOB_NAME="kusto-monitor-job"
export JOB_CRON="*/5 * * * *"               # every 5 minutes (UTC)
export IMAGE_TAG="v1"
# Optional: Teams Workflows / Slack incoming-webhook URL. Leave empty to skip chat alerts.
export WEBHOOK_URL=""
export WEBHOOK_FORMAT="teams"                # teams | slack | generic
VARS
echo "Wrote infra/env.sh:"; cat env.sh
