#!/usr/bin/env bash
# Show the agent's console output from recent job runs (Log Analytics; allow ~2-5 min ingestion delay).
set -euo pipefail
source "$(dirname "$0")/env.sh"

WORKSPACE="$(az containerapp env show -n "$ACA_ENV" -g "$RG" \
  --query properties.appLogsConfiguration.logAnalyticsConfiguration.customerId -o tsv)"
az monitor log-analytics query -w "$WORKSPACE" -o table --analytics-query "
ContainerAppConsoleLogs_CL
| where ContainerJobName_s == '$JOB_NAME' and TimeGenerated > ago(${1:-60}m)
| project TimeGenerated, Log_s
| order by TimeGenerated asc"
