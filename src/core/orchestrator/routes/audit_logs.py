"""Audit log query endpoint."""

import json
from datetime import datetime, timedelta, timezone

import azure.functions as func
from core.config import AUTH, DEFAULT_TEAM, TABLE_AUDIT, TABLE_USERS
from utils import create_cors_response

from core import security, storage, team_access

bp = func.Blueprint()


@bp.route(
    route="audit",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_audit_logs(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    table_client = storage.get_table_client(TABLE_AUDIT)

    # Verification of admin role
    session, error_res = security.get_authenticated_user(req)
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
        days_raw = req.params.get("days")
        try:
            days = min(max(int(days_raw or 30), 1), 180)
        except ValueError:
            days = 30

        logs = []
        # Legacy rows have no team: fall back to the operator's current team.
        user_teams = {
            str(u.get("RowKey") or ""): team_access.entity_team(u)
            for u in storage.get_table_client(TABLE_USERS).query_entities(
                query_filter="PartitionKey eq 'Operator'"
            )
        }
        see_all = team_access.is_admin(session)
        allowed_teams = {DEFAULT_TEAM, team_access.session_team(session)}
        now = datetime.now(timezone.utc)
        for day_offset in range(days):
            pk = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
            entities = table_client.query_entities(
                query_filter=f"PartitionKey eq '{pk}'",
                select=["timestamp", "operator", "action", "target", "details", "team"],
            )
            for e in entities:
                operator = str(e.get("operator") or "")
                row_team = team_access.normalize_team(
                    e.get("team")
                    or user_teams.get(operator.removesuffix("-api"))
                    or DEFAULT_TEAM
                )
                if not see_all and row_team not in allowed_teams:
                    continue
                logs.append(
                    {
                        "timestamp": e.get("timestamp"),
                        "team": row_team,
                        "operator": e.get("operator"),
                        "action": e.get("action"),
                        "target": e.get("target"),
                        "details": e.get("details"),
                    }
                )
            if limit and len(logs) >= limit:
                break

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
