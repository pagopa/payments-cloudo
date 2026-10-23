"""Slack/JSM notifications that are not driven by smart routing."""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from core.config import SETTINGS_CACHE_TTL_SECONDS, STORAGE_CONN, TABLE_SETTINGS

from core import storage, team_access

_settings_cache: dict[str, Any] = {
    "token": "",
    "channel": "#cloudo-test",
    "expires_at": 0.0,
}


def get_default_notification_settings() -> tuple[str, str]:
    now = time.time()
    if _settings_cache.get("expires_at", 0.0) > now:
        return _settings_cache.get("token", ""), _settings_cache.get(
            "channel", "#cloudo-test"
        )

    token = (os.environ.get("SLACK_TOKEN_DEFAULT") or "").strip()
    channel = (os.environ.get("SLACK_CHANNEL") or "").strip() or "#cloudo-test"

    try:
        if os.environ.get(STORAGE_CONN):
            table_client = storage.get_table_client(TABLE_SETTINGS)
            token_entity = table_client.get_entity(
                partition_key="GlobalConfig", row_key="SLACK_TOKEN_DEFAULT"
            )
            channel_entity = table_client.get_entity(
                partition_key="GlobalConfig", row_key="SLACK_CHANNEL"
            )
            token = token_entity.get("value", "").strip()
            channel = channel_entity.get("value", "").strip() or "#cloudo-test"
    except Exception as e:
        logging.warning(
            f"Failed to fetch Slack settings from Table Storage, falling back to ENV: {e}"
        )

    _settings_cache["token"] = token
    _settings_cache["channel"] = channel
    _settings_cache["expires_at"] = now + SETTINGS_CACHE_TTL_SECONDS
    return token, channel


def format_compact_resource_info(resource_info: Optional[dict]) -> Optional[str]:
    """Formats resource_info into a compact mrkdwn string, excluding _raw and resource_id."""
    if not resource_info:
        return None

    # Filter out _raw, resource_id and empty/null values
    filtered = {
        k: v
        for k, v in resource_info.items()
        if k not in ["_raw", "resource_id"] and v is not None and str(v).strip() != ""
    }

    if not filtered:
        return None

    # Format as a single line or compact list
    items = [f"*{k}*: `{v}`" for k, v in filtered.items()]
    return " | ".join(items)


def ui_base_url() -> str:
    """Base URL of the web UI, used for deep links in notifications."""
    ui_base = (os.getenv("NEXTJS_URL") or "http://localhost:3000").strip().rstrip("/")
    if not ui_base.startswith("http"):
        ui_base = f"https://{ui_base}" if ui_base else "http://localhost:3000"
    return ui_base


def notify_slack_decision(
    exec_id: str,
    schema_id: str,
    decision: str,
    approver: str,
    extra: str = "",
    routing_info: Optional[dict] = None,
    team: Optional[str] = None,
) -> None:
    from escalation import send_jsm_alert, send_slack_execution
    from smart_routing import resolve_jsm_apikey

    token, channel = get_default_notification_settings()

    if not token:
        return

    emoji = "✅" if decision == "approved" else "❌"

    ui_base = ui_base_url()
    partition_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    ui_url = f"{ui_base}/executions?execId={exec_id}&partitionKey={partition_key}"

    # Truncate extra to avoid Slack invalid_blocks (max 3000 chars for mrkdwn sections)
    extra_truncated = (extra or "").strip()
    if len(extra_truncated) > 1500:
        extra_truncated = extra_truncated[:1500] + "\n... (truncated)"

    try:
        send_slack_execution(
            token=token,
            channel=channel,
            message=f"[{exec_id}] {emoji} {decision.upper()} - {schema_id}",
            blocks=[
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Gate Decision: {decision.upper()} {emoji}",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Execution request for *{schema_id}* has been *{decision.upper()}* by *{approver}*.",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Schema:*\n`{schema_id}`"},
                        {"type": "mrkdwn", "text": f"*ExecId:*\n`{exec_id}`"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Team:*\n`{team_access.normalize_team(team)}`",
                        },
                    ],
                },
                *(
                    [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Reason/Details:*\n{extra_truncated}",
                            },
                        }
                    ]
                    if extra_truncated
                    else []
                ),
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View Execution 🔍",
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
                            "text": f"Timestamp: <!date^{int(datetime.now(timezone.utc).timestamp())}^{{date_short}} {{time}}|now> | System: `cloudo-orchestrator`",
                        }
                    ],
                },
            ],
        )
    except Exception as e:
        logging.error(f"[{exec_id}] Slack decision notify failed: {e}")

    # JSM notify for decision
    if routing_info:
        jsm_token = (
            routing_info.get("jsm_token")
            or routing_info.get("opsgenie_token")
            or resolve_jsm_apikey(routing_info.get("team"))
        )
        if jsm_token:
            try:
                send_jsm_alert(
                    api_key=jsm_token,
                    message=f"[{exec_id}] {emoji} GATE {decision.upper()}: {schema_id}",
                    description=(
                        f"Execution request for {schema_id} has been {decision.upper()} by {approver}.\n\n"
                        f"ExecId: {exec_id}\n"
                        f"Team: {team_access.normalize_team(team)}\n"
                        f"Reason/Details: {extra or '-'}\n\n"
                        f"View Execution: {ui_url}"
                    ),
                    priority="P3",
                    alias=exec_id,
                    tags=["gate-decision", decision],
                    details={
                        "execId": exec_id,
                        "schemaId": schema_id,
                        "team": team_access.normalize_team(team),
                        "decision": decision,
                        "approver": approver,
                    },
                )
            except Exception as e:
                logging.error(f"[{exec_id}] JSM decision notify failed: {e}")
