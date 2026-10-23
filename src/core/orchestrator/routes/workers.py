"""Worker registry endpoints and the stale-worker cleanup timer."""

import json
import logging
import os
from datetime import datetime, timedelta

import azure.functions as func
from core.config import (
    AUTH,
    DEFAULT_TEAM,
    STORAGE_CONN,
    TABLE_TEAMS,
    TABLE_WORKERS_SCHEMAS,
)
from utils import as_bool, create_cors_response

from core import security, storage, team_access

bp = func.Blueprint()


@bp.route(
    route="workers/register",
    methods=[func.HttpMethod.POST],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def register_worker(req: func.HttpRequest) -> func.HttpResponse:
    import utils
    from azure.data.tables import UpdateMode

    expected_key = os.environ.get("CLOUDO_SECRET_KEY")
    request_key = req.headers.get("x-cloudo-key")

    if not expected_key or request_key != expected_key:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    try:
        body = req.get_json()

        capability = (body.get("capability") or body.get("id") or "").strip()
        worker_instance_id = (body.get("worker_id") or "").strip()
        worker_queue = body.get("queue")
        worker_team = team_access.normalize_team(body.get("team"))

        if not capability or not worker_queue or not worker_instance_id:
            return func.HttpResponse(
                "Missing capability, worker_id or url", status_code=400
            )

        if worker_team != DEFAULT_TEAM:
            try:
                team_entity = storage.get_table_client(TABLE_TEAMS).get_entity(
                    partition_key="Team", row_key=worker_team
                )
                if not as_bool(team_entity.get("enabled"), default=True):
                    raise ValueError("Team is disabled")
            except Exception:
                return func.HttpResponse(
                    json.dumps({"error": "Worker team does not exist"}),
                    status_code=400,
                )

        table_client = storage.get_table_client(TABLE_WORKERS_SCHEMAS)

        entity = {
            "PartitionKey": capability,
            "RowKey": worker_instance_id,
            "Queue": worker_queue,
            "LastSeen": utils.utc_now_iso(),
            "Region": body.get("region", "default"),
            "Load": body.get("load", 0),
            "team": worker_team,
        }

        table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

        return func.HttpResponse(
            json.dumps({"status": "registered", "timestamp": entity["LastSeen"]}),
            status_code=200,
        )

    except Exception as e:
        logging.error(f"Register failed: {e}")
        return func.HttpResponse(str(e), status_code=500)


@bp.route(
    route="workers",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@bp.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def list_workers(req: func.HttpRequest, workers: str) -> func.HttpResponse:
    """
    Returns the list of registered workers available in the registry.
    """
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    try:
        # Parse binding result (can be string or list depending on extension version)
        # Monitoring views (?for=monitoring) show every node; other callers stay team-scoped.
        rows = storage.rows_from_binding(workers) or []
        if req.params.get("for") == "monitoring":
            data = [{**row, "team": team_access.entity_team(row)} for row in rows]
        else:
            data = team_access.filter_entities_by_team(rows, req, session)

        return func.HttpResponse(
            json.dumps(data, ensure_ascii=False),
            status_code=200,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to list workers: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@bp.route(
    route="workers/processes",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_worker_processes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy endpoint: calls the worker API from the backend.
    Expected param: worker (hostname/ip:port)
    """
    import requests

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    worker = req.params.get("worker")
    if not worker:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'worker' param"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    # Construct target URL (assuming http protocol for internal workers)
    # If your workers use https or a specific port logic, adjust here.
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        target_url = f"https://{worker}.azurewebsites.net/api/processes"
    else:
        target_url = f"http://{worker}/api/processes"

    try:
        # Timeout short to avoid blocking the orchestrator for too long
        resp = requests.get(
            target_url,
            headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
            timeout=5,
        )
        body_text = resp.text
        if resp.status_code == 200:
            try:
                parsed_body = resp.json()
                runs = (
                    parsed_body
                    if isinstance(parsed_body, list)
                    else (parsed_body.get("runs") or parsed_body.get("processes") or [])
                )
                can_act = session.get("role") != "VIEWER"
                for run in runs:
                    if isinstance(run, dict):
                        run_team = team_access.normalize_team(run.get("team"))
                        run["team"] = run_team
                        run["can_stop"] = can_act and (
                            team_access.is_admin(session)
                            or run_team == team_access.session_team(session)
                        )
                body_text = json.dumps(parsed_body, ensure_ascii=False)
            except Exception as e:
                logging.warning(f"Could not annotate worker processes: {e}")
        return func.HttpResponse(
            body_text,
            status_code=resp.status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to proxy processes for {worker}: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Failed to reach worker: {str(e)}"}, ensure_ascii=False
            ),
            status_code=502,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@bp.route(
    route="workers/stop",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def stop_worker_process(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy endpoint: calls the worker STOP API from the backend.
    Expected param: worker (hostname/ip:port), exec_id
    """
    import requests

    logging.debug(f"Stop worker process requested. Params: {req.params}")

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot stop processes"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    worker = req.params.get("worker")
    exec_id = req.params.get("exec_id")

    if not worker or not exec_id:
        return func.HttpResponse(
            json.dumps(
                {"error": "Missing 'worker' or 'exec_id' param"}, ensure_ascii=False
            ),
            status_code=400,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        target_url = (
            f"https://{worker}.azurewebsites.net/api/processes/stop?exec_id={exec_id}"
        )
    else:
        target_url = f"http://{worker}/api/processes/stop?exec_id={exec_id}"

    try:
        if not team_access.is_admin(session):
            # Non-admins can only stop runs of their own team.
            list_url = target_url.split("/processes/stop")[0] + "/processes"
            listing = requests.get(
                list_url,
                headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
                timeout=5,
            )
            listing.raise_for_status()
            payload = listing.json()
            runs = (
                payload
                if isinstance(payload, list)
                else (payload.get("runs") or payload.get("processes") or [])
            )
            run = next(
                (
                    r
                    for r in runs
                    if isinstance(r, dict) and str(r.get("exec_id")) == str(exec_id)
                ),
                None,
            )
            if run and team_access.normalize_team(
                run.get("team")
            ) != team_access.session_team(session):
                return func.HttpResponse(
                    json.dumps({"error": "Execution belongs to another team"}),
                    status_code=403,
                    mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
        resp = requests.delete(
            target_url,
            headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
            timeout=5,
        )
        return func.HttpResponse(
            resp.text,
            status_code=resp.status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to proxy stop for {worker}/{exec_id}: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Failed to reach worker: {str(e)}"}, ensure_ascii=False
            ),
            status_code=502,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@bp.schedule(
    schedule="0 */1 * * * *",
    arg_name="cleanupTimer",
    run_on_startup=False,
    use_monitor=False,
)
def worker_cleanup(cleanupTimer: func.TimerRequest) -> None:
    """
    Garbage Collector: Cleanup old workers where LastSeen is > 3 minutes.
    """
    import utils

    table_client = storage.get_table_client(TABLE_WORKERS_SCHEMAS)

    now_str = utils.utc_now_iso()
    now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
    limit_time = now_dt - timedelta(minutes=3)
    limit_iso = limit_time.isoformat()
    logging.debug(f"[Cleanup] Cleaning up {limit_iso}")

    filter_query = f"LastSeen lt '{limit_iso}'"

    logging.debug(f"[Cleanup] Searching for zombies older than {limit_iso}...")

    try:
        dead_workers = table_client.query_entities(query_filter=filter_query)

        count = 0
        for w in dead_workers:
            table_client.delete_entity(
                partition_key=w["PartitionKey"], row_key=w["RowKey"]
            )
            logging.debug(
                f"[Cleanup] Deleted zombie: {w['RowKey']} (Partition: {w['PartitionKey']})"
            )
            count += 1

        logging.debug(f"[Cleanup] Completed. Removed {count} workers.")

    except Exception as e:
        logging.error(f"[Cleanup] Failed: {e}")
