"""Execution log entities and the status lifecycle shared by trigger/receiver/approvals."""

import json
from datetime import datetime
from typing import Any, Optional

from core.config import DEFAULT_TEAM, STATUS_PRIORITY

from core import log_blobs, team_access


def status_priority(status: Any) -> int:
    return STATUS_PRIORITY.get(str(status or "").strip().lower(), 0)


def parse_dt_local(v: str) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        try:
            return datetime.strptime(v.replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def latest_log_entity(entities: list[dict]) -> Optional[dict]:
    """Pick the row representing the current state of an execution:
    highest status priority, then most recent RequestedAt, then RowKey"""

    def _key(e: dict):
        dt = parse_dt_local(str(e.get("RequestedAt") or ""))
        return (
            status_priority(e.get("Status")),
            dt is not None,
            dt or datetime.min,
            str(e.get("RowKey") or ""),
        )

    return max(entities, key=_key, default=None)


def resolve_status(header_status: Optional[str]) -> str:
    # Map incoming header status to a canonical label for logs
    normalized = (header_status or "").strip().lower()
    return "succeeded" if normalized == "completed" else normalized


def build_log_entry(
    *,
    status: str,
    partition_key: str,
    row_key: str,
    exec_id: Optional[str],
    requested_at: str,
    name: Optional[str],
    schema_id: Optional[str],
    runbook: Optional[str],
    run_args: Optional[str],
    worker: Optional[str],
    group: Optional[str],
    log_msg: Optional[str],
    oncall: Optional[bool] = None,
    monitor_condition: Optional[str],
    severity: Optional[str],
    team: Optional[str] = DEFAULT_TEAM,
    initiator: Optional[str] = None,
    resource_info: Optional[dict[str, Any]] = None,
    approval_required: Optional[bool] = None,
    approval_expires_at: Optional[str] = None,
    approval_decision_by: Optional[str] = None,
    notifications: Optional[str] = None,
) -> dict[str, Any]:
    # Normalized log entity for Azure Table Storage (with optional approval fields)
    return {
        "PartitionKey": partition_key,
        "RowKey": row_key,
        "ExecId": exec_id,
        "Status": status,
        "RequestedAt": requested_at,
        "Name": name,
        "Id": schema_id,
        "Runbook": runbook,
        "Run_Args": run_args,
        "Worker": worker,
        "Group": group,
        "Log": log_msg,
        "OnCall": oncall,
        "Initiator": initiator,
        "ResourceInfo": json.dumps(resource_info, ensure_ascii=False)
        if resource_info
        else None,
        "MonitorCondition": monitor_condition,
        "Severity": severity,
        "team": team_access.normalize_team(team),
        "ApprovalRequired": approval_required,
        "ApprovalExpiresAt": approval_expires_at,
        "ApprovalDecisionBy": approval_decision_by,
        "Notifications": notifications,
    }


def notifications_json(decision: Any, results: Any) -> Optional[str]:
    """Serialize where an execution was notified (routing decision + per-target outcome)."""
    if not isinstance(results, list) or not results:
        return None
    return json.dumps(
        {
            "reason": getattr(decision, "reason", None),
            "rule": getattr(decision, "matched_rule_index", None),
            "targets": results,
        },
        ensure_ascii=False,
    )


def post_status(payload: dict, status: str, log_message: str) -> str:
    """
    Build the status message (with base64-encoded, truncated logs) to send
    on the notification queue. Used by the orchestrator to talk to the Receiver.
    """
    from utils import format_requested_at

    exec_id = payload.get("exec_id")
    log_text = log_message or ""
    log_bytes = log_blobs.encode_logs(log_text)

    MAX_LOG_BODY_BYTES = 64 * 1024
    if len(log_bytes) > MAX_LOG_BODY_BYTES:
        log_bytes = log_bytes[:MAX_LOG_BODY_BYTES]

    message = {
        "requestedAt": payload.get("requestedAt"),
        "id": payload.get("id"),
        "name": payload.get("name"),
        "exec_id": exec_id,
        "runbook": payload.get("runbook"),
        "run_args": payload.get("run_args"),
        "worker": payload.get("worker"),
        "status": status,
        "oncall": payload.get("oncall"),
        "initiator": payload.get("initiator"),
        "monitor_condition": payload.get("monitor_condition"),
        "severity": payload.get("severity"),
        "team": payload.get("team"),
        "resource_info": payload.get("resource_info"),
        "routing_info": payload.get("routing_info"),
        "logs_b64": log_bytes.decode("utf-8"),
        "content_type": "text/plain; charset=utf-8",
        "sent_at": format_requested_at(),
    }
    return json.dumps(message, ensure_ascii=False)


def build_exec_log_prefix(exec_id: Optional[str], initiator: Optional[str]) -> str:
    safe_exec_id = str(exec_id or "-").strip() or "-"
    safe_initiator = str(initiator or "SYSTEM").strip() or "SYSTEM"
    return f"[{safe_exec_id}] [initiator={safe_initiator}]"


def only_pending_for_exec(rows: list[dict], exec_id: str) -> bool:
    """
    True if ExecId had only 'pending' (o nothing).
    False if there are some other rows not 'pending'.
    """
    for e in rows:
        if str(e.get("ExecId") or "") != exec_id:
            continue
        st = str(e.get("Status") or "").strip().lower()
        if st != "pending":
            return False
    return True
