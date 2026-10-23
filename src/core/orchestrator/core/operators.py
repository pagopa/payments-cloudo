"""Operator (user) lookups shared by auth, admin and audit."""

import time
from typing import Any

from core.config import DEFAULT_TEAM, TABLE_USERS

from core import storage, team_access

_user_team_cache: dict[str, tuple[str, float]] = {}


def resolve_user_row_key(table_client, username: Any) -> str:
    """Login lowercases usernames: reuse an existing row (even mixed-case), else lowercase."""
    raw = str(username or "").strip()
    for candidate in dict.fromkeys([raw.lower(), raw]):
        try:
            table_client.get_entity(partition_key="Operator", row_key=candidate)
            return candidate
        except Exception:
            continue
    return raw.lower()


def team_of_user(username: Any) -> str:
    """Team of an operator (API users are stored as `<name>-api`); default if unknown."""
    name = str(username or "").strip()
    if name.endswith("-api"):
        name = name[: -len("-api")]
    if not name:
        return DEFAULT_TEAM
    cached = _user_team_cache.get(name)
    if cached and cached[1] > time.time():
        return cached[0]
    team = DEFAULT_TEAM
    try:
        entity = storage.get_table_client(TABLE_USERS).get_entity(
            partition_key="Operator", row_key=name
        )
        team = team_access.entity_team(entity)
    except Exception:
        pass
    _user_team_cache[name] = (team, time.time() + 60)
    return team
