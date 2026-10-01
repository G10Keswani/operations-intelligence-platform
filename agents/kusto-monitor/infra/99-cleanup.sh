#!/usr/bin/env bash
# Delete EVERYTHING created for the demo (the whole resource group). Irreversible.
set -euo pipefail
source "$(dirname "$0")/env.sh"

read -r -p "Delete resource group '$RG' and everything in it? Type the name to confirm: " ANSWER
[[ "$ANSWER" == "$RG" ]] || { echo "Aborted."; exit 1; }
az group delete --name "$RG" --yes --no-wait
echo "Deletion started (runs in the background, ~10 min)."
