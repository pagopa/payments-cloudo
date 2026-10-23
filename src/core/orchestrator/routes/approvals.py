"""Approval gate endpoints (approve / reject a pending execution)."""

import json
import logging
import os
import uuid

import azure.functions as func
from core.config import (
    AUTH,
    DEFAULT_TEAM,
    STORAGE_CONN,
    TABLE_NAME,
    TABLE_SCHEMAS,
    TABLE_WORKERS_SCHEMAS,
)
from models import Schema
from utils import create_cors_response

from core import (
    audit,
    executions,
    notify,
    schema_lookup,
    security,
    storage,
    team_access,
)

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
    import utils
    from worker_routing import worker_routing

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot approve executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    route_params = getattr(req, "route_params", {}) or {}
    execId = (route_params.get("execId") or "").strip()

    p = (req.params.get("p") or "").strip()
    s = (req.params.get("s") or "").strip()

    # Security: check session token if not in LOCAL_DEV mode
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        session, error_res = security.get_authenticated_user(req)
        if error_res:
            # Fallback for Slack/Email links
            approver = (
                req.headers.get("x-cloudo-user")
                or req.headers.get("X-Approver")
                or "anonymous-link"
            )
        else:
            approver = session.get("username")
    else:
        # Trust headers in LOCAL_DEV
        approver = (
            req.headers.get("x-cloudo-user")
            or req.headers.get("X-Approver")
            or "unknown"
        )
        # Try to get session if available anyway
        session, _ = security.get_authenticated_user(req)
        if session:
            approver = session.get("username")

    if not execId:
        return func.HttpResponse(
            json.dumps({"error": "Missing execId in route"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    ok, payload = security.verify_signed_payload(execId, p, s)
    if not ok:
        return func.HttpResponse(
            json.dumps({"error": "Invalid or expired payload"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    rows = storage.rows_from_binding(today_logs) or []
    if not executions.only_pending_for_exec(rows, execId):
        return func.HttpResponse(
            json.dumps(
                {"message": "Already decided or executed for this ExecId"},
                ensure_ascii=False,
            ),
            status_code=409,
            mimetype="application/json",
        )

    schema_id = payload.get("schemaId") or ""
    initiator = payload.get("initiator")
    log_prefix = executions.build_exec_log_prefix(execId, initiator)
    resource_info = payload.get("resource_info") or None
    routing_info = payload.get("routing_info") or None
    monitor_condition = payload.get("monitorCondition") or ""
    severity = payload.get("severity") or ""
    resource_team = team_access.normalize_team(payload.get("team"))

    # Load schema entity
    parsed = storage.rows_from_binding(schemas)
    if parsed is None:
        return func.HttpResponse(
            json.dumps({"error": "Schemas not available"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )

    schema_entity = schema_lookup.find_schema_entity(parsed, [str(schema_id)])
    if not schema_entity:
        return func.HttpResponse(
            json.dumps({"error": "Schema not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    schema = Schema(id=schema_lookup.get_entity_id(schema_entity), entity=schema_entity)
    if (
        session
        and not team_access.is_admin(session)
        and resource_team
        not in {
            DEFAULT_TEAM,
            team_access.session_team(session),
        }
    ):
        return func.HttpResponse(
            json.dumps({"error": "Approval belongs to another team"}),
            status_code=403,
            mimetype="application/json",
        )

    partition_key = utils.today_partition_key()
    requested_at = utils.format_requested_at()

    # Execute once (pass embedded resource_info and propagate the function key if needed)
    try:
        # ---------------------------------------------------------
        # DYNAMIC WORKER SELECTION (Binding Version)
        # ---------------------------------------------------------
        target_queue = worker_routing(workers, schema)

        api_body = {}
        status_code = 202

        if target_queue:
            logging.debug(
                f"{log_prefix} 🎯 Dynamic Routing: Selected Queue '{target_queue}'"
            )

            try:
                # Construct the payload (formerly HTTP headers)
                queue_payload = {
                    "runbook": schema.runbook,
                    "run_args": schema.run_args,
                    "id": schema.id,
                    "name": schema.name or "",
                    "requestedAt": requested_at,
                    "exec_id": execId,
                    "oncall": schema.oncall,
                    "aks_login_required": schema.aks_login_required,
                    "aks_cluster_name": schema.aks_cluster_name,
                    "aks_resource_group": schema.aks_resource_group,
                    "aks_namespace": schema.aks_namespace,
                    "monitor_condition": monitor_condition,
                    "severity": severity,
                    "team": schema.team,
                    "worker": schema.worker,
                    "group": schema.group,
                    "initiator": initiator,
                    "resource_info": resource_info or {},
                    "routing_info": routing_info or {},
                }

                storage.enqueue_queue_payload(target_queue, queue_payload)

                api_body = {
                    "status": "accepted",
                    "queue": target_queue,
                    "payload": queue_payload,
                }

            except Exception as e:
                logging.error(f"{log_prefix} ❌ Queue send failed: {e}")
                status_code = 500
                api_body = {"error": str(e)}
        else:
            err_msg = f"❌ No workers ({schema.worker}) available and no static queue configured for {schema.id}"
            logging.error(f"{log_prefix} {err_msg}")
            status_code = 500
            api_body = {"error": err_msg}
        # ---------------------------------------------------------

        # Status label for logs
        status_label = "accepted" if status_code == 202 else "error"

        log_entity = executions.build_log_entry(
            status=status_label,
            partition_key=partition_key,
            row_key=str(uuid.uuid4()),
            exec_id=execId,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=json.dumps(
                {
                    "message": f"Approved and executed by {approver}",
                    "response": api_body,
                    "resource_info": resource_info,
                },
                ensure_ascii=False,
            ),
            oncall=schema.oncall,
            initiator=payload.get("initiator"),
            resource_info=resource_info,
            monitor_condition=monitor_condition,
            severity=severity,
            team=schema.team,
            approval_required=True,
            approval_decision_by=approver,
        )
        log_table.set(json.dumps(log_entity, ensure_ascii=False))

        audit.log_audit(
            user=approver,
            action="RUNBOOK_APPROVE",
            target=execId,
            details=f"Runbook: {schema.runbook}, Schema: {schema.id}, Approver: {approver}",
        )

        # smart routing notification (if routing module available)
        if route_alert and execute_actions:
            ctx = {
                "resourceId": ((resource_info or {}).get("resource_id") or ""),
                "resourceGroup": ((resource_info or {}).get("resource_group") or ""),
                "resourceName": ((resource_info or {}).get("resource_name") or ""),
                "schemaName": (schema.name or ""),
                "severity": severity,
                "namespace": ((resource_info or {}).get("namespace") or ""),
                "oncall": schema.oncall,
                "status": status_label,
                "execId": execId,
                "name": schema.name or "",
                "id": schema.id,
                "team": schema.team,
                "routing_info": routing_info,
            }
            decision = route_alert(ctx)
            logging.debug(f"{log_prefix} Approval: {decision}")

            # Notify Slack directly (bypassing smart routing for Slack as requested)
            notify.notify_slack_decision(
                exec_id=execId,
                schema_id=schema_id,
                decision="approved",
                approver=approver,
                routing_info=routing_info,
                team=resource_team,
            )

            # We still execute other actions via smart routing if any (excluding Slack)
            payload = {
                "slack": None  # Skip slack in execute_actions
            }
            try:
                execute_actions(decision, payload, send_slack_fn=None)
            except Exception as e:
                logging.error(
                    f"{log_prefix} smart routing approval actions failed: {e}"
                )

        return func.HttpResponse(
            json.dumps(
                {
                    "message": f"Approved and executed by {approver}",
                    "response": api_body,
                    "resource_info": resource_info,
                }
            ),
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
            exec_id=execId,
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
            execId,
            schema_id,
            f"approved {execId} by {approver}",
            approver,
            extra=f"*Error:* {str(e)}",
            routing_info=routing_info,
            team=resource_team,
        )
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )


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
    import utils

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot reject executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    route_params = getattr(req, "route_params", {}) or {}
    execId = (route_params.get("execId") or "").strip()

    rows = storage.rows_from_binding(today_logs) or []
    if not executions.only_pending_for_exec(rows, execId):
        return func.HttpResponse(
            json.dumps(
                {"message": "Already decided or executed for this ExecId"},
                ensure_ascii=False,
            ),
            status_code=409,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    p = (req.params.get("p") or "").strip()
    s = (req.params.get("s") or "").strip()

    # Security: check session token if not in LOCAL_DEV mode
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        session, error_res = security.get_authenticated_user(req)
        if error_res:
            # Fallback for Slack/Email links
            approver = (
                req.headers.get("x-cloudo-user")
                or req.headers.get("X-Approver")
                or "anonymous-link"
            )
        else:
            approver = session.get("username")
    else:
        # Trust headers in LOCAL_DEV
        approver = (
            req.headers.get("x-cloudo-user")
            or req.headers.get("X-Approver")
            or "unknown"
        )
        # Try to get session if available anyway
        session, _ = security.get_authenticated_user(req)
        if session:
            approver = session.get("username")

    if not execId:
        return func.HttpResponse(
            json.dumps({"error": "Missing execId in route"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    ok, payload = security.verify_signed_payload(execId, p, s)
    if not ok:
        return func.HttpResponse(
            json.dumps({"error": "Invalid or expired payload"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    schema_id = payload.get("schemaId") or ""
    initiator = payload.get("initiator")
    log_prefix = executions.build_exec_log_prefix(execId, initiator)
    resource_info = payload.get("resource_info") or None
    routing_info = payload.get("routing_info") or None
    monitor_condition = payload.get("monitorCondition") or ""
    severity = payload.get("severity") or ""
    resource_team = team_access.normalize_team(payload.get("team"))

    # Load schema entity
    parsed = storage.rows_from_binding(schemas)
    if parsed is None:
        return func.HttpResponse(
            json.dumps({"error": "Schemas not available"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )

    schema_entity = schema_lookup.find_schema_entity(parsed, [str(schema_id)])
    if not schema_entity:
        return func.HttpResponse(
            json.dumps({"error": "Schema not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    schema = Schema(id=schema_lookup.get_entity_id(schema_entity), entity=schema_entity)
    if (
        session
        and not team_access.is_admin(session)
        and resource_team
        not in {
            DEFAULT_TEAM,
            team_access.session_team(session),
        }
    ):
        return func.HttpResponse(
            json.dumps({"error": "Approval belongs to another team"}),
            status_code=403,
            mimetype="application/json",
        )

    partition_key = utils.today_partition_key()
    requested_at = utils.format_requested_at()

    log_entity = executions.build_log_entry(
        status="rejected",
        partition_key=partition_key,
        row_key=str(uuid.uuid4()),
        exec_id=execId,
        requested_at=requested_at,
        name=schema.name or "",
        schema_id=schema.id,
        runbook=schema.runbook,
        run_args=schema.run_args,
        worker=schema.worker,
        group=schema.group,
        log_msg=json.dumps(
            {"message": f"Rejected by approver: {approver}"}, ensure_ascii=False
        ),
        oncall=schema.oncall,
        initiator=payload.get("initiator"),
        resource_info=resource_info,
        monitor_condition=monitor_condition,
        severity=severity,
        team=schema.team,
        approval_required=True,
        approval_decision_by=approver,
    )
    log_table.set(json.dumps(log_entity, ensure_ascii=False))

    audit.log_audit(
        user=approver,
        action="RUNBOOK_REJECT",
        target=execId,
        details=f"Runbook: {schema.runbook}, Schema: {schema.id}",
    )

    # Notify Slack directly
    notify.notify_slack_decision(
        exec_id=execId,
        schema_id=schema_id,
        decision="rejected",
        approver=approver,
        routing_info=routing_info,
        team=resource_team,
    )

    # smart routing notification (if routing module available)
    if route_alert and execute_actions:
        ctx = {
            "resourceId": ((resource_info or {}).get("resource_id") or ""),
            "resourceGroup": ((resource_info or {}).get("resource_group") or ""),
            "resourceName": ((resource_info or {}).get("resource_name") or ""),
            "schemaName": (schema.name or ""),
            "severity": severity,
            "namespace": ((resource_info or {}).get("namespace") or ""),
            "oncall": schema.oncall,
            "status": "rejected",
            "execId": execId,
            "name": schema.name or "",
            "id": schema.id,
            "team": schema.team,
            "routing_info": routing_info,
        }
        decision = route_alert(ctx)
        logging.debug(f"{log_prefix} Reject: {decision}")

        payload = {
            "slack": None  # Skip slack in execute_actions
        }
        try:
            execute_actions(decision, payload, send_slack_fn=None)
        except Exception as e:
            logging.error(f"{log_prefix} smart routing rejection failed: {e}")

    return func.HttpResponse(
        json.dumps({"message": f"Rejected by approver: {approver}"}),
        status_code=200,
        mimetype="application/json",
        headers={
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    )
