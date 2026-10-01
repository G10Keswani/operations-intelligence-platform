#!/usr/bin/env bash
# Step 2: resource group + Azure Data Explorer Dev cluster + database + Admin rights for you.
# Cluster creation takes ~10-20 minutes.
set -euo pipefail
source "$(dirname "$0")/env.sh"

echo "Subscription: $(az account show --query name -o tsv)"
az extension add --name kusto --upgrade --yes --only-show-errors
az provider register --namespace Microsoft.Kusto --wait

az group create --name "$RG" --location "$LOCATION" -o none
echo "Resource group $RG ready."

if ! az kusto cluster show --cluster-name "$ADX_CLUSTER" -g "$RG" -o none 2>/dev/null; then
  echo "Creating ADX cluster $ADX_CLUSTER (Dev/No-SLA SKU) - grab a coffee, ~10-20 min..."
  az kusto cluster create --cluster-name "$ADX_CLUSTER" -g "$RG" --location "$LOCATION" \
    --sku name="Dev(No SLA)_Standard_E2a_v4" tier="Basic" capacity=1 -o none
fi

if ! az kusto database show --cluster-name "$ADX_CLUSTER" --database-name "$KUSTO_DATABASE" -g "$RG" -o none 2>/dev/null; then
  az kusto database create --cluster-name "$ADX_CLUSTER" --database-name "$KUSTO_DATABASE" -g "$RG" \
    --read-write-database soft-delete-period=P30D hot-cache-period=P7D location="$LOCATION" -o none
fi
echo "Database $KUSTO_DATABASE ready."

TENANT_ID="$(az account show --query tenantId -o tsv)"
ME="$(az ad signed-in-user show --query id -o tsv)"
az kusto database-principal-assignment show --cluster-name "$ADX_CLUSTER" --database-name "$KUSTO_DATABASE" \
  -g "$RG" --principal-assignment-name "demo-admin-me" -o none 2>/dev/null \
|| az kusto database-principal-assignment create --cluster-name "$ADX_CLUSTER" --database-name "$KUSTO_DATABASE" \
  -g "$RG" --principal-assignment-name "demo-admin-me" --principal-id "$ME" --principal-type User \
  --role Admin --tenant-id "$TENANT_ID" -o none

URI="$(az kusto cluster show --cluster-name "$ADX_CLUSTER" -g "$RG" --query uri -o tsv)"
echo
echo "✅ Cluster URL: $URI"
echo "Next: export KUSTO_CLUSTER_URL=$URI KUSTO_DATABASE=$KUSTO_DATABASE KUSTO_AUTH=cli"
