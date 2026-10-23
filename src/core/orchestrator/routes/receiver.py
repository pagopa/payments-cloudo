"""Execution status receiver (notification queue trigger + HTTP fast path)."""

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import azure.functions as func
from core.config import (
    AI_ANALYSIS_QUEUE_NAME,
    MAX_TABLE_CHARS,
    NOTIFICATION_QUEUE_NAME,
    STORAGE_CONN,
    STORAGE_CONNECTION,
    TABLE_AI_ANALYSIS,
    TABLE_NAME,
)

from core import executions, log_blobs, notify, storage, team_access

bp = func.Blueprint()


def _process_receiver_body(body: dict, log_table: func.Out[str]) -> None:
    """Internal helper function to process receiver message body (shared by HTTP and queue)."""
    import utils
    from escalation import format_jsm_description, send_jsm_alert, send_slack_execution

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    try:
        receiver_prefix = executions.build_exec_log_prefix(
            body.get("exec_id"), body.get("initiator")
        )
        # Normalize fields that can arrive as list/scalars to keep Receiver robust.
        for key in ("exec_id", "status", "name", "id", "runbook", "team"):
            value = body.get(key)
            if isinstance(value, list):
                first = next((str(v).strip() for v in value if str(v).strip()), "")
                body[key] = first
            elif value is None:
                body[key] = ""
            else:
                body[key] = str(value).strip()

        body_for_log = dict(body)
        if isinstance(body.get("routing_info"), dict):
            body_for_log["routing_info"] = {
                "team": body["routing_info"].get("team"),
                "slack_channel": body["routing_info"].get("slack_channel"),
                "redacted": True,
            }
    except Exception as e:
        logging.error(f"[Receiver] Invalid queue message: {e}")
        return

    required_fields = ["exec_id", "status", "name", "id", "runbook"]
    missing = [k for k in required_fields if not body.get(k)]
    if missing:
        logging.warning(f"{receiver_prefix} Missing required fields: {missing}")
        return

    logging.info(
        f"{receiver_prefix} [{body.get('status')}] Receiver invoked",
        extra={
            "headers": {
                "ExecId": body.get("exec_id"),
                "Status": body.get("status"),
                "Name": body.get("name"),
                "Id": body.get("id"),
                "Runbook": body.get("runbook"),
                "Run_Args": body.get("run_args"),
                "OnCall": body.get("oncall"),
                "Initiator": body.get("initiator"),
                "MonitorCondition": body.get("monitor_condition"),
                "Severity": body.get("severity"),
                "Team": body.get("team"),
            }
        },
    )

    requested_at = utils.format_requested_at()
    partition_key = utils.today_partition_key()
    row_key = str(uuid.uuid4())
    status_label = executions.resolve_status(body.get("status"))

    provided_log_ref = body.get("log_ref") or ""
    logs_raw = ""
    try:
        logs_raw = log_blobs.decode_base64(body.get("logs_b64") or "")
    except Exception:
        logs_raw = ""
    log_value: Optional[str] = None
    if isinstance(provided_log_ref, str) and log_blobs.parse_blob_ref(provided_log_ref):
        log_value = provided_log_ref
    elif logs_raw:
        try:
            log_value = log_blobs.upload_log_to_blob(
                partition_key=partition_key,
                exec_id=body.get("exec_id") or "",
                status=status_label,
                logs_raw=logs_raw,
            )
        except Exception as e:
            logging.warning(
                f"{receiver_prefix} blob upload failed, falling back to table log: {type(e).__name__}: {e}"
            )
            log_value = utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS)
    else:
        log_value = ""

    # Normalize resource_info/routing_info to dicts *before*
    # building the log entity
    resource_info = body.get("resource_info") or {}
    routing_info = body.get("routing_info") or {}
    if isinstance(resource_info, str):
        try:
            parsed = json.loads(resource_info)
            resource_info = parsed if isinstance(parsed, dict) else {}
        except Exception:
            resource_info = {}
    if isinstance(routing_info, str):
        try:
            parsed = json.loads(routing_info)
            routing_info = parsed if isinstance(parsed, dict) else {}
        except Exception:
            routing_info = {}

    body["team"] = team_access.normalize_team(
        body.get("team") or resource_info.get("team")
    )

    log_entity = executions.build_log_entry(
        status=status_label,
        partition_key=partition_key,
        row_key=row_key,
        exec_id=body.get("exec_id"),
        requested_at=requested_at,
        name=body.get("name"),
        schema_id=body.get("id"),
        runbook=body.get("runbook"),
        run_args=body.get("run_args"),
        worker=body.get("worker"),
        group=body.get("group"),
        log_msg=log_value,
        oncall=body.get("oncall"),
        initiator=body.get("initiator"),
        resource_info=resource_info,
        monitor_condition=body.get("monitor_condition"),
        severity=body.get("severity"),
        team=body.get("team") or resource_info.get("team"),
    )
    log_entity = log_blobs.ensure_log_entity_size_for_table(log_entity)
    log_table.set(json.dumps(log_entity, ensure_ascii=False))

    resource_id = (body.get("resource_id") or "") or (
        resource_info.get("resource_id") or ""
    )
    notification_results: list = []
    notification_decision: Any = None
    resource_group = (body.get("resource_group") or "") or (
        resource_info.get("resource_rg") or ""
    )
    resource_name = (body.get("resource_name") or "") or (
        resource_info.get("resource_name") or ""
    )
    namespace = (body.get("namespace") or "") or (
        resource_info.get("aks_namespace") or ""
    )

    if route_alert and execute_actions:
        exec_id = body.get("exec_id")
        ctx = {
            "resourceId": resource_id or None,
            "resourceGroup": resource_group or None,
            "resourceName": resource_name or None,
            "schemaName": body.get("name"),
            "severity": body.get("severity"),
            "namespace": namespace or None,
            "oncall": (body.get("oncall") or False),
            "status": status_label,
            "execId": exec_id,
            "name": body.get("name"),
            "id": body.get("id"),
            "team": body.get("team") or resource_info.get("team"),
            "routing_info": routing_info,  # sempre dict qui
        }
        decision = route_alert(ctx)
        logging.debug(f"{receiver_prefix} {decision}")
        status_emojis = {
            "succeeded": "✅",
            "running": "🏃",
            "skipped": "⏭️",
            "routed": "🧭",
            "error": "❌",
            "failed": "❌",
            "accepted": "📩",
            "pending": "⏳",
        }
        status_emoji = status_emojis.get(status_label, "ℹ️")

        # UI Base URL
        ui_base = (
            (os.getenv("NEXTJS_URL") or "http://localhost:3000").strip().rstrip("/")
        )
        if not ui_base.startswith("http"):
            ui_base = f"https://{ui_base}" if ui_base else "http://localhost:3000"
        partition_key = datetime.now(timezone.utc).strftime("%Y%m%d")
        ui_url = f"{ui_base}/executions?execId={exec_id}&partitionKey={partition_key}"

        # Truncate large fields for Slack blocks to avoid invalid_blocks
        resource_info_compact = notify.format_compact_resource_info(resource_info) or ""
        if len(resource_info_compact) > 600:
            resource_info_compact = resource_info_compact[:600] + "..."

        args_truncated = (body.get("run_args") or "").strip()
        if len(args_truncated) > 800:
            args_truncated = args_truncated[:800] + "\n... (truncated)"

        logs_truncated = (logs_raw or "").strip()
        if len(logs_truncated) > 1000:
            logs_truncated = logs_truncated[:1000] + "\n... (truncated)"

        payload = {
            "slack": {
                "message": f"[{exec_id}] {status_emoji} {status_label.upper()}: {body.get('name')}",
                "blocks": [
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
                            {
                                "type": "mrkdwn",
                                "text": f"*SchemaId:* `{body.get('id')}`",
                            },
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
                            {
                                "type": "mrkdwn",
                                "text": f"*On Call:* `{body.get('oncall') or '-'}`",
                            },
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
                ],
            },
            "jsm": {
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
                    "Teams:": ", ".join(
                        dict.fromkeys(
                            a.team for a in decision.actions if getattr(a, "team", None)
                        )
                    ),
                },
                "description": f"{format_jsm_description(exec_id, resource_info, utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS or ''))}",
            },
        }
        try:
            notification_decision = decision
            notification_results = (
                execute_actions(
                    decision,
                    payload,
                    send_slack_fn=lambda token, channel, **kw: send_slack_execution(
                        token=token, channel=channel, **kw
                    ),
                    send_jsm_fn=lambda api_key, **kw: send_jsm_alert(
                        api_key=api_key, **kw
                    ),
                )
                or []
            )
        except Exception as e:
            logging.error(f"[{exec_id}] smart routing failed: {e}")
    else:
        logging.warning("Routing module not available, keeping legacy notifications")

    notifications_value = executions.notifications_json(
        notification_decision, notification_results
    )
    if notifications_value:
        log_entity["Notifications"] = notifications_value
        log_table.set(json.dumps(log_entity, ensure_ascii=False))

    _enqueue_ai_agent_analysis(
        body=body,
        status_label=status_label,
        resource_info=resource_info,
        routing_info=routing_info,
        logs_raw=logs_raw,
    )


