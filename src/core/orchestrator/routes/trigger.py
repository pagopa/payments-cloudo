"""Runbook trigger endpoint: schema resolution, approval gate and worker dispatch."""

import json
import logging

import azure.functions as func
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from core.config import (
    DEFAULT_TEAM,
    NOTIFICATION_QUEUE_NAME,
    STORAGE_CONN,
    STORAGE_CONNECTION,
    TABLE_NAME,
    TABLE_SCHEMAS,
    TABLE_WORKERS_SCHEMAS,
)
from models import Schema
from services import approval_gate, dispatch, execution_request, smart_routing_api
from services.execution_request import ExecutionRequest
from utils import create_cors_response

from core import executions, schema_lookup, security, storage, team_access

bp = func.Blueprint()

_NOTIFICATION_QUEUE_READY = False


def _json_response(payload: dict, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _notification_queue():
    """Queue client used to post status to the Receiver (created on first use)."""
    global _NOTIFICATION_QUEUE_READY
    q_client = storage.get_queue_client(
        NOTIFICATION_QUEUE_NAME, conn_env=STORAGE_CONNECTION
    )
    if not _NOTIFICATION_QUEUE_READY:
        try:
            q_client.create_queue()
        except ResourceExistsError:
            _NOTIFICATION_QUEUE_READY = True
        except Exception as e:
            logging.warning(
                "Failed to ensure notification queue '%s' exists: %s",
                NOTIFICATION_QUEUE_NAME,
                e,
            )
        else:
            _NOTIFICATION_QUEUE_READY = True
    return q_client


def _post_routed_status(
    q_client, execution: ExecutionRequest, payload: dict, log_msg: str
) -> None:
    try:
        q_client.send_message(
            executions.post_status(payload, status="routed", log_message=log_msg)
        )
    except ResourceNotFoundError as e:
        logging.warning(
            "%s Notification queue '%s' not found, skipping status post: %s",
            execution.log_prefix,
            NOTIFICATION_QUEUE_NAME,
            e,
        )


def _route_unmatched_alert(q_client, execution: ExecutionRequest) -> func.HttpResponse:
    """No runbook for this alert: forward it as routed, or ignore a non-alert."""
    if not execution.is_alert:
        logging.warning(
            f"No alert detected for {execution.schema_ids}: "
            f"{execution.monitor_condition} - {execution.severity}"
        )
        return _json_response(
            {"ignored": f"No alert detected for {execution.schema_ids}"},
            status_code=204,
        )
    _post_routed_status(
        q_client,
        execution,
        {
            "requestedAt": execution.requested_at,
            "id": execution.primary_schema_id,
            "name": execution.resource_name or "",
            "exec_id": execution.exec_id,
            "runbook": "alarm routed",
            "run_args": "None",
            "worker": "No worker",
            "group": "-",
            "oncall": True,
            "initiator": execution.initiator,
            "monitor_condition": execution.monitor_condition or "",
            "severity": execution.severity or "",
            "resource_info": execution.resource_info,
            "routing_info": execution.routing_info,
        },
        log_msg="ALARM -> ROUTED (No runbook found)",
    )
    return _json_response(
        {
            "routed": (
                "Alarm detected.\n "
                "(This alert has not a runbook to be executed) -> ROUTED"
            )
        },
        status_code=200,
    )


def _route_disabled_runbook(
    q_client, execution: ExecutionRequest, schema: Schema
) -> func.HttpResponse:
    _post_routed_status(
        q_client,
        execution,
        {
            "requestedAt": execution.requested_at,
            "id": schema.id,
            "name": schema.name or execution.resource_name or "",
            "exec_id": execution.exec_id,
            "runbook": schema.runbook or "alarm routed",
            "run_args": schema.run_args,
            "worker": schema.worker,
            "group": schema.group,
            "oncall": schema.oncall,
            "initiator": execution.initiator,
            "monitor_condition": execution.monitor_condition or "",
            "severity": execution.severity or "",
            "resource_info": execution.resource_info,
            "routing_info": execution.routing_info,
        },
        log_msg=f"RUNBOOK DISABLED -> ROUTED ({schema.id})",
    )
    return _json_response(
        {
            "routed": (
                f"Runbook {schema.id} is disabled.\n "
                "(This execution has been ROUTED)"
            )
        },
        status_code=200,
    )


def _execution_error(
    execution: ExecutionRequest,
    schema: Schema,
    error: Exception,
    log_table: func.Out[str],
) -> func.HttpResponse:
    error_log = executions.build_log_entry(
        status="error",
        partition_key=execution.partition_key,
        exec_id=execution.exec_id,
        row_key=execution.exec_id,
        requested_at=execution.requested_at,
        name=schema.name or "",
        schema_id=schema.id,
        runbook=schema.runbook,
        run_args=schema.run_args,
        worker=schema.worker,
        group=schema.group,
        log_msg=str(error),
        oncall=schema.oncall,
        monitor_condition=execution.monitor_condition,
        severity=execution.severity,
        team=schema.team,
    )
    log_table.set(json.dumps(error_log, ensure_ascii=False))
    return func.HttpResponse(
        dispatch.build_response_body(
            status_code=500,
            schema=schema,
            partition_key=execution.partition_key,
            exec_id=execution.exec_id,
            api_json={"error": str(error)},
        ),
        status_code=500,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@bp.route(
    route="Trigger/{team?}",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@bp.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="entities",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def Trigger(
    req: func.HttpRequest,
    log_table: func.Out[str],
    entities: str,
    workers: str,
) -> func.HttpResponse:
    try:
        q_client = _notification_queue()
    except Exception as e:
        logging.error(f"Failed to initialize queue client: {e}")
        return func.HttpResponse("Failed to initialize queue client", status_code=500)

    if req.method == "OPTIONS":
        return create_cors_response()

    smart_routing = smart_routing_api.load()

    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res
    if session.get("role") == "VIEWER":
        return _json_response(
            {"error": "Unauthorized: Viewer cannot trigger executions"},
            status_code=403,
        )

    execution = execution_request.from_http(req, session, smart_routing)

    rows = storage.rows_from_binding(entities)
    if rows is None:
        return _json_response(
            {"error": "Unexpected table result format"}, status_code=500
        )

    schema_entity = schema_lookup.find_schema_entity(rows, execution.schema_ids)
    if schema_entity and not team_access.can_view_all_teams(req, session):
        if team_access.entity_team(schema_entity) not in {
            DEFAULT_TEAM,
            team_access.session_team(session),
        }:
            return _json_response(
                {"error": "Schema belongs to another team"}, status_code=403
            )
    if not schema_entity:
        return _route_unmatched_alert(q_client, execution)

    schema_id = schema_lookup.get_entity_id(schema_entity)
    logging.debug(f"{execution.log_prefix} Getting schema entity id '{schema_id}'")
    schema = Schema(
        id=schema_id,
        entity=schema_entity,
        monitor_condition=execution.monitor_condition,
        severity=execution.severity,
    )
    if not schema.enabled:
        return _route_disabled_runbook(q_client, execution, schema)

    try:
        if schema.require_approval:
            return approval_gate.request_approval(
                req, execution, schema, log_table, smart_routing
            )
        return dispatch.dispatch_to_worker(
            execution, schema, workers, log_table, smart_routing
        )
    except Exception as e:
        return _execution_error(execution, schema, e, log_table)
