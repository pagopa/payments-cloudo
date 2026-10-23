"""Team management endpoints."""

import json
import logging
from datetime import datetime, timezone

import azure.functions as func
from core.config import (
    AUTH,
    DEFAULT_TEAM,
    TABLE_SCHEDULES,
    TABLE_SCHEMAS,
    TABLE_TEAMS,
    TABLE_USERS,
)
from utils import as_bool, create_cors_response

from core import audit, security, storage, team_access

bp = func.Blueprint()


@bp.route(
    route="teams",
    methods=[
        func.HttpMethod.GET,
        func.HttpMethod.POST,
        func.HttpMethod.DELETE,
        func.HttpMethod.OPTIONS,
    ],
    auth_level=AUTH,
)
def teams_management(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if req.method in {"POST", "DELETE"} and not team_access.is_admin(session):
        return func.HttpResponse(
            json.dumps({"error": "Admin role required"}),
            status_code=403,
            mimetype="application/json",
        )

    table_client = storage.get_table_client(TABLE_TEAMS)

    if req.method == "GET":
        try:
            entities = list(
                table_client.query_entities(query_filter="PartitionKey eq 'Team'")
            )
            # ?for=routing lists every team so rules can notify other teams.
            if team_access.is_admin(session) or req.params.get("for") == "routing":
                visible = entities
            else:
                visible = [
                    entity
                    for entity in entities
                    if team_access.normalize_team(entity.get("RowKey"))
                    in {DEFAULT_TEAM, team_access.session_team(session)}
                ]
            teams = [
                {
                    "id": DEFAULT_TEAM,
                    "name": "default",
                    "description": "Shared resources",
                    "enabled": True,
                    "shared": True,
                }
            ]
            teams.extend(
                {
                    "id": team_access.normalize_team(entity.get("RowKey")),
                    "name": entity.get("name") or entity.get("RowKey"),
                    "description": entity.get("description") or "",
                    "enabled": as_bool(entity.get("enabled"), default=True),
                    "shared": False,
                }
                for entity in visible
                if team_access.normalize_team(entity.get("RowKey")) != DEFAULT_TEAM
            )
            return func.HttpResponse(
                json.dumps(teams, ensure_ascii=False),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            logging.error(f"Failed to list teams: {e}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch teams"}), status_code=500
            )

    if req.method == "POST":
        try:
            body = req.get_json()
            if not isinstance(body, dict):
                return func.HttpResponse(
                    json.dumps({"error": "Invalid request body"}), status_code=400
                )
            team_id = team_access.normalize_team(body.get("id") or body.get("name"))
            if team_id == DEFAULT_TEAM or not team_id:
                return func.HttpResponse(
                    json.dumps({"error": "The default team is reserved"}),
                    status_code=400,
                )
            entity = {
                "PartitionKey": "Team",
                "RowKey": team_id,
                "name": str(body.get("name") or team_id).strip(),
                "description": str(body.get("description") or "").strip(),
                "enabled": as_bool(body.get("enabled"), default=True),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            table_client.upsert_entity(entity=entity)
            audit.log_audit(
                user=session.get("username") or "SYSTEM",
                action="TEAM_UPSERT",
                target=team_id,
                details=f"Name: {entity['name']}",
            )
            return func.HttpResponse(
                json.dumps({"success": True, "id": team_id}), status_code=200
            )
        except Exception as e:
            logging.error(f"Failed to save team: {e}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to save team"}), status_code=500
            )

    if req.method == "DELETE":
        team_id = team_access.normalize_team(req.params.get("id"))
        if team_id == DEFAULT_TEAM:
            return func.HttpResponse(
                json.dumps({"error": "The default team cannot be deleted"}),
                status_code=400,
            )
        try:
            for table_name in (TABLE_USERS, TABLE_SCHEMAS, TABLE_SCHEDULES):
                rows = storage.get_table_client(table_name).query_entities(
                    query_filter=f"team eq '{team_id}'"
                )
                if next(iter(rows), None):
                    return func.HttpResponse(
                        json.dumps({"error": "Team is still assigned to resources"}),
                        status_code=409,
                    )
            table_client.delete_entity(partition_key="Team", row_key=team_id)
            audit.log_audit(
                user=session.get("username") or "SYSTEM",
                action="TEAM_DELETE",
                target=team_id,
            )
            return func.HttpResponse(json.dumps({"success": True}), status_code=200)
        except Exception as e:
            logging.error(f"Failed to delete team: {e}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to delete team"}), status_code=500
            )
