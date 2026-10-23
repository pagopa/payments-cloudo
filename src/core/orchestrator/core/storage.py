"""Cached Azure Storage clients (tables, queues, blobs) and binding helpers."""

import json
import os
from typing import Any, Optional, Union

from core.config import STORAGE_CONN

_table_clients: dict[tuple[str, str], Any] = {}


_queue_clients: dict[tuple[str, str], Any] = {}


_blob_services: dict[str, Any] = {}


def require_connection_string(conn_env: str = STORAGE_CONN) -> str:
    conn_str = (os.environ.get(conn_env) or "").strip()
    if not conn_str:
        raise ValueError(f"Missing storage connection string in env '{conn_env}'")
    return conn_str


def get_table_client(table_name: str, conn_env: str = STORAGE_CONN):
    from azure.data.tables import TableClient

    conn_str = require_connection_string(conn_env)
    key = (conn_str, table_name)
    client = _table_clients.get(key)
    if client is None:
        client = TableClient.from_connection_string(conn_str, table_name=table_name)
        _table_clients[key] = client
    return client


def get_queue_client(queue_name: str, conn_env: str = STORAGE_CONN):
    from azure.storage.queue import (
        QueueClient,
        TextBase64DecodePolicy,
        TextBase64EncodePolicy,
    )

    conn_str = require_connection_string(conn_env)
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


def get_blob_service(conn_env: str = STORAGE_CONN):
    from azure.storage.blob import BlobServiceClient

    conn_str = require_connection_string(conn_env)
    service = _blob_services.get(conn_str)
    if service is None:
        service = BlobServiceClient.from_connection_string(conn_str)
        _blob_services[conn_str] = service
    return service


def enqueue_queue_payload(
    queue_name: str, payload: dict, conn_env: str = STORAGE_CONN
) -> None:
    q_client = get_queue_client(queue_name, conn_env=conn_env)
    q_client.send_message(json.dumps(payload, ensure_ascii=False))


def rows_from_binding(rows: Union[str, list[dict], None]) -> Optional[list[dict]]:
    try:
        parsed = json.loads(rows) if isinstance(rows, str) else rows
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None
