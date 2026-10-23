"""Global and per-team settings endpoints."""

import json
import logging

import azure.functions as func
from core.config import AUTH, DEFAULT_TEAM, TABLE_SETTINGS, TABLE_TEAMS
from utils import as_bool, create_cors_response

from core import audit, security, storage, team_access

bp = func.Blueprint()


def _build_operator_settings_update(
    table_client, body: dict, team: str, known_teams: set[str]
) -> dict[str, str]:
    """Reduce an operator settings POST to what their own team may change."""
    suffix = team.upper().replace("-", "_")
    allowed = {
        f"SLACK_TOKEN_{suffix}",
        f"SLACK_CHANNEL_{suffix}",
        f"JSM_API_KEY_{suffix}",
        f"JSM_TEAM_{suffix}",
    }
    updates = {k: str(v) for k, v in body.items() if k in allowed}
    if "ROUTING_RULES" not in body:
        return updates

    submitted = json.loads(body["ROUTING_RULES"] or "{}")
    try:
        stored = json.loads(
            table_client.get_entity(
                partition_key="GlobalConfig", row_key="ROUTING_RULES"
            ).get("value")
            or "{}"
        )
    except Exception:
        stored = {}

    stored_teams = stored.setdefault("teams", {})
    stored_team = dict(stored_teams.get(team) or {})
    submitted_team = (submitted.get("teams") or {}).get(team) or {}
    for section, field in (("slack", "channel"), ("jsm", "team")):
        value = (submitted_team.get(section) or {}).get(field)
        if value is not None:
            stored_team[section] = {
                **(stored_team.get(section) or {}),
                field: str(value).strip(),
            }
    stored_teams[team] = stored_team
    channel = (stored_team.get("slack") or {}).get("channel")
    if channel is not None:
        updates[f"SLACK_CHANNEL_{suffix}"] = channel

    own_rules = []
    for rule in submitted.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        then = []
        for action in rule.get("then") or []:
            if not isinstance(action, dict) or action.get("type") not in (
                "slack",
                "jsm",
            ):
                continue
            # Targets may notify other known teams; no inline secrets.
            target_team = team_access.normalize_team(action.get("team"))
            entry = {
                "type": action["type"],
                "team": target_team if target_team in known_teams else team,
            }
            if action["type"] == "slack" and action.get("channel"):
                entry["channel"] = str(action["channel"]).strip()
            then.append(entry)
        own_rules.append({"team": team, "when": rule.get("when") or {}, "then": then})
    stored["rules"] = [
        rule
        for rule in stored.get("rules") or []
        if team_access.normalize_team(rule.get("team")) != team
    ] + own_rules
    updates["ROUTING_RULES"] = json.dumps(stored, ensure_ascii=False)
    return updates


@bp.route(
    route="settings",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def settings_management(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    table_client = storage.get_table_client(TABLE_SETTINGS)

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

    operator_team = team_access.session_team(session)
    if req.method == "POST" and session.get("role") != "ADMIN":
        # Operators may only edit the routing of their own non-default team.
        if session.get("role") != "OPERATOR" or operator_team == "default":
            return func.HttpResponse(
                json.dumps(
                    {"error": "Unauthorized: Admin role required to modify settings"}
                ),
                status_code=403,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if req.method == "GET":
        try:
            entities = table_client.query_entities(
                query_filter="PartitionKey eq 'GlobalConfig'"
            )
            is_admin = session.get("role") == "ADMIN"
            current_team = team_access.session_team(session)
            settings: dict[str, str] = {}
            for entity in entities:
                key = str(entity.get("RowKey") or "")
                if is_admin:
                    settings[key] = entity.get("value", "")
                    continue
                if key == "ROUTING_RULES":
                    try:
                        routing_config = json.loads(entity.get("value") or "{}")
                        routing_config["teams"] = {
                            current_team: (routing_config.get("teams") or {}).get(
                                current_team, {}
                            )
                        }
                        routing_config["rules"] = [
                            rule
                            for rule in routing_config.get("rules", [])
                            if team_access.normalize_team(rule.get("team"))
                            == current_team
                        ]
                        settings[key] = json.dumps(routing_config, ensure_ascii=False)
                    except Exception:
                        settings[key] = json.dumps({"teams": {}, "rules": []})
                    continue
                normalized_key = key.upper()
                allowed_prefixes = {
                    f"SLACK_CHANNEL_{current_team.upper().replace('-', '_')}",
                    f"JSM_TEAM_{current_team.upper().replace('-', '_')}",
                }
                if normalized_key in allowed_prefixes:
                    settings[key] = entity.get("value", "")
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
            if session.get("role") != "ADMIN":
                known_teams = {DEFAULT_TEAM, operator_team}
                try:
                    known_teams.update(
                        team_access.normalize_team(entity.get("RowKey"))
                        for entity in storage.get_table_client(
                            TABLE_TEAMS
                        ).query_entities(query_filter="PartitionKey eq 'Team'")
                        if as_bool(entity.get("enabled"), default=True)
                    )
                except Exception as e:
                    logging.warning(f"Could not load teams for routing update: {e}")
                body = _build_operator_settings_update(
                    table_client, body, operator_team, known_teams
                )
            for key, value in body.items():
                entity = {
                    "PartitionKey": "GlobalConfig",
                    "RowKey": key,
                    "value": str(value),
                }
                table_client.upsert_entity(entity=entity)

            try:
                import smart_routing

                smart_routing.invalidate_caches()
            except Exception:
                pass

            audit.log_audit(
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
