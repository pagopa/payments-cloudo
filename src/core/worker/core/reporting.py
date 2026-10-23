"""Execution status reporting to the orchestrator (HTTP receiver, queue fallback, log blobs)."""

import json
import logging
import os
from threading import Lock
from typing import Optional

import azure.functions as func
import requests
from core.config import (
    LOGS_BLOB_CONTAINER,
    LOGS_REF_PREFIX,
    MAX_INLINE_LOG_BYTES,
    NOTIFICATION_QUEUE_NAME,
    RECEIVER_HTTP_TIMEOUT,
    RECEIVER_URL,
    STORAGE_CONNECTION,
    WORKER_TEAM,
)
from utils import _format_requested_at, encode_logs

from core import security

_STORAGE_CONN_STR = os.environ.get(STORAGE_CONNECTION)


_BLOB_CONTAINER_CLIENT = None


_NOTIFICATION_QUEUE_CLIENT = None


_STORAGE_CLIENT_LOCK = Lock()


def _get_blob_container_client():
    from azure.storage.blob import BlobServiceClient

    global _BLOB_CONTAINER_CLIENT
    if _BLOB_CONTAINER_CLIENT is not None:
        return _BLOB_CONTAINER_CLIENT

    if not _STORAGE_CONN_STR:
        raise RuntimeError("Missing AzureWebJobsStorage connection string")

    with _STORAGE_CLIENT_LOCK:
        if _BLOB_CONTAINER_CLIENT is None:
            service = BlobServiceClient.from_connection_string(_STORAGE_CONN_STR)
            container = service.get_container_client(LOGS_BLOB_CONTAINER)
            try:
                container.create_container()
            except Exception:
                pass
            _BLOB_CONTAINER_CLIENT = container
    return _BLOB_CONTAINER_CLIENT


def _upload_log_blob(blob_name: str, content: str) -> str:
    container = _get_blob_container_client()
    blob = container.get_blob_client(blob_name)
    blob.upload_blob((content or "").encode("utf-8", errors="replace"), overwrite=True)
    return f"{LOGS_REF_PREFIX}{LOGS_BLOB_CONTAINER}/{blob_name}"


def _build_status_headers(payload: dict, status: str, log_message: str) -> dict:
    """Build lightweight headers for the Receiver call; move logs into the JSON body."""
    return {
        "runbook": payload.get("runbook"),
        "run_args": payload.get("run_args"),
        "Id": payload.get("id"),
        "Name": payload.get("name"),
        "ExecId": payload.get("exec_id"),
        "Worker": payload.get("worker"),
        "Group": payload.get("group"),
        "Content-Type": "application/json",
        "Status": status,
        "OnCall": payload.get("oncall"),
        "Initiator": payload.get("initiator"),
        "MonitorCondition": payload.get("monitor_condition"),
        "Severity": payload.get("severity"),
        "Team": payload.get("team") or WORKER_TEAM,
        "ResourceInfo": payload.get("resource_info"),
        "RoutingInfo": payload.get("routing_info"),
    }


def _make_worker_log_blob_name(payload: dict, status: str) -> str:
    requested_at = str(payload.get("requestedAt") or "").strip()
    if (
        len(requested_at) >= 10
        and requested_at[4:5] == "-"
        and requested_at[7:8] == "-"
    ):
        partition_key = requested_at[:10].replace("-", "")
    else:
        partition_key = _format_requested_at()[:10].replace("-", "")

    safe_exec_id = str(payload.get("exec_id") or "unknown").strip() or "unknown"
    safe_status_raw = str(status or "UNKNOWN").strip().upper() or "UNKNOWN"
    safe_status = "".join(ch if ch.isalnum() else "_" for ch in safe_status_raw).strip(
        "_"
    )
    safe_status = safe_status or "UNKNOWN"
    return f"{partition_key}/{safe_exec_id}/{safe_status}_{safe_exec_id}.log"


def update_running_log_blob(payload: dict, log_message: str) -> None:
    """Persist latest running output into a single blob snapshot (overwrite)."""
    blob_name = _make_worker_log_blob_name(payload, "running")
    _upload_log_blob(blob_name, log_message)


