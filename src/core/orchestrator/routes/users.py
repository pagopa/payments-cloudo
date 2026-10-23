"""Operator (user) management endpoints."""

import json
from datetime import datetime, timezone

import azure.functions as func
from core.config import AUTH, DEFAULT_TEAM, TABLE_USERS
from utils import create_cors_response

from core import audit, operators, security, storage, team_access

bp = func.Blueprint()


@bp.route(
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

    if req.method in ["POST", "DELETE"] and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify users"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    from azure.data.tables import UpdateMode

    table_client = storage.get_table_client(TABLE_USERS)

    if req.method == "GET":
        try:
            entities = table_client.query_entities(
                query_filter="PartitionKey eq 'Operator'"
            )
            if not team_access.is_admin(session):
                entities = team_access.filter_entities_by_team(
                    list(entities), req, session
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
                        "team": team_access.entity_team(e),
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
            username = operators.resolve_user_row_key(table_client, username)

            # Check if user exists to preserve created_at
            try:
                existing_user = table_client.get_entity(
                    partition_key="Operator", row_key=username
                )
                if team_access.entity_team(existing_user) not in {
                    DEFAULT_TEAM,
                    team_access.session_team(session),
                } and not team_access.is_admin(session):
                    return func.HttpResponse(
                        json.dumps({"error": "User belongs to another team"}),
                        status_code=403,
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

            team, team_error = team_access.requested_write_team(
                body, session, existing_user if "existing_user" in locals() else None
            )
            if team_error:
                return func.HttpResponse(
                    json.dumps({"error": team_error}), status_code=403
                )

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
                "team": team,
            }
            table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

            # Audit log
            audit.log_audit(
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
            username = operators.resolve_user_row_key(table_client, username)
            existing_user = table_client.get_entity(
                partition_key="Operator", row_key=username
            )
            if team_access.entity_team(existing_user) not in {
                DEFAULT_TEAM,
                team_access.session_team(session),
            } and not team_access.is_admin(session):
                return func.HttpResponse(
                    json.dumps({"error": "User belongs to another team"}),
                    status_code=403,
                )
            table_client.delete_entity(partition_key="Operator", row_key=username)

            # Audit log
            audit.log_audit(
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
