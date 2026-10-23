"""Execution log payloads: base64 encoding, blob offloading and table size limits."""

import base64
import json
import logging
from typing import Any, Optional

from core.config import (
    LOGS_BLOB_CONTAINER,
    LOGS_REF_PREFIX,
    MAX_TABLE_CHARS,
    MAX_TABLE_ENTITY_BODY_BYTES,
    STORAGE_CONN,
)

from core import storage

_LOGS_CONTAINER_READY = False


def decode_base64(data: str) -> str:
    """Decode base64 encoded string to utf-8 string"""
    try:
        return base64.b64decode(data).decode("utf-8")
    except Exception as e:
        logging.warning(f"Failed to decode base64 encoded string: {e}")
        return data


def encode_logs(text: str) -> bytes:
    """Encode log text in base64 UTF-8."""
    raw = (text or "").encode("utf-8", errors="replace")
    return base64.b64encode(raw)


def make_log_blob_name(partition_key: str, exec_id: str, status: str) -> str:
    safe_exec_id = (exec_id or "unknown").strip() or "unknown"
    safe_status_raw = (status or "UNKNOWN").strip().upper() or "UNKNOWN"
    safe_status = "".join(ch if ch.isalnum() else "_" for ch in safe_status_raw).strip(
        "_"
    )
    safe_status = safe_status or "UNKNOWN"
    return f"{partition_key}/{safe_exec_id}/{safe_status}_{safe_exec_id}.log"


def blob_ref(blob_name: str) -> str:
    return f"{LOGS_REF_PREFIX}{LOGS_BLOB_CONTAINER}/{blob_name}"


def parse_blob_ref(log_value: Optional[str]) -> Optional[tuple[str, str]]:
    if not isinstance(log_value, str) or not log_value.startswith(LOGS_REF_PREFIX):
        return None
    prefix_len = len(LOGS_REF_PREFIX)
    ref = log_value[prefix_len:]
    if "/" not in ref:
        return None
    container, blob_name = ref.split("/", 1)
    container = container.strip()
    blob_name = blob_name.strip()
    if not container or not blob_name:
        return None
    return container, blob_name


def upload_log_to_blob(
    partition_key: str, exec_id: str, status: str, logs_raw: str
) -> str:
    global _LOGS_CONTAINER_READY

    blob_name = make_log_blob_name(partition_key, exec_id, status)
    service = storage.get_blob_service(STORAGE_CONN)
    container = service.get_container_client(LOGS_BLOB_CONTAINER)
    if not _LOGS_CONTAINER_READY:
        try:
            container.create_container()
        except Exception:
            pass
        _LOGS_CONTAINER_READY = True
    blob = container.get_blob_client(blob_name)
    blob.upload_blob((logs_raw or "").encode("utf-8", errors="replace"), overwrite=True)
    return blob_ref(blob_name)


def download_log_from_blob_ref(log_value: Optional[str]) -> Optional[str]:
    parsed = parse_blob_ref(log_value)
    if not parsed:
        return None
    container, blob_name = parsed
    service = storage.get_blob_service(STORAGE_CONN)
    blob = service.get_blob_client(container=container, blob=blob_name)
    content = blob.download_blob().readall()
    return content.decode("utf-8", errors="replace")


def hydrate_log_field(entity: dict[str, Any]) -> dict[str, Any]:
    current_log = entity.get("Log")
    resolved_log = download_log_from_blob_ref(current_log)
    if resolved_log is None:
        return entity
    hydrated = dict(entity)
    hydrated["Log"] = resolved_log
    hydrated["LogRef"] = current_log
    return hydrated


def ensure_log_entity_size_for_table(entity: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(entity, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= MAX_TABLE_ENTITY_BODY_BYTES:
        return entity

    adjusted = dict(entity)
    shrink_plan = [
        ("ResourceInfo", 4000),
        ("Run_Args", 4000),
        ("MonitorCondition", 2000),
        ("Runbook", 2000),
        ("Log", MAX_TABLE_CHARS),
    ]

    for field, limit in shrink_plan:
        value = adjusted.get(field)
        if isinstance(value, str) and value:
            adjusted[field] = value[:limit]
            serialized = json.dumps(adjusted, ensure_ascii=False)
            if len(serialized.encode("utf-8")) <= MAX_TABLE_ENTITY_BODY_BYTES:
                return adjusted

    if isinstance(adjusted.get("Log"), str) and adjusted.get("Log"):
        adjusted["Log"] = (
            "[omitted due to Azure Table request size limit; see worker output/blob logs]"
        )

    serialized = json.dumps(adjusted, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= MAX_TABLE_ENTITY_BODY_BYTES:
        return adjusted

    for field in ("ResourceInfo", "Run_Args", "MonitorCondition", "Runbook"):
        if adjusted.get(field):
            adjusted[field] = None

    return adjusted
