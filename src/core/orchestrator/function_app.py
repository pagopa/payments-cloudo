import base64
import json
import logging
import os
import types
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import azure.functions as func
from core.config import (
    AI_ANALYSIS_QUEUE_NAME,
    APPROVAL_TTL_MIN,
    AUTH,
    DEFAULT_TEAM,
    GITHUB_BRANCH,
    GITHUB_PATH_PREFIX,
    GITHUB_REPO,
    GITHUB_TOKEN,
    MAX_TABLE_CHARS,
    NOTIFICATION_QUEUE_NAME,
    STORAGE_CONN,
    STORAGE_CONNECTION,
    TABLE_AI_ANALYSIS,
    TABLE_NAME,
    TABLE_SCHEDULES,
    TABLE_SCHEMAS,
    TABLE_TEAMS,
    TABLE_WORKERS_SCHEMAS,
)
from models import Schema
from routes import audit_logs as audit_logs_routes
from routes import auth as auth_routes
from routes import settings as settings_routes
from routes import teams as teams_routes
from routes import users as users_routes
from utils import as_bool, create_cors_response

from core import audit, executions, log_blobs, notify, security, storage, team_access

app = func.FunctionApp()
app.register_functions(auth_routes.bp)
app.register_functions(teams_routes.bp)
app.register_functions(users_routes.bp)
app.register_functions(settings_routes.bp)
app.register_functions(audit_logs_routes.bp)

_NOTIFICATION_QUEUE_READY = False


def _get_entity_id(e: dict) -> str:
    return str(e.get("Id") or e.get("id") or "").strip()


def _find_schema_entity(rows: list[dict], candidate_ids: list[str]) -> Optional[dict]:
    if not rows or not candidate_ids:
        return None

    if len(candidate_ids) == 1:
        target = candidate_ids[0]
        for e in rows:
            if _get_entity_id(e) == target:
                return e
        return None

    entities_by_id: dict[str, dict] = {}
    for e in rows:
        sid = _get_entity_id(e)
        if sid and sid not in entities_by_id:
            entities_by_id[sid] = e

    for sid in candidate_ids:
        if sid in entities_by_id:
            return entities_by_id[sid]
    return None


def _team_approval_targets(
    team: Any, slack_token: Any, slack_channel: Any, jsm_token: Any
) -> tuple[Any, Any, Any]:
    """Prefer the resource team's own Slack/JSM config for approval requests."""
    team = team_access.normalize_team(team)
    if team == DEFAULT_TEAM:
        return slack_token, slack_channel, jsm_token
    try:
        from smart_routing import (
            get_setting,
            load_routing_config,
            resolve_jsm_apikey,
            resolve_slack_token,
        )

        team_cfg = (load_routing_config().get("teams") or {}).get(team) or {}
        channel = (team_cfg.get("slack") or {}).get("channel") or get_setting(
            f"SLACK_CHANNEL_{team.upper().replace('-', '_')}"
        )
        return (
            resolve_slack_token(team) or slack_token,
            channel or slack_channel,
            resolve_jsm_apikey(team) or jsm_token,
        )
    except Exception as e:
        logging.warning(f"Team approval targets unavailable for '{team}': {e}")
        return slack_token, slack_channel, jsm_token


def build_response_body(
    status_code: int,
    schema: "Schema",
    partition_key: str,
    exec_id: str,
    api_json: Optional[Union[dict, str]],
) -> str:
    # Build the HTTP response payload returned by this function
    return json.dumps(
        {
            "status": status_code,
            "schema": {
                "id": schema.id,
                "name": schema.name,
                "description": schema.description,
                "oncall": schema.oncall,
                "runbook": schema.runbook,
                "run_args": schema.run_args,
                "worker": schema.worker,
                "group": schema.group,
                "monitor_condition": schema.monitor_condition,
                "severity": schema.severity,
                "team": schema.team,
            },
            "response": api_json,
            "log": {"partitionKey": partition_key, "exec_id": exec_id},
        },
        ensure_ascii=False,
    )


# =========================
# HTTP Function: Trigger
# =========================


