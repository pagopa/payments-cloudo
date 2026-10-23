"""Running process inspection and stop endpoints."""

import json
import logging

import azure.functions as func
from core.config import NOTIFICATION_QUEUE_NAME, STORAGE_CONNECTION, WORKER_TEAM
from utils import _utc_now_iso

from core import registry, reporting, security

bp = func.Blueprint()


@bp.route(
    route="processes",
    methods=[func.HttpMethod.GET],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def list_processes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Lists the "in progress" runs of the RunbookTest endpoint (jobs not yet completed).
    Parameters:
    - q: text filter on exec_id, id, name, runbook (optional)

    Example:
    - GET /api/processes — returns only the “running” runs of RunbookTest on this instance
    - GET /api/processes?q=python — Filter by text on exec_id, id, name, runbook

    """
    if not security.is_authorized_request(req):
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    q = (req.params.get("q") or "").lower().strip()
    with registry.ACTIVE_LOCK:
        items = list(registry.ACTIVE_RUNS.values())

    if q:

        def match(item: dict) -> bool:
            return any(
                (str(item.get(k) or "").lower().find(q) != -1)
                for k in ("exec_id", "id", "name", "runbook")
            )

        items = [i for i in items if match(i)]

    # Order by startedAt desc
    items.sort(key=lambda x: x.get("startedAt") or "", reverse=True)

    body = json.dumps(
        {
            "status": "ok",
            "time": _utc_now_iso(),
            "count": len(items),
            "runs": items,
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(
        body,
        status_code=200,
        mimetype="application/json",
        headers={
            **{"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        },
    )


@bp.route(
    route="processes/stop",
    methods=[func.HttpMethod.DELETE],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@bp.queue_output(
    arg_name="cloudo_notification_q",
    queue_name=NOTIFICATION_QUEUE_NAME,
    connection=STORAGE_CONNECTION,
)
def stop_process(
    req: func.HttpRequest, cloudo_notification_q: func.Out[str]
) -> func.HttpResponse:
    """
    Stop a job by exec_id.
    example: POST /api/processes/stop?exec_id=123
    """

    if not security.is_authorized_request(req):
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    exec_id = (req.params.get("exec_id") or req.headers.get("ExecId") or "").strip()
    if not exec_id:
        return func.HttpResponse(
            json.dumps({"error": "exec_id missing"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    with registry.ACTIVE_LOCK:
        proc = registry.PROCESS_BY_EXEC.get(exec_id)
        run_info = registry.ACTIVE_RUNS.get(exec_id)

    if not proc:
        return func.HttpResponse(
            json.dumps({"status": "not_found", "exec_id": exec_id}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    try:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as e:
                logging.error(f"[{exec_id}] terminate error: {e}")
            try:
                proc.wait(timeout=10)
            except Exception:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception as e:
                        logging.error(f"[{exec_id}] kill error: {e}")
        status = "stopped"
        code = 200
    except Exception as e:
        status = f"error: {type(e).__name__}: {e}"
        code = 500

    try:
        if run_info:
            run_info["status"] = "stopped"
            payload = {
                "runbook": run_info.get("runbook"),
                "run_args": run_info.get("run_args"),
                "id": run_info.get("id"),
                "name": run_info.get("name"),
                "exec_id": run_info.get("exec_id"),
                "worker": run_info.get("worker"),
                "group": run_info.get("group"),
                "initiator": run_info.get("initiator"),
                "oncall": None,
                "monitor_condition": None,
                "severity": None,
                "requestedAt": run_info.get("requestedAt"),
                "team": run_info.get("team") or WORKER_TEAM,
            }
            reporting.dispatch_status(
                payload,
                status="stopped",
                log_message=f"Execution {exec_id} stopped by request",
                queue_out=cloudo_notification_q,
            )
    except Exception:
        logging.warning("[%s] Unable to send status stop", exec_id)

    return func.HttpResponse(
        json.dumps({"status": status, "exec_id": exec_id}, ensure_ascii=False),
        status_code=code,
        mimetype="application/json",
    )
