"""Team-based visibility and write permissions for stored entities."""

from typing import Any, Optional

import azure.functions as func
from core.config import DEFAULT_TEAM, TABLE_TEAMS
from utils import as_bool

from core import storage


def normalize_team(value: Any) -> str:
    team = str(value or DEFAULT_TEAM).strip().lower()
    return team or DEFAULT_TEAM


def session_team(session: Optional[dict]) -> str:
    return normalize_team((session or {}).get("team"))


def is_admin(session: Optional[dict]) -> bool:
    return str((session or {}).get("role") or "").upper() == "ADMIN"


def can_view_all_teams(req: func.HttpRequest, session: Optional[dict]) -> bool:
    raw = req.params.get("includeAllTeams") or req.params.get("include_all_teams")
    return is_admin(session) and as_bool(raw, default=False)


def entity_team(entity: dict) -> str:
    return normalize_team(entity.get("team", entity.get("Team")))


def filter_entities_by_team(
    entities: list[dict], req: func.HttpRequest, session: Optional[dict]
) -> list[dict]:
    if can_view_all_teams(req, session):
        return entities
    allowed = {DEFAULT_TEAM, session_team(session)}
    return [entity for entity in entities if entity_team(entity) in allowed]


def can_modify_entity(entity: dict, session: Optional[dict]) -> bool:
    """Admins can change anything; others only resources owned by their own team."""
    return is_admin(session) or entity_team(entity) == session_team(session)


def requested_write_team(
    body: dict, session: Optional[dict], existing: Optional[dict] = None
) -> tuple[Optional[str], Optional[str]]:
    requested = normalize_team(
        body.get("team") if "team" in body else (existing or {}).get("team")
    )
    if not is_admin(session):
        own_team = session_team(session)
        if requested not in {DEFAULT_TEAM, own_team}:
            return None, "Only administrators can assign resources to another team"
        requested = entity_team(existing) if existing else own_team
        if requested != own_team:
            return None, "Resource belongs to another team"
    if requested != DEFAULT_TEAM:
        try:
            team_entity = storage.get_table_client(TABLE_TEAMS).get_entity(
                partition_key="Team", row_key=requested
            )
            if not as_bool(team_entity.get("enabled"), default=True):
                return None, "Team is disabled"
        except Exception:
            return None, "Team does not exist"
    return requested, None