@app.route(
    route="Trigger/{team?}",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="entities",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def Trigger(
    req: func.HttpRequest,
    log_table: func.Out[str],
    entities: str,
    workers: str,
) -> func.HttpResponse:
    global _NOTIFICATION_QUEUE_READY
    import detection
    import utils
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
    from escalation import format_jsm_description, send_jsm_alert, send_slack_execution
    from worker_routing import worker_routing

    try:
        q_client = storage.get_queue_client(
            NOTIFICATION_QUEUE_NAME, conn_env=STORAGE_CONNECTION
        )
        if not _NOTIFICATION_QUEUE_READY:
            try:
                q_client.create_queue()
            except ResourceExistsError:
                _NOTIFICATION_QUEUE_READY = True
            except Exception as e:
                logging.warning(
                    "Failed to ensure notification queue '%s' exists: %s",
                    NOTIFICATION_QUEUE_NAME,
                    e,
                )
            else:
                _NOTIFICATION_QUEUE_READY = True
    except Exception as e:
        logging.error(f"Failed to initialize queue client: {e}")
        return func.HttpResponse("Failed to initialize queue client", status_code=500)

    if req.method == "OPTIONS":
        return create_cors_response()

    try:
        from smart_routing import (
            execute_actions,
            resolve_jsm_apikey,
            resolve_slack_token,
            route_alert,
        )
    except ImportError:
        route_alert = None
        execute_actions = None

        def resolve_slack_token(_):
            return None

        def resolve_jsm_apikey(_):
            return None

    def _json_response(payload: dict, status_code: int) -> func.HttpResponse:
        return func.HttpResponse(
            json.dumps(payload, ensure_ascii=False),
            status_code=status_code,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    def _send_status_to_receiver(
        payload_for_status: dict, status: str, log_msg: str
    ) -> None:
        try:
            q_client.send_message(
                executions.post_status(
                    payload_for_status, status=status, log_message=log_msg
                )
            )
        except ResourceNotFoundError as e:
            logging.warning(
                "%s Notification queue '%s' not found, skipping status post: %s",
                log_prefix,
                NOTIFICATION_QUEUE_NAME,
                e,
            )

    # Init payload variables to None
    resource_name = resource_group = resource_id = schema_id = monitor_condition = (
        severity
    ) = ""
    route_params = getattr(req, "route_params", {}) or {}

    # Pre-compute logging fields
    requested_at = utils.format_requested_at()
    partition_key = utils.today_partition_key()
    exec_id = str(uuid.uuid4())

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res
    requester_username = session.get("username")
    log_prefix = executions.build_exec_log_prefix(exec_id, requester_username)

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot trigger executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    token, channel = notify.get_default_notification_settings()

    # Resolve alert fields from payload, then optionally override schema_id from route/query.
    parsed_body = detection.parse_resource_fields(req)

    resource_name = parsed_body.get("resource_name") or ""
    resource_group = parsed_body.get("resource_group") or ""
    resource_id = parsed_body.get("resource_id") or ""
    monitor_condition = parsed_body.get("monitorCondition") or ""
    severity = parsed_body.get("severity") or ""

    schema_id = parsed_body.get("schema_id")
    if (req.params.get("id")) is not None:
        schema_id = detection.extract_schema_id_from_req(req) or schema_id
    if not isinstance(schema_id, list):
        schema_id = [str(schema_id)] if schema_id else []
    primary_schema_id = str(schema_id[0]).strip() if schema_id else ""

    resource_info = {
        "_raw": parsed_body.get("_raw"),
        "schema_id": schema_id,
        "monitor_condition": monitor_condition,
        "severity": severity,
        "resource_name": resource_name,
        "resource_rg": resource_group,
        "resource_id": resource_id,
        "aks_namespace": parsed_body.get("namespace"),
        "aks_pod": parsed_body.get("pod"),
        "aks_deployment": parsed_body.get("deployment"),
        "aks_job": parsed_body.get("job"),
        "aks_horizontalpodautoscaler": parsed_body.get("horizontalpodautoscaler"),
        "team": route_params.get("team"),
        "payload": parsed_body.get("payload"),
    }

    routing_info = {
        "team": route_params.get("team") or "default",
        "slack_token": req.params.get("slack_token")
        or resolve_slack_token(route_params.get("team") or "default")
        or token,
        "slack_channel": req.params.get("slack_channel")
        or channel
        or (os.environ.get("SLACK_CHANNEL") or "#cloudo-test").strip(),
        "jsm_token": req.params.get("jsm_api_key")
        or req.params.get("opsgenie_api_key")
        or resolve_jsm_apikey(route_params.get("team") or "default"),
    }
    logging.debug(f"{log_prefix} Resource info: %s", resource_info)

    # Parse bound table entities (binding returns a JSON array)
    parsed = storage.rows_from_binding(entities)

    if parsed is None:
        return func.HttpResponse(
            json.dumps({"error": "Unexpected table result format"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    schema_entity = _find_schema_entity(parsed, schema_id)
    if schema_entity and not team_access.can_view_all_teams(req, session):
        if team_access.entity_team(schema_entity) not in {
            DEFAULT_TEAM,
            team_access.session_team(session),
        }:
            return _json_response(
                {"error": "Schema belongs to another team"}, status_code=403
            )

    if not schema_entity:
        if monitor_condition and severity:
            log_msg = "ALARM -> ROUTED (No runbook found)"
            payload_for_status = {
                "requestedAt": requested_at,
                "id": primary_schema_id,
                "name": resource_name or "",
                "exec_id": exec_id,
                "runbook": "alarm routed",
                "run_args": "None",
                "worker": "No worker",
                "group": "-",
                "oncall": True,
                "initiator": requester_username,
                "monitor_condition": monitor_condition or "",
                "severity": severity or "",
                "resource_info": resource_info,
                "routing_info": routing_info,
            }
            _send_status_to_receiver(
                payload_for_status, status="routed", log_msg=log_msg
            )
            return _json_response(
                {
                    "routed": (
                        "Alarm detected.\n "
                        "(This alert has not a runbook to be executed) -> ROUTED"
                    )
                },
                status_code=200,
            )
        else:
            logging.warning(
                f"No alert detected for {schema_id}: {monitor_condition} - {severity}"
            )
            return _json_response(
                {"ignored": f"No alert detected for {schema_id}"},
                status_code=204,
            )

    logging.debug(
        f"{log_prefix} Getting schema entity id '{_get_entity_id(schema_entity)}'"
    )
    # Build domain model
    schema = Schema(
        id=_get_entity_id(schema_entity),
        entity=schema_entity,
        monitor_condition=monitor_condition,
        severity=severity,
    )

    if not schema.enabled:
        log_msg = f"RUNBOOK DISABLED -> ROUTED ({schema.id})"
        payload_for_status = {
            "requestedAt": requested_at,
            "id": schema.id,
            "name": schema.name or resource_name or "",
            "exec_id": exec_id,
            "runbook": schema.runbook or "alarm routed",
            "run_args": schema.run_args,
            "worker": schema.worker,
            "group": schema.group,
            "oncall": schema.oncall,
            "initiator": requester_username,
            "monitor_condition": monitor_condition or "",
            "severity": severity or "",
            "resource_info": resource_info,
            "routing_info": routing_info,
        }
        _send_status_to_receiver(payload_for_status, status="routed", log_msg=log_msg)
        return _json_response(
            {
                "routed": (
                    f"Runbook {schema.id} is disabled.\n "
                    "(This execution has been ROUTED)"
                )
            },
            status_code=200,
        )

    try:
        # Approval-required path: create pending with signed URL embedding resource_info and function key
        if schema.require_approval:
            expires_at = (
                (datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_TTL_MIN))
                .isoformat()
                .replace(" ", "")
            )
            # function key to pass along (from header or query)
            func_key = (
                req.headers.get("x-functions-key") or req.params.get("code") or ""
            )
            # Build payload
            payload = {
                "execId": exec_id,
                "schemaId": schema.id,
                "exp": expires_at,
                "initiator": requester_username,
                "resource_info": resource_info or {},
                "routing_info": routing_info or {},
                "code": func_key or "",
                "monitorCondition": monitor_condition,
                "severity": severity,
                "team": schema.team,
                "worker": schema.worker,
                "group": schema.group,
            }
            payload_b64 = security.b64url_encode(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            sig = security.sign_payload_b64(payload_b64)

            base_env = os.getenv("ORCHESTRATOR_BASE_URL")
            if not base_env:
                hostname = os.getenv("WEBSITE_HOSTNAME", "localhost:7071")
                scheme = "https" if "localhost" not in hostname else "http"
                base = f"{scheme}://{hostname}"
            else:
                base = base_env.rstrip("/")
            approve_url = f"{base}/api/approvals/{partition_key}/{exec_id}/approve?p={payload_b64}&s={sig}&code={func_key}"
            reject_url = f"{base}/api/approvals/{partition_key}/{exec_id}/reject?p={payload_b64}&s={sig}&code={func_key}"

            pending_log_payload = {
                "message": "Awaiting approval",
                "approve": approve_url,
                "reject": reject_url,
                "resource_info": resource_info,
            }

            if requester_username:
                audit.log_audit(
                    user=requester_username,
                    action="RUNBOOK_GATE_SCHEDULE",
                    target=exec_id,
                    details=f"ID: {schema.id}, Runbook: {schema.runbook}, Args: {schema.run_args}",
                )

            # Optional Slack notify
            slack_token = routing_info.get("slack_token")
            slack_channel = routing_info.get("slack_channel")
            jsm_token = (
                routing_info.get("jsm_token")
                or routing_info.get("opsgenie_token")
                or resolve_jsm_apikey(routing_info.get("team"))
            )
            slack_token, slack_channel, jsm_token = _team_approval_targets(
                schema.team, slack_token, slack_channel, jsm_token
            )
            notification_warnings: list[str] = []
            approval_notifications: list[dict[str, Any]] = []
            approval_team = team_access.normalize_team(schema.team)

            # UI Base URL
            ui_base = (
                (os.getenv("NEXTJS_URL") or "http://localhost:3000").strip().rstrip("/")
            )
            if not ui_base.startswith("http"):
                ui_base = f"https://{ui_base}" if ui_base else "http://localhost:3000"
            ui_url = (
                f"{ui_base}/executions?execId={exec_id}&partitionKey={partition_key}"
            )

            if slack_token and slack_channel:
                try:
                    # Truncate description and compact resource info to avoid Slack limits
                    description_truncated = (
                        schema.description or "No description provided."
                    ).strip()
                    if len(description_truncated) > 400:
                        description_truncated = description_truncated[:400] + "..."

                    resource_info_compact = (
                        notify.format_compact_resource_info(resource_info) or ""
                    )
                    if len(resource_info_compact) > 600:
                        resource_info_compact = resource_info_compact[:600] + "..."

                    # Truncate arguments for better Slack display
                    args_truncated = (schema.run_args or "").strip()
                    if len(args_truncated) > 800:
                        args_truncated = args_truncated[:800] + "\n... (truncated)"

                    slack_sent = send_slack_execution(
                        token=slack_token,
                        channel=slack_channel,
                        message=f"[{exec_id}] ⚠️ APPROVAL REQUIRED: {schema.name}",
                        blocks=[
                            {
                                "type": "header",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Gate Approval Required ⚠️",
                                    "emoji": True,
                                },
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"<!here> *{schema.name}* is requesting permission to execute a restricted runbook.\n"
                                        f"> *Description:* {description_truncated}"
                                    ),
                                },
                            },
                            {
                                "type": "section",
                                "fields": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*SchemaId:* `{schema.id}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Severity:* `{severity or '-'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Runbook:* `{schema.runbook or '-'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Worker:* `{schema.worker or 'unknown'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Group:* `{schema.group}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Team:* `{schema.team}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Initiator:* `{requester_username or 'SYSTEM'}`",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*On Call:* `{schema.oncall}`",
                                    },
                                ],
                            },
                            *(
                                [
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"*Resource Context:*\n{resource_info_compact}",
                                        },
                                    }
                                ]
                                if resource_info_compact
                                else []
                            ),
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Arguments:*\n```{(args_truncated or 'None')}```",
                                },
                            },
                            {
                                "type": "actions",
                                "elements": [
                                    {
                                        "type": "button",
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Full Context 🔍",
                                            "emoji": True,
                                        },
                                        "url": ui_url,
                                    },
                                ],
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"ExecId: `{exec_id}` | Requested: <!date^{int(datetime.now(timezone.utc).timestamp())}^{{date_short}} {{time}}|now>",
                                    }
                                ],
                            },
                        ],
                    )
                    approval_notifications.append(
                        {
                            "type": "slack",
                            "team": approval_team,
                            "channel": slack_channel,
                            "status": "sent" if slack_sent else "failed",
                        }
                    )
                except Exception as e:
                    approval_notifications.append(
                        {
                            "type": "slack",
                            "team": approval_team,
                            "channel": slack_channel,
                            "status": "failed",
                            "error": str(e)[:200],
                        }
                    )
                    msg = f"Slack approval notify failed: {e}"
                    logging.debug(f"[{exec_id}] {msg}")
                    notification_warnings.append(msg)
            else:
                msg = "Slack approval notify skipped: missing token or channel"
                logging.debug(f"[{exec_id}] {msg}")
                notification_warnings.append(msg)

            if jsm_token and as_bool(schema.oncall):
                try:
                    jsm_message = f"[{exec_id}] ⚠️ APPROVAL REQUIRED: {schema.name}"
                    jsm_description = (
                        f"{schema.name} is requesting permission to execute a restricted runbook.\n\n"
                        f"Description: {schema.description or 'No description provided.'}\n"
                        f"SchemaId: {schema.id}\n"
                        f"Severity: {severity or '-'}\n"
                        f"Runbook: {schema.runbook or '-'}\n"
                        f"Worker: {schema.worker or 'unknown'}\n"
                        f"Group: {schema.group}\n"
                        f"Team: {schema.team}\n"
                        f"Initiator: {requester_username or 'SYSTEM'}\n"
                        f"On Call: {schema.oncall}\n\n"
                        f"Full Context: {ui_url}"
                    )
                    jsm_sent = send_jsm_alert(
                        api_key=jsm_token,
                        message=jsm_message,
                        description=jsm_description,
                        priority="P2",
                        alias=exec_id,
                        tags=["approval-required", schema.group or "cloudo"],
                        details={
                            "execId": exec_id,
                            "schemaId": schema.id,
                            "team": schema.team,
                            "runbook": schema.runbook or "-",
                            "initiator": requester_username or "SYSTEM",
                        },
                    )
                    approval_notifications.append(
                        {
                            "type": "jsm",
                            "team": approval_team,
                            "status": "sent" if jsm_sent else "failed",
                        }
                    )
                except Exception as e:
                    approval_notifications.append(
                        {
                            "type": "jsm",
                            "team": approval_team,
                            "status": "failed",
                            "error": str(e)[:200],
                        }
                    )
                    msg = f"JSM approval notify failed: {e}"
                    logging.debug(f"[{exec_id}] {msg}")
                    notification_warnings.append(msg)
            elif not as_bool(schema.oncall):
                logging.debug(f"[{exec_id}] JSM approval notify skipped: not on-call")
            else:
                msg = "JSM approval notify skipped: missing token"
                logging.debug(f"[{exec_id}] {msg}")
                notification_warnings.append(msg)

            if notification_warnings:
                pending_log_payload["notification_status"] = "warning"
                pending_log_payload["notification_warnings"] = notification_warnings

            pending_log = executions.build_log_entry(
                status="pending",
                partition_key=partition_key,
                exec_id=exec_id,
                row_key=exec_id,
                requested_at=requested_at,
                name=schema.name or "",
                schema_id=schema.id,
                runbook=schema.runbook,
                run_args=schema.run_args,
                worker=schema.worker,
                group=schema.group,
                log_msg=json.dumps(pending_log_payload, ensure_ascii=False),
                oncall=schema.oncall,
                initiator=requester_username,
                resource_info=resource_info,
                monitor_condition=monitor_condition,
                severity=severity,
                team=schema.team,
                approval_required=True,
                approval_expires_at=expires_at,
                notifications=executions.notifications_json(
                    types.SimpleNamespace(
                        reason="approval_request", matched_rule_index=None
                    ),
                    approval_notifications,
                ),
            )
            log_table.set(json.dumps(pending_log, ensure_ascii=False))

            body = json.dumps(
                {
                    "status": 202,
                    "message": "Job is pending approval",
                    "exec_id": exec_id,
                    "approve": approve_url,
                    "reject": reject_url,
                    "expires_at (UTC)": expires_at,
                    "notification_status": (
                        "warning" if notification_warnings else "ok"
                    ),
                    "notification_warnings": notification_warnings,
                },
                ensure_ascii=False,
            )
            return func.HttpResponse(
                body,
                status_code=202,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

        # ---------------------------------------------------------
        # DYNAMIC WORKER SELECTION (Binding Version)
        # ---------------------------------------------------------
        target_queue = worker_routing(workers, schema)

        api_body = {}
        status_code = 202

        if target_queue:
            logging.debug(
                f"{log_prefix} 🎯 Dynamic Routing: Selected Queue '{target_queue}'"
            )

            try:
                # Construct the payload (formerly HTTP headers)
                queue_payload = {
                    "runbook": schema.runbook,
                    "run_args": schema.run_args,
                    "id": schema.id,
                    "name": schema.name or "",
                    "requestedAt": requested_at,
                    "exec_id": exec_id,
                    "oncall": schema.oncall,
                    "aks_login_required": schema.aks_login_required,
                    "aks_cluster_name": schema.aks_cluster_name,
                    "aks_resource_group": schema.aks_resource_group,
                    "aks_namespace": schema.aks_namespace,
                    "monitor_condition": monitor_condition,
                    "severity": severity,
                    "team": schema.team,
                    "worker": schema.worker,
                    "group": schema.group,
                    "initiator": requester_username,
                    "resource_info": resource_info or {},
                    "routing_info": routing_info or {},
                }

                storage.enqueue_queue_payload(target_queue, queue_payload)

                api_body = {"status": "accepted", "queue": target_queue}

                audit.log_audit(
                    user=requester_username,
                    action="RUNBOOK_EXECUTE",
                    target=exec_id,
                    details=f"ID: {schema.id}, Runbook: {schema.runbook}, Args: {schema.run_args}",
                )

            except Exception as e:
                logging.error(f"{log_prefix} ❌ Queue send failed: {e}")
                status_code = 500
                api_body = {"error": str(e)}
        else:
            err_msg = f"❌ No workers ({schema.worker}) available and no static queue configured for {schema.id}"
            logging.error(f"{log_prefix} {err_msg}")
            status_code = 500
            api_body = {"error": err_msg}
        # ---------------------------------------------------------

        # Status label for logs
        status_label = "accepted" if status_code == 202 else "error"

        # Write log entry to the table
        start_log = executions.build_log_entry(
            status=status_label,
            partition_key=partition_key,
            exec_id=exec_id,
            row_key=exec_id,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=api_body,
            oncall=schema.oncall,
            initiator=requester_username,
            monitor_condition=monitor_condition,
            severity=severity,
            team=schema.team,
            resource_info=resource_info,
        )
        log_table.set(json.dumps(start_log, ensure_ascii=False))

        # smart routing notification (if routing module available)
        if status_label != "accepted":
            if route_alert and execute_actions:
                ctx = {
                    "resourceId": resource_id,
                    "resourceGroup": resource_group,
                    "resourceName": resource_name,
                    "schemaName": (schema.name or ""),
                    "severity": severity,
                    "namespace": ((resource_info or {}).get("namespace") or ""),
                    "oncall": schema.oncall,
                    "status": status_label,
                    "execId": exec_id,
                    "name": schema.name or "",
                    "id": schema.id,
                    "team": schema.team,
                    "routing_info": routing_info,
                }
                decision = route_alert(ctx)
                logging.debug(f"{log_prefix} {decision}")
                status_emoji = "✅" if status_label == "succeeded" else "❌"
                payload = {
                    "slack": {
                        "message": f"[{exec_id}] Status: {status_label}: {schema.name or ''}",
                        "blocks": [
                            {
                                "type": "header",
                                "text": {
                                    "type": "plain_text",
                                    "text": f"{status_emoji}\t{schema.name or ''}\texecution",
                                    "emoji": True,
                                },
                            },
                            {
                                "type": "section",
                                "fields": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Name:*\n{schema.name or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Id:*\n{schema.id or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Group:*\n{schema.group}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Team:*\n{schema.team}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*ExecId:*\n{exec_id or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Status:*\n{status_label}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Severity:*\n{severity or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*OnCall:*\n{schema.oncall or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Origin*:\n{schema.worker or 'unknown(?)'}",
                                    },
                                ],
                            },
                            {
                                "type": "section",
                                "fields": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Runbook:*\n{schema.runbook or ''}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*MonitorCondition:*\n{schema.monitor_condition or ''}",
                                    },
                                ],
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Run Args:*\n```{schema.run_args or ''}```",
                                },
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Logs (truncated):*\n```{(json.dumps(api_body, ensure_ascii=False) if isinstance(api_body, (dict, list)) else str(api_body))[:1500]}```",
                                },
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Severity:* {severity}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Teams:* {', '.join(dict.fromkeys(a.team for a in decision.actions if getattr(a, 'team', None)))}",
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"Timestamp: <!date^{int(__import__('time').time())}^{{date_short}} {{time}}|now>",
                                    },
                                ],
                            },
                            {"type": "divider"},
                        ],
                    },
                    "jsm": {
                        "message": f"[{schema.id}] [{severity}] {schema.name}",
                        "priority": f"P{int(str(severity).strip().lower().replace('sev', '') or '4') + 1}",
                        "alias": schema.id,
                        "monitor_condition": monitor_condition or "",
                        "details": {
                            "Name": schema.name,
                            "Id": schema.id,
                            "ExecId": exec_id,
                            "Status": status_label,
                            "Runbook": schema.runbook,
                            "Run_Args": schema.run_args,
                            "Group": schema.group,
                            "Team": schema.team,
                            "OnCall": schema.oncall,
                            "MonitorCondition": monitor_condition,
                            "Severity": severity,
                            "Teams:": ", ".join(
                                dict.fromkeys(
                                    a.team
                                    for a in decision.actions
                                    if getattr(a, "team", None)
                                )
                            ),
                        },
                        "description": f"{format_jsm_description(exec_id, resource_info, api_body)}",
                    },
                }
                try:
                    trigger_results = (
                        execute_actions(
                            decision,
                            payload,
                            send_slack_fn=lambda token,
                            channel,
                            **kw: send_slack_execution(
                                token=token, channel=channel, **kw
                            ),
                            send_jsm_fn=lambda api_key, **kw: send_jsm_alert(
                                api_key=api_key, **kw
                            ),
                        )
                        or []
                    )
                    trigger_notifications = executions.notifications_json(
                        decision, trigger_results
                    )
                    if trigger_notifications:
                        start_log["Notifications"] = trigger_notifications
                        log_table.set(json.dumps(start_log, ensure_ascii=False))
                except Exception as e:
                    logging.error(f"{log_prefix} smart routing failed: {e}")

        # Return HTTP response mirroring downstream status
        response_body = build_response_body(
            status_code=status_code,
            schema=schema,
            partition_key=partition_key,
            exec_id=exec_id,
            api_json=api_body,
        )
        return func.HttpResponse(
            response_body,
            status_code=status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        # Build error response
        response_body = build_response_body(
            status_code=500,
            schema=schema,
            partition_key=partition_key,
            exec_id=exec_id,
            api_json={"error": str(e)},
        )

        # Log error to table
        error_log = executions.build_log_entry(
            status="error",
            partition_key=partition_key,
            exec_id=exec_id,
            row_key=exec_id,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=str(e),
            oncall=schema.oncall,
            monitor_condition=monitor_condition,
            severity=severity,
            team=schema.team,
        )
        log_table.set(json.dumps(error_log, ensure_ascii=False))

        return func.HttpResponse(
            response_body,
            status_code=500,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


# =========================
# HTTP Function: Approval
# =========================
@app.route(
    route="approvals/{partitionKey}/{execId}/approve",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="schemas",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="today_logs",
    table_name=TABLE_NAME,
    partition_key="{partitionKey}",
    connection=STORAGE_CONN,
)
def approve(
    req: func.HttpRequest,
    log_table: func.Out[str],
    schemas: str,
    today_logs: str,
    workers: str,
) -> func.HttpResponse:
    import utils
    from worker_routing import worker_routing

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot approve executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    route_params = getattr(req, "route_params", {}) or {}
    execId = (route_params.get("execId") or "").strip()

    p = (req.params.get("p") or "").strip()
    s = (req.params.get("s") or "").strip()

    # Security: check session token if not in LOCAL_DEV mode
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        session, error_res = security.get_authenticated_user(req)
        if error_res:
            # Fallback for Slack/Email links
            approver = (
                req.headers.get("x-cloudo-user")
                or req.headers.get("X-Approver")
                or "anonymous-link"
            )
        else:
            approver = session.get("username")
    else:
        # Trust headers in LOCAL_DEV
        approver = (
            req.headers.get("x-cloudo-user")
            or req.headers.get("X-Approver")
            or "unknown"
        )
        # Try to get session if available anyway
        session, _ = security.get_authenticated_user(req)
        if session:
            approver = session.get("username")

    if not execId:
        return func.HttpResponse(
            json.dumps({"error": "Missing execId in route"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    ok, payload = security.verify_signed_payload(execId, p, s)
    if not ok:
        return func.HttpResponse(
            json.dumps({"error": "Invalid or expired payload"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    rows = storage.rows_from_binding(today_logs) or []
    if not executions.only_pending_for_exec(rows, execId):
        return func.HttpResponse(
            json.dumps(
                {"message": "Already decided or executed for this ExecId"},
                ensure_ascii=False,
            ),
            status_code=409,
            mimetype="application/json",
        )

    schema_id = payload.get("schemaId") or ""
    initiator = payload.get("initiator")
    log_prefix = executions.build_exec_log_prefix(execId, initiator)
    resource_info = payload.get("resource_info") or None
    routing_info = payload.get("routing_info") or None
    monitor_condition = payload.get("monitorCondition") or ""
    severity = payload.get("severity") or ""
    resource_team = team_access.normalize_team(payload.get("team"))

    # Load schema entity
    parsed = storage.rows_from_binding(schemas)
    if parsed is None:
        return func.HttpResponse(
            json.dumps({"error": "Schemas not available"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )

    schema_entity = _find_schema_entity(parsed, [str(schema_id)])
    if not schema_entity:
        return func.HttpResponse(
            json.dumps({"error": "Schema not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    schema = Schema(id=_get_entity_id(schema_entity), entity=schema_entity)
    if (
        session
        and not team_access.is_admin(session)
        and resource_team
        not in {
            DEFAULT_TEAM,
            team_access.session_team(session),
        }
    ):
        return func.HttpResponse(
            json.dumps({"error": "Approval belongs to another team"}),
            status_code=403,
            mimetype="application/json",
        )

    partition_key = utils.today_partition_key()
    requested_at = utils.format_requested_at()

    # Execute once (pass embedded resource_info and propagate the function key if needed)
    try:
        # ---------------------------------------------------------
        # DYNAMIC WORKER SELECTION (Binding Version)
        # ---------------------------------------------------------
        target_queue = worker_routing(workers, schema)

        api_body = {}
        status_code = 202

        if target_queue:
            logging.debug(
                f"{log_prefix} 🎯 Dynamic Routing: Selected Queue '{target_queue}'"
            )

            try:
                # Construct the payload (formerly HTTP headers)
                queue_payload = {
                    "runbook": schema.runbook,
                    "run_args": schema.run_args,
                    "id": schema.id,
                    "name": schema.name or "",
                    "requestedAt": requested_at,
                    "exec_id": execId,
                    "oncall": schema.oncall,
                    "aks_login_required": schema.aks_login_required,
                    "aks_cluster_name": schema.aks_cluster_name,
                    "aks_resource_group": schema.aks_resource_group,
                    "aks_namespace": schema.aks_namespace,
                    "monitor_condition": monitor_condition,
                    "severity": severity,
                    "team": schema.team,
                    "worker": schema.worker,
                    "group": schema.group,
                    "initiator": initiator,
                    "resource_info": resource_info or {},
                    "routing_info": routing_info or {},
                }

                storage.enqueue_queue_payload(target_queue, queue_payload)

                api_body = {
                    "status": "accepted",
                    "queue": target_queue,
                    "payload": queue_payload,
                }

            except Exception as e:
                logging.error(f"{log_prefix} ❌ Queue send failed: {e}")
                status_code = 500
                api_body = {"error": str(e)}
        else:
            err_msg = f"❌ No workers ({schema.worker}) available and no static queue configured for {schema.id}"
            logging.error(f"{log_prefix} {err_msg}")
            status_code = 500
            api_body = {"error": err_msg}
        # ---------------------------------------------------------

        # Status label for logs
        status_label = "accepted" if status_code == 202 else "error"

        log_entity = executions.build_log_entry(
            status=status_label,
            partition_key=partition_key,
            row_key=str(uuid.uuid4()),
            exec_id=execId,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=json.dumps(
                {
                    "message": f"Approved and executed by {approver}",
                    "response": api_body,
                    "resource_info": resource_info,
                },
                ensure_ascii=False,
            ),
            oncall=schema.oncall,
            initiator=payload.get("initiator"),
            resource_info=resource_info,
            monitor_condition=monitor_condition,
            severity=severity,
            team=schema.team,
            approval_required=True,
            approval_decision_by=approver,
        )
        log_table.set(json.dumps(log_entity, ensure_ascii=False))

        audit.log_audit(
            user=approver,
            action="RUNBOOK_APPROVE",
            target=execId,
            details=f"Runbook: {schema.runbook}, Schema: {schema.id}, Approver: {approver}",
        )

        # smart routing notification (if routing module available)
        if route_alert and execute_actions:
            ctx = {
                "resourceId": ((resource_info or {}).get("resource_id") or ""),
                "resourceGroup": ((resource_info or {}).get("resource_group") or ""),
                "resourceName": ((resource_info or {}).get("resource_name") or ""),
                "schemaName": (schema.name or ""),
                "severity": severity,
                "namespace": ((resource_info or {}).get("namespace") or ""),
                "oncall": schema.oncall,
                "status": status_label,
                "execId": execId,
                "name": schema.name or "",
                "id": schema.id,
                "team": schema.team,
                "routing_info": routing_info,
            }
            decision = route_alert(ctx)
            logging.debug(f"{log_prefix} Approval: {decision}")

            # Notify Slack directly (bypassing smart routing for Slack as requested)
            notify.notify_slack_decision(
                exec_id=execId,
                schema_id=schema_id,
                decision="approved",
                approver=approver,
                routing_info=routing_info,
                team=resource_team,
            )

            # We still execute other actions via smart routing if any (excluding Slack)
            payload = {
                "slack": None  # Skip slack in execute_actions
            }
            try:
                execute_actions(decision, payload, send_slack_fn=None)
            except Exception as e:
                logging.error(
                    f"{log_prefix} smart routing approval actions failed: {e}"
                )

        return func.HttpResponse(
            json.dumps(
                {
                    "message": f"Approved and executed by {approver}",
                    "response": api_body,
                    "resource_info": resource_info,
                }
            ),
            status_code=200,
            mimetype="application/json",
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except Exception as e:
        err_log = executions.build_log_entry(
            status="error",
            partition_key=partition_key,
            row_key=str(uuid.uuid4()),
            exec_id=execId,
            requested_at=requested_at,
            name=schema.name or "",
            schema_id=schema.id,
            runbook=schema.runbook,
            run_args=schema.run_args,
            worker=schema.worker,
            group=schema.group,
            log_msg=f"Approve failed: {str(e)}",
            oncall=schema.oncall,
            monitor_condition=None,
            severity=None,
            approval_required=True,
            approval_decision_by=approver,
        )
        log_table.set(json.dumps(err_log, ensure_ascii=False))
        notify.notify_slack_decision(
            execId,
            schema_id,
            f"approved {execId} by {approver}",
            approver,
            extra=f"*Error:* {str(e)}",
            routing_info=routing_info,
            team=resource_team,
        )
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )


# =========================
# HTTP Function: Rejecter
# =========================
@app.route(
    route="approvals/{partitionKey}/{execId}/reject",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="schemas",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="today_logs",
    table_name=TABLE_NAME,
    partition_key="{partitionKey}",
    connection=STORAGE_CONN,
)
def reject(
    req: func.HttpRequest, log_table: func.Out[str], schemas: str, today_logs: str
) -> func.HttpResponse:
    import utils

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot reject executions"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    route_params = getattr(req, "route_params", {}) or {}
    execId = (route_params.get("execId") or "").strip()

    rows = storage.rows_from_binding(today_logs) or []
    if not executions.only_pending_for_exec(rows, execId):
        return func.HttpResponse(
            json.dumps(
                {"message": "Already decided or executed for this ExecId"},
                ensure_ascii=False,
            ),
            status_code=409,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    p = (req.params.get("p") or "").strip()
    s = (req.params.get("s") or "").strip()

    # Security: check session token if not in LOCAL_DEV mode
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        session, error_res = security.get_authenticated_user(req)
        if error_res:
            # Fallback for Slack/Email links
            approver = (
                req.headers.get("x-cloudo-user")
                or req.headers.get("X-Approver")
                or "anonymous-link"
            )
        else:
            approver = session.get("username")
    else:
        # Trust headers in LOCAL_DEV
        approver = (
            req.headers.get("x-cloudo-user")
            or req.headers.get("X-Approver")
            or "unknown"
        )
        # Try to get session if available anyway
        session, _ = security.get_authenticated_user(req)
        if session:
            approver = session.get("username")

    if not execId:
        return func.HttpResponse(
            json.dumps({"error": "Missing execId in route"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    ok, payload = security.verify_signed_payload(execId, p, s)
    if not ok:
        return func.HttpResponse(
            json.dumps({"error": "Invalid or expired payload"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    schema_id = payload.get("schemaId") or ""
    initiator = payload.get("initiator")
    log_prefix = executions.build_exec_log_prefix(execId, initiator)
    resource_info = payload.get("resource_info") or None
    routing_info = payload.get("routing_info") or None
    monitor_condition = payload.get("monitorCondition") or ""
    severity = payload.get("severity") or ""
    resource_team = team_access.normalize_team(payload.get("team"))

    # Load schema entity
    parsed = storage.rows_from_binding(schemas)
    if parsed is None:
        return func.HttpResponse(
            json.dumps({"error": "Schemas not available"}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )

    schema_entity = _find_schema_entity(parsed, [str(schema_id)])
    if not schema_entity:
        return func.HttpResponse(
            json.dumps({"error": "Schema not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    schema = Schema(id=_get_entity_id(schema_entity), entity=schema_entity)
    if (
        session
        and not team_access.is_admin(session)
        and resource_team
        not in {
            DEFAULT_TEAM,
            team_access.session_team(session),
        }
    ):
        return func.HttpResponse(
            json.dumps({"error": "Approval belongs to another team"}),
            status_code=403,
            mimetype="application/json",
        )

    partition_key = utils.today_partition_key()
    requested_at = utils.format_requested_at()

    log_entity = executions.build_log_entry(
        status="rejected",
        partition_key=partition_key,
        row_key=str(uuid.uuid4()),
        exec_id=execId,
        requested_at=requested_at,
        name=schema.name or "",
        schema_id=schema.id,
        runbook=schema.runbook,
        run_args=schema.run_args,
        worker=schema.worker,
        group=schema.group,
        log_msg=json.dumps(
            {"message": f"Rejected by approver: {approver}"}, ensure_ascii=False
        ),
        oncall=schema.oncall,
        initiator=payload.get("initiator"),
        resource_info=resource_info,
        monitor_condition=monitor_condition,
        severity=severity,
        team=schema.team,
        approval_required=True,
        approval_decision_by=approver,
    )
    log_table.set(json.dumps(log_entity, ensure_ascii=False))

    audit.log_audit(
        user=approver,
        action="RUNBOOK_REJECT",
        target=execId,
        details=f"Runbook: {schema.runbook}, Schema: {schema.id}",
    )

    # Notify Slack directly
    notify.notify_slack_decision(
        exec_id=execId,
        schema_id=schema_id,
        decision="rejected",
        approver=approver,
        routing_info=routing_info,
        team=resource_team,
    )

    # smart routing notification (if routing module available)
    if route_alert and execute_actions:
        ctx = {
            "resourceId": ((resource_info or {}).get("resource_id") or ""),
            "resourceGroup": ((resource_info or {}).get("resource_group") or ""),
            "resourceName": ((resource_info or {}).get("resource_name") or ""),
            "schemaName": (schema.name or ""),
            "severity": severity,
            "namespace": ((resource_info or {}).get("namespace") or ""),
            "oncall": schema.oncall,
            "status": "rejected",
            "execId": execId,
            "name": schema.name or "",
            "id": schema.id,
            "team": schema.team,
            "routing_info": routing_info,
        }
        decision = route_alert(ctx)
        logging.debug(f"{log_prefix} Reject: {decision}")

        payload = {
            "slack": None  # Skip slack in execute_actions
        }
        try:
            execute_actions(decision, payload, send_slack_fn=None)
        except Exception as e:
            logging.error(f"{log_prefix} smart routing rejection failed: {e}")

    return func.HttpResponse(
        json.dumps({"message": f"Rejected by approver: {approver}"}),
        status_code=200,
        mimetype="application/json",
        headers={
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    )


# =========================
# HTTP Function: receiver
# =========================


def _process_receiver_body(body: dict, log_table: func.Out[str]) -> None:
    """Internal helper function to process receiver message body (shared by HTTP and queue)."""
    import utils
    from escalation import format_jsm_description, send_jsm_alert, send_slack_execution

    try:
        from smart_routing import execute_actions, route_alert
    except ImportError:
        route_alert = None
        execute_actions = None

    try:
        receiver_prefix = executions.build_exec_log_prefix(
            body.get("exec_id"), body.get("initiator")
        )
        # Normalize fields that can arrive as list/scalars to keep Receiver robust.
        for key in ("exec_id", "status", "name", "id", "runbook", "team"):
            value = body.get(key)
            if isinstance(value, list):
                first = next((str(v).strip() for v in value if str(v).strip()), "")
                body[key] = first
            elif value is None:
                body[key] = ""
            else:
                body[key] = str(value).strip()

        body_for_log = dict(body)
        if isinstance(body.get("routing_info"), dict):
            body_for_log["routing_info"] = {
                "team": body["routing_info"].get("team"),
                "slack_channel": body["routing_info"].get("slack_channel"),
                "redacted": True,
            }
    except Exception as e:
        logging.error(f"[Receiver] Invalid queue message: {e}")
        return

    required_fields = ["exec_id", "status", "name", "id", "runbook"]
    missing = [k for k in required_fields if not body.get(k)]
    if missing:
        logging.warning(f"{receiver_prefix} Missing required fields: {missing}")
        return

    logging.info(
        f"{receiver_prefix} [{body.get('status')}] Receiver invoked",
        extra={
            "headers": {
                "ExecId": body.get("exec_id"),
                "Status": body.get("status"),
                "Name": body.get("name"),
                "Id": body.get("id"),
                "Runbook": body.get("runbook"),
                "Run_Args": body.get("run_args"),
                "OnCall": body.get("oncall"),
                "Initiator": body.get("initiator"),
                "MonitorCondition": body.get("monitor_condition"),
                "Severity": body.get("severity"),
                "Team": body.get("team"),
            }
        },
    )

    requested_at = utils.format_requested_at()
    partition_key = utils.today_partition_key()
    row_key = str(uuid.uuid4())
    status_label = executions.resolve_status(body.get("status"))

    provided_log_ref = body.get("log_ref") or ""
    logs_raw = ""
    try:
        logs_raw = log_blobs.decode_base64(body.get("logs_b64") or "")
    except Exception:
        logs_raw = ""
    log_value: Optional[str] = None
    if isinstance(provided_log_ref, str) and log_blobs.parse_blob_ref(provided_log_ref):
        log_value = provided_log_ref
    elif logs_raw:
        try:
            log_value = log_blobs.upload_log_to_blob(
                partition_key=partition_key,
                exec_id=body.get("exec_id") or "",
                status=status_label,
                logs_raw=logs_raw,
            )
        except Exception as e:
            logging.warning(
                f"{receiver_prefix} blob upload failed, falling back to table log: {type(e).__name__}: {e}"
            )
            log_value = utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS)
    else:
        log_value = ""

    # Normalize resource_info/routing_info to dicts *before*
    # building the log entity
    resource_info = body.get("resource_info") or {}
    routing_info = body.get("routing_info") or {}
    if isinstance(resource_info, str):
        try:
            parsed = json.loads(resource_info)
            resource_info = parsed if isinstance(parsed, dict) else {}
        except Exception:
            resource_info = {}
    if isinstance(routing_info, str):
        try:
            parsed = json.loads(routing_info)
            routing_info = parsed if isinstance(parsed, dict) else {}
        except Exception:
            routing_info = {}

    body["team"] = team_access.normalize_team(
        body.get("team") or resource_info.get("team")
    )

    log_entity = executions.build_log_entry(
        status=status_label,
        partition_key=partition_key,
        row_key=row_key,
        exec_id=body.get("exec_id"),
        requested_at=requested_at,
        name=body.get("name"),
        schema_id=body.get("id"),
        runbook=body.get("runbook"),
        run_args=body.get("run_args"),
        worker=body.get("worker"),
        group=body.get("group"),
        log_msg=log_value,
        oncall=body.get("oncall"),
        initiator=body.get("initiator"),
        resource_info=resource_info,
        monitor_condition=body.get("monitor_condition"),
        severity=body.get("severity"),
        team=body.get("team") or resource_info.get("team"),
    )
    log_entity = log_blobs.ensure_log_entity_size_for_table(log_entity)
    log_table.set(json.dumps(log_entity, ensure_ascii=False))

    resource_id = (body.get("resource_id") or "") or (
        resource_info.get("resource_id") or ""
    )
    notification_results: list = []
    notification_decision: Any = None
    resource_group = (body.get("resource_group") or "") or (
        resource_info.get("resource_rg") or ""
    )
    resource_name = (body.get("resource_name") or "") or (
        resource_info.get("resource_name") or ""
    )
    namespace = (body.get("namespace") or "") or (
        resource_info.get("aks_namespace") or ""
    )

    if route_alert and execute_actions:
        exec_id = body.get("exec_id")
        ctx = {
            "resourceId": resource_id or None,
            "resourceGroup": resource_group or None,
            "resourceName": resource_name or None,
            "schemaName": body.get("name"),
            "severity": body.get("severity"),
            "namespace": namespace or None,
            "oncall": (body.get("oncall") or False),
            "status": status_label,
            "execId": exec_id,
            "name": body.get("name"),
            "id": body.get("id"),
            "team": body.get("team") or resource_info.get("team"),
            "routing_info": routing_info,  # sempre dict qui
        }
        decision = route_alert(ctx)
        logging.debug(f"{receiver_prefix} {decision}")
        status_emojis = {
            "succeeded": "✅",
            "running": "🏃",
            "skipped": "⏭️",
            "routed": "🧭",
            "error": "❌",
            "failed": "❌",
            "accepted": "📩",
            "pending": "⏳",
        }
        status_emoji = status_emojis.get(status_label, "ℹ️")

        # UI Base URL
        ui_base = (
            (os.getenv("NEXTJS_URL") or "http://localhost:3000").strip().rstrip("/")
        )
        if not ui_base.startswith("http"):
            ui_base = f"https://{ui_base}" if ui_base else "http://localhost:3000"
        partition_key = datetime.now(timezone.utc).strftime("%Y%m%d")
        ui_url = f"{ui_base}/executions?execId={exec_id}&partitionKey={partition_key}"

        # Truncate large fields for Slack blocks to avoid invalid_blocks
        resource_info_compact = notify.format_compact_resource_info(resource_info) or ""
        if len(resource_info_compact) > 600:
            resource_info_compact = resource_info_compact[:600] + "..."

        args_truncated = (body.get("run_args") or "").strip()
        if len(args_truncated) > 800:
            args_truncated = args_truncated[:800] + "\n... (truncated)"

        logs_truncated = (logs_raw or "").strip()
        if len(logs_truncated) > 1000:
            logs_truncated = logs_truncated[:1000] + "\n... (truncated)"

        payload = {
            "slack": {
                "message": f"[{exec_id}] {status_emoji} {status_label.upper()}: {body.get('name')}",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{status_emoji}\t{body.get('name')}\texecution",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"Execution *{body.get('name')}* transitioned to: *{status_label.upper()}*."
                            ),
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*SchemaId:* `{body.get('id')}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Severity:* `{body.get('severity') or '-'}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Worker:* `{body.get('worker') or 'unknown'}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Group:* `{body.get('group') or 'default'}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Team:* `{body.get('team') or 'default'}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Runbook:* `{body.get('runbook') or '-'}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Initiator:* `{body.get('initiator') or 'SYSTEM'}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*On Call:* `{body.get('oncall') or '-'}`",
                            },
                        ],
                    },
                    *(
                        [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"*Resource Context:*\n{resource_info_compact}",
                                },
                            }
                        ]
                        if resource_info_compact
                        else []
                    ),
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Arguments:*\n```{(args_truncated or 'None')}```",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Telemetry Output:*\n```{(logs_truncated or 'No telemetry available')}```",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "View Full Execution 🔍",
                                    "emoji": True,
                                },
                                "url": ui_url,
                            }
                        ],
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"ExecId: `{exec_id}` | Event: <!date^{int(datetime.now(timezone.utc).timestamp())}^{{date_short}} {{time}}|now>",
                            }
                        ],
                    },
                ],
            },
            "jsm": {
                "message": f"[{status_label.upper()}] [{body.get('id')}] [{body.get('severity')}] {body.get('name')}",
                "priority": f"P{int(str(body.get('severity') or '').strip().lower().replace('sev', '') or '4') + 1}",
                "alias": body.get("id"),
                "monitor_condition": body.get("monitor_condition") or "",
                "details": {
                    "Name": body.get("name"),
                    "Id": body.get("id"),
                    "ExecId": exec_id,
                    "Status": body.get("status"),
                    "Runbook": body.get("runbook"),
                    "Run_Args": body.get("run_args"),
                    "Worker": body.get("worker"),
                    "Group": body.get("group"),
                    "Team": body.get("team") or "default",
                    "OnCall": body.get("oncall"),
                    "MonitorCondition": body.get("monitor_condition"),
                    "Severity": body.get("severity"),
                    "Teams:": ", ".join(
                        dict.fromkeys(
                            a.team for a in decision.actions if getattr(a, "team", None)
                        )
                    ),
                },
                "description": f"{format_jsm_description(exec_id, resource_info, utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS or ''))}",
            },
        }
        try:
            notification_decision = decision
            notification_results = (
                execute_actions(
                    decision,
                    payload,
                    send_slack_fn=lambda token, channel, **kw: send_slack_execution(
                        token=token, channel=channel, **kw
                    ),
                    send_jsm_fn=lambda api_key, **kw: send_jsm_alert(
                        api_key=api_key, **kw
                    ),
                )
                or []
            )
        except Exception as e:
            logging.error(f"[{exec_id}] smart routing failed: {e}")
    else:
        logging.warning("Routing module not available, keeping legacy notifications")

    notifications_value = executions.notifications_json(
        notification_decision, notification_results
    )
    if notifications_value:
        log_entity["Notifications"] = notifications_value
        log_table.set(json.dumps(log_entity, ensure_ascii=False))

    _enqueue_ai_agent_analysis(
        body=body,
        status_label=status_label,
        resource_info=resource_info,
        routing_info=routing_info,
        logs_raw=logs_raw,
    )


def _enqueue_ai_agent_analysis(
    body: dict,
    status_label: str,
    resource_info: dict,
    routing_info: dict,
    logs_raw: str,
) -> None:
    """Forward failed/errored executions to the ClouDO Agent (see
    src/core/agent) for asynchronous AI triage. No-op unless
    AI_AGENT_ENABLED=true, so this is fully backward compatible when the
    agent service is not deployed.

    The JSM alias sent here (`jsm_alias`) matches the alias used by the
    smart-routing JSM alert above (body["id"], the schema id), so the
    agent's triage note lands on the same alert.
    """
    try:
        from smart_routing import get_setting

        agent_enabled = (get_setting("AI_AGENT_ENABLED") or "false").strip().lower()
    except Exception:
        agent_enabled = os.getenv("AI_AGENT_ENABLED", "false").strip().lower()
    if agent_enabled != "true":
        return
    if status_label not in ("failed", "error"):
        return

    # Manual "Dev Test Run" executions launched from Studio.
    is_dev_test_run = str(body.get("name") or "").strip().lower() == "dev test run" or (
        isinstance(resource_info, dict)
        and str(resource_info.get("team") or "").strip().lower() == "dev-test"
    )
    if is_dev_test_run:
        logging.debug(
            "[%s] Agent: skipping AI triage for Dev Test Run execution",
            body.get("exec_id"),
        )
        return

    import utils

    try:
        ai_queue = storage.get_queue_client(
            AI_ANALYSIS_QUEUE_NAME, conn_env=STORAGE_CONNECTION
        )
        ai_payload = {
            "exec_id": body.get("exec_id"),
            "jsm_alias": body.get("id"),
            "id": body.get("id"),
            "name": body.get("name"),
            "status": status_label,
            "runbook": body.get("runbook"),
            "run_args": body.get("run_args"),
            "monitor_condition": body.get("monitor_condition"),
            "severity": body.get("severity"),
            "initiator": body.get("initiator"),
            "resource_info": resource_info if isinstance(resource_info, dict) else {},
            "routing_info": (
                {"team": routing_info.get("team")}
                if isinstance(routing_info, dict)
                else {}
            ),
            "logs": utils._truncate_for_table(logs_raw, MAX_TABLE_CHARS)
            if logs_raw
            else "",
        }
        _write_ai_analysis_placeholder(body.get("exec_id"), body.get("team"))
        ai_queue.send_message(json.dumps(ai_payload, ensure_ascii=False))
    except Exception as e:
        logging.error(
            f"[{body.get('exec_id')}] Failed to enqueue AI agent analysis: {e}"
        )


def _write_ai_analysis_placeholder(exec_id: str, team: Any = None) -> None:
    """Upsert a 'processing' placeholder as soon as an execution is enqueued
    for AI triage, so the UI can immediately show an in-progress state
    instead of "not found" while the Agent service works through the queue.
    The Agent overwrites this entity with the final result (see
    agent/utils.py write_analysis_result)."""
    if not exec_id:
        return
    try:
        table_client = storage.get_table_client(
            TABLE_AI_ANALYSIS, conn_env=STORAGE_CONNECTION
        )
        table_client.upsert_entity(
            entity={
                "PartitionKey": "AiAnalysis",
                "RowKey": str(exec_id),
                "team": team_access.normalize_team(team),
                "status": "processing",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as e:
        logging.error(f"[{exec_id}] Failed to write AI analysis placeholder: {e}")


# =========================
# Queue Trigger: Receiver
# =========================


def _decode_queue_message_text(raw: str) -> dict:
    """Parse a queue message body as JSON.

    The Storage Queue trigger is configured (host.json: extensions.queues.
    messageEncoding = "base64") to auto base64-decode message content before
    handing it to the function, matching the TextBase64EncodePolicy used when
    messages are sent. If that host-side decode is ever bypassed (e.g. bundle
    resolution differences), we still get handed the raw base64 text here, so
    fall back to decoding it manually before giving up.
    """
    text = raw or ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            decoded = base64.b64decode(text).decode("utf-8")
        except Exception:
            raise
        return json.loads(decoded)


@app.queue_trigger(
    arg_name="msg", queue_name=NOTIFICATION_QUEUE_NAME, connection=STORAGE_CONNECTION
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
def Receiver(msg: func.QueueMessage, log_table: func.Out[str]) -> None:
    """Queue trigger endpoint for receiving execution status from workers."""
    try:
        body = _decode_queue_message_text(msg.get_body().decode("utf-8"))
        _process_receiver_body(body, log_table)
    except Exception as e:
        logging.error(f"[Receiver] Failed to process queue message: {e}")


# =========================
# HTTP Endpoint: Receiver (fast path)
# =========================


@app.route(
    route="receiver",
    methods=[func.HttpMethod.POST],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
def ReceiverHttp(req: func.HttpRequest, log_table: func.Out[str]) -> func.HttpResponse:
    """HTTP endpoint for direct status calls from workers (faster than queue)."""
    try:
        body = req.get_json()
        if not isinstance(body, dict):
            return func.HttpResponse(
                json.dumps(
                    {"error": "Invalid JSON: expected object"}, ensure_ascii=False
                ),
                status_code=400,
                mimetype="application/json",
            )
        _process_receiver_body(body, log_table)
        return func.HttpResponse(
            json.dumps({"status": "ok", "message": "Status received"}),
            status_code=200,
            mimetype="application/json",
        )
    except ValueError as e:
        return func.HttpResponse(
            json.dumps({"error": f"Invalid JSON: {e}"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )
    except Exception as e:
        logging.error(f"[ReceiverHttp] Error processing request: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Internal error: {type(e).__name__}"}, ensure_ascii=False
            ),
            status_code=500,
            mimetype="application/json",
        )


# =========================
# Development Test Run Endpoint
# =========================


@app.route(route="dev/testrun", auth_level=AUTH)
@app.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def dev_test_run(
    req: func.HttpRequest, log_table: func.Out[str], workers: str
) -> func.HttpResponse:
    """
    Development endpoint to test runbook execution with feature flag.
    Accepts a runbook/script and arguments, selects a worker by capability using smart routing,
    and sends to worker via queue. Only available when FEATURE_DEV=true.

    Request body (JSON):
    {
      "script": "script_name.sh",
      "args": "command_args",
      "body": {"json": "payload"},
      "capability": "worker_capability"
    }
    """
    # Feature flag for test execution
    if os.getenv("FEATURE_DEV", "false").lower() != "true":
        return func.HttpResponse("Not found", status_code=404)

    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    # Import utilities needed for this function
    import detection
    import utils
    from worker_routing import worker_routing

    script = req_body.get("script", "").strip()
    run_args = req_body.get("args") or ""
    capability = req_body.get("capability", "").strip()
    script_type = req_body.get("scriptType", "python").lower()  # Default to python

    # Get authenticated user
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res
    initiator = session.get("username") or "dev-user"
    team = session.get("team") or "default"

    # Parse resource_info from the body using detection (same as Trigger endpoint)
    parsed_body = detection.parse_resource_fields(req_body.get("body"))
    resource_info = {
        "_raw": parsed_body.get("_raw"),
        "schema_id": parsed_body.get("schema_id"),
        "resource_name": parsed_body.get("resource_name"),
        "resource_rg": parsed_body.get("resource_group"),
        "resource_id": parsed_body.get("resource_id"),
        "aks_namespace": parsed_body.get("namespace"),
        "aks_pod": parsed_body.get("pod"),
        "aks_deployment": parsed_body.get("deployment"),
        "aks_job": parsed_body.get("job"),
        "aks_horizontalpodautoscaler": parsed_body.get("horizontalpodautoscaler"),
        "team": team,
        "payload": parsed_body.get("payload"),
    }

    if not script:
        return func.HttpResponse(
            json.dumps(
                {"error": "missing 'script' field in request body"},
                ensure_ascii=False,
            ),
            status_code=400,
            mimetype="application/json",
        )

    # Generate unique test name for logging (instead of dumping full script)
    script_name = f"dev-test-{uuid.uuid4().hex[:8]}"

    if not capability:
        return func.HttpResponse(
            json.dumps(
                {"error": "missing 'capability' field in request body"},
                ensure_ascii=False,
            ),
            status_code=400,
            mimetype="application/json",
        )

    # Generate execution ID
    exec_id = str(uuid.uuid4())
    requested_at = utils.format_requested_at()

    logging.debug(
        f"[DEV TEST] Scheduling test run: {script_name} with capability {capability} (exec_id={exec_id}, initiator={initiator})"
    )

    try:
        # Create a simple schema-like object for worker_routing
        class TestSchema:
            def __init__(self, worker):
                self.worker = worker

        schema = TestSchema(capability)

        # Use smart routing to select worker by capability
        target_queue = worker_routing(workers, schema)

        if not target_queue:
            return func.HttpResponse(
                json.dumps(
                    {
                        "status": "error",
                        "script": script_name,
                        "error": f"No active workers found for capability '{capability}'",
                    },
                    ensure_ascii=False,
                ),
                status_code=404,
                mimetype="application/json",
            )

        # Construct queue payload for worker (like Trigger endpoint does)
        queue_payload = {
            "runbook": script_name,
            "script": script,
            "script_type": script_type,
            "run_args": run_args,
            "id": script_name,
            "name": "Dev Test Run",
            "requestedAt": requested_at,
            "exec_id": exec_id,
            "oncall": "false",
            "initiator": initiator,
            "monitor_condition": parsed_body.get("monitor_condition") or "Fired",
            "severity": parsed_body.get("severity") or "Sev4",
            "worker": capability,
            "team": team,
            "group": "-",
            "resource_info": resource_info or {},
            "routing_info": {},
        }

        logging.debug(
            f"[DEV TEST] Selected queue '{target_queue}' for capability '{capability}'. Sending payload: {queue_payload}"
        )

        # Send to the selected queue
        storage.enqueue_queue_payload(target_queue, queue_payload)

        # Log audit entry for test run
        audit.log_audit(
            user=initiator,
            action="RUNBOOK_DEV_TEST_RUN",
            target=exec_id,
            details=f"Script: {script_name}, Capability: {capability}, Args: {run_args}",
        )

        # Write log entry to the table
        api_body = {"status": "accepted", "queue": target_queue}
        partition_key = utils.today_partition_key()
        start_log = executions.build_log_entry(
            status="accepted",
            partition_key=partition_key,
            exec_id=exec_id,
            row_key=exec_id,
            requested_at=requested_at,
            name="Dev Test Run" or "",
            schema_id=script_name,
            runbook=script_name,
            run_args=run_args,
            worker=capability,
            group="-",
            log_msg=api_body,
            oncall="false",
            initiator=initiator,
            monitor_condition=parsed_body.get("monitor_condition", "Fired"),
            severity=parsed_body.get("severity", "Sev4"),
            team=team,
            resource_info=resource_info,
        )
        log_table.set(json.dumps(start_log, ensure_ascii=False))

        response_body = json.dumps(
            {
                "status": "accepted",
                "exec_id": exec_id,
                "script": script_name,
                "capability": capability,
                "queue": target_queue,
                "message": "Test run scheduled on worker",
            },
            ensure_ascii=False,
        )
        return func.HttpResponse(
            response_body, status_code=202, mimetype="application/json"
        )

    except Exception as e:
        logging.error(
            f"[DEV TEST] Error scheduling test run: {type(e).__name__}: {str(e)}"
        )
        error_body = json.dumps(
            {
                "status": "error",
                "script": script_name,
                "error": f"{type(e).__name__}: {str(e)}",
            },
            ensure_ascii=False,
        )
        return func.HttpResponse(
            error_body, status_code=500, mimetype="application/json"
        )


# =========================
# Features Flag Endpoint
# =========================


@app.route(route="features", auth_level=AUTH)
def features(req: func.HttpRequest) -> func.HttpResponse:
    """Expose feature flags to frontend"""
    import os

    feature_dev_enabled = os.getenv("FEATURE_DEV", "false").lower() == "true"

    body = json.dumps(
        {
            "FEATURE_DEV": feature_dev_enabled,
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(body, status_code=200, mimetype="application/json")


# =========================
# Heartbeat
# =========================


@app.route(route="healthz", auth_level=AUTH)
def heartbeat(req: func.HttpRequest) -> func.HttpResponse:
    import utils

    now_utc = utils.utc_now_iso()
    body = json.dumps(
        {
            "status": "ok",
            "time": now_utc,
            "service": "Trigger",
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(
        body,
        status_code=200,
        mimetype="application/json",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# =========================
# Table Storage READ (input binding)
# =========================


@app.route(route="logs/{partitionKey}/{execId}", auth_level=AUTH)
@app.table_input(
    arg_name="log_entity",
    table_name="RunbookLogs",
    partition_key="{partitionKey}",
    filter="ExecId eq '{execId}'",
    connection="AzureWebJobsStorage",
)
def get_log(req: func.HttpRequest, log_entity: str) -> func.HttpResponse:
    """
    Returns the entity from the RunbookLogs table identified by PartitionKey and RowKey.
    Uso: GET /api/logs/{partitionKey}/{execId}
    """
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    # If the entity does not exist, the binding returns None/empty.
    if not log_entity:
        return func.HttpResponse(
            json.dumps({"error": "Entity not found"}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    try:
        parsed = json.loads(log_entity)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else None
        if isinstance(parsed, dict):
            if not team_access.can_view_all_teams(
                req, session
            ) and team_access.entity_team(parsed) not in {
                DEFAULT_TEAM,
                team_access.session_team(session),
            }:
                return func.HttpResponse(
                    json.dumps({"error": "Execution belongs to another team"}),
                    status_code=403,
                    mimetype="application/json",
                )
            parsed = log_blobs.hydrate_log_field(parsed)
            log_entity = json.dumps(parsed, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Failed to hydrate get_log entity: {e}")

    # log_entity is a JSON string of the complete entity
    return func.HttpResponse(
        log_entity,
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="ai-analysis/{execId}", auth_level=AUTH)
def get_ai_analysis(req: func.HttpRequest) -> func.HttpResponse:
    """Returns the AI Agent's triage result for a given exec_id, if any."""
    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    exec_id = req.route_params.get("execId")
    if not exec_id:
        return func.HttpResponse(
            json.dumps({"error": "Missing execId"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    try:
        from azure.core.exceptions import ResourceNotFoundError

        table_client = storage.get_table_client(
            TABLE_AI_ANALYSIS, conn_env=STORAGE_CONNECTION
        )
        try:
            entity = table_client.get_entity(
                partition_key="AiAnalysis", row_key=str(exec_id)
            )
        except ResourceNotFoundError:
            payload = {
                "status": "not_found",
                "analysis": None,
                "error": None,
                "updated_at": None,
            }
            return func.HttpResponse(
                json.dumps(payload, ensure_ascii=False),
                status_code=200,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        analysis_raw = entity.get("analysis")
        if not team_access.filter_entities_by_team([dict(entity)], req, session):
            return func.HttpResponse(
                json.dumps({"error": "Analysis belongs to another team"}),
                status_code=403,
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        try:
            analysis = json.loads(analysis_raw) if analysis_raw else None
        except (TypeError, ValueError):
            analysis = None
        payload = {
            "status": entity.get("status") or "processing",
            "analysis": analysis,
            "error": entity.get("error"),
            "updated_at": entity.get("updated_at"),
        }
    except Exception as e:
        logging.error(f"[{exec_id}] Error fetching AI analysis: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Failed to fetch AI analysis"}, ensure_ascii=False),
            status_code=502,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


# TODO Manage empty partitions
# @app.table_input(
#     arg_name="rows",
#     table_name=TABLE_NAME,
#     partition_key="{partitionKey}",
#     connection=STORAGE_CONN,
# )
@app.route(
    route="logs/query",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def logs_query(req: func.HttpRequest) -> func.HttpResponse:
    """
    Query dei log via Table Input Binding:
    - partitionKey (required) -> used for the binding
    - execId, status -> filtered by memory
    - q (contains on some filed), from/to (range on RequestedAt), order, limit -> in memory
    """
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    try:

        def _odata_escape(value: str) -> str:
            return str(value or "").replace("'", "''")

        parse_dt_local = executions.parse_dt_local

        partition_key = (req.params.get("partitionKey") or "").strip()
        if not partition_key:
            return func.HttpResponse(
                json.dumps({"error": "partitionKey required"}, ensure_ascii=False),
                status_code=400,
                mimetype="application/json",
            )

        exec_id = (req.params.get("execId") or "").strip()
        status_param = (
            (req.params.get("status") or req.params.get("statuses") or "")
            .strip()
            .lower()
        )
        statuses = {s.strip() for s in status_param.split(",") if s.strip()}
        latest_only_raw = (
            req.params.get("latestOnly") or req.params.get("latest_only") or "true"
        )
        latest_only = str(latest_only_raw).strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        q = (req.params.get("q") or "").strip()
        from_dt = (req.params.get("from") or "").strip()
        to_dt = (req.params.get("to") or "").strip()
        include_log_content_raw = (
            req.params.get("includeLogContent")
            or req.params.get("include_log_content")
            or req.params.get("includeLogs")
            or req.params.get("include_logs")
            or "true"
        )
        include_log_content = str(include_log_content_raw).strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )

        try:
            limit = min(max(int(req.params.get("limit") or 200), 1), 5000)
        except Exception:
            limit = 200
        order = (req.params.get("order") or "desc").strip().lower()

        table_client = storage.get_table_client(TABLE_NAME)

        filter_parts = [f"PartitionKey eq '{_odata_escape(partition_key)}'"]
        if exec_id:
            filter_parts.append(f"ExecId eq '{_odata_escape(exec_id)}'")
        if (not latest_only) and statuses:
            if len(statuses) == 1:
                filter_parts.append(
                    f"Status eq '{_odata_escape(next(iter(statuses)))}'"
                )
            else:
                status_filter = " or ".join(
                    f"Status eq '{_odata_escape(s)}'" for s in sorted(statuses)
                )
                filter_parts.append(f"({status_filter})")

        f_dt = parse_dt_local(from_dt)
        t_dt = parse_dt_local(to_dt)
        if f_dt:
            filter_parts.append(f"RequestedAt ge '{_odata_escape(f_dt.isoformat())}'")
        if t_dt:
            filter_parts.append(f"RequestedAt le '{_odata_escape(t_dt.isoformat())}'")

        filter_query = " and ".join(filter_parts)
        selected_columns = [
            "PartitionKey",
            "RowKey",
            "ExecId",
            "Status",
            "RequestedAt",
            "ApprovalRequired",
            "ApprovalExpiresAt",
            "Name",
            "Id",
            "Runbook",
            "Run_Args",
            "Worker",
            "Group",
            "OnCall",
            "Initiator",
            "Severity",
            "MonitorCondition",
            "ResourceInfo",
            "team",
            "Notifications",
        ]
        if include_log_content or q:
            selected_columns.append("Log")

        try:
            entities = table_client.query_entities(
                query_filter=filter_query, select=selected_columns
            )
            data = list(entities)
            data = team_access.filter_entities_by_team(data, req, session)
        except Exception as e:
            logging.error(f"Table query failed: {e}")
            return func.HttpResponse(
                json.dumps(
                    {"error": "Failed to fetch data from storage"}, ensure_ascii=False
                ),
                status_code=500,
                mimetype="application/json",
            )

        def contains_any(e: dict, s: str) -> bool:
            s = s.lower()
            for k in (
                "Name",
                "Id",
                "Url",
                "Runbook",
                "Log",
                "Run_Args",
                "Initiator",
            ):
                v = e.get(k)
                if v is None:
                    continue
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                if s in str(v).lower():
                    return True
            return False

        # Memory filters
        filtered: list[dict[str, Any]] = []
        for e in data:
            ok = True
            if exec_id and str(e.get("ExecId") or "").strip() != exec_id:
                ok = False
            # When latest_only is enabled we apply status filter after collapse,
            # so it matches the final state of each execution.
            if (
                ok
                and (not latest_only)
                and statuses
                and str(e.get("Status") or "").strip().lower() not in statuses
            ):
                ok = False
            if ok and q and not contains_any(e, q):
                ok = False
            if ok and (f_dt or t_dt):
                rd = parse_dt_local(str(e.get("RequestedAt") or ""))
                if not rd:
                    ok = False
                else:
                    if f_dt and rd < f_dt:
                        ok = False
                    if t_dt and rd > t_dt:
                        ok = False
            if ok:
                filtered.append(e)

        # Keep one entity per ExecId based on status priority and recency.
        if latest_only:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for e in filtered:
                e_exec_id = str(e.get("ExecId") or "").strip()
                if e_exec_id:
                    grouped.setdefault(e_exec_id, []).append(e)

            filtered = [executions.latest_log_entity(rows) for rows in grouped.values()]

            if statuses:
                filtered = [
                    e
                    for e in filtered
                    if str(e.get("Status") or "").strip().lower() in statuses
                ]

        # Order by RequestedAt
        def key_dt(e: dict):
            d = parse_dt_local(str(e.get("RequestedAt") or "")) or datetime.min
            return d

        reverse = order != "asc"
        filtered.sort(key=key_dt, reverse=reverse)

        # Apply limits
        if len(filtered) > limit:
            filtered = filtered[:limit]

        hydrated_items = []
        for item in filtered:
            if not include_log_content:
                hydrated_items.append(item)
                continue
            try:
                hydrated_items.append(log_blobs.hydrate_log_field(item))
            except Exception as e:
                logging.error(f"Failed to hydrate log item {item.get('RowKey')}: {e}")
                hydrated_items.append(item)

        body = json.dumps({"items": hydrated_items}, ensure_ascii=False)
        return func.HttpResponse(
            body,
            status_code=200,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    except Exception as e:
        logging.exception("logs_query (binding) failed")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
        )


@app.route(
    route="workers/register",
    methods=[func.HttpMethod.POST],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def register_worker(req: func.HttpRequest) -> func.HttpResponse:
    import utils
    from azure.data.tables import UpdateMode

    expected_key = os.environ.get("CLOUDO_SECRET_KEY")
    request_key = req.headers.get("x-cloudo-key")

    if not expected_key or request_key != expected_key:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    try:
        body = req.get_json()

        capability = (body.get("capability") or body.get("id") or "").strip()
        worker_instance_id = (body.get("worker_id") or "").strip()
        worker_queue = body.get("queue")
        worker_team = team_access.normalize_team(body.get("team"))

        if not capability or not worker_queue or not worker_instance_id:
            return func.HttpResponse(
                "Missing capability, worker_id or url", status_code=400
            )

        if worker_team != DEFAULT_TEAM:
            try:
                team_entity = storage.get_table_client(TABLE_TEAMS).get_entity(
                    partition_key="Team", row_key=worker_team
                )
                if not as_bool(team_entity.get("enabled"), default=True):
                    raise ValueError("Team is disabled")
            except Exception:
                return func.HttpResponse(
                    json.dumps({"error": "Worker team does not exist"}),
                    status_code=400,
                )

        table_client = storage.get_table_client(TABLE_WORKERS_SCHEMAS)

        entity = {
            "PartitionKey": capability,
            "RowKey": worker_instance_id,
            "Queue": worker_queue,
            "LastSeen": utils.utc_now_iso(),
            "Region": body.get("region", "default"),
            "Load": body.get("load", 0),
            "team": worker_team,
        }

        table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

        return func.HttpResponse(
            json.dumps({"status": "registered", "timestamp": entity["LastSeen"]}),
            status_code=200,
        )

    except Exception as e:
        logging.error(f"Register failed: {e}")
        return func.HttpResponse(str(e), status_code=500)


@app.route(
    route="workers",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@app.table_input(
    arg_name="workers",
    table_name=TABLE_WORKERS_SCHEMAS,
    connection=STORAGE_CONN,
)
def list_workers(req: func.HttpRequest, workers: str) -> func.HttpResponse:
    """
    Returns the list of registered workers available in the registry.
    """
    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    try:
        # Parse binding result (can be string or list depending on extension version)
        # Monitoring views (?for=monitoring) show every node; other callers stay team-scoped.
        rows = storage.rows_from_binding(workers) or []
        if req.params.get("for") == "monitoring":
            data = [{**row, "team": team_access.entity_team(row)} for row in rows]
        else:
            data = team_access.filter_entities_by_team(rows, req, session)

        return func.HttpResponse(
            json.dumps(data, ensure_ascii=False),
            status_code=200,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to list workers: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@app.route(
    route="workers/processes",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_worker_processes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy endpoint: calls the worker API from the backend.
    Expected param: worker (hostname/ip:port)
    """
    import requests

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    worker = req.params.get("worker")
    if not worker:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'worker' param"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    # Construct target URL (assuming http protocol for internal workers)
    # If your workers use https or a specific port logic, adjust here.
    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        target_url = f"https://{worker}.azurewebsites.net/api/processes"
    else:
        target_url = f"http://{worker}/api/processes"

    try:
        # Timeout short to avoid blocking the orchestrator for too long
        resp = requests.get(
            target_url,
            headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
            timeout=5,
        )
        body_text = resp.text
        if resp.status_code == 200:
            try:
                parsed_body = resp.json()
                runs = (
                    parsed_body
                    if isinstance(parsed_body, list)
                    else (parsed_body.get("runs") or parsed_body.get("processes") or [])
                )
                can_act = session.get("role") != "VIEWER"
                for run in runs:
                    if isinstance(run, dict):
                        run_team = team_access.normalize_team(run.get("team"))
                        run["team"] = run_team
                        run["can_stop"] = can_act and (
                            team_access.is_admin(session)
                            or run_team == team_access.session_team(session)
                        )
                body_text = json.dumps(parsed_body, ensure_ascii=False)
            except Exception as e:
                logging.warning(f"Could not annotate worker processes: {e}")
        return func.HttpResponse(
            body_text,
            status_code=resp.status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to proxy processes for {worker}: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Failed to reach worker: {str(e)}"}, ensure_ascii=False
            ),
            status_code=502,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@app.route(
    route="schedules",
    methods=[
        func.HttpMethod.GET,
        func.HttpMethod.POST,
        func.HttpMethod.DELETE,
        func.HttpMethod.OPTIONS,
    ],
    auth_level=AUTH,
)
def schedules_management(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    from azure.data.tables import UpdateMode

    table_client = storage.get_table_client(TABLE_SCHEDULES)

    def _is_terraform_locked(entity: Optional[dict[str, Any]]) -> bool:
        if not entity:
            return False
        managed_by = str(entity.get("managed_by") or "").strip().lower()
        locked = as_bool(entity.get("locked"), default=False)
        return managed_by == "terraform" or locked

    # Verification of authentication
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if req.method in ["POST", "DELETE"] and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify schedules"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if req.method == "GET":
        try:
            entities = table_client.query_entities(
                query_filter="PartitionKey eq 'Schedule'"
            )
            entities = team_access.filter_entities_by_team(list(entities), req, session)
            schedules = []
            for e in entities:
                schedules.append(
                    {
                        "id": e.get("RowKey"),
                        "name": e.get("name"),
                        "cron": e.get("cron"),
                        "runbook": e.get("runbook"),
                        "run_args": e.get("run_args"),
                        "queue": e.get("queue"),
                        "worker_pool": e.get("worker_pool"),
                        "enabled": e.get("enabled"),
                        "oncall": e.get("oncall"),
                        "last_run": e.get("last_run"),
                        "managed_by": e.get("managed_by") or "manual",
                        "locked": _is_terraform_locked(e),
                        "team": team_access.entity_team(e),
                    }
                )
            return func.HttpResponse(
                json.dumps(schedules),
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
            if not isinstance(body, dict):
                return func.HttpResponse(
                    json.dumps({"error": "Invalid request body"}),
                    status_code=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            schedule_id = body.get("id") or str(uuid.uuid4())

            existing = None
            try:
                existing = table_client.get_entity(
                    partition_key="Schedule", row_key=schedule_id
                )
            except Exception:
                existing = None

            if existing and not team_access.can_modify_entity(existing, session):
                return func.HttpResponse(
                    json.dumps({"error": "Schedule belongs to another team"}),
                    status_code=403,
                )

            team, team_error = team_access.requested_write_team(body, session, existing)
            if team_error:
                return func.HttpResponse(
                    json.dumps({"error": team_error}), status_code=403
                )

            if _is_terraform_locked(existing):
                immutable_fields = [
                    "name",
                    "cron",
                    "runbook",
                    "run_args",
                    "queue",
                    "worker_pool",
                ]
                for field in immutable_fields:
                    if field in body and (body.get(field) or "") != (
                        existing.get(field) or ""
                    ):
                        return func.HttpResponse(
                            json.dumps(
                                {
                                    "error": "Terraform-managed schedule allows only enabled toggle. Other fields are read-only."
                                }
                            ),
                            status_code=403,
                            headers={"Access-Control-Allow-Origin": "*"},
                        )
                if "oncall" in body and as_bool(
                    body.get("oncall"), default=True
                ) != as_bool(existing.get("oncall"), default=True):
                    return func.HttpResponse(
                        json.dumps(
                            {
                                "error": "Terraform-managed schedule allows only enabled toggle. Other fields are read-only."
                            }
                        ),
                        status_code=403,
                        headers={"Access-Control-Allow-Origin": "*"},
                    )

                entity = {
                    "PartitionKey": "Schedule",
                    "RowKey": schedule_id,
                    "name": existing.get("name"),
                    "cron": existing.get("cron"),
                    "runbook": existing.get("runbook"),
                    "run_args": existing.get("run_args"),
                    "queue": existing.get("queue"),
                    "worker_pool": existing.get("worker_pool"),
                    "enabled": as_bool(
                        body.get("enabled"),
                        default=as_bool(existing.get("enabled"), default=True),
                    ),
                    "oncall": as_bool(existing.get("oncall"), default=True),
                    "last_run": existing.get("last_run", ""),
                    "managed_by": existing.get("managed_by", "terraform"),
                    "locked": as_bool(existing.get("locked"), default=True),
                    "team": team,
                }
            else:
                entity = {
                    "PartitionKey": "Schedule",
                    "RowKey": schedule_id,
                    "name": body.get("name"),
                    "cron": body.get("cron"),
                    "runbook": body.get("runbook"),
                    "run_args": body.get("run_args"),
                    "queue": body.get("queue"),
                    "worker_pool": body.get("worker_pool"),
                    "enabled": as_bool(body.get("enabled"), default=True),
                    "oncall": as_bool(body.get("oncall"), default=True),
                    "last_run": (existing or {}).get("last_run", ""),
                    "managed_by": (existing or {}).get("managed_by", "manual"),
                    "locked": as_bool((existing or {}).get("locked"), default=False),
                    "team": team,
                }
            table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

            audit.log_audit(
                user=session.get("username") or "SYSTEM",
                action="SCHEDULE_UPSERT",
                target=schedule_id,
                details=f"Name: {entity.get('name')}, Cron: {entity.get('cron')}",
            )
            return func.HttpResponse(
                json.dumps({"success": True, "id": schedule_id}),
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
            schedule_id = req.params.get("id")
            if not schedule_id:
                return func.HttpResponse(
                    json.dumps({"error": "Missing id"}),
                    status_code=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            try:
                existing = table_client.get_entity(
                    partition_key="Schedule", row_key=schedule_id
                )
            except Exception:
                return func.HttpResponse(
                    json.dumps({"error": f"Schedule '{schedule_id}' not found"}),
                    status_code=404,
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            if _is_terraform_locked(existing):
                return func.HttpResponse(
                    json.dumps(
                        {
                            "error": "Terraform-managed schedule is read-only and cannot be deleted."
                        }
                    ),
                    status_code=403,
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            if not team_access.can_modify_entity(existing, session):
                return func.HttpResponse(
                    json.dumps({"error": "Schedule belongs to another team"}),
                    status_code=403,
                )

            table_client.delete_entity(partition_key="Schedule", row_key=schedule_id)
            audit.log_audit(
                user=session.get("username") or "SYSTEM",
                action="SCHEDULE_DELETE",
                target=schedule_id,
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


@app.route(
    route="workers/stop",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def stop_worker_process(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy endpoint: calls the worker STOP API from the backend.
    Expected param: worker (hostname/ip:port), exec_id
    """
    import requests

    logging.debug(f"Stop worker process requested. Params: {req.params}")

    if req.method == "OPTIONS":
        return create_cors_response()

    # Security: check session token
    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot stop processes"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    worker = req.params.get("worker")
    exec_id = req.params.get("exec_id")

    if not worker or not exec_id:
        return func.HttpResponse(
            json.dumps(
                {"error": "Missing 'worker' or 'exec_id' param"}, ensure_ascii=False
            ),
            status_code=400,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )

    if os.getenv("LOCAL_DEV", "false").lower() != "true":
        target_url = (
            f"https://{worker}.azurewebsites.net/api/processes/stop?exec_id={exec_id}"
        )
    else:
        target_url = f"http://{worker}/api/processes/stop?exec_id={exec_id}"

    try:
        if not team_access.is_admin(session):
            # Non-admins can only stop runs of their own team.
            list_url = target_url.split("/processes/stop")[0] + "/processes"
            listing = requests.get(
                list_url,
                headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
                timeout=5,
            )
            listing.raise_for_status()
            payload = listing.json()
            runs = (
                payload
                if isinstance(payload, list)
                else (payload.get("runs") or payload.get("processes") or [])
            )
            run = next(
                (
                    r
                    for r in runs
                    if isinstance(r, dict) and str(r.get("exec_id")) == str(exec_id)
                ),
                None,
            )
            if run and team_access.normalize_team(
                run.get("team")
            ) != team_access.session_team(session):
                return func.HttpResponse(
                    json.dumps({"error": "Execution belongs to another team"}),
                    status_code=403,
                    mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
        resp = requests.delete(
            target_url,
            headers={"x-cloudo-key": os.getenv("CLOUDO_SECRET_KEY")},
            timeout=5,
        )
        return func.HttpResponse(
            resp.text,
            status_code=resp.status_code,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        logging.error(f"Failed to proxy stop for {worker}/{exec_id}: {e}")
        return func.HttpResponse(
            json.dumps(
                {"error": f"Failed to reach worker: {str(e)}"}, ensure_ascii=False
            ),
            status_code=502,
            mimetype="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
            },
        )


@app.route(
    route="schemas",
    methods=[
        func.HttpMethod.GET,
        func.HttpMethod.POST,
        func.HttpMethod.OPTIONS,
        func.HttpMethod.DELETE,
        func.HttpMethod.PUT,
    ],
    auth_level=AUTH,
)
@app.table_input(arg_name="entities", table_name=TABLE_SCHEMAS, connection=STORAGE_CONN)
@app.table_output(
    arg_name="outputTable", table_name=TABLE_SCHEMAS, connection=STORAGE_CONN
)
def runbook_schemas(
    req: func.HttpRequest, entities: str, outputTable: func.Out[str]
) -> func.HttpResponse:
    logging.debug(f"Processing {req.method} request for schemas.")

    if req.method == "OPTIONS":
        return create_cors_response()

    session, error_res = security.get_authenticated_user(req)
    if error_res:
        return error_res

    if req.method in ["POST", "PUT", "DELETE"] and session.get("role") == "VIEWER":
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized: Viewer cannot modify schemas"}),
            status_code=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    requester_username = session.get("username")

    def as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    if req.method == "GET":
        try:
            schemas_data = json.loads(entities)
            if isinstance(schemas_data, list):
                schemas_data = team_access.filter_entities_by_team(
                    schemas_data, req, session
                )
                for item in schemas_data:
                    if isinstance(item, dict):
                        item["enabled"] = as_bool(item.get("enabled"), default=True)
                        item["team"] = team_access.entity_team(item)
            logging.debug(f"schemas: {str(schemas_data)}")

            return func.HttpResponse(
                body=json.dumps(schemas_data),
                status_code=200,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )
        except Exception as e:
            logging.error(f"Error processing schemas: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": "Failed to fetch schemas"}),
                status_code=500,
                mimetype="application/json",
            )

    if req.method == "POST":
        try:
            body = req.get_json()
            if not isinstance(body, dict):
                return func.HttpResponse(
                    body=json.dumps({"error": "Invalid request body"}),
                    status_code=400,
                    mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            schema_id = body.get("id", str(uuid.uuid4()))
            try:
                existing_schema = storage.get_table_client(
                    TABLE_SCHEMAS
                ).query_entities(
                    query_filter="id eq @schema_id",
                    parameters={
                        "schema_id": schema_id,
                    },
                )
                existing_schema = next(iter(existing_schema), None)
            except Exception as e:
                existing_schema = None
                logging.info(
                    f"Existing schema not found: {existing_schema}, error: {str(e)}"
                )
            if existing_schema is not None:
                return func.HttpResponse(
                    body=json.dumps(
                        {
                            "error": (
                                "Schema already exists on team "
                                f"{existing_schema.get('team') or 'default'}"
                            ),
                        }
                    ),
                    status_code=409,
                    mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            team, team_error = team_access.requested_write_team(body, session)
            if team_error:
                return func.HttpResponse(
                    body=json.dumps({"error": team_error}), status_code=403
                )
            new_entity = {
                "PartitionKey": body.get("PartitionKey", "RunbookSchema"),
                "RowKey": schema_id,
                **body,
                "enabled": as_bool(body.get("enabled"), default=True),
                "team": team,
            }

            outputTable.set(json.dumps(new_entity))

            # Audit log
            audit.log_audit(
                user=requester_username or "SYSTEM",
                action="SCHEMA_CREATE",
                target=schema_id,
                details=f"Name: {body.get('name')}, Runbook: {body.get('runbook')}",
            )

            return func.HttpResponse(
                body=json.dumps(new_entity),
                status_code=201,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json",
                },
            )
        except Exception as e:
            logging.error(f"Error creating schema: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": "Failed to create schema"}),
                status_code=400,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

    if req.method == "PUT":
        try:
            from azure.data.tables import UpdateMode

            body = req.get_json()
            if not isinstance(body, dict):
                return func.HttpResponse(
                    body=json.dumps({"error": "Invalid request body"}),
                    status_code=400,
                    mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            schema_id = body.get("id")

            if not schema_id:
                return func.HttpResponse(
                    body=json.dumps({"error": "Missing 'id' field"}),
                    status_code=400,
                    mimetype="application/json",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                    },
                )

            table_client = storage.get_table_client(TABLE_SCHEMAS)
            try:
                existing_entity = table_client.get_entity(
                    partition_key=body.get("PartitionKey", "RunbookSchema"),
                    row_key=schema_id,
                )
            except Exception:
                existing_entity = None
            if existing_entity and not team_access.can_modify_entity(
                existing_entity, session
            ):
                return func.HttpResponse(
                    body=json.dumps({"error": "Schema belongs to another team"}),
                    status_code=403,
                )
            team, team_error = team_access.requested_write_team(
                body, session, existing_entity
            )
            if team_error:
                return func.HttpResponse(
                    body=json.dumps({"error": team_error}), status_code=403
                )

            updated_entity = {
                "PartitionKey": body.get("PartitionKey", "RunbookSchema"),
                "RowKey": schema_id,
                **body,
                "enabled": as_bool(body.get("enabled"), default=True),
                "team": team,
            }

            table_client = storage.get_table_client(TABLE_SCHEMAS)
            table_client.upsert_entity(entity=updated_entity, mode=UpdateMode.REPLACE)

            # Audit log
            audit.log_audit(
                user=requester_username or "SYSTEM",
                action="SCHEMA_UPDATE",
                target=schema_id,
                details=f"Name: {body.get('name')}",
            )

            return func.HttpResponse(
                body=json.dumps(updated_entity),
                status_code=200,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json",
                },
            )
        except Exception as e:
            logging.error(f"Error updating schema: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": f"Failed to update schema: {str(e)}"}),
                status_code=400,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

    if req.method == "DELETE":
        try:
            # Try to get schema_id from query params first, then body
            schema_id = req.params.get("id")
            partition_key = req.params.get("PartitionKey", "RunbookSchema")

            if not schema_id:
                try:
                    body = req.get_json()
                    schema_id = body.get("id")
                    partition_key = body.get("PartitionKey", "RunbookSchema")
                except Exception:
                    pass

            if not schema_id:
                return func.HttpResponse(
                    body=json.dumps({"error": "Missing 'id' field in params or body"}),
                    status_code=400,
                    mimetype="application/json",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                    },
                )

            table_client = storage.get_table_client(TABLE_SCHEMAS)
            try:
                existing_entity = table_client.get_entity(
                    partition_key=partition_key, row_key=schema_id
                )
            except Exception:
                existing_entity = None
            if existing_entity and not team_access.can_modify_entity(
                existing_entity, session
            ):
                return func.HttpResponse(
                    body=json.dumps({"error": "Schema belongs to another team"}),
                    status_code=403,
                )
            table_client.delete_entity(partition_key=partition_key, row_key=schema_id)

            # Audit log
            audit.log_audit(
                user=requester_username or "SYSTEM",
                action="SCHEMA_DELETE",
                target=schema_id,
                details=f"PartitionKey: {partition_key}",
            )

            return func.HttpResponse(
                body=json.dumps(
                    {"message": "Schema deleted successfully", "id": schema_id}
                ),
                status_code=200,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json",
                },
            )
        except Exception as e:
            logging.error(f"Error deleting schema: {str(e)}")
            return func.HttpResponse(
                body=json.dumps({"error": f"Failed to delete schema: {str(e)}"}),
                status_code=400,
                mimetype="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

    return func.HttpResponse(
        body=json.dumps({"error": "Method not allowed"}),
        status_code=405,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route(
    route="runbooks/content",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_runbook_content(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    script_name = req.params.get("name")
    if not script_name:
        return func.HttpResponse(
            json.dumps({"error": "Query parameter 'name' is required"}),
            status_code=400,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    owner_repo = (GITHUB_REPO or "").strip()
    if not owner_repo or "/" not in owner_repo:
        return func.HttpResponse(
            json.dumps({"error": "GITHUB_REPO not configured correctly"}),
            status_code=500,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    branch = (GITHUB_BRANCH or "main").strip()
    prefix = (GITHUB_PATH_PREFIX or "").strip().strip("/")
    path_parts = [p for p in [prefix, script_name] if p]
    repo_path = "/".join(path_parts)

    content_text = None
    error_msg = "File not found"

    # LOCAL_DEV: read from local file system
    if os.getenv("LOCAL_DEV", "false").lower() == "true":
        try:
            # Check if a dev script path is explicitly set (e.g. in Docker)
            dev_script_path = os.getenv("DEV_SCRIPT_PATH")
            if dev_script_path:
                local_path = os.path.join(dev_script_path, script_name)
            else:
                # Fallback to relative path discovery for local development
                # We assume runbooks are in src/runbooks relative to project root.
                # The function app runs in src/core/orchestrator.
                # __file__ is src/core/orchestrator/function_app.py
                base_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                local_path = os.path.join(base_dir, "src", "runbooks", script_name)

            if os.path.exists(local_path):
                with open(local_path, encoding="utf-8") as f:
                    content_text = f.read()
                logging.debug(f"Loaded runbook from local path: {local_path}")
            else:
                logging.error(f"Local runbook not found at {local_path}")
        except Exception as e:
            logging.error(f"Error reading local runbook: {e}")

    if content_text is not None:
        return func.HttpResponse(
            json.dumps({"content": content_text}),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # We try both Contents API and Raw download
    import requests

    headers_list = []
    base_headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers_list.append({**base_headers, "Authorization": f"Bearer {GITHUB_TOKEN}"})
        headers_list.append({**base_headers, "Authorization": f"token {GITHUB_TOKEN}"})
    else:
        headers_list.append(base_headers)

    content_text = None
    error_msg = "File not found"

    # Try Contents API first
    api_url = f"https://api.github.com/repos/{owner_repo}/contents/{repo_path}"
    for h in headers_list:
        try:
            resp = requests.get(api_url, headers=h, params={"ref": branch}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if (
                    isinstance(data, dict)
                    and data.get("encoding") == "base64"
                    and "content" in data
                ):
                    import base64

                    content_text = base64.b64decode(
                        data["content"].replace("\n", "")
                    ).decode("utf-8")
                    break
            elif resp.status_code in (401, 403):
                error_msg = f"GitHub Auth Error: {resp.status_code}"
                continue
        except Exception as e:
            logging.error(f"GitHub API error: {e}")

    # Fallback to Raw
    if content_text is None:
        raw_url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{repo_path}"
        for h in headers_list:
            try:
                resp = requests.get(raw_url, headers=h, timeout=10)
                if resp.status_code == 200:
                    content_text = resp.text
                    break
            except Exception as e:
                logging.error(f"GitHub Raw error: {e}")

    if content_text is not None:
        return func.HttpResponse(
            json.dumps({"content": content_text}),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    else:
        return func.HttpResponse(
            json.dumps({"error": error_msg}),
            status_code=404,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )


@app.route(
    route="runbooks/list",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def list_runbooks(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    runbooks = []

    # LOCAL_DEV: list from local file system
    if os.getenv("LOCAL_DEV", "false").lower() == "true":
        try:
            dev_script_path = os.getenv("DEV_SCRIPT_PATH")
            if dev_script_path:
                local_dir = dev_script_path
            else:
                base_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                local_dir = os.path.join(base_dir, "src", "runbooks")

            if os.path.exists(local_dir):
                for root, dirs, files in os.walk(local_dir):
                    for file in files:
                        if file.endswith(".sh") or file.endswith(".py"):
                            rel_path = os.path.relpath(
                                os.path.join(root, file), local_dir
                            )
                            runbooks.append(rel_path)
                logging.info(f"Listed runbooks from local path: {local_dir}")
        except Exception as e:
            logging.error(f"Error listing local runbooks: {e}")

    # If no local runbooks found or not in DEV, try GitHub
    if not runbooks:
        owner_repo = (GITHUB_REPO or "").strip()
        branch = (GITHUB_BRANCH or "main").strip()
        prefix = (GITHUB_PATH_PREFIX or "").strip().strip("/")

        if owner_repo and "/" in owner_repo:
            import requests

            headers_list = []
            base_headers = {"Accept": "application/vnd.github.v3+json"}
            if GITHUB_TOKEN:
                headers_list.append(
                    {**base_headers, "Authorization": f"Bearer {GITHUB_TOKEN}"}
                )
                headers_list.append(
                    {**base_headers, "Authorization": f"token {GITHUB_TOKEN}"}
                )
            else:
                headers_list.append(base_headers)

            api_url = f"https://api.github.com/repos/{owner_repo}/git/trees/{branch}?recursive=1"
            for h in headers_list:
                try:
                    resp = requests.get(api_url, headers=h, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        tree = data.get("tree", [])
                        for item in tree:
                            path = item.get("path", "")
                            # Filter by prefix and extension
                            if path.startswith(prefix) and (
                                path.endswith(".sh") or path.endswith(".py")
                            ):
                                # If prefix is present, remove it from the path to get relative path
                                if prefix:
                                    prefix_len = len(prefix)
                                    rel_path = path[prefix_len:].lstrip("/")
                                    if rel_path:
                                        runbooks.append(rel_path)
                                else:
                                    runbooks.append(path)
                        break
                except Exception as e:
                    logging.error(f"GitHub API list error: {e}")

    return func.HttpResponse(
        json.dumps({"runbooks": sorted(list(set(runbooks)))}),
        status_code=200,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.schedule(
    schedule="0 */1 * * * *",
    arg_name="schedulerTimer",
    run_on_startup=False,
    use_monitor=False,
)
def scheduler_engine(schedulerTimer: func.TimerRequest) -> None:
    """
    Scheduler Engine: Check for scheduled runbooks and execute them.
    """
    import logging
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from utils import format_requested_at, is_cron_now, today_partition_key

    table_client = storage.get_table_client(TABLE_SCHEDULES)
    log_table_client = storage.get_table_client(TABLE_NAME)
    workers_table = storage.get_table_client(TABLE_WORKERS_SCHEMAS)

    try:
        worker_pool_to_queue: dict[tuple[str, str], str] = {}
        try:
            workers = workers_table.query_entities(
                query_filter="PartitionKey ne ''",
                select=["PartitionKey", "Queue", "team"],
            )
            for w in workers:
                pool = str(w.get("PartitionKey") or "").strip()
                queue_name = str(w.get("Queue") or "").strip()
                worker_team = team_access.entity_team(w)
                if (
                    pool
                    and queue_name
                    and (pool, worker_team) not in worker_pool_to_queue
                ):
                    worker_pool_to_queue[(pool, worker_team)] = queue_name
        except Exception as werr:
            logging.error(f"[Scheduler] Failed to load WorkersRegistry map: {werr}")

        schedules = table_client.query_entities(
            query_filter="PartitionKey eq 'Schedule'"
        )
        now = datetime.now(ZoneInfo("Europe/Rome"))
        # FastAPI-based scheduler can drift by 1-2s from the minute boundary.
        # Normalize seconds for cron matching to avoid missing "0 * * * * *" jobs.
        now_for_cron = now.replace(second=0, microsecond=0)

        for s in schedules:
            # Terraform-backed entities can store booleans as strings ("true"/"false").
            if not as_bool(s.get("enabled"), default=True):
                continue

            cron_expr = s.get("cron", "0 */1 * * * *")
            last_run_str = s.get("last_run", "")
            oncall_enabled = as_bool(s.get("oncall"), default=True)

            should_run_by_cron = is_cron_now(cron_expr, now_for_cron)

            should_run = False
            if should_run_by_cron:
                if not last_run_str:
                    should_run = True
                else:
                    last_run_dt = datetime.fromisoformat(
                        last_run_str.replace("Z", "+02:00")
                    )
                    if (now - last_run_dt).total_seconds() >= 45:
                        should_run = True

            if should_run:
                exec_id = str(uuid.uuid4())
                logging.info(
                    f"[Scheduler] Triggering {s['name']} (ID: {s['RowKey']}) -> ExecId: {exec_id}"
                )

                worker_pool = s.get("worker_pool")
                schedule_team = team_access.entity_team(s)
                target_queue = "cloudo-default"

                if worker_pool:
                    target_queue = worker_pool_to_queue.get(
                        (worker_pool, schedule_team),
                        worker_pool_to_queue.get(
                            (worker_pool, DEFAULT_TEAM), target_queue
                        ),
                    )

                requested_at = format_requested_at()
                partition_key = today_partition_key()

                queue_payload = {
                    "runbook": s.get("runbook"),
                    "run_args": s.get("run_args"),
                    "worker": worker_pool,
                    "group": "-",
                    "exec_id": exec_id,
                    "id": s.get("RowKey"),
                    "name": s.get("name"),
                    "status": "scheduled",
                    "oncall": oncall_enabled,
                    "require_approval": False,
                    "requested_at": requested_at,
                    "team": team_access.entity_team(s),
                }

                try:
                    log_entry = executions.build_log_entry(
                        status="scheduled",
                        partition_key=partition_key,
                        row_key=str(uuid.uuid4()),
                        exec_id=exec_id,
                        requested_at=requested_at,
                        name=s.get("name"),
                        schema_id=s.get("RowKey"),
                        runbook=s.get("runbook"),
                        run_args=s.get("run_args"),
                        worker=worker_pool,
                        group="-",
                        oncall=oncall_enabled,
                        log_msg=json.dumps(
                            {
                                "status": "scheduled",
                                "queue": target_queue,
                            },
                            ensure_ascii=False,
                        ),
                        monitor_condition="",
                        severity="",
                        team=team_access.entity_team(s),
                    )
                    log_table_client.create_entity(entity=log_entry)
                except Exception as le:
                    logging.error(f"[Scheduler] Failed to log scheduled status: {le}")

                q_name = target_queue
                queue_service = storage.get_queue_client(q_name)
                try:
                    queue_service.send_message(
                        json.dumps(queue_payload, ensure_ascii=False)
                    )
                except Exception as qe:
                    if "QueueNotFound" in str(qe):
                        logging.warning(f"[Scheduler] Queue {q_name} not found")
                    else:
                        raise qe

                s["last_run"] = now.isoformat()
                table_client.update_entity(entity=s)

    except Exception as e:
        logging.error(f"[Scheduler] Error: {e}")


@app.schedule(
    schedule="0 */1 * * * *",
    arg_name="cleanupTimer",
    run_on_startup=False,
    use_monitor=False,
)
def worker_cleanup(cleanupTimer: func.TimerRequest) -> None:
    """
    Garbage Collector: Cleanup old workers where LastSeen is > 3 minutes.
    """
    import utils

    table_client = storage.get_table_client(TABLE_WORKERS_SCHEMAS)

    now_str = utils.utc_now_iso()
    now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
    limit_time = now_dt - timedelta(minutes=3)
    limit_iso = limit_time.isoformat()
    logging.debug(f"[Cleanup] Cleaning up {limit_iso}")

    filter_query = f"LastSeen lt '{limit_iso}'"

    logging.debug(f"[Cleanup] Searching for zombies older than {limit_iso}...")

    try:
        dead_workers = table_client.query_entities(query_filter=filter_query)

        count = 0
        for w in dead_workers:
            table_client.delete_entity(
                partition_key=w["PartitionKey"], row_key=w["RowKey"]
            )
            logging.debug(
                f"[Cleanup] Deleted zombie: {w['RowKey']} (Partition: {w['PartitionKey']})"
            )
            count += 1

        logging.debug(f"[Cleanup] Completed. Removed {count} workers.")

    except Exception as e:
        logging.error(f"[Cleanup] Failed: {e}")