def _enqueue_ai_agent_analysis(
    body: dict,
    status_label: str,
    resource_info: dict,
    routing_info: dict,
    logs_raw: str,
) -> None:
    """Forward failed/errored executions to the ClouDO Agent (see
    src/core/agent) for asynchronous AI triage. No-op unless
    AI_AGENT_ENABLED=true, so this is fully backward compatible when the
    agent service is not deployed.

    The JSM alias sent here (`jsm_alias`) matches the alias used by the
    smart-routing JSM alert above (body["id"], the schema id), so the
    agent's triage note lands on the same alert.
    """
    try:
        from smart_routing import get_setting

        agent_enabled = (get_setting("AI_AGENT_ENABLED") or "false").strip().lower()
    except Exception:
        agent_enabled = os.getenv("AI_AGENT_ENABLED", "false").strip().lower()
    if agent_enabled != "true":
        return
    if status_label not in ("failed", "error"):
        return

    # Manual "Dev Test Run" executions launched from Studio.
    is_dev_test_run = str(body.get("name") or "").strip().lower() == "dev test run" or (
        isinstance(resource_info, dict)
        and str(resource_info.get("team") or "").strip().lower() == "dev-test"
    )
    if is_dev_test_run:
        logging.debug(
            "[%s] Agent: skipping AI triage for Dev Test Run execution",
            body.get("exec_id"),
        )
        return

    import utils

    try:
        ai_queue = storage.get_queue_client(
            AI_ANALYSIS_QUEUE_NAME, conn_env=STORAGE_CONNECTION
        )
        ai_payload = {
            "exec_id": body.get("exec_id"),
            "jsm_alias": body.get("id"),
            "id": body.get("id"),
            "name": body.get("name"),
            "status": status_label,
            "runbook": body.get("runbook"),
            "run_args": body.get("run_args"),
            "monitor_condition": body.get("monitor_condition"),
            "severity": body.get("severity"),
            "initiator": body.get("initiator"),
            "resource_info": resource_info if isinstance(resource_info, dict) else {},
            "routing_info": (
                {"team": routing_info.get("team")}
                if isinstance(routing_info, dict)
                else {}
            ),
            "logs": utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS)
            if logs_raw
            else "",
        }
        _write_ai_analysis_placeholder(body.get("exec_id"), body.get("team"))
        ai_queue.send_message(json.dumps(ai_payload, ensure_ascii=False))
    except Exception as e:
        logging.error(
            f"[{body.get('exec_id')}] Failed to enqueue AI agent analysis: {e}"
        )


