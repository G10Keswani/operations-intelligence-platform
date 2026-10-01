#!/usr/bin/env bash
# Step 9: build the image in ACR and run the agent as a scheduled Azure Container Apps Job
# using a user-assigned managed identity (no secrets for Kusto access).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env.sh"

az extension add --name containerapp --upgrade --yes --only-show-errors
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.ContainerRegistry --wait

KUSTO_URI="$(az kusto cluster show --cluster-name "$ADX_CLUSTER" -g "$RG" --query uri -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"

echo "== 9a. Container registry + image build (built in Azure, no local Docker needed)"
az acr show -n "$ACR_NAME" -g "$RG" -o none 2>/dev/null || az acr create -n "$ACR_NAME" -g "$RG" --sku Basic -o none
az acr build -r "$ACR_NAME" -t "kusto-monitor:$IMAGE_TAG" "$HERE/.."
IMAGE="$ACR_NAME.azurecr.io/kusto-monitor:$IMAGE_TAG"

echo "== 9b. Managed identity"
az identity show -n "$IDENTITY_NAME" -g "$RG" -o none 2>/dev/null || az identity create -n "$IDENTITY_NAME" -g "$RG" -o none
IDENTITY_ID="$(az identity show -n "$IDENTITY_NAME" -g "$RG" --query id -o tsv)"
CLIENT_ID="$(az identity show -n "$IDENTITY_NAME" -g "$RG" --query clientId -o tsv)"
PRINCIPAL_ID="$(az identity show -n "$IDENTITY_NAME" -g "$RG" --query principalId -o tsv)"

echo "== 9c. Permissions: AcrPull on the registry, Viewer + Ingestor on the Kusto database"
ACR_ID="$(az acr show -n "$ACR_NAME" -g "$RG" --query id -o tsv)"
if [[ -z "$(az role assignment list --assignee "$PRINCIPAL_ID" --role AcrPull --scope "$ACR_ID" --query '[].id' -o tsv)" ]]; then
  az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role AcrPull --scope "$ACR_ID" -o none
fi
for ROLE in Viewer Ingestor; do
  NAME="agent-$(echo "$ROLE" | tr '[:upper:]' '[:lower:]')"
  az kusto database-principal-assignment show --cluster-name "$ADX_CLUSTER" --database-name "$KUSTO_DATABASE" \
    -g "$RG" --principal-assignment-name "$NAME" -o none 2>/dev/null \
  || az kusto database-principal-assignment create --cluster-name "$ADX_CLUSTER" --database-name "$KUSTO_DATABASE" \
    -g "$RG" --principal-assignment-name "$NAME" --principal-id "$CLIENT_ID" --principal-type App \
    --role "$ROLE" --tenant-id "$TENANT_ID" -o none
done

echo "== 9d. Container Apps environment"
az containerapp env show -n "$ACA_ENV" -g "$RG" -o none 2>/dev/null \
  || az containerapp env create -n "$ACA_ENV" -g "$RG" --location "$LOCATION" -o none

echo "== 9e. Scheduled job ($JOB_CRON)"
ENV_VARS=(KUSTO_CLUSTER_URL="$KUSTO_URI" KUSTO_DATABASE="$KUSTO_DATABASE" KUSTO_AUTH=default
          AZURE_CLIENT_ID="$CLIENT_ID" RECORD_ALERTS=true WEBHOOK_FORMAT="$WEBHOOK_FORMAT")
SECRET_ARGS=()
if [[ -n "$WEBHOOK_URL" ]]; then
  SECRET_ARGS=(--secrets "webhook-url=$WEBHOOK_URL")
  ENV_VARS+=(WEBHOOK_URL=secretref:webhook-url)
fi

if az containerapp job show -n "$JOB_NAME" -g "$RG" -o none 2>/dev/null; then
  if [[ -n "$WEBHOOK_URL" ]]; then
    az containerapp job secret set -n "$JOB_NAME" -g "$RG" --secrets "webhook-url=$WEBHOOK_URL" -o none
  fi
  az containerapp job update -n "$JOB_NAME" -g "$RG" --image "$IMAGE" --cron-expression "$JOB_CRON" \
    --set-env-vars "${ENV_VARS[@]}" -o none
  echo "Updated existing job."
else
  az containerapp job create -n "$JOB_NAME" -g "$RG" --environment "$ACA_ENV" \
    --trigger-type Schedule --cron-expression "$JOB_CRON" \
    --replica-timeout 300 --replica-retry-limit 0 --replica-completion-count 1 --parallelism 1 \
    --image "$IMAGE" --registry-server "$ACR_NAME.azurecr.io" --registry-identity "$IDENTITY_ID" \
    --mi-user-assigned "$IDENTITY_ID" --cpu 0.25 --memory 0.5Gi \
    ${SECRET_ARGS[@]+"${SECRET_ARGS[@]}"} --env-vars "${ENV_VARS[@]}" -o none
  echo "Created job."
fi

echo
echo "✅ Deployed. Run it now:   az containerapp job start -n $JOB_NAME -g $RG"
echo "   History:              az containerapp job execution list -n $JOB_NAME -g $RG -o table"
echo "   Logs:                 ./infra/logs.sh"
