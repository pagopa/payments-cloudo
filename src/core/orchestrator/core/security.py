"""Request authentication: session tokens, API keys and signed approval links."""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import azure.functions as func
from core.config import APPROVAL_SECRET, DEFAULT_TEAM, SESSION_SECRET, TABLE_USERS

from core import storage, team_access


def b64url_encode(data: bytes) -> str:
    # Base64 URL-safe without padding
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def b64url_decode(s: str) -> bytes:
    # Decode Base64 URL-safe without padding
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_payload_b64(payload_b64: str) -> str:
    # HMAC-SHA256 signature over base64url payload (no padding)
    import hashlib
    import hmac

    key = (APPROVAL_SECRET or "default").encode("utf-8")
    return hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signed_payload(exec_id_path: str, p: str, s: str) -> tuple[bool, dict]:
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
        expected = sign_payload_b64(p)
        import hmac

        if not hmac.compare_digest(expected, s):
            return False, {}
        payload_raw = b64url_decode(p)
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
        logging.error(f"verify signed payload failed: {e}")
        return False, {}


def create_session_token(
    username: str, role: str, expires_at: str, team: str = DEFAULT_TEAM
) -> str:
    import hashlib
    import hmac

    payload = json.dumps(
        {
            "username": username,
            "role": role,
            "team": team_access.normalize_team(team),
            "expires_at": expires_at,
        }
    ).encode("utf-8")
    payload_b64 = b64url_encode(payload)
    key = SESSION_SECRET.encode("utf-8")
    sig = hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_session_token(token: str) -> tuple[bool, dict]:
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

        payload_raw = b64url_decode(p_b64)
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


def get_authenticated_user(
    req: func.HttpRequest,
) -> tuple[Optional[dict], Optional[func.HttpResponse]]:
    # 1. Check Bearer Token
    auth_header = req.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        ok, session = verify_session_token(token)
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
                "team": DEFAULT_TEAM,
            }, None

        # Check personal API tokens
        try:
            table_client = storage.get_table_client(TABLE_USERS)

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
                    "team": team_access.normalize_team(u.get("team")),
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
            "team": DEFAULT_TEAM,
        }, None

    return None, func.HttpResponse(
        json.dumps({"error": "Unauthorized: Missing or invalid credentials"}),
        status_code=401,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )
