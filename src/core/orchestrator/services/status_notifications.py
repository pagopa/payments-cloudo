"""Smart-routing notification of an execution status change (Slack + JSM)."""

import logging
from datetime import datetime, timezone
from typing import Any

import escalation
import utils
from core.config import MAX_TABLE_CHARS
from services.smart_routing_api import SmartRoutingApi

from core import notify

STATUS_EMOJIS = {
    "succeeded": "✅",
    "running": "🏃",
    "skipped": "⏭️",
    "routed": "🧭",
    "error": "❌",
    "failed": "❌",
    "accepted": "📩",
    "pending": "⏳",
}


def _first_present(body: dict, key: str, resource_info: dict, fallback_key: str) -> str:
    return (body.get(key) or "") or (resource_info.get(fallback_key) or "")


def routing_context(
    body: dict, status_label: str, resource_info: dict, routing_info: dict
) -> dict[str, Any]:
    """Context matched by smart routing rules; body fields win over resource_info."""
    return {
        "resourceId": _first_present(body, "resource_id", resource_info, "resource_id")
        or None,
        "resourceGroup": _first_present(
            body, "resource_group", resource_info, "resource_rg"
        )
        or None,
        "resourceName": _first_present(
            body, "resource_name", resource_info, "resource_name"
        )
        or None,
        "schemaName": body.get("name"),
        "severity": body.get("severity"),
        "namespace": _first_present(body, "namespace", resource_info, "aks_namespace")
        or None,
        "oncall": (body.get("oncall") or False),
        "status": status_label,
        "execId": body.get("exec_id"),
        "name": body.get("name"),
        "id": body.get("id"),
        "team": body.get("team") or resource_info.get("team"),
        "routing_info": routing_info,
    }


def _truncate(text: str, limit: int, suffix: str) -> str:
    return text[:limit] + suffix if len(text) > limit else text


def _slack_blocks(
    body: dict,
    status_label: str,
    status_emoji: str,
    resource_info: dict,
    logs_raw: str,
) -> list[dict]:
    exec_id = body.get("exec_id")
    # The execution row lives in today's partition; link to it from the UI.
    partition_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    ui_url = f"{notify.ui_base_url()}/executions?execId={exec_id}&partitionKey={partition_key}"

    # Truncate large fields for Slack blocks to avoid invalid_blocks
    resource_info_compact = _truncate(
        notify.format_compact_resource_info(resource_info) or "", 600, "..."
    )
    args_truncated = _truncate(
        (body.get("run_args") or "").strip(), 800, "\n... (truncated)"
    )
    logs_truncated = _truncate((logs_raw or "").strip(), 1000, "\n... (truncated)")

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_emoji}\t{body.get('name')}\texecution",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Execution *{body.get('name')}* transitioned to: *{status_label.upper()}*."
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*SchemaId:* `{body.get('id')}`"},
                {
                    "type": "mrkdwn",
                    "text": f"*Severity:* `{body.get('severity') or '-'}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Worker:* `{body.get('worker') or 'unknown'}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Group:* `{body.get('group') or 'default'}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Team:* `{body.get('team') or 'default'}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Runbook:* `{body.get('runbook') or '-'}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Initiator:* `{body.get('initiator') or 'SYSTEM'}`",
                },
                {"type": "mrkdwn", "text": f"*On Call:* `{body.get('oncall') or '-'}`"},
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
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Telemetry Output:*\n```{(logs_truncated or 'No telemetry available')}```",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View Full Execution 🔍",
                        "emoji": True,
                    },
                    "url": ui_url,
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"ExecId: `{exec_id}` | Event: <!date^{int(datetime.now(timezone.utc).timestamp())}^{{date_short}} {{time}}|now>",
                }
            ],
        },
    ]


def _jsm_payload(
    body: dict,
    status_label: str,
    resource_info: dict,
    logs_raw: str,
    decision: Any,
) -> dict[str, Any]:
    exec_id = body.get("exec_id")
    teams = ", ".join(
        dict.fromkeys(a.team for a in decision.actions if getattr(a, "team", None))
    )
    return {
        "message": f"[{status_label.upper()}] [{body.get('id')}] [{body.get('severity')}] {body.get('name')}",
        "priority": f"P{int(str(body.get('severity') or '').strip().lower().replace('sev', '') or '4') + 1}",
        "alias": body.get("id"),
        "monitor_condition": body.get("monitor_condition") or "",
        "details": {
            "Name": body.get("name"),
            "Id": body.get("id"),
            "ExecId": exec_id,
            "Status": body.get("status"),
            "Runbook": body.get("runbook"),
            "Run_Args": body.get("run_args"),
            "Worker": body.get("worker"),
            "Group": body.get("group"),
            "Team": body.get("team") or "default",
            "OnCall": body.get("oncall"),
            "MonitorCondition": body.get("monitor_condition"),
            "Severity": body.get("severity"),
            "Teams:": teams,
        },
        "description": f"{escalation.format_jsm_description(exec_id, resource_info, utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS or ''))}",
    }


def notify_status(
    body: dict,
    status_label: str,
    resource_info: dict,
    routing_info: dict,
    logs_raw: str,
    log_prefix: str,
    smart_routing: SmartRoutingApi,
) -> tuple[Any, list]:
    """Route the status change; returns (decision, delivery results) for the log."""
    ctx = routing_context(body, status_label, resource_info, routing_info)
    decision = smart_routing.route_alert(ctx)
    logging.debug(f"{log_prefix} {decision}")
    status_emoji = STATUS_EMOJIS.get(status_label, "ℹ️")
    exec_id = body.get("exec_id")
    payload = {
        "slack": {
            "message": f"[{exec_id}] {status_emoji} {status_label.upper()}: {body.get('name')}",
            "blocks": _slack_blocks(
                body, status_label, status_emoji, resource_info, logs_raw
            ),
        },
        "jsm": _jsm_payload(body, status_label, resource_info, logs_raw, decision),
    }
    try:
        results = (
            smart_routing.execute_actions(
                decision,
                payload,
                send_slack_fn=lambda token,
                channel,
                **kw: escalation.send_slack_execution(
                    token=token, channel=channel, **kw
                ),
                send_jsm_fn=lambda api_key, **kw: escalation.send_jsm_alert(
                    api_key=api_key, **kw
                ),
            )
            or []
        )
        return decision, results
    except Exception as e:
        logging.error(f"[{exec_id}] smart routing failed: {e}")
        return decision, []
