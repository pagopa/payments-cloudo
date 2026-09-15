#!/usr/bin/env bash
set -euo pipefail

CONN_STR="${AZURITE_CONNECTION_STRING:-DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;}"
declare -a TABLE_NAME=(
  "RunbookSchemas"
  "RunbookLogs"
  "WorkersRegistry"
  "CloudoAuditLogs"
  "CloudoSchedules"
  "CloudoSettings"
  "CloudoUsers"
  "CloudoAiAnalysis"
)
declare -a QUEUE_NAME=(
  "local"
  "alert"
  "cloudo-ai-analysis"
)
WORKER="${1:-worker}"

command -v az >/dev/null 2>&1 || { echo "Error: 'az' not found in PATH"; exit 1; }

for i in "${TABLE_NAME[@]}"
do
  echo "Creating table '${i}' (idempotent)..."
  az storage table create \
    --name "${i}" \
    --connection-string "${CONN_STR}" \
    --only-show-errors >/dev/null
done

for i in "${QUEUE_NAME[@]}"
do
  echo "Creating queue '${i}' (idempotent)..."
  az storage queue create \
    --name "${i}" \
    --connection-string "${CONN_STR}" \
    --only-show-errors >/dev/null
done

echo "Add mock admin credential"
az storage entity insert \
  --table-name "CloudoUsers" \
  --entity \
    PartitionKey=Operator \
    RowKey=admin \
    password="admin" \
    role="ADMIN" \
  --if-exists merge \
  --connection-string "${CONN_STR}" \
  --only-show-errors >/dev/null

echo "Upserting test entity PK='test' RK='test-0001'..."
az storage entity insert \
  --table-name "RunbookSchemas" \
  --entity \
    PartitionKey=test \
    RowKey=test-0001 \
    id="test" \
    name=test-entity \
    description='Hello Test!' \
    worker=local \
    runbook=check_sys.sh \
    oncall=false \
    tags=terraform,test \
    require_approval=true \
  --if-exists merge \
  --connection-string "${CONN_STR}" \
  --only-show-errors >/dev/null

echo "Upserting test entity PK='test-2' RK='test-0002'..."
az storage entity insert \
  --table-name "RunbookSchemas" \
  --entity \
    PartitionKey=test-2 \
    RowKey=test-0002 \
    id="test-2" \
    name=test-entity-2 \
    description='Hello Test 2!' \
    worker=local \
    runbook=printenv.sh \
    run_args="" \
    oncall=true \
    tags=terraform,test \
  --if-exists merge \
  --connection-string "${CONN_STR}" \
  --only-show-errors >/dev/null

echo "Upserting test entity PK='test-3' RK='test-0003'..."
az storage entity insert \
  --table-name "RunbookSchemas" \
  --entity \
    PartitionKey=test-3 \
    RowKey=test-0003 \
    id="11111111-2222-3333-4444-555555555555" \
    name=test-entity-3 \
    description='Hello Test 3!' \
    worker=alert \
    runbook=check_sys.sh \
    oncall=false \
    tags=terraform,test \
  --if-exists merge \
  --connection-string "${CONN_STR}" \
  --only-show-errors >/dev/null

echo "Done."
