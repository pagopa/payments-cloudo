# Runbook examples

Templates and worked examples for writing ClouDO runbooks.

This folder is deliberately outside `src/runbooks`, because everything under the
configured runbook path appears in the catalog and can be selected in a schema. To
use one of these files, copy it into your runbook path and adapt it.

The worker downloads runbooks from GitHub at execution time, so a runbook merged into
the configured branch is live without a deploy. The same applies when you move or
delete one: schemas that point to it start failing.

## Templates

Starting points that fail with "not implemented" until you fill them in, so that a
schema pointing to an unfinished template cannot report a false success.

| Template                                     | Use it for                         |
| -------------------------------------------- | ---------------------------------- |
| `templates/template-bash-alert-handler.sh`   | Alert handler in bash              |
| `templates/template_python_alert_handler.py` | Alert handler in Python            |
| `templates/template-bash-manual.sh`          | Manual runbook with args in bash   |
| `templates/template_python_manual.py`        | Manual runbook with args in Python |

## Examples

Each file lists the schema fields it needs in its header.

| Example                                       | Use case                                  | Trigger          | Changes things | AKS login | What it shows                                                    |
| --------------------------------------------- | ----------------------------------------- | ---------------- | -------------- | --------- | ---------------------------------------------------------------- |
| `aks/collect-diagnostics.sh`                  | Capture pod state when an alert fires     | Alert            | No             | Yes       | Best-effort steps, a safe first automation                       |
| `aks/restart-deployment.sh`                   | Restart an unhealthy deployment           | Alert            | Yes            | Yes       | Failing if the rollout does not complete                         |
| `aks/scale-keda-max-replicas.sh`              | Allow more replicas during an alert       | Alert            | Yes            | Yes       | Fired and Resolved as a pair, idempotency                        |
| `aks/rerun-failed-job.sh`                     | Retry a failed CronJob run                | Alert            | Yes            | Yes       | `AKS_JOB`, a single retry per failure                            |
| `azure/appgw-backend-health.sh`               | Check Application Gateway backends        | Alert, schedule  | No             | No        | Azure CLI with Managed Identity                                  |
| `azure/resource_metrics.py`                   | Check a metric against a threshold        | Alert, schedule  | No             | No        | Azure SDK, reading `CLOUDO_PAYLOAD`                              |
| `azure/scale-node-pool.sh`                    | Add capacity to a node pool               | Manual           | Yes            | No        | `run_args`, dry run by default, approval                         |
| `azure/keyvault_expiring_secrets.py`          | Find secrets about to expire              | Schedule         | No             | No        | Reading metadata without secret values                           |
| `database/postgres_health.py`                 | Check connections, long queries and locks | Alert, schedule  | No             | No        | Entra authentication, a read-only session, no query text in logs |
| `database/postgres_terminate_long_queries.py` | Free a database from stuck sessions       | Manual           | Yes            | No        | Dry run, exclusions, a limit per run                             |
| `messaging/requeue_poison_messages.py`        | Retry messages after fixing a consumer    | Manual           | Yes            | No        | Moving data without losing it, a limit per run                   |
| `messaging/servicebus-queue-depth.sh`         | Check backlog and dead letters            | Alert, schedule  | No             | No        | Thresholds as `run_args`                                         |
| `application/http-health-check.sh`            | Check an endpoint from the worker         | Schedule, manual | No             | No        | Retries with a bounded number of attempts                        |
| `application/tls_cert_expiry.py`              | Find certificates about to expire         | Schedule         | No             | No        | No Azure access, several targets in one run                      |
| `application/restart-app-service.sh`          | Restart an unhealthy App Service          | Alert            | Yes            | No        | Verifying the outcome, approval                                  |
| `application/frontdoor-purge-cache.sh`        | Remove stale content from the edge        | Manual           | Yes            | No        | Dry run, explicit paths                                          |

## What the worker guarantees

**Outcome.** The exit code decides it: `0` is succeeded, anything else is failed.

**Logs.** Write to stdout. It is streamed while the runbook runs and saved as the
execution log. Stderr only appears when the runbook fails, and it is read after stdout
closes, so writing a lot to it can block the runbook.

**Files.** A runbook is downloaded as a single file and runs in a temporary directory,
so it cannot source helpers or read files stored next to it.

**Arguments.** `run_args` from the schema are split like a shell command line and
passed to the script.

**Environment.**

| Variable                                                           | Content                                   |
| ------------------------------------------------------------------ | ----------------------------------------- |
| `RESOURCE_ID`, `RESOURCE_RG`, `RESOURCE_NAME`                      | The Azure resource named by the alert     |
| `AKS_NAMESPACE`, `AKS_POD`, `AKS_DEPLOYMENT`, `AKS_JOB`, `AKS_HPA` | The Kubernetes objects named by the alert |
| `MONITOR_CONDITION`                                                | `Fired` or `Resolved`                     |
| `CLOUDO_PAYLOAD`                                                   | The alert's full `resource_info`, as JSON |
| `CLOUDO_EXEC_ID`, `CLOUDO_NAME`, `CLOUDO_RUNBOOK`, `CLOUDO_WORKER` | The current execution                     |
| `CLOUDO_ENVIRONMENT`                                               | The environment of the worker             |
| `KUBECONFIG`                                                       | Only set with `aks_login_required`        |

- Variables whose name contains `TOKEN`, `SECRET`, `KEY`, `PASS`, `PWD`, `AUTH` or
  similar reach the runbook masked as `******`. To use a secret, read it from Key Vault.

**AKS.** With `aks_login_required`, `kubectl` works without any login in the script:

- The token lasts 10 minutes, so keep every wait below that.
- Access is limited to the namespace, which rules out cluster-wide resources such as
  nodes. The default role covers workloads, jobs, HPAs, ScaledObjects and core objects.

**Azure.** The worker runs headless, so log in with `az login --identity` and never fall
back to an interactive login. Set the subscription if `AZURE_SUBSCRIPTION_ID` is present.

**Databases.** The worker image includes `psql` and `psycopg2`. Connect with a Microsoft
Entra token, as `database/postgres_health.py` does. If the server only supports passwords,
read the password from Key Vault at runtime: `PGPASSWORD` would reach the runbook masked.

## Writing a good runbook

- **Make it safe to run twice.** Alerts can fire repeatedly. Check the current state
  first, as `aks/rerun-failed-job.sh` does, rather than assuming a first run.
- **Decide what Resolved does.** Roll back, or exit with `0` and log that there is
  nothing to do. Do not let Resolved fall through to the remediation.
- **Check required variables up front**, with a clear error message.
- **Keep sensitive data out of the log.** Do not print query text, message content or
  secret values. Never pass secrets in `run_args`: the worker logs the full command line.
- **Limit what automation can do**, with a hard limit like the one in
  `aks/scale-keda-max-replicas.sh`, and use `require_approval` for risky actions.

## Trying a runbook

Paste it into the UI's Studio to run it on a worker without merging anything, or copy it
under `src/runbooks` to find it in the catalog of your local environment. To check the
logic without a cluster, put stub `kubectl` and `az` commands on your `PATH`.

## Adding an example

Add it to the table above and list its schema fields in the file header. Mark `.sh` files
as executable. `make test-worker` checks the syntax of everything in this folder.
