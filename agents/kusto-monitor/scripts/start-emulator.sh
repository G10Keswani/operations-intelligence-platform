#!/usr/bin/env bash
# Start the free local Kusto emulator (no Azure subscription needed) and wait until it answers.
set -euo pipefail

NAME=kusto-emulator
IMAGE=mcr.microsoft.com/azuredataexplorer/kustainer-linux:latest

if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "Emulator already running on http://localhost:8080"
  exit 0
fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -e ACCEPT_EULA=Y -m 4G -p 8080:8080 "$IMAGE" >/dev/null

echo -n "Waiting for the emulator"
for _ in $(seq 1 60); do
  if curl -s -X POST http://localhost:8080/v1/rest/mgmt -H 'Content-Type: application/json' \
       -d '{"csl":".show cluster"}' >/dev/null 2>&1; then
    echo " ready on http://localhost:8080"
    exit 0
  fi
  echo -n "."; sleep 5
done
echo " timed out. Check: docker logs $NAME" >&2
exit 1
