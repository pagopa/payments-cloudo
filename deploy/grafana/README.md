# CloudO Grafana dashboard

`cloudo-dashboard.json` gives you an overview of CloudO built on two datasources:

| Section                                                             | Datasource    | Source                                                                |
| ------------------------------------------------------------------- | ------------- | --------------------------------------------------------------------- |
| Executions (KPIs, per hour, per runbook/worker, failures with logs) | Infinity      | `GET /api/logs/query` (`RunbookLogs` table)                           |
| Workers (registry, heartbeat, running processes)                    | Infinity      | `GET /api/workers?for=monitoring`, `GET /api/workers/processes`       |
| Azure platform (requests, 5xx, latency, memory, queues, storage)    | Azure Monitor | Metrics for `Microsoft.Web/sites` and the `*cloudosa` storage account |

## Setup

1. **Infinity datasource**
   - _Authentication_: none. Under _Custom HTTP headers_, add `x-cloudo-key` with a personal API token
     (a VIEWER user works) or `CLOUDO_SECRET_KEY`.
   - _Allowed hosts_: `https://<prefix>-cloudo-orchestrator.azurewebsites.net`.
   - The orchestrator is only reachable from `vpn_subnet_id`. The Grafana instance needs
     network access to that endpoint.
2. **Azure Monitor datasource**: the identity needs `Monitoring Reader` on the CloudO resource group.
3. Import the JSON (_Dashboards → New → Import_). Choose the datasources, then set the `Orchestrator URL` variable
   and save it as the default value.

## Notes

- The `RunbookLogs` table is partitioned by day (`YYYYMMDD`, Europe/Rome). The Infinity panels show
  the **day the time range ends on** (`${__to:date:YYYYMMDD}`). To look at a past day, move the time picker.
- Execution counts use `latestOnly=true`, so each execution is counted once, in its final state.
- Worker and orchestrator run under uvicorn without OpenTelemetry, so App Insights does not receive application
  traces. To get KQL queries over long time ranges, enable `azure-monitor-opentelemetry` in the
  `requirements.txt` files.
