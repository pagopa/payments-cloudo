"""Shared steps of the approval gate decisions (approve / reject a pending run)."""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import azure.functions as func
import worker_routing
from core.config import DEFAULT_TEAM
from models import Schema
from services import dispatch
from services.smart_routing_api import SmartRoutingApi

from core import executions, schema_lookup, security, storage, team_access

_CORS = {"Access-Control-Allow-Origin": "*"}


def json_response(
    payload: dict, status_code: int, headers: Optional[dict] = None
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
        **({"headers": headers} if headers else {}),
    )


@dataclass
class PendingDecision:
    """Execution context carried by the signed approve/reject link."""

    exec_id: str
    schema_id: str
    initiator: Optional[str]
    resource_info: Optional[dict]
    routing_info: Optional[dict]
    monitor_condition: str
    severity: str
    resource_team: str

    @classmethod
    def from_payload(cls, exec_id: str, payload: dict) -> "PendingDecision":
        return cls(
            exec_id=exec_id,
            schema_id=payload.get("schemaId") or "",
            initiator=payload.get("initiator"),
            resource_info=payload.get("resource_info") or None,
            routing_info=payload.get("routing_info") or None,
            monitor_condition=payload.get("monitorCondition") or "",
            severity=payload.get("severity") or "",
            resource_team=team_access.normalize_team(payload.get("team")),
        )

    @property
    def log_prefix(self) -> str:
        return executions.build_exec_log_prefix(self.exec_id, self.initiator)


def viewer_forbidden(verb: str) -> func.HttpResponse:
    return json_response(
        {"error": f"Unauthorized: Viewer cannot {verb} executions"},
        status_code=403,
        headers=_CORS,
    )


def route_exec_id(req: func.HttpRequest) -> str:
    route_params = getattr(req, "route_params", {}) or {}
    return (route_params.get("execId") or "").strip()


def resolve_approver(req: func.HttpRequest) -> tuple[str, Optional[dict]]:
    """Who is deciding, and the session the team check must use."""
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
        return approver, session
    # Trust headers in LOCAL_DEV
    approver = (
        req.headers.get("x-cloudo-user") or req.headers.get("X-Approver") or "unknown"
    )
    # Try to get session if available anyway
    session, _ = security.get_authenticated_user(req)
    if session:
        approver = session.get("username")
    return approver, session


def verify_link(
    req: func.HttpRequest, exec_id: str
) -> tuple[Optional[PendingDecision], Optional[func.HttpResponse]]:
    """Validate the signed link (?p=&s=) for this execution."""
    if not exec_id:
        return None, json_response({"error": "Missing execId in route"}, 400)
    p = (req.params.get("p") or "").strip()
    s = (req.params.get("s") or "").strip()
    ok, payload = security.verify_signed_payload(exec_id, p, s)
    if not ok:
        return None, json_response({"error": "Invalid or expired payload"}, 401)
    return PendingDecision.from_payload(exec_id, payload), None


def already_decided(
    today_logs: str, exec_id: str, headers: Optional[dict] = None
) -> Optional[func.HttpResponse]:
    rows = storage.rows_from_binding(today_logs) or []
    if executions.only_pending_for_exec(rows, exec_id):
        return None
    return json_response(
        {"message": "Already decided or executed for this ExecId"}, 409, headers
    )


def load_schema(
    schemas: str, schema_id: str
) -> tuple[Optional[Schema], Optional[func.HttpResponse]]:
    parsed = storage.rows_from_binding(schemas)
    if parsed is None:
        return None, json_response({"error": "Schemas not available"}, 500)
    schema_entity = schema_lookup.find_schema_entity(parsed, [str(schema_id)])
    if not schema_entity:
        return None, json_response({"error": "Schema not found"}, 404)
    schema = Schema(id=schema_lookup.get_entity_id(schema_entity), entity=schema_entity)
    return schema, None


def team_forbidden(
    session: Optional[dict], resource_team: str
) -> Optional[func.HttpResponse]:
    if (
        session
        and not team_access.is_admin(session)
        and resource_team not in {DEFAULT_TEAM, team_access.session_team(session)}
    ):
        return json_response({"error": "Approval belongs to another team"}, 403)
    return None


def decision_log_entry(
    pending: PendingDecision,
    schema: Schema,
    *,
    status: str,
    log_payload: dict,
    approver: str,
    partition_key: str,
    requested_at: str,
    row_key: str,
) -> dict[str, Any]:
    return executions.build_log_entry(
        status=status,
        partition_key=partition_key,
        row_key=row_key,
        exec_id=pending.exec_id,
        requested_at=requested_at,
        name=schema.name or "",
        schema_id=schema.id,
        runbook=schema.runbook,
        run_args=schema.run_args,
        worker=schema.worker,
        group=schema.group,
        log_msg=json.dumps(log_payload, ensure_ascii=False),
        oncall=schema.oncall,
        initiator=pending.initiator,
        resource_info=pending.resource_info,
        monitor_condition=pending.monitor_condition,
        severity=pending.severity,
        team=schema.team,
        approval_required=True,
        approval_decision_by=approver,
    )


def enqueue_approved_run(
    pending: PendingDecision, schema: Schema, workers: str, requested_at: str
) -> tuple[int, dict[str, Any]]:
    """Dispatch the approved run; returns (status code, API body)."""
    target_queue = worker_routing.worker_routing(workers, schema)
    if not target_queue:
        err_msg = f"❌ No workers ({schema.worker}) available and no static queue configured for {schema.id}"
        logging.error(f"{pending.log_prefix} {err_msg}")
        return 500, {"error": err_msg}

    logging.debug(
        f"{pending.log_prefix} 🎯 Dynamic Routing: Selected Queue '{target_queue}'"
    )
    try:
        queue_payload = dispatch.build_queue_payload(
            schema,
            exec_id=pending.exec_id,
            requested_at=requested_at,
            initiator=pending.initiator,
            monitor_condition=pending.monitor_condition,
            severity=pending.severity,
            resource_info=pending.resource_info,
            routing_info=pending.routing_info,
        )
        storage.enqueue_queue_payload(target_queue, queue_payload)
        return 202, {
            "status": "accepted",
            "queue": target_queue,
            "payload": queue_payload,
        }
    except Exception as e:
        logging.error(f"{pending.log_prefix} ❌ Queue send failed: {e}")
        return 500, {"error": str(e)}


def route_decision(
    pending: PendingDecision,
    schema: Schema,
    status: str,
    smart_routing: SmartRoutingApi,
) -> Any:
    resource_info = pending.resource_info or {}
    ctx = {
        "resourceId": (resource_info.get("resource_id") or ""),
        "resourceGroup": (resource_info.get("resource_group") or ""),
        "resourceName": (resource_info.get("resource_name") or ""),
        "schemaName": (schema.name or ""),
        "severity": pending.severity,
        "namespace": (resource_info.get("namespace") or ""),
        "oncall": schema.oncall,
        "status": status,
        "execId": pending.exec_id,
        "name": schema.name or "",
        "id": schema.id,
        "team": schema.team,
        "routing_info": pending.routing_info,
    }
    return smart_routing.route_alert(ctx)


def run_non_slack_actions(
    decision: Any, smart_routing: SmartRoutingApi, failure_message: str
) -> None:
    """Slack is notified directly by notify_slack_decision; other targets via routing."""
    try:
        smart_routing.execute_actions(decision, {"slack": None}, send_slack_fn=None)
    except Exception as e:
        logging.error(f"{failure_message}: {e}")
