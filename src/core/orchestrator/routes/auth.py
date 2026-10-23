"""Authentication endpoints: local/Google login, registration and profile."""

import json
import logging
from datetime import datetime, timedelta, timezone

import azure.functions as func
from core.config import AUTH, DEFAULT_TEAM, TABLE_USERS
from utils import create_cors_response

from core import audit, operators, security, storage, team_access

bp = func.Blueprint()


@bp.route(
    route="auth/register",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def auth_register(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    body = req.get_json()
    try:
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

        table_client = storage.get_table_client(TABLE_USERS)

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
            "team": DEFAULT_TEAM,
        }

        table_client.create_entity(entity=entity)

        audit.log_audit(
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


@bp.route(
    route="auth/login",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def auth_login(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    body = req.get_json()
    try:
        username = body.get("username").lower()
        password = body.get("password")

        if not username or not password:
            return func.HttpResponse(
                json.dumps({"error": "Username and password required"}),
                status_code=400,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        table_client = storage.get_table_client(TABLE_USERS)

        user_entity = table_client.get_entity(
            partition_key="Operator",
            row_key=operators.resolve_user_row_key(table_client, body.get("username")),
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
                    logging.error(f"Bcrypt check failed: {e}")
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
                        logging.debug(f"User {username} password migrated to hash")
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

            audit.log_audit(
                user=user_entity.get("RowKey"),
                action="USER_LOGIN_SUCCESS",
                target=user_entity.get("email"),
                details=f"user: {user_entity.get('RowKey')}, email: {user_entity.get('email')}, role: {user_entity.get('role')}",
            )
            # Token expiration (e.g. 8 hours)
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

            # Create session token
            session_token = security.create_session_token(
                username=user_entity.get("RowKey"),
                role=user_entity.get("role"),
                expires_at=expires_at,
                team=team_access.entity_team(user_entity),
            )

            return func.HttpResponse(
                json.dumps(
                    {
                        "success": True,
                        "user": {
                            "username": user_entity.get("RowKey"),
                            "email": user_entity.get("email"),
                            "role": user_entity.get("role"),
                            "team": team_access.entity_team(user_entity),
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
        audit.log_audit(
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


@bp.route(
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

        table_client = storage.get_table_client(TABLE_USERS)

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
                "team": DEFAULT_TEAM,
            }
            table_client.create_entity(entity=user_entity)
            audit.log_audit(
                user=username,
                action="USER_PROVISIONED_SSO",
                target=email,
                details=f"Provider: Google, role: {user_entity.get('role')}",
            )

        if user_entity.get("role") == "PENDING":
            audit.log_audit(
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
        session_token = security.create_session_token(
            username=user_entity.get("RowKey"),
            role=user_entity.get("role"),
            expires_at=expires_at,
            team=team_access.entity_team(user_entity),
        )

        audit.log_audit(
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
                        "team": team_access.entity_team(user_entity),
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


@bp.route(
    route="auth/profile",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def auth_profile(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    username = session.get("username")
    from azure.data.tables import UpdateMode

    table_client = storage.get_table_client(TABLE_USERS)

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
                    "team": team_access.entity_team(user_entity),
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
            requested_team = body.get("team")

            if requested_team is not None and not team_access.is_admin(session):
                return func.HttpResponse(
                    json.dumps({"error": "Only administrators can assign teams"}),
                    status_code=403,
                    mimetype="application/json",
                )
            if requested_team is not None:
                user_entity["team"] = team_access.normalize_team(requested_team)

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

            audit.log_audit(
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
