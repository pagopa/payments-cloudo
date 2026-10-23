"""Execution status updates reported by workers: validate, persist and notify."""

import json
import logging
import uuid
from typing import Any, Optional

import azure.functions as func
import utils
from core.config import MAX_TABLE_CHARS
from services import ai_triage, smart_routing_api, status_notifications

from core import executions, log_blobs, team_access

REQUIRED_FIELDS = ["exec_id", "status", "name", "id", "runbook"]
# Fields that can arrive as list/scalars; normalized to a stripped string.
SCALAR_FIELDS = ("exec_id", "status", "name", "id", "runbook", "team")


def _normalize_scalar_fields(body: dict) -> None:
    for key in SCALAR_FIELDS:
        value = body.get(key)
        if isinstance(value, list):
            first = next((str(v).strip() for v in value if str(v).strip()), "")
            body[key] = first
        elif value is None:
            body[key] = ""
        else:
            body[key] = str(value).strip()


def _as_dict(value: Any) -> dict:
    """resource_info/routing_info may arrive as a dict or a JSON string."""
    value = value or {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return value


def _decode_logs(body: dict) -> str:
    try:
        return log_blobs.decode_base64(body.get("logs_b64") or "")
    except Exception:
        return ""


def _log_value(
    body: dict, status_label: str, logs_raw: str, partition_key: str, log_prefix: str
) -> Optional[str]:
    """Reuse the worker's blob ref, else offload logs to blob (table fallback)."""
    provided_log_ref = body.get("log_ref") or ""
    if isinstance(provided_log_ref, str) and log_blobs.parse_blob_ref(provided_log_ref):
        return provided_log_ref
    if not logs_raw:
        return ""
    try:
        return log_blobs.upload_log_to_blob(
            partition_key=partition_key,
            exec_id=body.get("exec_id") or "",
            status=status_label,
            logs_raw=logs_raw,
        )
    except Exception as e:
        logging.warning(
            f"{log_prefix} blob upload failed, falling back to table log: {type(e).__name__}: {e}"
        )
        return utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS)


def _log_invocation(body: dict, log_prefix: str) -> None:
    logging.info(
        f"{log_prefix} [{body.get('status')}] Receiver invoked",
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


def _write_log(
    body: dict,
    status_label: str,
    resource_info: dict,
    log_value: Optional[str],
    partition_key: str,
    log_table: func.Out[str],
) -> dict[str, Any]:
    log_entity = executions.build_log_entry(
        status=status_label,
        partition_key=partition_key,
        row_key=str(uuid.uuid4()),
        exec_id=body.get("exec_id"),
        requested_at=utils.format_requested_at(),
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
    return log_entity


def process(body: dict, log_table: func.Out[str]) -> None:
    """Handle one status message (shared by the queue trigger and HTTP fast path).

    `body` is normalized in place. Invalid or incomplete messages are dropped.
    """
    smart_routing = smart_routing_api.load()

    try:
        log_prefix = executions.build_exec_log_prefix(
            body.get("exec_id"), body.get("initiator")
        )
        _normalize_scalar_fields(body)
    except Exception as e:
        logging.error(f"[Receiver] Invalid queue message: {e}")
        return

    missing = [k for k in REQUIRED_FIELDS if not body.get(k)]
    if missing:
        logging.warning(f"{log_prefix} Missing required fields: {missing}")
        return

    _log_invocation(body, log_prefix)

    partition_key = utils.today_partition_key()
    status_label = executions.resolve_status(body.get("status"))
    logs_raw = _decode_logs(body)
    log_value = _log_value(body, status_label, logs_raw, partition_key, log_prefix)

    resource_info = _as_dict(body.get("resource_info"))
    routing_info = _as_dict(body.get("routing_info"))
    body["team"] = team_access.normalize_team(
        body.get("team") or resource_info.get("team")
    )
    log_entity = _write_log(
        body, status_label, resource_info, log_value, partition_key, log_table
    )

    decision, results = None, []
    if smart_routing.available:
        decision, results = status_notifications.notify_status(
            body,
            status_label,
            resource_info,
            routing_info,
            logs_raw,
            log_prefix,
            smart_routing,
        )
    else:
        logging.warning("Routing module not available, keeping legacy notifications")

    notifications_value = executions.notifications_json(decision, results)
    if notifications_value:
        log_entity["Notifications"] = notifications_value
        log_table.set(json.dumps(log_entity, ensure_ascii=False))

    ai_triage.enqueue_analysis(
        body=body,
        status_label=status_label,
        resource_info=resource_info,
        routing_info=routing_info,
        logs_raw=logs_raw,
    )
