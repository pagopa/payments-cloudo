import base64
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
from urllib.parse import urlsplit, urlunsplit

import azure.functions as func
from models import Schema
from utils import create_cors_response

app = func.FunctionApp()

# =========================
# Constants and Utilities
# =========================

# Centralize configuration strings to avoid "magic strings"
TABLE_NAME = "RunbookLogs"
TABLE_SCHEMAS = "RunbookSchemas"
TABLE_WORKERS_SCHEMAS = "WorkersRegistry"
TABLE_USERS = "CloudoUsers"
TABLE_SETTINGS = "CloudoSettings"
TABLE_AUDIT = "CloudoAuditLogs"
TABLE_SCHEDULES = "CloudoSchedules"
STORAGE_CONN = "AzureWebJobsStorage"
NOTIFICATION_QUEUE_NAME = os.environ.get(
    "NOTIFICATION_QUEUE_NAME", "cloudo-notification"
)
STORAGE_CONNECTION = "AzureWebJobsStorage"
MAX_TABLE_CHARS = int(os.getenv("MAX_TABLE_LOG_CHARS", "32000"))
MAX_TABLE_ENTITY_BODY_BYTES = int(os.getenv("MAX_TABLE_ENTITY_BODY_BYTES", "62000"))
LOGS_BLOB_CONTAINER = os.getenv("LOGS_BLOB_CONTAINER", "runbook-logs")
LOGS_REF_PREFIX = "blobref://"
APPROVAL_TTL_MIN = int(os.getenv("APPROVAL_TTL_MIN", "60"))
APPROVAL_SECRET = (os.getenv("APPROVAL_SECRET") or "").strip()
SESSION_SECRET = (os.getenv("SESSION_SECRET") or "").strip()
if not SESSION_SECRET and os.getenv("LOCAL_DEV", "false").lower() != "true":
    logging.error("CRITICAL: SESSION_SECRET not configured. Authentication will fail.")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pagopa/payments-cloudo")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_PATH_PREFIX = os.environ.get("GITHUB_PATH_PREFIX", "")

if os.getenv("LOCAL_DEV", "false").lower() != "true":
    AUTH = func.AuthLevel.FUNCTION
else:
    AUTH = func.AuthLevel.ANONYMOUS


def _b64url_encode(data: bytes) -> str:
    # Base64 URL-safe without padding
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    # Decode Base64 URL-safe without padding
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign_payload_b64(payload_b64: str) -> str:
    # HMAC-SHA256 signature over base64url payload (no padding)
    import hashlib
    import hmac

    key = (APPROVAL_SECRET or "default").encode("utf-8")
    return hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_signed_payload(exec_id_path: str, p: str, s: str) -> tuple[bool, dict]:
    """
    Verify signature, parse payload (JSON), and basic invariants:
    - signature matches
    - exp not expired
    - execId in payload matches route
    Returns (ok, payload_dict_or_empty)
    """
    try:
        if not p or not s:
            return False, {}
        expected = _sign_payload_b64(p)
        import hmac

        if not hmac.compare_digest(expected, s):
            return False, {}
        payload_raw = _b64url_decode(p)
        payload = json.loads(payload_raw.decode("utf-8"))
        # Validate execId match
        if (payload.get("execId") or "").strip() != (exec_id_path or "").strip():
            return False, {}
        # Validate expiration
        exp_str = str(payload.get("exp") or "").replace(" ", "").replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(exp_str)
        if datetime.now(timezone.utc) > exp_dt.astimezone(timezone.utc):
            return False, {}
        return True, payload
    except Exception as e:
        logging.warning(f"verify signed payload failed: {e}")
        return False, {}


def _create_session_token(username: str, role: str, expires_at: str) -> str:
    import hashlib
    import hmac

    payload = json.dumps(
        {"username": username, "role": role, "expires_at": expires_at}
    ).encode("utf-8")
    payload_b64 = _b64url_encode(payload)
    key = SESSION_SECRET.encode("utf-8")
    sig = hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_session_token(token: str) -> tuple[bool, dict]:
    try:
        if not token or "." not in token:
            return False, {}
        p_b64, s = token.split(".", 1)
        import hashlib
        import hmac

        key = SESSION_SECRET.encode("utf-8")
        expected_s = hmac.new(key, p_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_s, s):
            return False, {}

        payload_raw = _b64url_decode(p_b64)
        payload = json.loads(payload_raw.decode("utf-8"))

        exp_str = payload.get("expires_at")
        if not exp_str:
            return False, {}

        exp_dt = datetime.fromisoformat(exp_str)
        if datetime.now(timezone.utc) > exp_dt.astimezone(timezone.utc):
            return False, {}

        return True, payload
    except Exception as e:
        logging.warning(f"Session verification failed: {e}")
        return False, {}


def _get_authenticated_user(
    req: func.HttpRequest,
) -> tuple[Optional[dict], Optional[func.HttpResponse]]:
    # 1. Check Bearer Token
    auth_header = req.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        ok, session = _verify_session_token(token)
        if ok:
            return session, None

    # 2. Check x-cloudo-key (Personal API Token or Global Secret)
    cloudo_key = req.headers.get("x-cloudo-key")
    if cloudo_key:
        # Check global secret
        expected_global_key = os.environ.get("CLOUDO_SECRET_KEY")
        if expected_global_key and cloudo_key == expected_global_key:
            # If authenticated via global key, we might have a forwarded user from UI proxy or a direct caller
            forwarded_user = req.headers.get("x-cloudo-user")
            return {
                "user": forwarded_user or "api",
                "username": forwarded_user or "api",
                "role": "OPERATOR",
            }, None

        # Check personal API tokens
        try:
            from azure.data.tables import TableClient

            conn_str = os.environ.get(STORAGE_CONN)
            table_client = TableClient.from_connection_string(
                conn_str, table_name=TABLE_USERS
            )

            # This is not efficient (O(N)), but Table Storage doesn't support secondary indexes easily.
            # For a small number of users it's fine.
            # Alternatively, we could use a separate table for token lookup.
            users = table_client.query_entities(
                query_filter=f"api_token eq '{cloudo_key}'"
            )
            for u in users:
                return {
                    "username": f"{u.get('RowKey')}-api",
                    "role": u.get("role", "OPERATOR"),
                    "email": u.get("email"),
                }, None
        except Exception as e:
            logging.error(f"Error verifying personal API token: {e}")

    # 3. Fallback to x-functions-key (Azure Actions or direct calls)
    action_key = req.params.get("x-cloudo-key")
    expected_action_key = os.environ.get("CLOUDO_SECRET_KEY")
    if action_key and expected_action_key and action_key == expected_action_key:
        return {
            "user": "azure-action",
            "username": "azure-action",
            "role": "OPERATOR",
        }, None

    return None, func.HttpResponse(
        json.dumps({"error": "Unauthorized: Missing or invalid credentials"}),
        status_code=401,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _rows_from_binding(rows: Union[str, list[dict]]) -> list[dict]:
    try:
        return json.loads(rows) if isinstance(rows, str) else (rows or [])
    except Exception:
        return []


def log_audit(user: str, action: str, target: str, details: str = ""):
    """Log an action to the Audit table."""
    try:
        from azure.data.tables import TableClient

        conn_str = os.environ.get(STORAGE_CONN)
        table_client = TableClient.from_connection_string(
            conn_str, table_name=TABLE_AUDIT
        )

        now = datetime.now(timezone.utc)
        entity = {
            "PartitionKey": now.strftime("%Y%m%d"),
            "RowKey": str(uuid.uuid4()),
            "timestamp": now.isoformat(),
            "operator": user,
            "action": action,
            "target": target,
            "details": details,
        }
        table_client.create_entity(entity=entity)
    except Exception as e:
        logging.error(f"Failed to log audit: {e}")


def _only_pending_for_exec(rows: list[dict], exec_id: str) -> bool:
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


def _notify_slack_decision(
    exec_id: str, schema_id: str, decision: str, approver: str, extra: str = ""
) -> None:
    from azure.data.tables import TableClient
    from escalation import send_slack_execution

    # Fetch settings from Table Storage
    conn_str = os.environ.get(STORAGE_CONN)
    table_client = TableClient.from_connection_string(
        conn_str, table_name=TABLE_SETTINGS
    )

    try:
        # Get SLACK_TOKEN_DEFAULT and SLACK_CHANNEL from GlobalConfig
        token_entity = table_client.get_entity(
            partition_key="GlobalConfig", row_key="SLACK_TOKEN_DEFAULT"
        )
        token = token_entity.get("value", "").strip()

        channel_entity = table_client.get_entity(
            partition_key="GlobalConfig", row_key="SLACK_CHANNEL"
        )
        channel = channel_entity.get("value", "").strip() or "#cloudo-test"
    except Exception as e:
        logging.warning(
            f"Failed to fetch Slack settings from Table Storage, falling back to ENV: {e}"
        )
        token = (os.environ.get("SLACK_TOKEN_DEFAULT") or "").strip()
        channel = (os.environ.get("SLACK_CHANNEL") or "").strip() or "#cloudo-test"

    if not token:
        return

    emoji = "✅" if decision == "approved" else "❌"

    # UI Base URL
    ui_base = (os.getenv("NEXTJS_URL") or "http://localhost:3000").strip().rstrip("/")
    if not ui_base.startswith("http"):
        ui_base = f"https://{ui_base}" if ui_base else "http://localhost:3000"
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
        logging.error(f"[{exec_id}] Slack notify failed: {e}")


def decode_base64(data: str) -> str:
    """Decode base64 encoded string to utf-8 string"""
    try:
        return base64.b64decode(data).decode("utf-8")
    except Exception as e:
        logging.warning(f"Failed to decode base64 encoded string: {e}")
        return data


def _encode_logs(text: str) -> bytes:
    """Encode log text in base64 UTF-8."""
    raw = (text or "").encode("utf-8", errors="replace")
    return base64.b64encode(raw)


def _make_log_blob_name(partition_key: str, exec_id: str, status: str) -> str:
    safe_exec_id = (exec_id or "unknown").strip() or "unknown"
    safe_status_raw = (status or "UNKNOWN").strip().upper() or "UNKNOWN"
    safe_status = "".join(ch if ch.isalnum() else "_" for ch in safe_status_raw).strip(
        "_"
    )
    safe_status = safe_status or "UNKNOWN"
    return f"{partition_key}/{safe_exec_id}/{safe_status}_{safe_exec_id}.log"


def _blob_ref(blob_name: str) -> str:
    return f"{LOGS_REF_PREFIX}{LOGS_BLOB_CONTAINER}/{blob_name}"


def _parse_blob_ref(log_value: Optional[str]) -> Optional[tuple[str, str]]:
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


def _upload_log_to_blob(
    partition_key: str, exec_id: str, status: str, logs_raw: str
) -> str:
    from azure.storage.blob import BlobServiceClient

    blob_name = _make_log_blob_name(partition_key, exec_id, status)
    conn_str = os.environ.get(STORAGE_CONN)
    service = BlobServiceClient.from_connection_string(conn_str)
    container = service.get_container_client(LOGS_BLOB_CONTAINER)
    try:
        container.create_container()
    except Exception:
        pass
    blob = container.get_blob_client(blob_name)
    blob.upload_blob((logs_raw or "").encode("utf-8", errors="replace"), overwrite=True)
    return _blob_ref(blob_name)


def _download_log_from_blob_ref(log_value: Optional[str]) -> Optional[str]:
    parsed = _parse_blob_ref(log_value)
    if not parsed:
        return None
    container, blob_name = parsed
    from azure.storage.blob import BlobServiceClient

    conn_str = os.environ.get(STORAGE_CONN)
    service = BlobServiceClient.from_connection_string(conn_str)
    blob = service.get_blob_client(container=container, blob=blob_name)
    content = blob.download_blob().readall()
    return content.decode("utf-8", errors="replace")


def _hydrate_log_field(entity: dict[str, Any]) -> dict[str, Any]:
    current_log = entity.get("Log")
    resolved_log = _download_log_from_blob_ref(current_log)
    if resolved_log is None:
        return entity
    hydrated = dict(entity)
    hydrated["Log"] = resolved_log
    hydrated["LogRef"] = current_log
    return hydrated


def _ensure_log_entity_size_for_table(entity: dict[str, Any]) -> dict[str, Any]:
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


def get_header(
    req: func.HttpRequest, name: str, default: Optional[str] = None
) -> Optional[str]:
    # Safely read a header value with a default fallback
    return req.headers.get(name, default)


def resolve_status(header_status: Optional[str]) -> str:
    # Map incoming header status to a canonical label for logs
    normalized = (header_status or "").strip().lower()
    return "succeeded" if normalized == "completed" else normalized


def resolve_caller_url(req: func.HttpRequest) -> str:
    raw = (
        get_header(req, "X-Caller-Url")
        or get_header(req, "Referer")
        or get_header(req, "Origin")
        or req.url
    )
    parts = urlsplit(raw)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _strip_after_api(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or ""
    idx = path.lower().find("/api")
    new_path = path[:idx] if idx != -1 else path
    if new_path in ("", "/"):
        new_path = ""
    return urlunsplit((parts.scheme, parts.netloc, new_path, "", ""))


def safe_json(response) -> Optional[Union[dict, str]]:
    # Safely parse response body, falling back to text or None
    try:
        return response.json()
    except Exception:
        try:
            return response.text
        except Exception:
            return None


def build_headers(
    schema: "Schema",
    exec_id: str,
    resource_info: Optional[dict],
    routing_info: Optional[dict],
    monitor_condition: Optional[str],
    severity: Optional[str],
) -> dict:
    # Standardize request headers sent to the downstream runbook endpoint
    headers = {
        "runbook": f"{schema.runbook}",
        "run_args": f"{schema.run_args}",
        "Id": schema.id,
        "Name": schema.name or "",
        "ExecId": exec_id,
        "OnCall": schema.oncall,
        "Content-Type": "application/json",
        "MonitorCondition": monitor_condition,
        "Severity": severity,
        "Worker": schema.worker,
        "Group": schema.group,
        "x-cloudo-key": os.environ.get("CLOUDO_SECRET_KEY", ""),
    }
    if resource_info is not None:
        headers["resource_info"] = json.dumps(resource_info, ensure_ascii=False)
    if routing_info is not None:
        headers["routing_info"] = json.dumps(routing_info, ensure_ascii=False)
    return headers


def _format_compact_resource_info(resource_info: Optional[dict]) -> Optional[str]:
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


def build_response_body(
    status_code: int,
    schema: "Schema",
    partition_key: str,
    exec_id: str,
    api_json: Optional[Union[dict, str]],
) -> str:
    # Build the HTTP response payload returned by this function
    return json.dumps(
        {
            "status": status_code,
            "schema": {
                "id": schema.id,
                "name": schema.name,
                "description": schema.description,
                "oncall": schema.oncall,
                "runbook": schema.runbook,
                "run_args": schema.run_args,
                "worker": schema.worker,
                "group": schema.group,
                "monitor_condition": schema.monitor_condition,
                "severity": schema.severity,
            },
            "response": api_json,
            "log": {"partitionKey": partition_key, "exec_id": exec_id},
        },
        ensure_ascii=False,
    )


def parse_header_json(req, name):
    raw = get_header(req, name)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


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
    oncall: Optional[str],
    monitor_condition: Optional[str],
    severity: Optional[str],
    initiator: Optional[str] = None,
    resource_info: Optional[dict[str, Any]] = None,
    approval_required: Optional[bool] = None,
    approval_expires_at: Optional[str] = None,
    approval_decision_by: Optional[str] = None,
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
        "ApprovalRequired": approval_required,
        "ApprovalExpiresAt": approval_expires_at,
        "ApprovalDecisionBy": approval_decision_by,
    }


def _post_status(payload: dict, status: str, log_message: str) -> str:
    """
    Build the status message (with base64-encoded, truncated logs) to send
    on the notification queue. Used by the orchestrator to talk to the Receiver.
    """
    from utils import format_requested_at

    exec_id = payload.get("exec_id")
    log_text = log_message or ""
    log_bytes = _encode_logs(log_text)

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
        "resource_info": payload.get("resource_info"),
        "routing_info": payload.get("routing_info"),
        "logs_b64": log_bytes.decode("utf-8"),
        "content_type": "text/plain; charset=utf-8",
        "sent_at": format_requested_at(),
    }
    return json.dumps(message, ensure_ascii=False)


