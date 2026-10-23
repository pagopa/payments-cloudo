"""Approval gate endpoints (approve / reject a pending execution)."""

import json
import logging
import uuid

import azure.functions as func
import utils
from core.config import (
    AUTH,
    STORAGE_CONN,
    TABLE_NAME,
    TABLE_SCHEMAS,
    TABLE_WORKERS_SCHEMAS,
)
from services import approval_decision as decision_steps
from services import smart_routing_api
from utils import create_cors_response

from core import audit, executions, notify, security

bp = func.Blueprint()


@bp.route(
    route="approvals/{partitionKey}/{execId}/approve",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
@bp.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="schemas",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="today_logs",
    table_name=TABLE_NAME,
    partition_key="{partitionKey}",
    connection=STORAGE_CONN,
)
def approve(
    req: func.HttpRequest,
    log_table: func.Out[str],
    schemas: str,
    today_logs: str,
    workers: str,
) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res
    if session.get("role") == "VIEWER":
        return decision_steps.viewer_forbidden("approve")

    smart_routing = smart_routing_api.load()
    exec_id = decision_steps.route_exec_id(req)
    approver, session = decision_steps.resolve_approver(req)

    pending, error_res = decision_steps.verify_link(req, exec_id)
    if error_res:
        return error_res
    error_res = decision_steps.already_decided(today_logs, exec_id)
    if error_res:
        return error_res
    schema, error_res = decision_steps.load_schema(schemas, pending.schema_id)
    if error_res:
        return error_res
    error_res = decision_steps.team_forbidden(session, pending.resource_team)
    if error_res:
        return error_res

    partition_key = utils.today_partition_key()
    requested_at = utils.format_requested_at()
    try:
        status_code, api_body = decision_steps.enqueue_approved_run(
            pending, schema, workers, requested_at
        )
        status_label = "accepted" if status_code == 202 else "error"
        result = {
            "message": f"Approved and executed by {approver}",
            "response": api_body,
            "resource_info": pending.resource_info,
        }
        log_entity = decision_steps.decision_log_entry(
            pending,
            schema,
            status=status_label,
            log_payload=result,
            approver=approver,
            partition_key=partition_key,
            requested_at=requested_at,
            row_key=str(uuid.uuid4()),
        )
        log_table.set(json.dumps(log_entity, ensure_ascii=False))

        audit.log_audit(
            user=approver,
            action="RUNBOOK_APPROVE",
            target=exec_id,
            details=f"Runbook: {schema.runbook}, Schema: {schema.id}, Approver: {approver}",
        )

        if smart_routing.available:
            decision = decision_steps.route_decision(
                pending, schema, status_label, smart_routing
            )
            logging.debug(f"{pending.log_prefix} Approval: {decision}")
            notify.notify_slack_decision(
                exec_id=exec_id,
                schema_id=pending.schema_id,
                decision="approved",
                approver=approver,
                routing_info=pending.routing_info,
                team=pending.resource_team,
                partition_key=partition_key,
            )
            decision_steps.run_non_slack_actions(
                decision,
                smart_routing,
                f"{pending.log_prefix} smart routing approval actions failed",
            )

        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json",
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except Exception as e:
        err_log = executions.build_log_entry(
            status="error",
            partition_key=partition_key,
            row_key=str(uuid.uuid4()),
            exec_id=exec_id,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=f"Approve failed: {str(e)}",
            oncall=schema.oncall,
            monitor_condition=None,
            severity=None,
            approval_required=True,
            approval_decision_by=approver,
        )
        log_table.set(json.dumps(err_log, ensure_ascii=False))
        notify.notify_slack_decision(
            exec_id,
            pending.schema_id,
            f"approved {exec_id} by {approver}",
            approver,
            extra=f"*Error:* {str(e)}",
            routing_info=pending.routing_info,
            team=pending.resource_team,
            partition_key=partition_key,
        )
        return decision_steps.json_response({"error": str(e)}, 500)


@bp.route(
    route="approvals/{partitionKey}/{execId}/reject",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
@bp.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="schemas",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="today_logs",
    table_name=TABLE_NAME,
    partition_key="{partitionKey}",
    connection=STORAGE_CONN,
)
def reject(
    req: func.HttpRequest, log_table: func.Out[str], schemas: str, today_logs: str
) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res
    if session.get("role") == "VIEWER":
        return decision_steps.viewer_forbidden("reject")

    smart_routing = smart_routing_api.load()
    exec_id = decision_steps.route_exec_id(req)

    error_res = decision_steps.already_decided(
        today_logs, exec_id, headers={"Access-Control-Allow-Origin": "*"}
    )
    if error_res:
        return error_res
    approver, session = decision_steps.resolve_approver(req)
    pending, error_res = decision_steps.verify_link(req, exec_id)
    if error_res:
        return error_res
    schema, error_res = decision_steps.load_schema(schemas, pending.schema_id)
    if error_res:
        return error_res
    error_res = decision_steps.team_forbidden(session, pending.resource_team)
    if error_res:
        return error_res

    partition_key = utils.today_partition_key()
    log_entity = decision_steps.decision_log_entry(
        pending,
        schema,
        status="rejected",
        log_payload={"message": f"Rejected by approver: {approver}"},
        approver=approver,
        partition_key=partition_key,
        requested_at=utils.format_requested_at(),
        row_key=str(uuid.uuid4()),
    )
    log_table.set(json.dumps(log_entity, ensure_ascii=False))

    audit.log_audit(
        user=approver,
        action="RUNBOOK_REJECT",
        target=exec_id,
        details=f"Runbook: {schema.runbook}, Schema: {schema.id}",
    )
    notify.notify_slack_decision(
        exec_id=exec_id,
        schema_id=pending.schema_id,
        decision="rejected",
        approver=approver,
        routing_info=pending.routing_info,
        team=pending.resource_team,
        partition_key=partition_key,
    )

    if smart_routing.available:
        decision = decision_steps.route_decision(
            pending, schema, "rejected", smart_routing
        )
        logging.debug(f"{pending.log_prefix} Reject: {decision}")
        decision_steps.run_non_slack_actions(
            decision,
            smart_routing,
            f"{pending.log_prefix} smart routing rejection failed",
        )

    return func.HttpResponse(
        json.dumps({"message": f"Rejected by approver: {approver}"}),
        status_code=200,
        mimetype="application/json",
        headers={
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    )
