"""Schedule management endpoints and the scheduler timer."""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

import azure.functions as func
from core.config import (
    AUTH,
    DEFAULT_TEAM,
    TABLE_NAME,
    TABLE_SCHEDULES,
    TABLE_WORKERS_SCHEMAS,
)
from utils import as_bool, create_cors_response

from core import audit, executions, security, storage, team_access

bp = func.Blueprint()


@bp.route(
    route="schedules",
    methods=[
        func.HttpMethod.GET,
        func.HttpMethod.POST,
        func.HttpMethod.DELETE,
        func.HttpMethod.OPTIONS,
    ],
    auth_level=AUTH,
)
def schedules_management(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    from azure.data.tables import UpdateMode

    table_client = storage.get_table_client(TABLE_SCHEDULES)

    def _is_terraform_locked(entity: Optional[dict[str, Any]]) -> bool:
        if not entity:
            return False
        managed_by = str(entity.get("managed_by") or "").strip().lower()
        locked = as_bool(entity.get("locked"), default=False)
        return managed_by == "terraform" or locked

    # Verification of authentication
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if req.method in ["POST", "DELETE"] and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify schedules"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method == "GET":
        try:
            entities = table_client.query_entities(
                query_filter="PartitionKey eq 'Schedule'"
            )
            entities = team_access.filter_entities_by_team(list(entities), req, session)
            schedules = []
            for e in entities:
                schedules.append(
                    {
                        "id": e.get("RowKey"),
                        "name": e.get("name"),
                        "cron": e.get("cron"),
                        "runbook": e.get("runbook"),
                        "run_args": e.get("run_args"),
                        "queue": e.get("queue"),
                        "worker_pool": e.get("worker_pool"),
                        "enabled": e.get("enabled"),
                        "oncall": e.get("oncall"),
                        "last_run": e.get("last_run"),
                        "managed_by": e.get("managed_by") or "manual",
                        "locked": _is_terraform_locked(e),
                        "team": team_access.entity_team(e),
                    }
                )
            return func.HttpResponse(
                json.dumps(schedules),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception:
            return func.HttpResponse(
                json.dumps([]),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "POST":
        try:
            body = req.get_json()
            if not isinstance(body, dict):
                return func.HttpResponse(
                    json.dumps({"error": "Invalid request body"}),
                    status_code=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            schedule_id = body.get("id") or str(uuid.uuid4())

            existing = None
            try:
                existing = table_client.get_entity(
                    partition_key="Schedule", row_key=schedule_id
                )
            except Exception:
                existing = None

            if existing and not team_access.can_modify_entity(existing, session):
                return func.HttpResponse(
                    json.dumps({"error": "Schedule belongs to another team"}),
                    status_code=403,
                )

            team, team_error = team_access.requested_write_team(body, session, existing)
            if team_error:
                return func.HttpResponse(
                    json.dumps({"error": team_error}), status_code=403
                )

            if _is_terraform_locked(existing):
                immutable_fields = [
                    "name",
                    "cron",
                    "runbook",
                    "run_args",
                    "queue",
                    "worker_pool",
                ]
                for field in immutable_fields:
                    if field in body and (body.get(field) or "") != (
                        existing.get(field) or ""
                    ):
                        return func.HttpResponse(
                            json.dumps(
                                {
                                    "error": "Terraform-managed schedule allows only enabled toggle. Other fields are read-only."
                                }
                            ),
                            status_code=403,
                            headers={"Access-Control-Allow-Origin": "*"},
                        )
                if "oncall" in body and as_bool(
                    body.get("oncall"), default=True
                ) != as_bool(existing.get("oncall"), default=True):
                    return func.HttpResponse(
                        json.dumps(
                            {
                                "error": "Terraform-managed schedule allows only enabled toggle. Other fields are read-only."
                            }
                        ),
                        status_code=403,
                        headers={"Access-Control-Allow-Origin": "*"},
                    )

                entity = {
                    "PartitionKey": "Schedule",
                    "RowKey": schedule_id,
                    "name": existing.get("name"),
                    "cron": existing.get("cron"),
                    "runbook": existing.get("runbook"),
                    "run_args": existing.get("run_args"),
                    "queue": existing.get("queue"),
                    "worker_pool": existing.get("worker_pool"),
                    "enabled": as_bool(
                        body.get("enabled"),
                        default=as_bool(existing.get("enabled"), default=True),
                    ),
                    "oncall": as_bool(existing.get("oncall"), default=True),
                    "last_run": existing.get("last_run", ""),
                    "managed_by": existing.get("managed_by", "terraform"),
                    "locked": as_bool(existing.get("locked"), default=True),
                    "team": team,
                }
            else:
                entity = {
                    "PartitionKey": "Schedule",
                    "RowKey": schedule_id,
                    "name": body.get("name"),
                    "cron": body.get("cron"),
                    "runbook": body.get("runbook"),
                    "run_args": body.get("run_args"),
                    "queue": body.get("queue"),
                    "worker_pool": body.get("worker_pool"),
                    "enabled": as_bool(body.get("enabled"), default=True),
                    "oncall": as_bool(body.get("oncall"), default=True),
                    "last_run": (existing or {}).get("last_run", ""),
                    "managed_by": (existing or {}).get("managed_by", "manual"),
                    "locked": as_bool((existing or {}).get("locked"), default=False),
                    "team": team,
                }
            table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

            audit.log_audit(
                user=session.get("username") or "SYSTEM",
                action="SCHEDULE_UPSERT",
                target=schedule_id,
                details=f"Name: {entity.get('name')}, Cron: {entity.get('cron')}",
            )
            return func.HttpResponse(
                json.dumps({"success": True, "id": schedule_id}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "DELETE":
        try:
            schedule_id = req.params.get("id")
            if not schedule_id:
                return func.HttpResponse(
                    json.dumps({"error": "Missing id"}),
                    status_code=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            try:
                existing = table_client.get_entity(
                    partition_key="Schedule", row_key=schedule_id
                )
            except Exception:
                return func.HttpResponse(
                    json.dumps({"error": f"Schedule '{schedule_id}' not found"}),
                    status_code=404,
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            if _is_terraform_locked(existing):
                return func.HttpResponse(
                    json.dumps(
                        {
                            "error": "Terraform-managed schedule is read-only and cannot be deleted."
                        }
                    ),
                    status_code=403,
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            if not team_access.can_modify_entity(existing, session):
                return func.HttpResponse(
                    json.dumps({"error": "Schedule belongs to another team"}),
                    status_code=403,
                )

            table_client.delete_entity(partition_key="Schedule", row_key=schedule_id)
            audit.log_audit(
                user=session.get("username") or "SYSTEM",
                action="SCHEDULE_DELETE",
                target=schedule_id,
            )
            return func.HttpResponse(
                json.dumps({"success": True}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )


@bp.schedule(
    schedule="0 */1 * * * *",
    arg_name="schedulerTimer",
    run_on_startup=False,
    use_monitor=False,
)
def scheduler_engine(schedulerTimer: func.TimerRequest) -> None:
    """
    Scheduler Engine: Check for scheduled runbooks and execute them.
    """
    from zoneinfo import ZoneInfo

    from utils import format_requested_at, is_cron_now, today_partition_key

    table_client = storage.get_table_client(TABLE_SCHEDULES)
    log_table_client = storage.get_table_client(TABLE_NAME)
    workers_table = storage.get_table_client(TABLE_WORKERS_SCHEMAS)

    try:
        worker_pool_to_queue: dict[tuple[str, str], str] = {}
        try:
            workers = workers_table.query_entities(
                query_filter="PartitionKey ne ''",
                select=["PartitionKey", "Queue", "team"],
            )
            for w in workers:
                pool = str(w.get("PartitionKey") or "").strip()
                queue_name = str(w.get("Queue") or "").strip()
                worker_team = team_access.entity_team(w)
                if (
                    pool
                    and queue_name
                    and (pool, worker_team) not in worker_pool_to_queue
                ):
                    worker_pool_to_queue[(pool, worker_team)] = queue_name
        except Exception as werr:
            logging.error(f"[Scheduler] Failed to load WorkersRegistry map: {werr}")

        schedules = table_client.query_entities(
            query_filter="PartitionKey eq 'Schedule'"
        )
        now = datetime.now(ZoneInfo("Europe/Rome"))
        # FastAPI-based scheduler can drift by 1-2s from the minute boundary.
        # Normalize seconds for cron matching to avoid missing "0 * * * * *" jobs.
        now_for_cron = now.replace(second=0, microsecond=0)

        for s in schedules:
            # Terraform-backed entities can store booleans as strings ("true"/"false").
            if not as_bool(s.get("enabled"), default=True):
                continue

            cron_expr = s.get("cron", "0 */1 * * * *")
            last_run_str = s.get("last_run", "")
            oncall_enabled = as_bool(s.get("oncall"), default=True)

            should_run_by_cron = is_cron_now(cron_expr, now_for_cron)

            should_run = False
            if should_run_by_cron:
                if not last_run_str:
                    should_run = True
                else:
                    last_run_dt = datetime.fromisoformat(
                        last_run_str.replace("Z", "+02:00")
                    )
                    if (now - last_run_dt).total_seconds() >= 45:
                        should_run = True

            if should_run:
                exec_id = str(uuid.uuid4())
                logging.info(
                    f"[Scheduler] Triggering {s['name']} (ID: {s['RowKey']}) -> ExecId: {exec_id}"
                )

                worker_pool = s.get("worker_pool")
                schedule_team = team_access.entity_team(s)
                target_queue = "cloudo-default"

                if worker_pool:
                    target_queue = worker_pool_to_queue.get(
                        (worker_pool, schedule_team),
                        worker_pool_to_queue.get(
                            (worker_pool, DEFAULT_TEAM), target_queue
                        ),
                    )

                requested_at = format_requested_at()
                partition_key = today_partition_key()

                queue_payload = {
                    "runbook": s.get("runbook"),
                    "run_args": s.get("run_args"),
                    "worker": worker_pool,
                    "group": "-",
                    "exec_id": exec_id,
                    "id": s.get("RowKey"),
                    "name": s.get("name"),
                    "status": "scheduled",
                    "oncall": oncall_enabled,
                    "require_approval": False,
                    "requested_at": requested_at,
                    "team": team_access.entity_team(s),
                }

                try:
                    log_entry = executions.build_log_entry(
                        status="scheduled",
                        partition_key=partition_key,
                        row_key=str(uuid.uuid4()),
                        exec_id=exec_id,
                        requested_at=requested_at,
                        name=s.get("name"),
                        schema_id=s.get("RowKey"),
                        runbook=s.get("runbook"),
                        run_args=s.get("run_args"),
                        worker=worker_pool,
                        group="-",
                        oncall=oncall_enabled,
                        log_msg=json.dumps(
                            {
                                "status": "scheduled",
                                "queue": target_queue,
                            },
                            ensure_ascii=False,
                        ),
                        monitor_condition="",
                        severity="",
                        team=team_access.entity_team(s),
                    )
                    log_table_client.create_entity(entity=log_entry)
                except Exception as le:
                    logging.error(f"[Scheduler] Failed to log scheduled status: {le}")

                q_name = target_queue
                queue_service = storage.get_queue_client(q_name)
                try:
                    queue_service.send_message(
                        json.dumps(queue_payload, ensure_ascii=False)
                    )
                except Exception as qe:
                    if "QueueNotFound" in str(qe):
                        logging.warning(f"[Scheduler] Queue {q_name} not found")
                    else:
                        raise qe

                s["last_run"] = now.isoformat()
                table_client.update_entity(entity=s)

    except Exception as e:
        logging.error(f"[Scheduler] Error: {e}")