# =========================
# HTTP Function: Trigger
# =========================


@app.route(
    route="Trigger/{team?}",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="entities",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def Trigger(
    req: func.HttpRequest,
    log_table: func.Out[str],
    entities: str,
    workers: str,
) -> func.HttpResponse:
    import detection
    import utils
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy
    from escalation import (
        format_opsgenie_description,
        send_opsgenie_alert,
        send_slack_execution,
    )
    from worker_routing import worker_routing

    try:
        q_client = QueueClient.from_connection_string(
            conn_str=os.environ.get(STORAGE_CONNECTION),
            queue_name=NOTIFICATION_QUEUE_NAME,
            message_encode_policy=TextBase64EncodePolicy(),
        )
    except Exception as e:
        logging.error(f"Failed to initialize queue client: {e}")
        return func.HttpResponse("Failed to initialize queue client", status_code=500)

    if req.method == "OPTIONS":
        return create_cors_response()

    try:
        from smart_routing import (
            execute_actions,
            resolve_opsgenie_apikey,
            resolve_slack_token,
            route_alert,
        )
    except ImportError:
        route_alert = None
        execute_actions = None

        def resolve_slack_token(_):
            return None

        def resolve_opsgenie_apikey(_):
            return None

    # Init payload variables to None
    resource_name = resource_group = resource_id = schema_id = monitor_condition = (
        severity
    ) = ""
    route_params = getattr(req, "route_params", {}) or {}
    logging.debug(route_params)
    # Pre-compute logging fields
    requested_at = utils.format_requested_at()
    partition_key = utils.today_partition_key()
    exec_id = str(uuid.uuid4())

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res
    requester_username = session.get("username")

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot trigger executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    from azure.data.tables import TableClient

    # Fetch settings from Table Storage
    conn_str = os.environ.get(STORAGE_CONN)
    table_client = TableClient.from_connection_string(
        conn_str, table_name=TABLE_SETTINGS
    )

    try:
        # Get SLACK_TOKEN_DEFAULT and SLACK_CHANNEL from GlobalConfig
        token_entity = table_client.get_entity(
            partition_key="GlobalConfig", row_key="SLACK_TOKEN_DEFAULT"
        )
        token = token_entity.get("value", "").strip()

        channel_entity = table_client.get_entity(
            partition_key="GlobalConfig", row_key="SLACK_CHANNEL"
        )
        channel = channel_entity.get("value", "").strip() or "#cloudo-test"
    except Exception as e:
        logging.warning(
            f"Failed to fetch Slack settings from Table Storage, falling back to ENV: {e}"
        )
        token = (os.environ.get("SLACK_TOKEN_DEFAULT") or "").strip()
        channel = (os.environ.get("SLACK_CHANNEL") or "").strip() or "#cloudo-test"

    # Resolve schema_id from route first; fallback to query/body (alertId/schemaId)
    if (req.params.get("id")) is not None:
        schema_id = detection.extract_schema_id_from_req(req)
        resource_info = {}
        routing_info = {
            "team": route_params.get("team") or "",
            "slack_token": req.params.get("slack_token")
            or resolve_slack_token(route_params.get("team") or "")
            or token,
            "slack_channel": req.params.get("slack_channel")
            or channel
            or (os.environ.get("SLACK_CHANNEL") or "#cloudo-test").strip(),
            "opsgenie_token": req.params.get("opsgenie_api_key")
            or resolve_opsgenie_apikey(route_params.get("team") or ""),
        }
    else:
        (
            _raw,
            resource_name,
            resource_group,
            resource_id,
            schema_id,
            namespace,
            pod,
            deployment,
            horizontalpodautoscaler,
            job,
            monitor_condition,
            severity,
        ) = detection.parse_resource_fields(req).values()
        resource_info = (
            {
                "_raw": _raw,
                "resource_name": resource_name,
                "resource_rg": resource_group,
                "resource_id": resource_id,
                "aks_namespace": namespace,
                "aks_pod": pod,
                "aks_deployment": deployment,
                "aks_job": job,
                "aks_horizontalpodautoscaler": horizontalpodautoscaler,
                "team": route_params.get("team"),
            }
            if resource_name
            else {}
        )
        routing_info = {
            "team": route_params.get("team") or "",
            "slack_token": req.params.get("slack_token")
            or resolve_slack_token(route_params.get("team") or "")
            or token,
            "slack_channel": req.params.get("slack_channel")
            or channel
            or (os.environ.get("SLACK_CHANNEL") or "#cloudo-test").strip(),
            "opsgenie_token": req.params.get("opsgenie_api_key")
            or resolve_opsgenie_apikey(route_params.get("team") or ""),
        }
        logging.debug(f"[{exec_id}] Resource info: %s", resource_info)

    # Parse bound table entities (binding returns a JSON array)
    try:
        parsed = json.loads(entities) if isinstance(entities, str) else entities
    except Exception:
        parsed = None

    if not isinstance(parsed, list):
        return func.HttpResponse(
            json.dumps({"error": "Unexpected table result format"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    # Apply optional filter in code (case-insensitive fallback on 'Id'/'id')
    def get_id(e: dict) -> str:
        return str(e.get("Id") or e.get("id") or "").strip()

    schema_entity = next((e for e in parsed if get_id(e) in schema_id), None)

    if not schema_entity:
        if monitor_condition and severity:
            log_msg = "ALARM -> ROUTED (No runbook found)"
            payload_for_status = {
                "requestedAt": requested_at,
                "id": "NaN",
                "name": resource_name or "",
                "exec_id": exec_id,
                "runbook": "alarm routed",
                "run_args": "NaN",
                "worker": "NaN",
                "group": "-",
                "oncall": "NaN",
                "monitor_condition": monitor_condition or "",
                "severity": severity or "",
                "resource_info": resource_info if "resource_info" in locals() else {},
                "routing_info": routing_info if "routing_info" in locals() else {},
            }
            q_client.send_message(
                _post_status(payload_for_status, status="routed", log_message=log_msg)
            )
            return func.HttpResponse(
                json.dumps(
                    {
                        "routed": (
                            "Alarm detected.\n "
                            "(This alert has not a runbook to be executed) -> ROUTED"
                        )
                    },
                    ensure_ascii=False,
                ),
                status_code=200,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )
        else:
            return func.HttpResponse(
                json.dumps(
                    {
                        "ignored": f"No alert detected for {schema_id}",
                    },
                    ensure_ascii=False,
                ),
                status_code=204,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

    logging.info(f"[{exec_id}] Getting schema entity id '{schema_entity}'")
    # Build domain model
    schema = Schema(
        id=schema_entity.get("id"),
        entity=schema_entity,
        monitor_condition=monitor_condition,
        severity=severity,
    )

    if not schema.enabled:
        log_msg = f"RUNBOOK DISABLED -> ROUTED ({schema.id})"
        payload_for_status = {
            "requestedAt": requested_at,
            "id": schema.id,
            "name": schema.name or resource_name or "",
            "exec_id": exec_id,
            "runbook": schema.runbook or "alarm routed",
            "run_args": schema.run_args,
            "worker": schema.worker,
            "group": schema.group,
            "oncall": schema.oncall,
            "monitor_condition": monitor_condition or "",
            "severity": severity or "",
            "resource_info": resource_info if "resource_info" in locals() else {},
            "routing_info": routing_info if "routing_info" in locals() else {},
        }
        q_client.send_message(
            _post_status(payload_for_status, status="routed", log_message=log_msg)
        )
        return func.HttpResponse(
            json.dumps(
                {
                    "routed": (
                        f"Runbook {schema.id} is disabled.\n "
                        "(This execution has been ROUTED)"
                    )
                },
                ensure_ascii=False,
            ),
            status_code=200,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    try:
        # Approval-required path: create pending with signed URL embedding resource_info and function key
        if schema.require_approval:
            expires_at = (
                (datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_TTL_MIN))
                .isoformat()
                .replace(" ", "")
            )
            # function key to pass along (from header or query)
            func_key = (
                req.headers.get("x-functions-key") or req.params.get("code") or ""
            )
            # Build payload
            payload = {
                "execId": exec_id,
                "schemaId": schema.id,
                "exp": expires_at,
                "resource_info": resource_info or {},
                "routing_info": routing_info or {},
                "code": func_key or "",
                "monitorCondition": monitor_condition,
                "severity": severity,
                "worker": schema.worker,
                "group": schema.group,
            }
            payload_b64 = _b64url_encode(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            sig = _sign_payload_b64(payload_b64)

            base_env = os.getenv("ORCHESTRATOR_BASE_URL")
            if not base_env:
                hostname = os.getenv("WEBSITE_HOSTNAME", "localhost:7071")
                scheme = "https" if "localhost" not in hostname else "http"
                base = f"{scheme}://{hostname}"
            else:
                base = base_env.rstrip("/")
            approve_url = f"{base}/api/approvals/{partition_key}/{exec_id}/approve?p={payload_b64}&s={sig}&code={func_key}"
            reject_url = f"{base}/api/approvals/{partition_key}/{exec_id}/reject?p={payload_b64}&s={sig}&code={func_key}"

            pending_log = build_log_entry(
                status="pending",
                partition_key=partition_key,
                exec_id=exec_id,
                row_key=exec_id,
                requested_at=requested_at,
                name=schema.name or "",
                schema_id=schema.id,
                runbook=schema.runbook,
                run_args=schema.run_args,
                worker=schema.worker,
                group=schema.group,
                log_msg=json.dumps(
                    {
                        "message": "Awaiting approval",
                        "approve": approve_url,
                        "reject": reject_url,
                        "resource_info": resource_info,
                    },
                    ensure_ascii=False,
                ),
                oncall=schema.oncall,
                initiator=requester_username,
                resource_info=resource_info,
                monitor_condition=monitor_condition,
                severity=severity,
                approval_required=True,
                approval_expires_at=expires_at,
            )
            log_table.set(json.dumps(pending_log, ensure_ascii=False))

            if requester_username:
                log_audit(
                    user=requester_username,
                    action="RUNBOOK_GATE_SCHEDULE",
                    target=exec_id,
                    details=f"ID: {schema.id}, Runbook: {schema.runbook}, Args: {schema.run_args}",
                )

            # Optional Slack notify
            slack_token = routing_info.get("slack_token")
            slack_channel = routing_info.get("slack_channel")
            if slack_token:
                try:
                    # UI Base URL
                    ui_base = (
                        (os.getenv("NEXTJS_URL") or "http://localhost:3000")
                        .strip()
                        .rstrip("/")
                    )
                    if not ui_base.startswith("http"):
                        ui_base = (
                            f"https://{ui_base}" if ui_base else "http://localhost:3000"
                        )
                    ui_url = f"{ui_base}/executions?execId={exec_id}&partitionKey={partition_key}"

                    # Truncate description and compact resource info to avoid Slack limits
                    description_truncated = (
                        schema.description or "No description provided."
                    ).strip()
                    if len(description_truncated) > 400:
                        description_truncated = description_truncated[:400] + "..."

                    resource_info_compact = (
                        _format_compact_resource_info(resource_info) or ""
                    )
                    if len(resource_info_compact) > 600:
                        resource_info_compact = resource_info_compact[:600] + "..."

                    # Truncate arguments for better Slack display
                    args_truncated = (schema.run_args or "").strip()
                    if len(args_truncated) > 800:
                        args_truncated = args_truncated[:800] + "\n... (truncated)"

                    send_slack_execution(
                        token=slack_token,
                        channel=slack_channel,
                        message=f"[{exec_id}] ⚠️ APPROVAL REQUIRED: {schema.name}",
                        blocks=[
                            {
                                "type": "header",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Gate Approval Required ⚠️",
                                    "emoji": True,
                                },
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"<!here> *{schema.name}* is requesting permission to execute a restricted runbook.\n"
                                        f"> *Description:* {description_truncated}"
                                    ),
                                },
                            },
                            {
                                "type": "section",
                                "fields": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*SchemaId:* `{schema.id}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Severity:* `{severity or '-'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Runbook:* `{schema.runbook or '-'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Worker:* `{schema.worker or 'unknown'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Group:* `{schema.group}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Initiator:* `{requester_username or 'SYSTEM'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*On Call:* `{schema.oncall}`",
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
                                "type": "actions",
                                "elements": [
                                    {
                                        "type": "button",
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Full Context 🔍",
                                            "emoji": True,
                                        },
                                        "url": ui_url,
                                    },
                                ],
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"ExecId: `{exec_id}` | Requested: <!date^{int(datetime.now(timezone.utc).timestamp())}^{{date_short}} {{time}}|now>",
                                    }
                                ],
                            },
                        ],
                    )
                except Exception as e:
                    logging.error(f"[{exec_id}] Slack approval notify failed: {e}")

            body = json.dumps(
                {
                    "status": 202,
                    "message": "Job is pending approval",
                    "exec_id": exec_id,
                    "approve": approve_url,
                    "reject": reject_url,
                    "expires_at (UTC)": expires_at,
                },
                ensure_ascii=False,
            )
            return func.HttpResponse(
                body,
                status_code=202,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

        # ---------------------------------------------------------
        # DYNAMIC WORKER SELECTION (Binding Version)
        # ---------------------------------------------------------
        target_queue = worker_routing(workers, schema)

        api_body = {}
        status_code = 202

        if target_queue:
            logging.info(
                f"[{exec_id}] 🎯 Dynamic Routing: Selected Queue '{target_queue}'"
            )

            try:
                from azure.storage.queue import QueueClient, TextBase64EncodePolicy

                # Construct the payload (formerly HTTP headers)
                queue_payload = {
                    "runbook": schema.runbook,
                    "run_args": schema.run_args,
                    "id": schema.id,
                    "name": schema.name or "",
                    "requestedAt": requested_at,
                    "exec_id": exec_id,
                    "oncall": schema.oncall,
                    "monitor_condition": monitor_condition,
                    "severity": severity,
                    "worker": schema.worker,
                    "group": schema.group,
                    "resource_info": resource_info or {},
                    "routing_info": routing_info or {},
                }

                # Send it to the specific dynamic queue
                # We use TextBase64EncodePolicy because Azure Function Triggers usually expect Base64 encoded strings
                q_client = QueueClient.from_connection_string(
                    conn_str=os.environ.get(STORAGE_CONN),
                    queue_name=target_queue,
                    message_encode_policy=TextBase64EncodePolicy(),
                )
                q_client.send_message(json.dumps(queue_payload, ensure_ascii=False))

                api_body = {"status": "accepted", "queue": target_queue}

                if resource_info == {}:
                    log_audit(
                        user=requester_username,
                        action="RUNBOOK_EXECUTE",
                        target=exec_id,
                        details=f"ID: {schema.id}, Runbook: {schema.runbook}, Args: {schema.run_args}",
                    )
                else:
                    log_audit(
                        user=requester_username,
                        action="RUNBOOK_EXECUTE",
                        target=exec_id,
                        details=f"ID: {schema.id}, Runbook: {schema.runbook}, Args: {schema.run_args}",
                    )

            except Exception as e:
                logging.error(f"[{exec_id}] ❌ Queue send failed: {e}")
                status_code = 500
                api_body = {"error": str(e)}
        else:
            err_msg = f"❌ No workers ({schema.worker}) available and no static queue configured for {schema.id}"
            logging.error(f"[{exec_id}] {err_msg}")
            status_code = 500
            api_body = {"error": err_msg}
        # ---------------------------------------------------------

        # Status label for logs
        status_label = "accepted" if status_code == 202 else "error"

        # Write log entry to the table
        start_log = build_log_entry(
            status=status_label,
            partition_key=partition_key,
            exec_id=exec_id,
            row_key=exec_id,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=api_body,
            oncall=schema.oncall,
            monitor_condition=monitor_condition,
            severity=severity,
            resource_info=resource_info,
        )
        log_table.set(json.dumps(start_log, ensure_ascii=False))

        # smart routing notification (if routing module available)
        if status_label != "accepted":
            if route_alert and execute_actions:
                ctx = {
                    "resourceId": resource_id,
                    "resourceGroup": resource_group,
                    "resourceName": resource_name,
                    "schemaName": (schema.name or ""),
                    "severity": severity,
                    "namespace": ((resource_info or {}).get("namespace") or ""),
                    "oncall": schema.oncall,
                    "status": status_label,
                    "execId": exec_id,
                    "name": schema.name or "",
                    "id": schema.id,
                    "routing_info": routing_info,
                }
                decision = route_alert(ctx)
                logging.debug(f"[{exec_id}] {decision}")
                status_emoji = "✅" if status_label == "succeeded" else "❌"
                payload = {
                    "slack": {
                        "message": f"[{exec_id}] Status: {status_label}: {schema.name or ''}",
                        "blocks": [
                            {
                                "type": "header",
                                "text": {
                                    "type": "plain_text",
                                    "text": f"{status_emoji}\t{schema.name or ''}\texecution",
                                    "emoji": True,
                                },
                            },
                            {
                                "type": "section",
                                "fields": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Name:*\n{schema.name or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Id:*\n{schema.id or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Group:*\n{schema.group}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*ExecId:*\n{exec_id or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Status:*\n{status_label}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Severity:*\n{severity or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*OnCall:*\n{schema.oncall or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Origin*:\n{schema.worker or 'unknown(?)'}",
                                    },
                                ],
                            },
                            {
                                "type": "section",
                                "fields": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Runbook:*\n{schema.runbook or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*MonitorCondition:*\n{schema.monitor_condition or ''}",
                                    },
                                ],
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Run Args:*\n```{schema.run_args or ''}```",
                                },
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Logs (truncated):*\n```{(json.dumps(api_body, ensure_ascii=False) if isinstance(api_body, (dict, list)) else str(api_body))[:1500]}```",
                                },
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Severity:* {severity}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Teams:* {', '.join(dict.fromkeys(a.team for a in decision.actions if getattr(a, 'team', None)))}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"Timestamp: <!date^{int(__import__('time').time())}^{{date_short}} {{time}}|now>",
                                    },
                                ],
                            },
                            {"type": "divider"},
                        ],
                    },
                    "opsgenie": {
                        "message": f"[{schema.id}] [{severity}] {schema.name}",
                        "priority": f"P{int(str(severity).strip().lower().replace('sev', '') or '4') + 1}",
                        "alias": schema.id,
                        "monitor_condition": monitor_condition or "",
                        "details": {
                            "Name": schema.name,
                            "Id": schema.id,
                            "ExecId": exec_id,
                            "Status": status_label,
                            "Runbook": schema.runbook,
                            "Run_Args": schema.run_args,
                            "Group": schema.group,
                            "OnCall": schema.oncall,
                            "MonitorCondition": monitor_condition,
                            "Severity": severity,
                            "Teams:": ", ".join(
                                dict.fromkeys(
                                    a.team
                                    for a in decision.actions
                                    if getattr(a, "team", None)
                                )
                            ),
                        },
                        "description": f"{format_opsgenie_description(exec_id, resource_info, api_body)}",
                    },
                }
                try:
                    execute_actions(
                        decision,
                        payload,
                        send_slack_fn=lambda token, channel, **kw: send_slack_execution(
                            token=token, channel=channel, **kw
                        ),
                        send_opsgenie_fn=lambda api_key, **kw: send_opsgenie_alert(
                            api_key=api_key, **kw
                        ),
                    )
                except Exception as e:
                    logging.error(f"[{exec_id}] smart routing failed: {e}")

        # Return HTTP response mirroring downstream status
        response_body = build_response_body(
            status_code=status_code,
            schema=schema,
            partition_key=partition_key,
            exec_id=exec_id,
            api_json=api_body,
        )
        return func.HttpResponse(
            response_body,
            status_code=status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        # Build error response
        response_body = build_response_body(
            status_code=500,
            schema=schema,
            partition_key=partition_key,
            exec_id=exec_id,
            api_json={"error": str(e)},
        )

        # Log error to table
        error_log = build_log_entry(
            status="error",
            partition_key=partition_key,
            exec_id=exec_id,
            row_key=exec_id,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=str(e),
            oncall=schema.oncall,
            monitor_condition=monitor_condition,
            severity=severity,
        )
        log_table.set(json.dumps(error_log, ensure_ascii=False))

        return func.HttpResponse(
            response_body,
            status_code=500,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


# =========================
# HTTP Function: Approval
# =========================
@app.route(
    route="approvals/{partitionKey}/{execId}/approve",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="schemas",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="today_logs",
    table_name=TABLE_NAME,
    partition_key="{partitionKey}",
    connection=STORAGE_CONN,
)
def approve(
    req: func.HttpRequest,
    log_table: func.Out[str],
    schemas: str,
    today_logs: str,
    workers: str,
) -> func.HttpResponse:
    import utils
    from worker_routing import worker_routing

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot approve executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    route_params = getattr(req, "route_params", {}) or {}
    execId = (route_params.get("execId") or "").strip()

    p = (req.params.get("p") or "").strip()
    s = (req.params.get("s") or "").strip()

    # Security: check session token if not in LOCAL_DEV mode
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        session, error_res = _get_authenticated_user(req)
        if error_res:
            # Fallback for Slack/Email links
            approver = (
                req.headers.get("x-cloudo-user")
                or req.headers.get("X-Approver")
                or "anonymous-link"
            )
        else:
            approver = session.get("username")
    else:
        # Trust headers in LOCAL_DEV
        approver = (
            req.headers.get("x-cloudo-user")
            or req.headers.get("X-Approver")
            or "unknown"
        )
        # Try to get session if available anyway
        session, _ = _get_authenticated_user(req)
        if session:
            approver = session.get("username")

    if not execId:
        return func.HttpResponse(
            json.dumps({"error": "Missing execId in route"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    ok, payload = _verify_signed_payload(execId, p, s)
    if not ok:
        return func.HttpResponse(
            json.dumps({"error": "Invalid or expired payload"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    rows = _rows_from_binding(today_logs)
    if not _only_pending_for_exec(rows, execId):
        return func.HttpResponse(
            json.dumps(
                {"message": "Already decided or executed for this ExecId"},
                ensure_ascii=False,
            ),
            status_code=409,
            mimetype="application/json",
        )

    schema_id = payload.get("schemaId") or ""
    resource_info = payload.get("resource_info") or None
    routing_info = payload.get("routing_info") or None
    monitor_condition = payload.get("monitorCondition") or ""
    severity = payload.get("severity") or ""

    # Load schema entity
    try:
        parsed = json.loads(schemas) if isinstance(schemas, str) else schemas
    except Exception:
        parsed = None
    if not isinstance(parsed, list):
        return func.HttpResponse(
            json.dumps({"error": "Schemas not available"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )

    def get_id(e: dict) -> str:
        return str(e.get("Id") or e.get("id") or "").strip()

    schema_entity = next((e for e in parsed if get_id(e) == schema_id), None)
    if not schema_entity:
        return func.HttpResponse(
            json.dumps({"error": "Schema not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    schema = Schema(id=schema_entity.get("id"), entity=schema_entity)

    partition_key = utils.today_partition_key()
    requested_at = utils.format_requested_at()

    # Execute once (pass embedded resource_info and propagate the function key if needed)
    try:
        # ---------------------------------------------------------
        # DYNAMIC WORKER SELECTION (Binding Version)
        # ---------------------------------------------------------
        target_queue = worker_routing(workers, schema)

        api_body = {}
        status_code = 202

        if target_queue:
            logging.info(
                f"[{execId}] 🎯 Dynamic Routing: Selected Queue '{target_queue}'"
            )

            try:
                from azure.storage.queue import QueueClient, TextBase64EncodePolicy

                # Construct the payload (formerly HTTP headers)
                queue_payload = {
                    "runbook": schema.runbook,
                    "run_args": schema.run_args,
                    "id": schema.id,
                    "name": schema.name or "",
                    "requestedAt": requested_at,
                    "exec_id": execId,
                    "oncall": schema.oncall,
                    "monitor_condition": monitor_condition,
                    "severity": severity,
                    "worker": schema.worker,
                    "group": schema.group,
                    "resource_info": resource_info or {},
                    "routing_info": routing_info or {},
                }

                # Send it to the specific dynamic queue
                # We use TextBase64EncodePolicy because Azure Function Triggers usually expect Base64 encoded strings
                q_client = QueueClient.from_connection_string(
                    conn_str=os.environ.get(STORAGE_CONN),
                    queue_name=target_queue,
                    message_encode_policy=TextBase64EncodePolicy(),
                )
                q_client.send_message(json.dumps(queue_payload, ensure_ascii=False))

                api_body = {
                    "status": "accepted",
                    "queue": target_queue,
                    "payload": queue_payload,
                }

            except Exception as e:
                logging.error(f"[{execId}] ❌ Queue send failed: {e}")
                status_code = 500
                api_body = {"error": str(e)}
        else:
            err_msg = f"❌ No workers ({schema.worker}) available and no static queue configured for {schema.id}"
            logging.error(f"[{execId}] {err_msg}")
            status_code = 500
            api_body = {"error": err_msg}
        # ---------------------------------------------------------

        # Status label for logs
        status_label = "accepted" if status_code == 202 else "error"

        log_entity = build_log_entry(
            status=status_label,
            partition_key=partition_key,
            row_key=str(uuid.uuid4()),
            exec_id=execId,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=json.dumps(
                {
                    "message": f"Approved and executed by {approver}",
                    "response": api_body,
                    "resource_info": resource_info,
                },
                ensure_ascii=False,
            ),
            oncall=schema.oncall,
            initiator=payload.get("initiator"),
            resource_info=resource_info,
            monitor_condition=monitor_condition,
            severity=severity,
            approval_required=True,
            approval_decision_by=approver,
        )
        log_table.set(json.dumps(log_entity, ensure_ascii=False))

        log_audit(
            user=approver,
            action="RUNBOOK_APPROVE",
            target=execId,
            details=f"Runbook: {schema.runbook}, Schema: {schema.id}, Approver: {approver}",
        )

        # smart routing notification (if routing module available)
        if route_alert and execute_actions:
            ctx = {
                "resourceId": ((resource_info or {}).get("resource_id") or ""),
                "resourceGroup": ((resource_info or {}).get("resource_group") or ""),
                "resourceName": ((resource_info or {}).get("resource_name") or ""),
                "schemaName": (schema.name or ""),
                "severity": severity,
                "namespace": ((resource_info or {}).get("namespace") or ""),
                "oncall": schema.oncall,
                "status": status_label,
                "execId": execId,
                "name": schema.name or "",
                "id": schema.id,
                "routing_info": routing_info,
            }
            decision = route_alert(ctx)
            logging.debug(f"[{execId}] Approval: {decision}")

            # Notify Slack directly (bypassing smart routing for Slack as requested)
            _notify_slack_decision(
                exec_id=execId,
                schema_id=schema_id,
                decision="approved",
                approver=approver,
            )

            # We still execute other actions via smart routing if any (excluding Slack)
            payload = {
                "slack": None  # Skip slack in execute_actions
            }
            try:
                execute_actions(decision, payload, send_slack_fn=None)
            except Exception as e:
                logging.error(f"[{execId}] smart routing approval actions failed: {e}")

        return func.HttpResponse(
            json.dumps(
                {
                    "message": f"Approved and executed by {approver}",
                    "response": api_body,
                    "resource_info": resource_info,
                }
            ),
            status_code=200,
            mimetype="application/json",
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except Exception as e:
        err_log = build_log_entry(
            status="error",
            partition_key=partition_key,
            row_key=str(uuid.uuid4()),
            exec_id=execId,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=f"Approve failed: {str(e)}",
            oncall=schema.oncall,
            monitor_condition=None,
            severity=None,
            approval_required=True,
            approval_decision_by=approver,
        )
        log_table.set(json.dumps(err_log, ensure_ascii=False))
        _notify_slack_decision(
            execId,
            schema_id,
            f"approved {execId} by {approver}",
            approver,
            extra=f"*Error:* {str(e)}",
        )
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )


# =========================
# HTTP Function: Rejecter
# =========================
@app.route(
    route="approvals/{partitionKey}/{execId}/reject",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="schemas",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="today_logs",
    table_name=TABLE_NAME,
    partition_key="{partitionKey}",
    connection=STORAGE_CONN,
)
def reject(
    req: func.HttpRequest, log_table: func.Out[str], schemas: str, today_logs: str
) -> func.HttpResponse:
    import utils

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot reject executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    route_params = getattr(req, "route_params", {}) or {}
    execId = (route_params.get("execId") or "").strip()

    rows = _rows_from_binding(today_logs)
    if not _only_pending_for_exec(rows, execId):
        return func.HttpResponse(
            json.dumps(
                {"message": "Already decided or executed for this ExecId"},
                ensure_ascii=False,
            ),
            status_code=409,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    p = (req.params.get("p") or "").strip()
    s = (req.params.get("s") or "").strip()

    # Security: check session token if not in LOCAL_DEV mode
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        session, error_res = _get_authenticated_user(req)
        if error_res:
            # Fallback for Slack/Email links
            approver = (
                req.headers.get("x-cloudo-user")
                or req.headers.get("X-Approver")
                or "anonymous-link"
            )
        else:
            approver = session.get("username")
    else:
        # Trust headers in LOCAL_DEV
        approver = (
            req.headers.get("x-cloudo-user")
            or req.headers.get("X-Approver")
            or "unknown"
        )
        # Try to get session if available anyway
        session, _ = _get_authenticated_user(req)
        if session:
            approver = session.get("username")

    if not execId:
        return func.HttpResponse(
            json.dumps({"error": "Missing execId in route"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    ok, payload = _verify_signed_payload(execId, p, s)
    if not ok:
        return func.HttpResponse(
            json.dumps({"error": "Invalid or expired payload"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    schema_id = payload.get("schemaId") or ""
    resource_info = payload.get("resource_info") or None
    routing_info = payload.get("routing_info") or None
    monitor_condition = payload.get("monitorCondition") or ""
    severity = payload.get("severity") or ""

    # Load schema entity
    try:
        parsed = json.loads(schemas) if isinstance(schemas, str) else schemas
    except Exception:
        parsed = None
    if not isinstance(parsed, list):
        return func.HttpResponse(
            json.dumps({"error": "Schemas not available"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )

    def get_id(e: dict) -> str:
        return str(e.get("Id") or e.get("id") or "").strip()

    schema_entity = next((e for e in parsed if get_id(e) == schema_id), None)
    if not schema_entity:
        return func.HttpResponse(
            json.dumps({"error": "Schema not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    schema = Schema(id=schema_entity.get("id"), entity=schema_entity)

    partition_key = utils.today_partition_key()
    requested_at = utils.format_requested_at()

    log_entity = build_log_entry(
        status="rejected",
        partition_key=partition_key,
        row_key=str(uuid.uuid4()),
        exec_id=execId,
        requested_at=requested_at,
        name=schema.name or "",
        schema_id=schema.id,
        runbook=schema.runbook,
        run_args=schema.run_args,
        worker=schema.worker,
        group=schema.group,
        log_msg=json.dumps(
            {"message": f"Rejected by approver: {approver}"}, ensure_ascii=False
        ),
        oncall=schema.oncall,
        initiator=payload.get("initiator"),
        resource_info=resource_info,
        monitor_condition=monitor_condition,
        severity=severity,
        approval_required=True,
        approval_decision_by=approver,
    )
    log_table.set(json.dumps(log_entity, ensure_ascii=False))

    log_audit(
        user=approver,
        action="RUNBOOK_REJECT",
        target=execId,
        details=f"Runbook: {schema.runbook}, Schema: {schema.id}",
    )

    # Notify Slack directly
    _notify_slack_decision(
        exec_id=execId, schema_id=schema_id, decision="rejected", approver=approver
    )

    # smart routing notification (if routing module available)
    if route_alert and execute_actions:
        ctx = {
            "resourceId": ((resource_info or {}).get("resource_id") or ""),
            "resourceGroup": ((resource_info or {}).get("resource_group") or ""),
            "resourceName": ((resource_info or {}).get("resource_name") or ""),
            "schemaName": (schema.name or ""),
            "severity": severity,
            "namespace": ((resource_info or {}).get("namespace") or ""),
            "oncall": schema.oncall,
            "status": "rejected",
            "execId": execId,
            "name": schema.name or "",
            "id": schema.id,
            "routing_info": routing_info,
        }
        decision = route_alert(ctx)
        logging.debug(f"[{execId}] Reject: {decision}")

        payload = {
            "slack": None  # Skip slack in execute_actions
        }
        try:
            execute_actions(decision, payload, send_slack_fn=None)
        except Exception as e:
            logging.error(f"[{execId}] smart routing rejection failed: {e}")

    return func.HttpResponse(
        json.dumps({"message": f"Rejected by approver: {approver}"}),
        status_code=200,
        mimetype="application/json",
        headers={
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    )


# =========================
# HTTP Function: receiver
# =========================


@app.queue_trigger(
    arg_name="msg", queue_name=NOTIFICATION_QUEUE_NAME, connection=STORAGE_CONNECTION
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
def Receiver(msg: func.QueueMessage, log_table: func.Out[str]) -> None:
    import utils
    from escalation import (
        format_opsgenie_description,
        send_opsgenie_alert,
        send_slack_execution,
    )

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    try:
        body = json.loads(msg.get_body().decode("utf-8"))
        logging.warning(f"[Receiver] Message received: {body}")
    except Exception as e:
        logging.error(f"[Receiver] Invalid queue message: {e}")
        return

    required_fields = ["exec_id", "status", "name", "id", "runbook"]
    missing = [k for k in required_fields if not (body.get(k) or "").strip()]
    if missing:
        logging.warning(f"[{body.get('exec_id')}] Missing required fields: {missing}")
        return

    logging.warning(
        f"[{body.get('exec_id')}][{body.get('status')}] Receiver invoked",
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
            }
        },
    )

    requested_at = utils.format_requested_at()
    partition_key = utils.today_partition_key()
    row_key = str(uuid.uuid4())
    status_label = resolve_status(body.get("status"))

    provided_log_ref = body.get("log_ref") or ""
    logs_raw = ""
    try:
        logs_raw = decode_base64(body.get("logs_b64") or "")
    except Exception:
        logs_raw = ""
    log_value: Optional[str] = None
    if isinstance(provided_log_ref, str) and _parse_blob_ref(provided_log_ref):
        log_value = provided_log_ref
    elif logs_raw:
        try:
            log_value = _upload_log_to_blob(
                partition_key=partition_key,
                exec_id=body.get("exec_id") or "",
                status=status_label,
                logs_raw=logs_raw,
            )
        except Exception as e:
            logging.warning(
                f"[{body.get('exec_id')}] blob upload failed, falling back to table log: {type(e).__name__}: {e}"
            )
            log_value = utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS)
    else:
        log_value = ""

    log_entity = build_log_entry(
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
        resource_info=body.get("resource_info"),
        monitor_condition=body.get("monitor_condition"),
        severity=body.get("severity"),
    )
    log_entity = _ensure_log_entity_size_for_table(log_entity)
    log_table.set(json.dumps(log_entity, ensure_ascii=False))

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

    resource_id = (body.get("resource_id") or "") or (
        resource_info.get("resource_id") or ""
    )
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
            "routing_info": routing_info,  # sempre dict qui
        }
        decision = route_alert(ctx)
        logging.debug(f"[{exec_id}] {decision}")
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
        resource_info_compact = _format_compact_resource_info(resource_info) or ""
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
            "opsgenie": {
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
                    "OnCall": body.get("oncall"),
                    "MonitorCondition": body.get("monitor_condition"),
                    "Severity": body.get("severity"),
                    "Teams:": ", ".join(
                        dict.fromkeys(
                            a.team for a in decision.actions if getattr(a, "team", None)
                        )
                    ),
                },
                "description": f"{format_opsgenie_description(exec_id, resource_info, utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS or ''))}",
            },
        }
        try:
            execute_actions(
                decision,
                payload,
                send_slack_fn=lambda token, channel, **kw: send_slack_execution(
                    token=token, channel=channel, **kw
                ),
                send_opsgenie_fn=lambda api_key, **kw: send_opsgenie_alert(
                    api_key=api_key, **kw
                ),
            )
        except Exception as e:
            logging.error(f"[{exec_id}] smart routing failed: {e}")
    else:
        logging.warning("Routing module not available, keeping legacy notifications")


# =========================
# Development Test Run Endpoint
# =========================


@app.route(route="dev/testrun", auth_level=AUTH)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def dev_test_run(
    req: func.HttpRequest, log_table: func.Out[str], workers: str
) -> func.HttpResponse:
    """
    Development endpoint to test runbook execution with feature flag.
    Accepts a runbook/script and arguments, selects a worker by capability using smart routing,
    and sends to worker via queue. Only available when FEATURE_DEV=true.

    Request body (JSON):
    {
      "script": "script_name.sh",
      "args": "command_args",
      "body": {"json": "payload"},
      "capability": "worker_capability"
    }
    """
    # Feature flag for test execution
    if os.getenv("FEATURE_DEV", "false").lower() != "true":
        return func.HttpResponse("Not found", status_code=404)

    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    # Import utilities needed for this function
    import detection
    import utils
    from worker_routing import worker_routing

    script = req_body.get("script", "").strip()
    run_args = req_body.get("args") or ""
    capability = req_body.get("capability", "").strip()
    script_type = req_body.get("scriptType", "python").lower()  # Default to python

    # Parse resource_info from the body using detection (same as Trigger endpoint)
    (
        _raw,
        resource_name,
        resource_group,
        resource_id,
        schema_id,
        namespace,
        pod,
        deployment,
        horizontalpodautoscaler,
        job,
        monitor_condition,
        severity,
    ) = detection.parse_resource_fields(req_body.get("body")).values()
    resource_info = {
        "_raw": _raw,
        "resource_name": resource_name,
        "resource_rg": resource_group,
        "resource_id": resource_id,
        "aks_namespace": namespace,
        "aks_pod": pod,
        "aks_deployment": deployment,
        "aks_job": job,
        "aks_horizontalpodautoscaler": horizontalpodautoscaler,
        "team": "dev-test",
    }

    if not script:
        return func.HttpResponse(
            json.dumps(
                {"error": "missing 'script' field in request body"},
                ensure_ascii=False,
            ),
            status_code=400,
            mimetype="application/json",
        )

    # Generate unique test name for logging (instead of dumping full script)
    script_name = f"dev-test-{uuid.uuid4().hex[:8]}"

    if not capability:
        return func.HttpResponse(
            json.dumps(
                {"error": "missing 'capability' field in request body"},
                ensure_ascii=False,
            ),
            status_code=400,
            mimetype="application/json",
        )

    # Generate execution ID
    exec_id = str(uuid.uuid4())
    requested_at = utils.format_requested_at()

    # Get authenticated user
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    logging.info(
        f"[DEV TEST] Scheduling test run: {script_name} with capability {capability} (exec_id={exec_id})"
    )

    try:
        # Create a simple schema-like object for worker_routing
        class TestSchema:
            def __init__(self, worker):
                self.worker = worker

        schema = TestSchema(capability)

        # Use smart routing to select worker by capability
        target_queue = worker_routing(workers, schema)

        if not target_queue:
            return func.HttpResponse(
                json.dumps(
                    {
                        "status": "error",
                        "script": script_name,
                        "error": f"No active workers found for capability '{capability}'",
                    },
                    ensure_ascii=False,
                ),
                status_code=404,
                mimetype="application/json",
            )

        # Construct queue payload for worker (like Trigger endpoint does)
        queue_payload = {
            "runbook": script_name,
            "script": script,
            "script_type": script_type,
            "run_args": run_args,
            "id": script_name,
            "name": "Dev Test Run",
            "requestedAt": requested_at,
            "exec_id": exec_id,
            "oncall": "false",
            "monitor_condition": "Fired",
            "severity": severity or "Sev4",
            "worker": capability,
            "group": "-",
            "resource_info": resource_info or {},
            "routing_info": {},
        }

        logging.info(
            f"[DEV TEST] Selected queue '{target_queue}' for capability '{capability}'. Sending payload: {queue_payload}"
        )

        # Send to the selected queue
        from azure.storage.queue import QueueClient, TextBase64EncodePolicy

        q_client = QueueClient.from_connection_string(
            conn_str=os.environ.get(STORAGE_CONN),
            queue_name=target_queue,
            message_encode_policy=TextBase64EncodePolicy(),
        )
        q_client.send_message(json.dumps(queue_payload, ensure_ascii=False))

        # Log audit entry for test run
        log_audit(
            user=session.get("username", "dev-user"),
            action="RUNBOOK_DEV_TEST_RUN",
            target=exec_id,
            details=f"Script: {script_name}, Capability: {capability}, Args: {run_args}",
        )

        # Write log entry to the table
        api_body = {"status": "accepted", "queue": target_queue}
        partition_key = utils.today_partition_key()
        start_log = build_log_entry(
            status="accepted",
            partition_key=partition_key,
            exec_id=exec_id,
            row_key=exec_id,
            requested_at=requested_at,
            name="Dev Test Run" or "",
            schema_id=script_name,
            runbook=script_name,
            run_args=run_args,
            worker=schema.worker,
            group="-",
            log_msg=api_body,
            oncall="false",
            monitor_condition=monitor_condition or "Fired",
            severity=severity or "Sev4",
            resource_info=resource_info,
        )
        log_table.set(json.dumps(start_log, ensure_ascii=False))

        response_body = json.dumps(
            {
                "status": "accepted",
                "exec_id": exec_id,
                "script": script_name,
                "capability": capability,
                "queue": target_queue,
                "message": "Test run scheduled on worker",
            },
            ensure_ascii=False,
        )
        return func.HttpResponse(
            response_body, status_code=202, mimetype="application/json"
        )

    except Exception as e:
        logging.error(
            f"[DEV TEST] Error scheduling test run: {type(e).__name__}: {str(e)}"
        )
        error_body = json.dumps(
            {
                "status": "error",
                "script": script_name,
                "error": f"{type(e).__name__}: {str(e)}",
            },
            ensure_ascii=False,
        )
        return func.HttpResponse(
            error_body, status_code=500, mimetype="application/json"
        )


# =========================
# Features Flag Endpoint
# =========================


@app.route(route="features", auth_level=AUTH)
def features(req: func.HttpRequest) -> func.HttpResponse:
    """Expose feature flags to frontend"""
    import os

    feature_dev_enabled = os.getenv("FEATURE_DEV", "false").lower() == "true"

    body = json.dumps(
        {
            "FEATURE_DEV": feature_dev_enabled,
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(body, status_code=200, mimetype="application/json")


# =========================
# Heartbeat
# =========================


@app.route(route="healthz", auth_level=AUTH)
def heartbeat(req: func.HttpRequest) -> func.HttpResponse:
    import utils

    now_utc = utils.utc_now_iso()
    body = json.dumps(
        {
            "status": "ok",
            "time": now_utc,
            "service": "Trigger",
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(
        body,
        status_code=200,
        mimetype="application/json",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# =========================
# Table Storage READ (input binding)
# =========================


@app.route(route="logs/{partitionKey}/{execId}", auth_level=AUTH)
@app.table_input(
    arg_name="log_entity",
    table_name="RunbookLogs",
    partition_key="{partitionKey}",
    filter="ExecId eq '{execId}'",
    connection="AzureWebJobsStorage",
)
def get_log(req: func.HttpRequest, log_entity: str) -> func.HttpResponse:
    """
    Returns the entity from the RunbookLogs table identified by PartitionKey and RowKey.
    Uso: GET /api/logs/{partitionKey}/{execId}
    """
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    # If the entity does not exist, the binding returns None/empty.
    if not log_entity:
        return func.HttpResponse(
            json.dumps({"error": "Entity not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    try:
        parsed = json.loads(log_entity)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else None
        if isinstance(parsed, dict):
            parsed = _hydrate_log_field(parsed)
            log_entity = json.dumps(parsed, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"Failed to hydrate get_log entity: {e}")

    # log_entity is a JSON string of the complete entity
    return func.HttpResponse(
        log_entity,
        status_code=200,
        mimetype="application/json",
    )


# TODO Manage empty partitions
# @app.table_input(
#     arg_name="rows",
#     table_name=TABLE_NAME,
#     partition_key="{partitionKey}",
#     connection=STORAGE_CONN,
# )
@app.route(
    route="logs/query",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def logs_query(req: func.HttpRequest) -> func.HttpResponse:
    """
    Query dei log via Table Input Binding:
    - partitionKey (required) -> used for the binding
    - execId, status -> filtered by memory
    - q (contains on some filed), from/to (range on RequestedAt), order, limit -> in memory
    """
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    from azure.data.tables import TableClient

    try:
        partition_key = (req.params.get("partitionKey") or "").strip()
        if not partition_key:
            return func.HttpResponse(
                json.dumps({"error": "partitionKey required"}, ensure_ascii=False),
                status_code=400,
                mimetype="application/json",
            )

        exec_id = (req.params.get("execId") or "").strip()
        status = (req.params.get("status") or "").strip().lower()
        q = (req.params.get("q") or "").strip()
        from_dt = (req.params.get("from") or "").strip()
        to_dt = (req.params.get("to") or "").strip()

        try:
            limit = min(max(int(req.params.get("limit") or 200), 1), 5000)
        except Exception:
            limit = 200
        order = (req.params.get("order") or "desc").strip().lower()

        conn_str = os.environ.get(STORAGE_CONN)
        table_client = TableClient.from_connection_string(
            conn_str, table_name=TABLE_NAME
        )

        filter_query = f"PartitionKey eq '{partition_key}'"
        if exec_id:
            filter_query += f" and ExecId eq '{exec_id}'"

        try:
            entities = table_client.query_entities(query_filter=filter_query)
            data = list(entities)
        except Exception as e:
            logging.error(f"Table query failed: {e}")
            return func.HttpResponse(
                json.dumps(
                    {"error": "Failed to fetch data from storage"}, ensure_ascii=False
                ),
                status_code=500,
                mimetype="application/json",
            )

        # Helpers
        def parse_dt_local(v: str) -> Optional[datetime]:
            if not v:
                return None
            try:
                return datetime.fromisoformat(v)
            except Exception:
                try:
                    from datetime import datetime as dt

                    return dt.strptime(v.replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    return None

        f_dt = parse_dt_local(from_dt)
        t_dt = parse_dt_local(to_dt)

        def contains_any(e: dict, s: str) -> bool:
            s = s.lower()
            for k in ("Name", "Id", "Url", "Runbook", "Log", "Run_Args"):
                v = e.get(k)
                if v is None:
                    continue
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                if s in str(v).lower():
                    return True
            return False

        # Memory filters
        filtered: list[dict[str, Any]] = []
        for e in data:
            ok = True
            if exec_id and str(e.get("ExecId") or "").strip() != exec_id:
                ok = False
            if ok and status and str(e.get("Status") or "").strip().lower() != status:
                ok = False
            if ok and q and not contains_any(e, q):
                ok = False
            if ok and (f_dt or t_dt):
                rd = parse_dt_local(str(e.get("RequestedAt") or ""))
                if not rd:
                    ok = False
                else:
                    if f_dt and rd < f_dt:
                        ok = False
                    if t_dt and rd > t_dt:
                        ok = False
            if ok:
                filtered.append(e)

        # Order by RequestedAt
        def key_dt(e: dict):
            d = parse_dt_local(str(e.get("RequestedAt") or "")) or datetime.min
            return d

        reverse = order != "asc"
        filtered.sort(key=key_dt, reverse=reverse)

        # Apply limits
        if len(filtered) > limit:
            filtered = filtered[:limit]

        hydrated_items = []
        for item in filtered:
            try:
                hydrated_items.append(_hydrate_log_field(item))
            except Exception as e:
                logging.warning(f"Failed to hydrate log item {item.get('RowKey')}: {e}")
                hydrated_items.append(item)

        body = json.dumps({"items": hydrated_items}, ensure_ascii=False)
        return func.HttpResponse(
            body,
            status_code=200,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    except Exception as e:
        logging.exception("logs_query (binding) failed")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )


@app.route(
    route="workers/register",
    methods=[func.HttpMethod.POST],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def register_worker(req: func.HttpRequest) -> func.HttpResponse:
    import utils
    from azure.data.tables import TableClient, UpdateMode

    expected_key = os.environ.get("CLOUDO_SECRET_KEY")
    request_key = req.headers.get("x-cloudo-key")

    if not expected_key or request_key != expected_key:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    try:
        body = req.get_json()

        capability = (body.get("capability") or body.get("id") or "").strip()
        worker_instance_id = (body.get("worker_id") or "").strip()
        worker_queue = body.get("queue")

        if not capability or not worker_queue or not worker_instance_id:
            return func.HttpResponse(
                "Missing capability, worker_id or url", status_code=400
            )

        conn_str = os.environ.get("AzureWebJobsStorage")
        table_client = TableClient.from_connection_string(
            conn_str, table_name="WorkersRegistry"
        )

        entity = {
            "PartitionKey": capability,
            "RowKey": worker_instance_id,
            "Queue": worker_queue,
            "LastSeen": utils.utc_now_iso(),
            "Region": body.get("region", "default"),
            "Load": body.get("load", 0),
        }

        table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

        return func.HttpResponse(
            json.dumps({"status": "registered", "timestamp": entity["LastSeen"]}),
            status_code=200,
        )

    except Exception as e:
        logging.error(f"Register failed: {e}")
        return func.HttpResponse(str(e), status_code=500)


@app.route(
    route="workers",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def list_workers(req: func.HttpRequest, workers: str) -> func.HttpResponse:
    """
    Returns the list of registered workers available in the registry.
    """
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    try:
        # Parse binding result (can be string or list depending on extension version)
        data = json.loads(workers) if isinstance(workers, str) else (workers or [])

        return func.HttpResponse(
            json.dumps(data, ensure_ascii=False),
            status_code=200,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to list workers: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@app.route(
    route="workers/processes",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_worker_processes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy endpoint: calls the worker API from the backend.
    Expected param: worker (hostname/ip:port)
    """
    import requests

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    worker = req.params.get("worker")
    if not worker:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'worker' param"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    # Construct target URL (assuming http protocol for internal workers)
    # If your workers use https or a specific port logic, adjust here.
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        target_url = f"https://{worker}.azurewebsites.net/api/processes"
    else:
        target_url = f"http://{worker}/api/processes"

    try:
        # Timeout short to avoid blocking the orchestrator for too long
        resp = requests.get(
            target_url,
            headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
            timeout=5,
        )
        return func.HttpResponse(
            resp.text,
            status_code=resp.status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to proxy processes for {worker}: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Failed to reach worker: {str(e)}"}, ensure_ascii=False
            ),
            status_code=502,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@app.route(
    route="auth/register",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def auth_register(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    body = req.get_json()
    try:
        from azure.data.tables import TableClient

        username = body.get("username").lower()
        password = body.get("password")
        email = body.get("email")

        if not username or not password or not email:
            return func.HttpResponse(
                json.dumps({"error": "Username, password and email required"}),
                status_code=400,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        conn_str = os.environ.get(STORAGE_CONN)
        table_client = TableClient.from_connection_string(
            conn_str, table_name=TABLE_USERS
        )

        try:
            table_client.get_entity(partition_key="Operator", row_key=username)
            return func.HttpResponse(
                json.dumps({"error": "Username already exists"}),
                status_code=409,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception:
            # User doesn't exist, proceed
            pass

        import bcrypt

        hashed_password = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        entity = {
            "PartitionKey": "Operator",
            "RowKey": username,
            "password": hashed_password,
            "email": email,
            "role": "PENDING",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        table_client.create_entity(entity=entity)

        log_audit(
            user=username,
            action="USER_REGISTER_REQUEST",
            target=username,
            details=f"user: {username}, email: {email}",
        )

        return func.HttpResponse(
            json.dumps({"success": True}),
            status_code=201,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    except Exception as e:
        logging.error(f"Registration error: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Registration failed"}),
            status_code=500,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )


@app.route(
    route="auth/login",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def auth_login(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    body = req.get_json()
    try:
        from azure.data.tables import TableClient

        username = body.get("username").lower()
        password = body.get("password")

        if not username or not password:
            return func.HttpResponse(
                json.dumps({"error": "Username and password required"}),
                status_code=400,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        conn_str = os.environ.get(STORAGE_CONN)
        table_client = TableClient.from_connection_string(
            conn_str, table_name=TABLE_USERS
        )

        user_entity = table_client.get_entity(
            partition_key="Operator", row_key=username
        )

        import bcrypt

        db_password = user_entity.get("password")
        is_valid = False

        if db_password:
            # Check if it's already hashed (bcrypt hashes start with $2b$ or $2a$)
            if db_password.startswith("$2b$") or db_password.startswith("$2a$"):
                try:
                    if bcrypt.checkpw(
                        password.encode("utf-8"), db_password.encode("utf-8")
                    ):
                        is_valid = True
                except Exception as e:
                    logging.warning(f"Bcrypt check failed: {e}")
            else:
                # Fallback for plain text (for migration period)
                if db_password == password:
                    is_valid = True
                    # Optional: auto-migrate to hash here if we have the plain password
                    try:
                        hashed = bcrypt.hashpw(
                            password.encode("utf-8"), bcrypt.gensalt()
                        ).decode("utf-8")
                        user_entity["password"] = hashed
                        table_client.update_entity(entity=user_entity)
                        logging.info(f"User {username} password migrated to hash")
                    except Exception as e:
                        logging.error(f"Failed to migrate password for {username}: {e}")

        if is_valid:
            if user_entity.get("role") == "PENDING":
                return func.HttpResponse(
                    json.dumps(
                        {"error": "Account pending approval. Contact administrator."}
                    ),
                    status_code=403,
                    mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            log_audit(
                user=user_entity.get("RowKey"),
                action="USER_LOGIN_SUCCESS",
                target=user_entity.get("email"),
                details=f"user: {user_entity.get('RowKey')}, email: {user_entity.get('email')}, role: {user_entity.get('role')}",
            )
            # Token expiration (e.g. 8 hours)
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

            # Create session token
            session_token = _create_session_token(
                username=user_entity.get("RowKey"),
                role=user_entity.get("role"),
                expires_at=expires_at,
            )

            return func.HttpResponse(
                json.dumps(
                    {
                        "success": True,
                        "user": {
                            "username": user_entity.get("RowKey"),
                            "email": user_entity.get("email"),
                            "role": user_entity.get("role"),
                        },
                        "expires_at": expires_at,
                        "token": session_token,
                    }
                ),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        else:
            return func.HttpResponse(
                json.dumps({"error": "Invalid credentials"}),
                status_code=401,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
    except Exception as e:
        logging.error(f"Login error: {e}")
        log_audit(
            user=body.get("username"),
            action="USER_LOGIN_FAILED",
            target=body.get("username"),
            details=f"user: {body.get('username')}",
        )

        return func.HttpResponse(
            json.dumps({"error": "Authentication failed"}),
            status_code=401,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )


@app.route(
    route="auth/google",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def auth_google(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    try:
        body = req.get_json()
        access_token = body.get("access_token")
        if not access_token:
            return func.HttpResponse(
                json.dumps({"error": "Google access token required"}),
                status_code=400,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        import requests

        # Verify token and get user info from Google
        google_res = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if not google_res.ok:
            return func.HttpResponse(
                json.dumps({"error": "Invalid Google token"}),
                status_code=401,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        google_user = google_res.json()
        email = google_user.get("email")
        picture = google_user.get("picture")

        if not email:
            return func.HttpResponse(
                json.dumps({"error": "Email not provided by Google"}),
                status_code=400,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        from azure.data.tables import TableClient

        conn_str = os.environ.get(STORAGE_CONN)
        table_client = TableClient.from_connection_string(
            conn_str, table_name=TABLE_USERS
        )

        username = email.split("@")[0].lower()

        try:
            user_entity = table_client.get_entity(
                partition_key="Operator", row_key=username
            )
            # Update picture if changed
            updated = False
            if picture and user_entity.get("picture") != picture:
                user_entity["picture"] = picture
                updated = True

            # Ensure sso_provider is set even for existing users who login via Google
            if user_entity.get("sso_provider") != "google":
                user_entity["sso_provider"] = "google"
                updated = True

            if updated:
                table_client.update_entity(entity=user_entity)
        except Exception:
            user_entity = {
                "PartitionKey": "Operator",
                "RowKey": username,
                "email": email,
                "role": "VIEWER",
                "picture": picture,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "sso_provider": "google",
            }
            table_client.create_entity(entity=user_entity)
            log_audit(
                user=username,
                action="USER_PROVISIONED_SSO",
                target=email,
                details=f"Provider: Google, role: {user_entity.get('role')}",
            )

        if user_entity.get("role") == "PENDING":
            log_audit(
                user=user_entity.get("RowKey"),
                action="USER_LOGIN_PENDING",
                target=user_entity.get("email"),
                details=f"Provider: Google, user: {user_entity.get('RowKey')}",
            )
            return func.HttpResponse(
                json.dumps({"error": "Account pending approval."}),
                status_code=403,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
        session_token = _create_session_token(
            username=user_entity.get("RowKey"),
            role=user_entity.get("role"),
            expires_at=expires_at,
        )

        log_audit(
            user=user_entity.get("RowKey"),
            action="USER_LOGIN_SUCCESS",
            target=user_entity.get("email"),
            details=f"Provider: Google, user: {user_entity.get('RowKey')}, email: {user_entity.get('email')}, role: {user_entity.get('role')}",
        )

        return func.HttpResponse(
            json.dumps(
                {
                    "success": True,
                    "user": {
                        "username": user_entity.get("RowKey"),
                        "email": user_entity.get("email"),
                        "role": user_entity.get("role"),
                        "picture": user_entity.get("picture"),
                    },
                    "expires_at": expires_at,
                    "token": session_token,
                }
            ),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    except Exception as e:
        logging.error(f"Google Auth error: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Internal server error during Google SSO"}),
            status_code=500,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )


@app.route(
    route="auth/profile",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def auth_profile(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    username = session.get("username")
    from azure.data.tables import TableClient, UpdateMode

    conn_str = os.environ.get(STORAGE_CONN)
    table_client = TableClient.from_connection_string(conn_str, table_name=TABLE_USERS)

    try:
        user_entity = table_client.get_entity(
            partition_key="Operator", row_key=username
        )
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "User profile not found"}),
            status_code=404,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method == "GET":
        return func.HttpResponse(
            json.dumps(
                {
                    "username": user_entity.get("RowKey"),
                    "email": user_entity.get("email"),
                    "role": user_entity.get("role"),
                    "picture": user_entity.get("picture"),
                    "created_at": user_entity.get("created_at"),
                    "api_token": user_entity.get("api_token"),
                    "sso_provider": user_entity.get("sso_provider"),
                }
            ),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method == "POST":
        try:
            body = req.get_json()
            new_email = body.get("email")
            new_password = body.get("password")
            generate_token = body.get("generate_token")

            # Check if user is SSO (Google)
            is_sso = user_entity.get("sso_provider") == "google"

            if new_email:
                if is_sso and new_email != user_entity.get("email"):
                    return func.HttpResponse(
                        json.dumps({"error": "Email cannot be modified for SSO users"}),
                        status_code=403,
                        mimetype="application/json",
                        headers={"Access-Control-Allow-Origin": "*"},
                    )
                user_entity["email"] = new_email

            if new_password:
                if is_sso:
                    return func.HttpResponse(
                        json.dumps(
                            {"error": "Password cannot be modified for SSO users"}
                        ),
                        status_code=403,
                        mimetype="application/json",
                        headers={"Access-Control-Allow-Origin": "*"},
                    )
                import bcrypt

                hashed_password = bcrypt.hashpw(
                    new_password.encode("utf-8"), bcrypt.gensalt()
                ).decode("utf-8")
                user_entity["password"] = hashed_password

            if generate_token:
                import secrets

                user_entity["api_token"] = f"cloudo_{secrets.token_urlsafe(32)}"

            table_client.update_entity(entity=user_entity, mode=UpdateMode.REPLACE)

            log_audit(
                user=username,
                action="USER_PROFILE_UPDATE",
                target=username,
                details=f"Updated profile for {username} (generate_token={generate_token})",
            )

            return func.HttpResponse(
                json.dumps(
                    {
                        "success": True,
                        "api_token": user_entity.get("api_token")
                        if generate_token
                        else None,
                    }
                ),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            logging.error(f"Profile update error: {e}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to update profile"}),
                status_code=500,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )


@app.route(
    route="users",
    methods=[
        func.HttpMethod.GET,
        func.HttpMethod.POST,
        func.HttpMethod.DELETE,
        func.HttpMethod.OPTIONS,
    ],
    auth_level=AUTH,
)
def users_management(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") not in ["ADMIN", "OPERATOR", "VIEWER"]:
        return func.HttpResponse(
            json.dumps(
                {"error": "Unauthorized: Admin, Operator or Viewer role required"}
            ),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method in ["POST", "DELETE"] and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify users"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    from azure.data.tables import TableClient, UpdateMode

    conn_str = os.environ.get(STORAGE_CONN)
    table_client = TableClient.from_connection_string(conn_str, table_name=TABLE_USERS)

    if req.method == "GET":
        try:
            entities = table_client.query_entities(
                query_filter="PartitionKey eq 'Operator'"
            )
            users = []
            for e in entities:
                users.append(
                    {
                        "username": e.get("RowKey"),
                        "email": e.get("email"),
                        "role": e.get("role"),
                        "created_at": e.get("created_at"),
                        "picture": e.get("picture"),
                        "sso_provider": e.get("sso_provider"),
                    }
                )
            return func.HttpResponse(
                json.dumps(users),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception:
            return func.HttpResponse(
                json.dumps([]),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "POST":
        try:
            body = req.get_json()
            username = body.get("username")
            if not username:
                return func.HttpResponse("Missing username", status_code=400)

            # Check if user exists to preserve created_at
            try:
                existing_user = table_client.get_entity(
                    partition_key="Operator", row_key=username
                )
                created_at = existing_user.get("created_at")

                # Check if user is SSO (Google)
                is_sso = existing_user.get("sso_provider") == "google"

                # If password is not provided in body, keep the old one
                password = body.get("password")
                if is_sso:
                    # For SSO users, never update email or password via this endpoint
                    email = existing_user.get("email")
                    password = existing_user.get("password")
                else:
                    email = body.get("email") or existing_user.get("email")
                    password = password or existing_user.get("password")

                sso_provider = existing_user.get("sso_provider")
                picture = existing_user.get("picture")
            except Exception:
                created_at = datetime.now(timezone.utc).isoformat()
                email = body.get("email")
                password = body.get("password")
                sso_provider = None
                picture = None

            import bcrypt

            # If password is provided and doesn't look like a bcrypt hash, hash it
            if password and not (
                password.startswith("$2b$") or password.startswith("$2a$")
            ):
                password = bcrypt.hashpw(
                    password.encode("utf-8"), bcrypt.gensalt()
                ).decode("utf-8")

            entity = {
                "PartitionKey": "Operator",
                "RowKey": username,
                "password": password,
                "email": email,
                "role": body.get("role", "OPERATOR"),
                "created_at": created_at,
                "sso_provider": sso_provider,
                "picture": picture,
            }
            table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

            # Audit log
            log_audit(
                user=session.get("username") or "SYSTEM",
                action="USER_ENROLL" if not body.get("created_at") else "USER_UPDATE",
                target=username,
                details=f"Role: {body.get('role')}, Email: {body.get('email')}",
            )

            return func.HttpResponse(
                json.dumps({"success": True}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "DELETE":
        try:
            username = req.params.get("username")
            if not username:
                return func.HttpResponse("Missing username", status_code=400)
            table_client.delete_entity(partition_key="Operator", row_key=username)

            # Audit log
            log_audit(
                user=session.get("username") or "SYSTEM",
                action="USER_REVOKE",
                target=username,
                details="Identity destroyed",
            )

            return func.HttpResponse(
                json.dumps({"success": True}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )


@app.route(
    route="settings",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def settings_management(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    from azure.data.tables import TableClient

    conn_str = os.environ.get(STORAGE_CONN)
    table_client = TableClient.from_connection_string(
        conn_str, table_name=TABLE_SETTINGS
    )

    # Verification of admin role
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") not in ["ADMIN", "OPERATOR", "VIEWER"]:
        return func.HttpResponse(
            json.dumps(
                {"error": "Unauthorized: Admin, Operator or Viewer role required"}
            ),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method == "POST" and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify settings"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method == "GET":
        try:
            entities = table_client.query_entities(
                query_filter="PartitionKey eq 'GlobalConfig'"
            )
            settings = {e["RowKey"]: e["value"] for e in entities}
            return func.HttpResponse(
                json.dumps(settings),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception:
            return func.HttpResponse(
                json.dumps({}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "POST":
        try:
            body = req.get_json()
            for key, value in body.items():
                entity = {
                    "PartitionKey": "GlobalConfig",
                    "RowKey": key,
                    "value": str(value),
                }
                table_client.upsert_entity(entity=entity)

            log_audit(
                user=session.get("username") or "SYSTEM",
                action="SETTINGS_UPDATE",
                target="GLOBAL_CONFIG",
                details=str(list(body.keys())),
            )
            return func.HttpResponse(
                json.dumps({"success": True}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )


@app.route(
    route="audit",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_audit_logs(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    from azure.data.tables import TableClient

    conn_str = os.environ.get(STORAGE_CONN)
    table_client = TableClient.from_connection_string(conn_str, table_name=TABLE_AUDIT)

    # Verification of admin role
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") not in ["ADMIN", "OPERATOR", "VIEWER"]:
        return func.HttpResponse(
            json.dumps(
                {"error": "Unauthorized: Admin, Operator or Viewer role required"}
            ),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        limit = req.params.get("limit")
        try:
            limit = int(limit) if limit and limit.lower() != "all" else None
        except ValueError:
            limit = None

        entities = table_client.query_entities(query_filter="")
        logs = []
        for e in entities:
            logs.append(
                {
                    "timestamp": e.get("timestamp"),
                    "operator": e.get("operator"),
                    "action": e.get("action"),
                    "target": e.get("target"),
                    "details": e.get("details"),
                }
            )
        # Sort by timestamp descending
        logs.sort(key=lambda x: x["timestamp"] or "", reverse=True)

        if limit:
            logs = logs[:limit]

        return func.HttpResponse(
            json.dumps(logs),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    except Exception:
        return func.HttpResponse(
            json.dumps([]),
            status_code=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )


@app.route(
    route="schedules",
    methods=[
        func.HttpMethod.GET,
        func.HttpMethod.POST,
        func.HttpMethod.DELETE,
        func.HttpMethod.OPTIONS,
    ],
    auth_level=AUTH,
)
def schedules_management(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    from azure.data.tables import TableClient, UpdateMode

    conn_str = os.environ.get(STORAGE_CONN)
    table_client = TableClient.from_connection_string(
        conn_str, table_name=TABLE_SCHEDULES
    )

    # Verification of authentication
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if req.method in ["POST", "DELETE"] and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify schedules"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method == "GET":
        try:
            entities = table_client.query_entities(
                query_filter="PartitionKey eq 'Schedule'"
            )
            schedules = []
            for e in entities:
                schedules.append(
                    {
                        "id": e.get("RowKey"),
                        "name": e.get("name"),
                        "cron": e.get("cron"),
                        "runbook": e.get("runbook"),
                        "run_args": e.get("run_args"),
                        "queue": e.get("queue"),
                        "worker_pool": e.get("worker_pool"),
                        "enabled": e.get("enabled"),
                        "oncall": e.get("oncall"),
                        "last_run": e.get("last_run"),
                    }
                )
            return func.HttpResponse(
                json.dumps(schedules),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception:
            return func.HttpResponse(
                json.dumps([]),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "POST":
        try:
            body = req.get_json()
            schedule_id = body.get("id") or str(uuid.uuid4())

            entity = {
                "PartitionKey": "Schedule",
                "RowKey": schedule_id,
                "name": body.get("name"),
                "cron": body.get("cron"),
                "runbook": body.get("runbook"),
                "run_args": body.get("run_args"),
                "queue": body.get("queue"),
                "worker_pool": body.get("worker_pool"),
                "enabled": body.get("enabled", True),
                "oncall": body.get("oncall", True),
                "last_run": body.get("last_run", ""),
            }
            table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

            log_audit(
                user=session.get("username") or "SYSTEM",
                action="SCHEDULE_UPSERT",
                target=schedule_id,
                details=f"Name: {body.get('name')}, Cron: {body.get('cron')}",
            )
            return func.HttpResponse(
                json.dumps({"success": True, "id": schedule_id}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "DELETE":
        try:
            schedule_id = req.params.get("id")
            if not schedule_id:
                return func.HttpResponse(
                    json.dumps({"error": "Missing id"}),
                    status_code=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            table_client.delete_entity(partition_key="Schedule", row_key=schedule_id)
            log_audit(
                user=session.get("username") or "SYSTEM",
                action="SCHEDULE_DELETE",
                target=schedule_id,
            )
            return func.HttpResponse(
                json.dumps({"success": True}),
                status_code=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )


@app.route(
    route="workers/stop",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def stop_worker_process(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy endpoint: calls the worker STOP API from the backend.
    Expected param: worker (hostname/ip:port), exec_id
    """
    import requests

    logging.info(f"Stop worker process requested. Params: {req.params}")

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot stop processes"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    worker = req.params.get("worker")
    exec_id = req.params.get("exec_id")

    if not worker or not exec_id:
        return func.HttpResponse(
            json.dumps(
                {"error": "Missing 'worker' or 'exec_id' param"}, ensure_ascii=False
            ),
            status_code=400,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        target_url = (
            f"https://{worker}.azurewebsites.net/api/processes/stop?exec_id={exec_id}"
        )
    else:
        target_url = f"http://{worker}/api/processes/stop?exec_id={exec_id}"

    try:
        resp = requests.delete(
            target_url,
            headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
            timeout=5,
        )
        return func.HttpResponse(
            resp.text,
            status_code=resp.status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to proxy stop for {worker}/{exec_id}: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Failed to reach worker: {str(e)}"}, ensure_ascii=False
            ),
            status_code=502,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@app.route(
    route="schemas",
    methods=[
        func.HttpMethod.GET,
        func.HttpMethod.POST,
        func.HttpMethod.OPTIONS,
        func.HttpMethod.DELETE,
        func.HttpMethod.PUT,
    ],
    auth_level=AUTH,
)
@app.table_input(arg_name="entities", table_name=TABLE_SCHEMAS, connection=STORAGE_CONN)
@app.table_output(
    arg_name="outputTable", table_name=TABLE_SCHEMAS, connection=STORAGE_CONN
)
def runbook_schemas(
    req: func.HttpRequest, entities: str, outputTable: func.Out[str]
) -> func.HttpResponse:
    logging.info(f"Processing {req.method} request for schemas.")

    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = _get_authenticated_user(req)
    if error_res:
        return error_res

    if req.method in ["POST", "PUT", "DELETE"] and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify schemas"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    requester_username = session.get("username")

    if req.method == "GET":
        try:
            schemas_data = json.loads(entities)
            logging.info(f"schemas: {str(schemas_data)}")

            return func.HttpResponse(
                body=json.dumps(schemas_data),
                status_code=200,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )
        except Exception as e:
            logging.error(f"Error processing schemas: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": "Failed to fetch schemas"}),
                status_code=500,
                mimetype="application/json",
            )

    if req.method == "POST":
        try:
            body = req.get_json()

            schema_id = body.get("id", str(uuid.uuid4()))
            new_entity = {
                "PartitionKey": body.get("PartitionKey", "RunbookSchema"),
                "RowKey": schema_id,
                **body,
            }

            outputTable.set(json.dumps(new_entity))

            # Audit log
            log_audit(
                user=requester_username or "SYSTEM",
                action="SCHEMA_CREATE",
                target=schema_id,
                details=f"Name: {body.get('name')}, Runbook: {body.get('runbook')}",
            )

            return func.HttpResponse(
                body=json.dumps(new_entity),
                status_code=201,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json",
                },
            )
        except Exception as e:
            logging.error(f"Error creating schema: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": "Failed to create schema"}),
                status_code=400,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

    if req.method == "PUT":
        try:
            from azure.data.tables import TableClient, UpdateMode

            body = req.get_json()
            schema_id = body.get("id")

            if not schema_id:
                return func.HttpResponse(
                    body=json.dumps({"error": "Missing 'id' field"}),
                    status_code=400,
                    mimetype="application/json",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                    },
                )

            updated_entity = {
                "PartitionKey": body.get("PartitionKey", "RunbookSchema"),
                "RowKey": schema_id,
                **body,
            }

            conn_str = os.environ.get(STORAGE_CONN)
            table_client = TableClient.from_connection_string(
                conn_str, table_name=TABLE_SCHEMAS
            )
            table_client.upsert_entity(entity=updated_entity, mode=UpdateMode.REPLACE)

            # Audit log
            log_audit(
                user=requester_username or "SYSTEM",
                action="SCHEMA_UPDATE",
                target=schema_id,
                details=f"Name: {body.get('name')}",
            )

            return func.HttpResponse(
                body=json.dumps(updated_entity),
                status_code=200,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json",
                },
            )
        except Exception as e:
            logging.error(f"Error updating schema: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": f"Failed to update schema: {str(e)}"}),
                status_code=400,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

    if req.method == "DELETE":
        try:
            from azure.data.tables import TableClient

            # Try to get schema_id from query params first, then body
            schema_id = req.params.get("id")
            partition_key = req.params.get("PartitionKey", "RunbookSchema")

            if not schema_id:
                try:
                    body = req.get_json()
                    schema_id = body.get("id")
                    partition_key = body.get("PartitionKey", "RunbookSchema")
                except Exception:
                    pass

            if not schema_id:
                return func.HttpResponse(
                    body=json.dumps({"error": "Missing 'id' field in params or body"}),
                    status_code=400,
                    mimetype="application/json",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                    },
                )

            conn_str = os.environ.get(STORAGE_CONN)
            table_client = TableClient.from_connection_string(
                conn_str, table_name=TABLE_SCHEMAS
            )
            table_client.delete_entity(partition_key=partition_key, row_key=schema_id)

            # Audit log
            log_audit(
                user=requester_username or "SYSTEM",
                action="SCHEMA_DELETE",
                target=schema_id,
                details=f"PartitionKey: {partition_key}",
            )

            return func.HttpResponse(
                body=json.dumps(
                    {"message": "Schema deleted successfully", "id": schema_id}
                ),
                status_code=200,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json",
                },
            )
        except Exception as e:
            logging.error(f"Error deleting schema: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": f"Failed to delete schema: {str(e)}"}),
                status_code=400,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

    return func.HttpResponse(
        body=json.dumps({"error": "Method not allowed"}),
        status_code=405,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route(
    route="runbooks/content",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_runbook_content(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    script_name = req.params.get("name")
    if not script_name:
        return func.HttpResponse(
            json.dumps({"error": "Query parameter 'name' is required"}),
            status_code=400,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    owner_repo = (GITHUB_REPO or "").strip()
    if not owner_repo or "/" not in owner_repo:
        return func.HttpResponse(
            json.dumps({"error": "GITHUB_REPO not configured correctly"}),
            status_code=500,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    branch = (GITHUB_BRANCH or "main").strip()
    prefix = (GITHUB_PATH_PREFIX or "").strip().strip("/")
    path_parts = [p for p in [prefix, script_name] if p]
    repo_path = "/".join(path_parts)

    content_text = None
    error_msg = "File not found"

    # LOCAL_DEV: read from local file system
    if os.getenv("LOCAL_DEV", "false").lower() == "true":
        try:
            # Check if a dev script path is explicitly set (e.g. in Docker)
            dev_script_path = os.getenv("DEV_SCRIPT_PATH")
            if dev_script_path:
                local_path = os.path.join(dev_script_path, script_name)
            else:
                # Fallback to relative path discovery for local development
                # We assume runbooks are in src/runbooks relative to project root.
                # The function app runs in src/core/orchestrator.
                # __file__ is src/core/orchestrator/function_app.py
                base_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                local_path = os.path.join(base_dir, "src", "runbooks", script_name)

            if os.path.exists(local_path):
                with open(local_path, encoding="utf-8") as f:
                    content_text = f.read()
                logging.info(f"Loaded runbook from local path: {local_path}")
            else:
                logging.warning(f"Local runbook not found at {local_path}")
        except Exception as e:
            logging.error(f"Error reading local runbook: {e}")

    if content_text is not None:
        return func.HttpResponse(
            json.dumps({"content": content_text}),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # We try both Contents API and Raw download
    import requests

    headers_list = []
    base_headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers_list.append({**base_headers, "Authorization": f"Bearer {GITHUB_TOKEN}"})
        headers_list.append({**base_headers, "Authorization": f"token {GITHUB_TOKEN}"})
    else:
        headers_list.append(base_headers)

    content_text = None
    error_msg = "File not found"

    # Try Contents API first
    api_url = f"https://api.github.com/repos/{owner_repo}/contents/{repo_path}"
    for h in headers_list:
        try:
            resp = requests.get(api_url, headers=h, params={"ref": branch}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if (
                    isinstance(data, dict)
                    and data.get("encoding") == "base64"
                    and "content" in data
                ):
                    import base64

                    content_text = base64.b64decode(
                        data["content"].replace("\n", "")
                    ).decode("utf-8")
                    break
            elif resp.status_code in (401, 403):
                error_msg = f"GitHub Auth Error: {resp.status_code}"
                continue
        except Exception as e:
            logging.error(f"GitHub API error: {e}")

    # Fallback to Raw
    if content_text is None:
        raw_url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{repo_path}"
        for h in headers_list:
            try:
                resp = requests.get(raw_url, headers=h, timeout=10)
                if resp.status_code == 200:
                    content_text = resp.text
                    break
            except Exception as e:
                logging.error(f"GitHub Raw error: {e}")

    if content_text is not None:
        return func.HttpResponse(
            json.dumps({"content": content_text}),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    else:
        return func.HttpResponse(
            json.dumps({"error": error_msg}),
            status_code=404,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )


@app.route(
    route="runbooks/list",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def list_runbooks(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    runbooks = []

    # LOCAL_DEV: list from local file system
    if os.getenv("LOCAL_DEV", "false").lower() == "true":
        try:
            dev_script_path = os.getenv("DEV_SCRIPT_PATH")
            if dev_script_path:
                local_dir = dev_script_path
            else:
                base_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                local_dir = os.path.join(base_dir, "src", "runbooks")

            if os.path.exists(local_dir):
                for root, dirs, files in os.walk(local_dir):
                    for file in files:
                        if file.endswith(".sh") or file.endswith(".py"):
                            rel_path = os.path.relpath(
                                os.path.join(root, file), local_dir
                            )
                            runbooks.append(rel_path)
                logging.info(f"Listed runbooks from local path: {local_dir}")
        except Exception as e:
            logging.error(f"Error listing local runbooks: {e}")

    # If no local runbooks found or not in DEV, try GitHub
    if not runbooks:
        owner_repo = (GITHUB_REPO or "").strip()
        branch = (GITHUB_BRANCH or "main").strip()
        prefix = (GITHUB_PATH_PREFIX or "").strip().strip("/")

        if owner_repo and "/" in owner_repo:
            import requests

            headers_list = []
            base_headers = {"Accept": "application/vnd.github.v3+json"}
            if GITHUB_TOKEN:
                headers_list.append(
                    {**base_headers, "Authorization": f"Bearer {GITHUB_TOKEN}"}
                )
                headers_list.append(
                    {**base_headers, "Authorization": f"token {GITHUB_TOKEN}"}
                )
            else:
                headers_list.append(base_headers)

            api_url = f"https://api.github.com/repos/{owner_repo}/git/trees/{branch}?recursive=1"
            for h in headers_list:
                try:
                    resp = requests.get(api_url, headers=h, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        tree = data.get("tree", [])
                        for item in tree:
                            path = item.get("path", "")
                            # Filter by prefix and extension
                            if path.startswith(prefix) and (
                                path.endswith(".sh") or path.endswith(".py")
                            ):
                                # If prefix is present, remove it from the path to get relative path
                                if prefix:
                                    prefix_len = len(prefix)
                                    rel_path = path[prefix_len:].lstrip("/")
                                    if rel_path:
                                        runbooks.append(rel_path)
                                else:
                                    runbooks.append(path)
                        break
                except Exception as e:
                    logging.error(f"GitHub API list error: {e}")

    return func.HttpResponse(
        json.dumps({"runbooks": sorted(list(set(runbooks)))}),
        status_code=200,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.schedule(
    schedule="0 */1 * * * *",
    arg_name="schedulerTimer",
    run_on_startup=False,
    use_monitor=False,
)
def scheduler_engine(schedulerTimer: func.TimerRequest) -> None:
    """
    Scheduler Engine: Check for scheduled runbooks and execute them.
    """
    import logging
    import os
    from datetime import datetime, timezone

    from azure.data.tables import TableClient
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy
    from utils import format_requested_at, is_cron_now, today_partition_key

    conn_str = os.environ.get("AzureWebJobsStorage")
    table_client = TableClient.from_connection_string(
        conn_str, table_name=TABLE_SCHEDULES
    )

    try:
        schedules = table_client.query_entities(
            query_filter="PartitionKey eq 'Schedule' and enabled eq true"
        )
        now = datetime.now(timezone.utc)

        for s in schedules:
            cron_expr = s.get("cron", "0 */1 * * * *")
            last_run_str = s.get("last_run", "")

            should_run_by_cron = is_cron_now(cron_expr, now)

            should_run = False
            if should_run_by_cron:
                if not last_run_str:
                    should_run = True
                else:
                    last_run_dt = datetime.fromisoformat(
                        last_run_str.replace("Z", "+00:00")
                    )
                    if (now - last_run_dt).total_seconds() >= 45:
                        should_run = True

            if should_run:
                exec_id = str(uuid.uuid4())
                logging.warning(
                    f"[Scheduler] Triggering {s['name']} (ID: {s['RowKey']}) -> ExecId: {exec_id}"
                )

                worker_pool = s.get("worker_pool")
                target_queue = "cloudo-default"

                if worker_pool:
                    try:
                        workers_table = TableClient.from_connection_string(
                            conn_str, table_name="WorkersRegistry"
                        )
                        entities = list(
                            workers_table.query_entities(
                                query_filter=f"PartitionKey eq '{worker_pool}'"
                            )
                        )
                        logging.warning(
                            f"[WorkersRegistry] Found {len(entities)} workers"
                        )
                        for w in entities:
                            if w.get("Queue"):
                                target_queue = w.get("Queue")
                                break
                    except Exception as e:
                        logging.error(
                            f"[Scheduler] Failed to resolve queue for pool {worker_pool}: {e}"
                        )

                requested_at = format_requested_at()
                partition_key = today_partition_key()

                queue_payload = {
                    "runbook": s.get("runbook"),
                    "run_args": s.get("run_args"),
                    "worker": worker_pool,
                    "group": "-",
                    "exec_id": exec_id,
                    "id": s.get("RowKey"),
                    "name": s.get("name"),
                    "status": "scheduled",
                    "oncall": s.get("oncall"),
                    "require_approval": False,
                    "requested_at": requested_at,
                }

                try:
                    log_table_client = TableClient.from_connection_string(
                        conn_str, table_name=TABLE_NAME
                    )
                    log_entry = build_log_entry(
                        status="scheduled",
                        partition_key=partition_key,
                        row_key=str(uuid.uuid4()),
                        exec_id=exec_id,
                        requested_at=requested_at,
                        name=s.get("name"),
                        schema_id=s.get("RowKey"),
                        runbook=s.get("runbook"),
                        run_args=s.get("run_args"),
                        worker=worker_pool,
                        group="-",
                        oncall=s.get("oncall"),
                        log_msg=json.dumps(
                            {
                                "status": "scheduled",
                                "queue": target_queue,
                            },
                            ensure_ascii=False,
                        ),
                        monitor_condition="",
                        severity="",
                    )
                    log_table_client.create_entity(entity=log_entry)
                except Exception as le:
                    logging.error(f"[Scheduler] Failed to log scheduled status: {le}")

                q_name = target_queue
                queue_service = QueueClient.from_connection_string(
                    conn_str, q_name, message_encode_policy=TextBase64EncodePolicy()
                )
                try:
                    queue_service.send_message(json.dumps(queue_payload))
                except Exception as qe:
                    if "QueueNotFound" in str(qe):
                        logging.warning(f"[Scheduler] Queue {q_name} not found")
                    else:
                        raise qe

                s["last_run"] = now.isoformat()
                table_client.update_entity(entity=s)

    except Exception as e:
        logging.error(f"[Scheduler] Error: {e}")


@app.schedule(
    schedule="0 */1 * * * *",
    arg_name="cleanupTimer",
    run_on_startup=False,
    use_monitor=False,
)
def worker_cleanup(cleanupTimer: func.TimerRequest) -> None:
    """
    Garbage Collector: Cleanup old workers where LastSeen is > 3 minutes.
    """
    import utils
    from azure.data.tables import TableClient

    conn_str = os.environ.get("AzureWebJobsStorage")
    table_client = TableClient.from_connection_string(
        conn_str, table_name="WorkersRegistry"
    )

    now_str = utils.utc_now_iso()
    now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
    limit_time = now_dt - timedelta(minutes=3)
    limit_iso = limit_time.isoformat()
    logging.info(f"[Cleanup] Cleaning up {limit_iso}")

    filter_query = f"LastSeen lt '{limit_iso}'"

    logging.debug(f"[Cleanup] Searching for zombies older than {limit_iso}...")

    try:
        dead_workers = table_client.query_entities(query_filter=filter_query)

        count = 0
        for w in dead_workers:
            table_client.delete_entity(
                partition_key=w["PartitionKey"], row_key=w["RowKey"]
            )
            logging.debug(
                f"[Cleanup] Deleted zombie: {w['RowKey']} (Partition: {w['PartitionKey']})"
            )
            count += 1

        logging.info(f"[Cleanup] Completed. Removed {count} workers.")

    except Exception as e:
        logging.error(f"[Cleanup] Failed: {e}")
