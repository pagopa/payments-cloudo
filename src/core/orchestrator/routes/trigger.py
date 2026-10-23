"""Runbook trigger endpoint: schema resolution, approval gate and worker dispatch."""

import json
import logging
import os
import types
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import azure.functions as func
from core.config import (
    APPROVAL_TTL_MIN,
    DEFAULT_TEAM,
    NOTIFICATION_QUEUE_NAME,
    STORAGE_CONN,
    STORAGE_CONNECTION,
    TABLE_NAME,
    TABLE_SCHEMAS,
    TABLE_WORKERS_SCHEMAS,
)
from models import Schema
from utils import as_bool, create_cors_response

from core import (
    audit,
    executions,
    notify,
    schema_lookup,
    security,
    storage,
    team_access,
)

bp = func.Blueprint()

_NOTIFICATION_QUEUE_READY = False


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


@bp.route(
    route="Trigger/{team?}",
    methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@bp.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@bp.table_input(
    arg_name="entities",
    table_name=TABLE_SCHEMAS,
    connection=STORAGE_CONN,
)
@bp.table_input(
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

    schema_entity = schema_lookup.find_schema_entity(parsed, schema_id)
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
        f"{log_prefix} Getting schema entity id '{schema_lookup.get_entity_id(schema_entity)}'"
    )
    # Build domain model
    schema = Schema(
        id=schema_lookup.get_entity_id(schema_entity),
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