def _write_ai_analysis_placeholder(exec_id: str, team: Any = None) -> None:
    """Upsert a 'processing' placeholder as soon as an execution is enqueued
    for AI triage, so the UI can immediately show an in-progress state
    instead of "not found" while the Agent service works through the queue.
    The Agent overwrites this entity with the final result (see
    agent/utils.py write_analysis_result)."""
    if not exec_id:
        return
    try:
        table_client = storage.get_table_client(
            TABLE_AI_ANALYSIS, conn_env=STORAGE_CONNECTION
        )
        table_client.upsert_entity(
            entity={
                "PartitionKey": "AiAnalysis",
                "RowKey": str(exec_id),
                "team": team_access.normalize_team(team),
                "status": "processing",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as e:
        logging.error(f"[{exec_id}] Failed to write AI analysis placeholder: {e}")


def _decode_queue_message_text(raw: str) -> dict:
    """Parse a queue message body as JSON.

    The Storage Queue trigger is configured (host.json: extensions.queues.
    messageEncoding = "base64") to auto base64-decode message content before
    handing it to the function, matching the TextBase64EncodePolicy used when
    messages are sent. If that host-side decode is ever bypassed (e.g. bundle
    resolution differences), we still get handed the raw base64 text here, so
    fall back to decoding it manually before giving up.
    """
    text = raw or ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            decoded = base64.b64decode(text).decode("utf-8")
        except Exception:
            raise
        return json.loads(decoded)


@bp.queue_trigger(
    arg_name="msg", queue_name=NOTIFICATION_QUEUE_NAME, connection=STORAGE_CONNECTION
)
@bp.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
def Receiver(msg: func.QueueMessage, log_table: func.Out[str]) -> None:
    """Queue trigger endpoint for receiving execution status from workers."""
    try:
        body = _decode_queue_message_text(msg.get_body().decode("utf-8"))
        _process_receiver_body(body, log_table)
    except Exception as e:
        logging.error(f"[Receiver] Failed to process queue message: {e}")


@bp.route(
    route="receiver",
    methods=[func.HttpMethod.POST],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@bp.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
def ReceiverHttp(req: func.HttpRequest, log_table: func.Out[str]) -> func.HttpResponse:
    """HTTP endpoint for direct status calls from workers (faster than queue)."""
    try:
        body = req.get_json()
        if not isinstance(body, dict):
            return func.HttpResponse(
                json.dumps(
                    {"error": "Invalid JSON: expected object"}, ensure_ascii=False
                ),
                status_code=400,
                mimetype="application/json",
            )
        _process_receiver_body(body, log_table)
        return func.HttpResponse(
            json.dumps({"status": "ok", "message": "Status received"}),
            status_code=200,
            mimetype="application/json",
        )
    except ValueError as e:
        return func.HttpResponse(
            json.dumps({"error": f"Invalid JSON: {e}"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )
    except Exception as e:
        logging.error(f"[ReceiverHttp] Error processing request: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Internal error: {type(e).__name__}"}, ensure_ascii=False
            ),
            status_code=500,
            mimetype="application/json",
        )
