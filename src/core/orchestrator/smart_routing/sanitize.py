"""Validation of identifiers coming from rules and runtime payloads."""

import logging
import re
from typing import Any, Optional

SAFE_TEAM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_CHANNEL_RE = re.compile(r"^#[A-Za-z0-9._-]{1,80}$")
SAFE_STATUS_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def sanitize_team(team: Optional[str]) -> Optional[str]:
    if team is None:
        return None
    t = str(team).strip()
    if not t:
        return None
    if SAFE_TEAM_RE.fullmatch(t):
        return t
    logging.warning("Rejected unsafe team identifier")
    return None


def sanitize_channel(channel: Optional[str]) -> Optional[str]:
    if channel is None:
        return None
    c = str(channel).strip()
    if not c:
        return None
    if SAFE_CHANNEL_RE.fullmatch(c):
        return c
    logging.warning("Rejected unsafe Slack channel format")
    return None


def safe_for_log(value: Any, max_len: int = 128) -> str:
    # Remove control chars/newlines to reduce log injection surface.
    txt = str(value or "")
    txt = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else "?" for ch in txt)
    return txt[:max_len]
