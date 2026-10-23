"""Hand failed executions to the ClouDO Agent (src/core/agent) for AI triage."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import utils
from core.config import (
    AI_ANALYSIS_QUEUE_NAME,
    MAX_TABLE_CHARS,
    STORAGE_CONNECTION,
    TABLE_AI_ANALYSIS,
)

from core import storage, team_access


def _agent_enabled() -> bool:
    try:
        from smart_routing import get_setting

        agent_enabled = (get_setting("AI_AGENT_ENABLED") or "false").strip().lower()
    except Exception:
        agent_enabled = os.getenv("AI_AGENT_ENABLED", "false").strip().lower()
    return agent_enabled == "true"


def _is_dev_test_run(body: dict, resource_info: Any) -> bool:
    """Manual "Dev Test Run" executions launched from Studio."""
    return str(body.get("name") or "").strip().lower() == "dev test run" or (
        isinstance(resource_info, dict)
        and str(resource_info.get("team") or "").strip().lower() == "dev-test"
    )


def enqueue_analysis(
    body: dict,
    status_label: str,
    resource_info: dict,
    routing_info: dict,
    logs_raw: str,
) -> None:
    """Forward failed/errored executions to the agent for asynchronous AI
    triage. No-op unless AI_AGENT_ENABLED=true, so this is fully backward
    compatible when the agent service is not deployed.

    The JSM alias sent here (`jsm_alias`) matches the alias used by the
    smart-routing JSM alert (body["id"], the schema id), so the agent's
    triage note lands on the same alert.
    """
    if not _agent_enabled():
        return
    if status_label not in ("failed", "error"):
        return
    if _is_dev_test_run(body, resource_info):
        logging.debug(
            "[%s] Agent: skipping AI triage for Dev Test Run execution",
            body.get("exec_id"),
        )
        return

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
        write_placeholder(body.get("exec_id"), body.get("team"))
        ai_queue.send_message(json.dumps(ai_payload, ensure_ascii=False))
    except Exception as e:
        logging.error(
            f"[{body.get('exec_id')}] Failed to enqueue AI agent analysis: {e}"
        )


def write_placeholder(exec_id: str, team: Any = None) -> None:
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
