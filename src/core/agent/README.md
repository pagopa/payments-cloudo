# ClouDO Agent

AI agent that analyzes failed/errored runbook executions (and the alert that
triggered them) using an LLM, and asynchronously attaches a triage note to
the corresponding JSM Ops alert — so whoever is on-call gets context
(probable root cause, recommended actions) without waiting on the LLM
round-trip.

It follows the same dual Azure Functions / FastAPI structure used by the
`orchestrator` and `worker` services:

- `function_app.py` — Azure Functions style handlers (`azure.functions`
  decorators): a queue trigger plus two HTTP endpoints.
- `fastapi_app.py` — the uvicorn/FastAPI entry point used when the service
  runs as a plain container (AKS/docker-compose). Runs a background thread
  that polls the same queue the Azure Functions trigger listens on.
- `analyzer.py` — LLM client abstraction (OpenAI or Azure OpenAI) and prompt
  building.
- `jsm_notes.py` — posts an asynchronous note to an existing JSM Ops alert
  (identified by alias == the schema id used by the orchestrator's
  smart-routing JSM alert, see `src/core/orchestrator/escalation.py`).
- `models.py` — `FailedRunbookAlert`, the normalized view of a failed
  execution consumed by the analyzer.
- `utils.py` — `get_setting()`, a Table-Storage-first/env-fallback resolver
  shared by `analyzer.py`/`function_app.py` so the service can be
  reconfigured live from the ClouDO admin UI (see below) without a redeploy.

## How it fits in ClouDO

1. The orchestrator's `Trigger`/`Receiver` flow already creates a JSM Ops
   alert (alias = `exec_id`) when a runbook execution fails.
2. When `AI_AGENT_ENABLED=true`, the orchestrator additionally enqueues the
   same execution payload onto `AI_ANALYSIS_QUEUE_NAME` (default
   `cloudo-ai-analysis`).
3. This service consumes that queue, calls the configured LLM to triage the
   failure, and posts the resulting summary as a note on the existing JSM
   alert — entirely asynchronously, never blocking the runbook execution
   path.

## Configuration

Every value below can be set either as an environment variable (Helm values /
docker-compose, applied at deploy time) **or** from the ClouDO admin UI
(`Administration -> AI Agent`, applied live within `AGENT_SETTINGS_CACHE_TTL_SECONDS`,
default 60s, no redeploy needed) — the UI writes to the same `CloudoSettings`
Table Storage used by the orchestrator's Smart Routing settings.

| Env var / Setting                                                             | Default              | Description                                                                                                                                          |
| ----------------------------------------------------------------------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AI_AGENT_ENABLED`                                                            | `false`              | Feature flag read by the orchestrator to enqueue failures onto `AI_ANALYSIS_QUEUE_NAME`.                                                             |
| `AI_ANALYSIS_QUEUE_NAME`                                                      | `cloudo-ai-analysis` | Storage queue this service consumes (env-only; infra-managed).                                                                                       |
| `AGENT_LLM_PROVIDER`                                                          | `openai`             | `openai`, `azure_openai` or `copilot_sdk` (GitHub Copilot token).                                                                                    |
| `AGENT_LLM_MODEL`                                                             | `gpt-4o-mini`        | Model name/deployment (unused when provider is `copilot_sdk`, see `COPILOT_MODEL` below).                                                            |
| `OPENAI_API_KEY`                                                              | —                    | Required when provider is `openai`.                                                                                                                  |
| `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_VERSION` | —                    | Required when provider is `azure_openai`.                                                                                                            |
| `COPILOT_GITHUB_TOKEN`                                                        | —                    | Required when provider is `copilot_sdk`. Must be a **fine-grained PAT with the "Copilot Requests" permission** — classic `ghp_` tokens are rejected. |
| `COPILOT_MODEL`                                                               | — (Copilot default)  | Optional model id passed to the Copilot SDK session; leave empty to let Copilot choose.                                                              |
| `JSM_API_KEY_DEFAULT`                                                         | —                    | GenieKey used to post notes to JSM Ops alerts (shared with Smart Routing defaults).                                                                  |
| `AGENT_LLM_TIMEOUT_SECONDS`                                                   | `30`                 | LLM request timeout.                                                                                                                                 |
| `AGENT_MAX_LOG_CHARS`                                                         | `8000`               | Tail of the execution logs sent to the LLM.                                                                                                          |

When `AGENT_LLM_PROVIDER=copilot_sdk`, the agent authenticates via the
official `github-copilot-sdk` package instead of an OpenAI/Azure OpenAI API
key. The SDK's native runtime binary is pre-downloaded at Docker build time
(`python -m copilot download-runtime`) so requests never pay a cold-start
network dependency; see `copilot_provider.py` for the adapter (a private
asyncio event loop bridges the SDK's async API to this service's synchronous
`analyze()` flow) and `Dockerfile` for the build-time step.

## Admin UI

The ClouDO UI exposes a dedicated `AI Agent` section (admin-only, under
`Administration` in the sidebar) to toggle the feature and edit all the
settings above without touching Helm values or restarting any pod. See
`src/core/cloudo-ui/app/ai-agent/page.tsx`.

## Local endpoints

- `POST /api/analyze` — synchronous, manual/on-demand triage of a given
  execution payload.
- `GET /api/healthz` — health check.

## Tests

```bash
cd src/core/agent
python -m pytest tests/ -q
```
