import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

STORAGE_CONNECTION = "AzureWebJobsStorage"
SETTINGS_TABLE_NAME = "CloudoSettings"
SETTINGS_CACHE_TTL_SECONDS = int(os.getenv("AGENT_SETTINGS_CACHE_TTL_SECONDS", "60"))
AI_ANALYSIS_TABLE_NAME = "CloudoAiAnalysis"

_SAFE_SETTING_KEY_RE = re.compile(r"^[A-Z0-9_]{1,96}$")
_INVISIBLE_WHITESPACE_RE = re.compile(
    "[\u00a0\u1680\u2000-\u200b\u202f\u205f\u2028\u2029\u3000\ufeff]"
)
_queue_clients: dict = {}
_settings_cache: dict = {}


def sanitize_setting_value(value: str) -> str:
    """Replace invisible/exotic Unicode whitespace with a plain space and
    strip the result. Safe to call on any free-text setting value."""
    if not value:
        return value
    return _INVISIBLE_WHITESPACE_RE.sub(" ", value).strip()


def decode_base64(value: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value.encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def get_queue_client(queue_name: str, conn_env: str = STORAGE_CONNECTION):
    from azure.storage.queue import (
        QueueClient,
        TextBase64DecodePolicy,
        TextBase64EncodePolicy,
    )

    conn_str = (os.environ.get(conn_env) or "").strip()
    if not conn_str:
        raise ValueError(f"Missing storage connection string in env '{conn_env}'")

    key = (conn_str, queue_name)
    client = _queue_clients.get(key)
    if client is None:
        client = QueueClient.from_connection_string(
            conn_str=conn_str,
            queue_name=queue_name,
            message_encode_policy=TextBase64EncodePolicy(),
            message_decode_policy=TextBase64DecodePolicy(),
        )
        _queue_clients[key] = client
    return client


def get_setting(key: str, default: str = "") -> str:
    """Resolve a configuration value with the same precedence used across ClouDO."""
    if not _SAFE_SETTING_KEY_RE.fullmatch(str(key or "")):
        return default

    now = time.time()
    cached = _settings_cache.get(key)
    if cached and cached[1] > now:
        return cached[0] or default

    resolved = None
    try:
        from azure.data.tables import TableClient

        conn_str = os.environ.get(STORAGE_CONNECTION)
        if conn_str:
            with TableClient.from_connection_string(
                conn_str, table_name=SETTINGS_TABLE_NAME
            ) as table_client:
                entity = table_client.get_entity(
                    partition_key="GlobalConfig", row_key=key
                )
                val = entity.get("value")
                if val:
                    resolved = sanitize_setting_value(
                        str(val).strip().strip('"').strip("'")
                    )
    except Exception:
        pass

    if not resolved:
        val = os.environ.get(key)
        if val:
            resolved = sanitize_setting_value(str(val).strip().strip('"').strip("'"))

    _settings_cache[key] = (resolved, now + SETTINGS_CACHE_TTL_SECONDS)
    return resolved or default


def write_analysis_result(
    exec_id: str,
    status: str,
    analysis: dict = None,
    error: str = None,
) -> None:
    """Persist the AI Agent's triage outcome so the ClouDO UI can display it
    on the execution detail view.
    """
    if not exec_id:
        return
    try:
        from azure.data.tables import TableClient

        conn_str = os.environ.get(STORAGE_CONNECTION)
        if not conn_str:
            return
        entity = {
            "PartitionKey": "AiAnalysis",
            "RowKey": str(exec_id),
            "status": status,
            "analysis": json.dumps(analysis, ensure_ascii=False) if analysis else None,
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with TableClient.from_connection_string(
            conn_str, table_name=AI_ANALYSIS_TABLE_NAME
        ) as table_client:
            table_client.upsert_entity(entity=entity)
    except Exception as exc:
        logging.warning("[%s] Failed to persist AI analysis result: %s", exec_id, exc)
