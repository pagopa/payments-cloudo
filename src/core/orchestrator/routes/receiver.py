"""Execution status receiver (notification queue trigger + HTTP fast path)."""

import base64
import json
import logging

import azure.functions as func
from core.config import (
    NOTIFICATION_QUEUE_NAME,
    STORAGE_CONN,
    STORAGE_CONNECTION,
    TABLE_NAME,
)
from services import status_update

bp = func.Blueprint()


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
        status_update.process(body, log_table)
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
        status_update.process(body, log_table)
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
