import json
import logging
import os

import azure.functions as func
import utils
from analyzer import analyze
from jsm_notes import add_jsm_alert_note, format_triage_note
from models import FailedRunbookAlert
from utils import STORAGE_CONNECTION
from utils import (  # noqa: F401  (used by fastapi_app poller)
    get_queue_client as _get_queue_client,
)

# =========================
# Constants and Utilities
# =========================

# Fallback defaults (env-based) used when no Table Storage override exists;
# see utils.get_setting() for the Table-Storage-first resolution used below,
# which lets the admin UI (Settings -> AI Agent) reconfigure this service
# without a redeploy.
AI_ANALYSIS_QUEUE_NAME = os.getenv("AI_ANALYSIS_QUEUE_NAME", "cloudo-ai-analysis")
JSM_API_KEY_DEFAULT = (
    os.getenv("JSM_API_KEY_DEFAULT") or os.getenv("JSM_API_KEY") or ""
).strip()

if os.getenv("LOCAL_DEV", "false").lower() != "true":
    AUTH = func.AuthLevel.FUNCTION
else:
    AUTH = func.AuthLevel.ANONYMOUS

app = func.FunctionApp()


def _json_response(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
    )


# =========================
# Core processing (shared by queue trigger, HTTP endpoint and the FastAPI
# background poller used when this service runs as a plain container).
# =========================


def analyze_failed_runbook_payload(raw_payload) -> dict:
    """Triage a failed/errored runbook execution and, asynchronously with
    respect to the run itself, attach the resulting note to the JSM Ops alert
    so whoever is on-call gets context without waiting on the LLM round-trip.
    """
    try:
        payload = (
            json.loads(raw_payload)
            if isinstance(raw_payload, (str, bytes))
            else raw_payload
        )
    except (TypeError, ValueError) as exc:
        logging.error("Agent: invalid JSON payload: %s", exc)
        return {"error": f"invalid_json: {exc}"}

    try:
        alert = FailedRunbookAlert.from_payload(payload)
    except ValueError as exc:
        logging.error("Agent: invalid alert payload: %s", exc)
        return {"error": str(exc)}

    logging.info(
        "[%s] Agent: received failed runbook alert (runbook=%s, alias=%s)",
        alert.exec_id,
        alert.runbook,
        alert.alias,
    )

    result = analyze(alert)
    note = format_triage_note(result.to_dict())

    # "error" only for a total LLM failure (no usable text at all); a
    # response that failed strict JSON parsing but still has some text is
    # still useful to display, so it's reported as "completed" with `error`
    # carrying the parse warning for transparency.
    utils.write_analysis_result(
        exec_id=alert.exec_id,
        status="error" if result.summary == "AI analysis unavailable." else "completed",
        analysis=result.to_dict(),
        error=result.error,
    )

    api_key = utils.get_setting("JSM_API_KEY_DEFAULT", JSM_API_KEY_DEFAULT) or (
        str(payload.get("jsm_api_key") or "") if isinstance(payload, dict) else ""
    )
    logging.debug(
        "[%s] Agent: posting JSM note (api_key_configured=%s)",
        alert.exec_id,
        bool(api_key),
    )
    posted = add_jsm_alert_note(api_key=api_key, alias=alert.alias, note=note)

    logging.info(
        "[%s] AI triage completed (confidence=%s, jsm_note_posted=%s)",
        alert.exec_id,
        result.confidence,
        posted,
    )
    return {
        "exec_id": alert.exec_id,
        "analysis": result.to_dict(),
        "jsm_note_posted": posted,
    }


# =========================
# Queue trigger: AnalyzeFailedRunbook
# =========================


@app.queue_trigger(
    arg_name="msg", queue_name=AI_ANALYSIS_QUEUE_NAME, connection=STORAGE_CONNECTION
)
def AnalyzeFailedRunbook(msg: func.QueueMessage) -> None:
    """Consumes failed/errored runbook executions enqueued by the
    orchestrator (queue: AI_ANALYSIS_QUEUE_NAME) and produces an asynchronous
    AI triage note on the related JSM alert."""
    try:
        body = msg.get_body().decode("utf-8")
        logging.info(
            "Agent: dequeued message id=%s from %s", msg.id, AI_ANALYSIS_QUEUE_NAME
        )
        analyze_failed_runbook_payload(body)
    except Exception as exc:
        logging.error(
            "Agent: failed to process queue message id=%s: %s",
            msg.id,
            exc,
            exc_info=True,
        )


# =========================
# HTTP Endpoint: Analyze (manual / on-demand re-analysis)
# =========================


@app.route(route="analyze", methods=[func.HttpMethod.POST], auth_level=AUTH)
def Analyze(req: func.HttpRequest) -> func.HttpResponse:
    """Synchronous HTTP endpoint, mainly for manual testing/local dev and for
    on-demand re-analysis triggered from the ClouDO UI."""
    try:
        body = req.get_json()
    except ValueError as exc:
        return _json_response({"error": f"Invalid JSON: {exc}"}, status_code=400)

    result = analyze_failed_runbook_payload(body)
    status_code = 400 if "error" in result else 200
    return _json_response(result, status_code=status_code)


# =========================
# HTTP Endpoint: healthz
# =========================


@app.route(route="healthz", auth_level=AUTH)
def Healthz(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({"status": "ok", "service": "agent"})