def post_status(payload: dict, status: str, log_message: str) -> str:
    """POST execution status to the Receiver with logs in the JSON body (truncated if too large)."""
    headers = _build_status_headers(payload, status, log_message)

    log_text = log_message or ""
    log_bytes = encode_logs(log_text)
    log_ref = ""
    if len(log_bytes) > MAX_INLINE_LOG_BYTES:
        try:
            blob_name = _make_worker_log_blob_name(payload, status)
            log_ref = _upload_log_blob(blob_name, log_text)
            log_bytes = b""
        except Exception as e:
            logging.warning(
                "[%s] worker blob upload failed, fallback to inline logs: %s: %s",
                payload.get("exec_id"),
                type(e).__name__,
                e,
            )
            log_bytes = log_bytes[:MAX_INLINE_LOG_BYTES]

    message = {
        "requestedAt": payload.get("requestedAt"),
        "id": headers.get("Id"),
        "name": headers.get("Name"),
        "exec_id": headers.get("ExecId"),
        "runbook": headers.get("runbook"),
        "run_args": headers.get("run_args"),
        "worker": headers.get("Worker"),
        "group": headers.get("Group"),
        "status": headers.get("Status"),
        "oncall": headers.get("OnCall"),
        "initiator": headers.get("Initiator"),
        "monitor_condition": headers.get("MonitorCondition"),
        "severity": headers.get("Severity"),
        "team": headers.get("Team"),
        "resource_info": headers.get("ResourceInfo"),
        "routing_info": headers.get("RoutingInfo"),
        "log_ref": log_ref,
        "logs_b64": log_bytes.decode("utf-8"),
        "content_type": "text/plain; charset=utf-8",
        "sent_at": _format_requested_at(),
    }
    return json.dumps(message, ensure_ascii=False)


def _send_status_to_receiver(message_json: str, payload: dict) -> bool:
    """
    Try to send status via HTTP first (fast path).
    Returns True if successful, False if should fall back to queue.
    """
    exec_id = payload.get("exec_id", "unknown")

    if not RECEIVER_URL or RECEIVER_URL.strip() == "":
        logging.debug(f"[{exec_id}] RECEIVER_URL not configured, using queue")
        return False

    try:
        headers = {
            "Content-Type": "application/json",
            "x-cloudo-key": security.CLOUDO_SECRET_KEY,
        }
        resp = requests.post(
            RECEIVER_URL,
            data=message_json,
            headers=headers,
            timeout=RECEIVER_HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            logging.debug(
                f"[{exec_id}] Status sent via HTTP: {payload.get('status', 'unknown')}"
            )
            return True
        else:
            logging.warning(
                f"[{exec_id}] HTTP receiver returned {resp.status_code}, "
                f"will retry via queue"
            )
            return False
    except requests.RequestException as e:
        logging.warning(
            f"[{exec_id}] HTTP receiver failed ({type(e).__name__}), "
            f"will retry via queue: {e}"
        )
        return False
    except Exception as e:
        logging.error(f"[{exec_id}] Unexpected error sending via HTTP: {e}")
        return False


def _send_status_via_queue(message_json: str, payload: dict) -> None:
    """Send status message via Azure Storage Queue (fallback)."""
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    global _NOTIFICATION_QUEUE_CLIENT
    exec_id = payload.get("exec_id", "unknown")
    try:
        if _NOTIFICATION_QUEUE_CLIENT is None:
            with _STORAGE_CLIENT_LOCK:
                if _NOTIFICATION_QUEUE_CLIENT is None:
                    _NOTIFICATION_QUEUE_CLIENT = QueueClient.from_connection_string(
                        conn_str=_STORAGE_CONN_STR,
                        queue_name=NOTIFICATION_QUEUE_NAME,
                        message_encode_policy=TextBase64EncodePolicy(),
                    )
        _NOTIFICATION_QUEUE_CLIENT.send_message(message_json)
        logging.debug(f"[{exec_id}] Status sent via queue")
    except Exception as e:
        logging.error(f"[{exec_id}] Failed to send status via queue: {e}")
        raise


def dispatch_status(
    payload: dict,
    status: str,
    log_message: str,
    queue_out: Optional[func.Out[str]] = None,
) -> None:
    message_json = post_status(payload, status=status, log_message=log_message)
    if _send_status_to_receiver(message_json, payload):
        return
    if queue_out is not None:
        queue_out.set(message_json)
    else:
        _send_status_via_queue(message_json, payload)
