"""Setting lookups (Table Storage, then env) and team credential resolution."""

import logging
import os
import re
import time
from typing import Optional

from smart_routing.sanitize import sanitize_team

ROUTING_SETTINGS_CACHE_TTL_SECONDS = int(
    os.getenv("ROUTING_SETTINGS_CACHE_TTL_SECONDS", "60")
)
_SAFE_ENV_KEY_RE = re.compile(r"^[A-Z0-9_]{1,96}$")
_INVISIBLE_WHITESPACE_RE = re.compile(
    "[\u00a0\u1680\u2000-\u200b\u202f\u205f\u2028\u2029\u3000\ufeff]"
)
_setting_cache: dict[str, tuple[Optional[str], float]] = {}


def invalidate() -> None:
    _setting_cache.clear()


def _clean(value: object) -> str:
    """Drop surrounding quotes and normalize invisible whitespace."""
    return _INVISIBLE_WHITESPACE_RE.sub(
        " ", str(value).strip().strip('"').strip("'")
    ).strip()


def _read_setting_from_table(key: str) -> Optional[str]:
    try:
        from azure.data.tables import TableClient

        conn_str = os.environ.get("AzureWebJobsStorage")
        if conn_str:
            with TableClient.from_connection_string(
                conn_str, table_name="CloudoSettings"
            ) as table_client:
                entity = table_client.get_entity(
                    partition_key="GlobalConfig", row_key=key
                )
                val = entity.get("value")
                if val:
                    return _clean(val)
    except Exception:
        pass
    return None


def _get_setting(key: str) -> Optional[str]:
    """
    Helper to get a setting from Azure Table Storage or Environment.
    """
    # Prevent unsafe environment/table key lookups from dynamic input.
    if not _SAFE_ENV_KEY_RE.fullmatch(str(key or "")):
        logging.warning("Rejected unsafe setting key lookup")
        return None

    now = time.time()
    cached = _setting_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    resolved = _read_setting_from_table(key)
    if resolved is None:
        val = os.environ.get(key)
        if val:
            resolved = _clean(val)

    _setting_cache[key] = (
        resolved,
        now + ROUTING_SETTINGS_CACHE_TTL_SECONDS,
    )
    return resolved


def get_setting(key: str) -> Optional[str]:
    """Table Storage-backed, env-fallback configuration value, such as feature
    flags configurable from the admin UI."""
    return _get_setting(key)


def resolve_jsm_apikey(team: Optional[str]) -> Optional[str]:
    """
    Resolve JSM apiKey from table storage or env using naming convention:
      - JSM_API_KEY_<TEAM> (preferred)
      - JSM_API_KEY_DEFAULT (fallback 1)
      - JSM_API_KEY (fallback 2)
    """
    team = sanitize_team(team)
    if team:
        key_name = f"JSM_API_KEY_{team}".upper().replace("-", "_")
        key = _get_setting(key_name)
        if key:
            return key

    return _get_setting("JSM_API_KEY_DEFAULT") or _get_setting("JSM_API_KEY")


def resolve_slack_token(team: Optional[str]) -> Optional[str]:
    """
    Resolve Slack token from table storage or env using naming convention:
      - SLACK_TOKEN_<TEAM> (preferred)
      - SLACK_TOKEN_DEFAULT (default)
    """
    team = sanitize_team(team)
    if team:
        key_name = f"SLACK_TOKEN_{team}".upper().replace("-", "_")
        tok = _get_setting(key_name)
        if tok:
            return tok
    return _get_setting("SLACK_TOKEN_DEFAULT")
