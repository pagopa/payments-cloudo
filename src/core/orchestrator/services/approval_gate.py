"""Approval gate: park an execution as pending and ask the owning team to approve it."""

import json
import logging
import os
import types
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import azure.functions as func
import escalation
from core.config import APPROVAL_TTL_MIN, DEFAULT_TEAM
from models import Schema
from services.execution_request import ExecutionRequest
from services.smart_routing_api import SmartRoutingApi
from utils import as_bool

from core import audit, executions, notify, security, team_access


def team_approval_targets(
    team: Any, slack_token: Any, slack_channel: Any, jsm_token: Any
) -> tuple[Any, Any, Any]:
    """Prefer the resource team's own Slack/JSM config for approval requests."""
    team = team_access.normalize_team(team)
    if team == DEFAULT_TEAM:
        return slack_token, slack_channel, jsm_token
    try:
        from smart_routing import (
            get_setting,
            load_routing_config,
            resolve_jsm_apikey,
            resolve_slack_token,
        )

        team_cfg = (load_routing_config().get("teams") or {}).get(team) or {}
        channel = (team_cfg.get("slack") or {}).get("channel") or get_setting(
            f"SLACK_CHANNEL_{team.upper().replace('-', '_')}"
        )
        return (
            resolve_slack_token(team) or slack_token,
            channel or slack_channel,
            resolve_jsm_apikey(team) or jsm_token,
        )
    except Exception as e:
        logging.warning(f"Team approval targets unavailable for '{team}': {e}")
        return slack_token, slack_channel, jsm_token


def _orchestrator_base_url() -> str:
    base_env = os.getenv("ORCHESTRATOR_BASE_URL")
    if not base_env:
        hostname = os.getenv("WEBSITE_HOSTNAME", "localhost:7071")
        scheme = "https" if "localhost" not in hostname else "http"
        return f"{scheme}://{hostname}"
    return base_env.rstrip("/")


def _ui_execution_url(execution: ExecutionRequest) -> str:
    ui_base = notify.ui_base_url()
    return f"{ui_base}/executions?execId={execution.exec_id}&partitionKey={execution.partition_key}"


def _signed_decision_links(
    execution: ExecutionRequest, schema: Schema, func_key: str, expires_at: str
) -> tuple[str, str]:
    """Approve/reject URLs carrying the signed execution context."""
    payload = {
        "execId": execution.exec_id,
        "schemaId": schema.id,
        "exp": expires_at,
        "initiator": execution.initiator,
        "resource_info": execution.resource_info or {},
        "routing_info": execution.routing_info or {},
        "code": func_key or "",
        "monitorCondition": execution.monitor_condition,
        "severity": execution.severity,
        "team": schema.team,
        "worker": schema.worker,
        "group": schema.group,
    }
    payload_b64 = security.b64url_encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    sig = security.sign_payload_b64(payload_b64)

    base = _orchestrator_base_url()
    path = f"{base}/api/approvals/{execution.partition_key}/{execution.exec_id}"
    query = f"p={payload_b64}&s={sig}&code={func_key}"
    return f"{path}/approve?{query}", f"{path}/reject?{query}"


def _approval_slack_blocks(
    execution: ExecutionRequest, schema: Schema, ui_url: str
) -> list[dict]:
    # Truncate description and compact resource info to avoid Slack limits
    description_truncated = (schema.description or "No description provided.").strip()
    if len(description_truncated) > 400:
        description_truncated = description_truncated[:400] + "..."

    resource_info_compact = (
        notify.format_compact_resource_info(execution.resource_info) or ""
    )
    if len(resource_info_compact) > 600:
        resource_info_compact = resource_info_compact[:600] + "..."

    # Truncate arguments for better Slack display
    args_truncated = (schema.run_args or "").strip()
    if len(args_truncated) > 800:
        args_truncated = args_truncated[:800] + "\n... (truncated)"

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Gate Approval Required ⚠️",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"<!here> *{schema.name}* is requesting permission to execute a restricted runbook.\n"
                    f"> *Description:* {description_truncated}"
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*SchemaId:* `{schema.id}`"},
                {
                    "type": "mrkdwn",
                    "text": f"*Severity:* `{execution.severity or '-'}`",
                },
                {"type": "mrkdwn", "text": f"*Runbook:* `{schema.runbook or '-'}`"},
                {"type": "mrkdwn", "text": f"*Worker:* `{schema.worker or 'unknown'}`"},
                {"type": "mrkdwn", "text": f"*Group:* `{schema.group}`"},
                {"type": "mrkdwn", "text": f"*Team:* `{schema.team}`"},
                {
                    "type": "mrkdwn",
                    "text": f"*Initiator:* `{execution.initiator or 'SYSTEM'}`",
                },
                {"type": "mrkdwn", "text": f"*On Call:* `{schema.oncall}`"},
            ],
        },
        *(
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Resource Context:*\n{resource_info_compact}",
                    },
                }
            ]
            if resource_info_compact
            else []
        ),
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Arguments:*\n```{(args_truncated or 'None')}```",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Full Context 🔍",
                        "emoji": True,
                    },
                    "url": ui_url,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"ExecId: `{execution.exec_id}` | Requested: <!date^{int(datetime.now(timezone.utc).timestamp())}^{{date_short}} {{time}}|now>",
                }
            ],
        },
    ]


def _notify_slack(
    execution: ExecutionRequest,
    schema: Schema,
    token: Any,
    channel: Any,
    team: str,
    ui_url: str,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Send the Slack approval request; returns (delivery record, warning)."""
    if not (token and channel):
        msg = "Slack approval notify skipped: missing token or channel"
        logging.debug(f"[{execution.exec_id}] {msg}")
        return None, msg
    try:
        sent = escalation.send_slack_execution(
            token=token,
            channel=channel,
            message=f"[{execution.exec_id}] ⚠️ APPROVAL REQUIRED: {schema.name}",
            blocks=_approval_slack_blocks(execution, schema, ui_url),
        )
        delivery = {
            "type": "slack",
            "team": team,
            "channel": channel,
            "status": "sent" if sent else "failed",
        }
        return delivery, None
    except Exception as e:
        delivery = {
            "type": "slack",
            "team": team,
            "channel": channel,
            "status": "failed",
            "error": str(e)[:200],
        }
        msg = f"Slack approval notify failed: {e}"
        logging.debug(f"[{execution.exec_id}] {msg}")
        return delivery, msg


def _notify_jsm(
    execution: ExecutionRequest, schema: Schema, jsm_token: Any, team: str, ui_url: str
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Page on-call through JSM; returns (delivery record, warning)."""
    if not as_bool(schema.oncall):
        logging.debug(f"[{execution.exec_id}] JSM approval notify skipped: not on-call")
        return None, None
    if not jsm_token:
        msg = "JSM approval notify skipped: missing token"
        logging.debug(f"[{execution.exec_id}] {msg}")
        return None, msg
    initiator = execution.initiator or "SYSTEM"
    try:
        sent = escalation.send_jsm_alert(
            api_key=jsm_token,
            message=f"[{execution.exec_id}] ⚠️ APPROVAL REQUIRED: {schema.name}",
            description=(
                f"{schema.name} is requesting permission to execute a restricted runbook.\n\n"
                f"Description: {schema.description or 'No description provided.'}\n"
                f"SchemaId: {schema.id}\n"
                f"Severity: {execution.severity or '-'}\n"
                f"Runbook: {schema.runbook or '-'}\n"
                f"Worker: {schema.worker or 'unknown'}\n"
                f"Group: {schema.group}\n"
                f"Team: {schema.team}\n"
                f"Initiator: {initiator}\n"
                f"On Call: {schema.oncall}\n\n"
                f"Full Context: {ui_url}"
            ),
            priority="P2",
            alias=execution.exec_id,
            tags=["approval-required", schema.group or "cloudo"],
            details={
                "execId": execution.exec_id,
                "schemaId": schema.id,
                "team": schema.team,
                "runbook": schema.runbook or "-",
                "initiator": initiator,
            },
        )
        return {
            "type": "jsm",
            "team": team,
            "status": "sent" if sent else "failed",
        }, None
    except Exception as e:
        delivery = {
            "type": "jsm",
            "team": team,
            "status": "failed",
            "error": str(e)[:200],
        }
        msg = f"JSM approval notify failed: {e}"
        logging.debug(f"[{execution.exec_id}] {msg}")
        return delivery, msg


def request_approval(
    req: func.HttpRequest,
    execution: ExecutionRequest,
    schema: Schema,
    log_table: func.Out[str],
    smart_routing: SmartRoutingApi,
) -> func.HttpResponse:
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_TTL_MIN))
        .isoformat()
        .replace(" ", "")
    )
    # function key to pass along (from header or query)
    func_key = req.headers.get("x-functions-key") or req.params.get("code") or ""
    approve_url, reject_url = _signed_decision_links(
        execution, schema, func_key, expires_at
    )
    pending_log_payload = {
        "message": "Awaiting approval",
        "approve": approve_url,
        "reject": reject_url,
        "resource_info": execution.resource_info,
    }

    if execution.initiator:
        audit.log_audit(
            user=execution.initiator,
            action="RUNBOOK_GATE_SCHEDULE",
            target=execution.exec_id,
            details=f"ID: {schema.id}, Runbook: {schema.runbook}, Args: {schema.run_args}",
        )

    routing_info = execution.routing_info
    slack_token, slack_channel, jsm_token = team_approval_targets(
        schema.team,
        routing_info.get("slack_token"),
        routing_info.get("slack_channel"),
        routing_info.get("jsm_token")
        or routing_info.get("opsgenie_token")
        or smart_routing.resolve_jsm_apikey(routing_info.get("team")),
    )
    approval_team = team_access.normalize_team(schema.team)
    ui_url = _ui_execution_url(execution)

    deliveries: list[dict[str, Any]] = []
    notification_warnings: list[str] = []
    for delivery, warning in (
        _notify_slack(
            execution, schema, slack_token, slack_channel, approval_team, ui_url
        ),
        _notify_jsm(execution, schema, jsm_token, approval_team, ui_url),
    ):
        if delivery:
            deliveries.append(delivery)
        if warning:
            notification_warnings.append(warning)

    if notification_warnings:
        pending_log_payload["notification_status"] = "warning"
        pending_log_payload["notification_warnings"] = notification_warnings

    pending_log = executions.build_log_entry(
        status="pending",
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
        log_msg=json.dumps(pending_log_payload, ensure_ascii=False),
        oncall=schema.oncall,
        initiator=execution.initiator,
        resource_info=execution.resource_info,
        monitor_condition=execution.monitor_condition,
        severity=execution.severity,
        team=schema.team,
        approval_required=True,
        approval_expires_at=expires_at,
        notifications=executions.notifications_json(
            types.SimpleNamespace(reason="approval_request", matched_rule_index=None),
            deliveries,
        ),
    )
    log_table.set(json.dumps(pending_log, ensure_ascii=False))

    body = json.dumps(
        {
            "status": 202,
            "message": "Job is pending approval",
            "exec_id": execution.exec_id,
            "approve": approve_url,
            "reject": reject_url,
            "expires_at (UTC)": expires_at,
            "notification_status": ("warning" if notification_warnings else "ok"),
            "notification_warnings": notification_warnings,
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(
        body,
        status_code=202,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )
