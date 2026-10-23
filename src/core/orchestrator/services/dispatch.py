"""Worker dispatch: pick a worker queue, enqueue the run and record its start."""

import json
import logging
import time
from typing import Any, Optional, Union

import azure.functions as func
import escalation
import worker_routing
from models import Schema
from services.execution_request import ExecutionRequest
from services.smart_routing_api import SmartRoutingApi

from core import audit, executions, storage


def build_response_body(
    status_code: int,
    schema: "Schema",
    partition_key: str,
    exec_id: str,
    api_json: Optional[Union[dict, str]],
) -> str:
    # Build the HTTP response payload returned by the Trigger endpoint
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


def _enqueue_run(
    execution: ExecutionRequest, schema: Schema, target_queue: str
) -> tuple[int, dict[str, Any]]:
    """Hand the run to the worker queue; returns (status code, API body)."""
    logging.debug(
        f"{execution.log_prefix} 🎯 Dynamic Routing: Selected Queue '{target_queue}'"
    )
    try:
        queue_payload = {
            "runbook": schema.runbook,
            "run_args": schema.run_args,
            "id": schema.id,
            "name": schema.name or "",
            "requestedAt": execution.requested_at,
            "exec_id": execution.exec_id,
            "oncall": schema.oncall,
            "aks_login_required": schema.aks_login_required,
            "aks_cluster_name": schema.aks_cluster_name,
            "aks_resource_group": schema.aks_resource_group,
            "aks_namespace": schema.aks_namespace,
            "monitor_condition": execution.monitor_condition,
            "severity": execution.severity,
            "team": schema.team,
            "worker": schema.worker,
            "group": schema.group,
            "initiator": execution.initiator,
            "resource_info": execution.resource_info or {},
            "routing_info": execution.routing_info or {},
        }
        storage.enqueue_queue_payload(target_queue, queue_payload)
        audit.log_audit(
            user=execution.initiator,
            action="RUNBOOK_EXECUTE",
            target=execution.exec_id,
            details=f"ID: {schema.id}, Runbook: {schema.runbook}, Args: {schema.run_args}",
        )
        return 202, {"status": "accepted", "queue": target_queue}
    except Exception as e:
        logging.error(f"{execution.log_prefix} ❌ Queue send failed: {e}")
        return 500, {"error": str(e)}


def _decision_teams(decision: Any) -> str:
    return ", ".join(
        dict.fromkeys(a.team for a in decision.actions if getattr(a, "team", None))
    )


def _failure_notification_payload(
    execution: ExecutionRequest,
    schema: Schema,
    status_label: str,
    api_body: dict[str, Any],
    decision: Any,
) -> dict[str, Any]:
    """Slack/JSM payload consumed by smart routing when a dispatch fails."""
    exec_id = execution.exec_id
    severity = execution.severity
    status_emoji = "✅" if status_label == "succeeded" else "❌"
    logs = (
        json.dumps(api_body, ensure_ascii=False)
        if isinstance(api_body, (dict, list))
        else str(api_body)
    )
    return {
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
                        {"type": "mrkdwn", "text": f"*Name:*\n{schema.name or ''}"},
                        {"type": "mrkdwn", "text": f"*Id:*\n{schema.id or ''}"},
                        {"type": "mrkdwn", "text": f"*Group:*\n{schema.group}"},
                        {"type": "mrkdwn", "text": f"*Team:*\n{schema.team}"},
                        {"type": "mrkdwn", "text": f"*ExecId:*\n{exec_id or ''}"},
                        {"type": "mrkdwn", "text": f"*Status:*\n{status_label}"},
                        {"type": "mrkdwn", "text": f"*Severity:*\n{severity or ''}"},
                        {"type": "mrkdwn", "text": f"*OnCall:*\n{schema.oncall or ''}"},
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
                        "text": f"*Logs (truncated):*\n```{logs[:1500]}```",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"*Severity:* {severity}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Teams:* {_decision_teams(decision)}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"Timestamp: <!date^{int(time.time())}^{{date_short}} {{time}}|now>",
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
            "monitor_condition": execution.monitor_condition or "",
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
                "MonitorCondition": execution.monitor_condition,
                "Severity": severity,
                "Teams:": _decision_teams(decision),
            },
            "description": f"{escalation.format_jsm_description(exec_id, execution.resource_info, api_body)}",
        },
    }


def _notify_dispatch_failure(
    execution: ExecutionRequest,
    schema: Schema,
    status_label: str,
    api_body: dict[str, Any],
    start_log: dict[str, Any],
    log_table: func.Out[str],
    smart_routing: SmartRoutingApi,
) -> None:
    """Route the failure through smart routing and record where it was notified."""
    resource_info = execution.resource_info
    ctx = {
        "resourceId": execution.resource_id,
        "resourceGroup": execution.resource_group,
        "resourceName": execution.resource_name,
        "schemaName": (schema.name or ""),
        "severity": execution.severity,
        "namespace": ((resource_info or {}).get("aks_namespace") or ""),
        "oncall": schema.oncall,
        "status": status_label,
        "execId": execution.exec_id,
        "name": schema.name or "",
        "id": schema.id,
        "team": schema.team,
        "routing_info": execution.routing_info,
    }
    decision = smart_routing.route_alert(ctx)
    logging.debug(f"{execution.log_prefix} {decision}")
    payload = _failure_notification_payload(
        execution, schema, status_label, api_body, decision
    )
    try:
        trigger_results = (
            smart_routing.execute_actions(
                decision,
                payload,
                send_slack_fn=lambda token,
                channel,
                **kw: escalation.send_slack_execution(
                    token=token, channel=channel, **kw
                ),
                send_jsm_fn=lambda api_key, **kw: escalation.send_jsm_alert(
                    api_key=api_key, **kw
                ),
            )
            or []
        )
        trigger_notifications = executions.notifications_json(decision, trigger_results)
        if trigger_notifications:
            start_log["Notifications"] = trigger_notifications
            log_table.set(json.dumps(start_log, ensure_ascii=False))
    except Exception as e:
        logging.error(f"{execution.log_prefix} smart routing failed: {e}")


def dispatch_to_worker(
    execution: ExecutionRequest,
    schema: Schema,
    workers: str,
    log_table: func.Out[str],
    smart_routing: SmartRoutingApi,
) -> func.HttpResponse:
    target_queue = worker_routing.worker_routing(workers, schema)
    if target_queue:
        status_code, api_body = _enqueue_run(execution, schema, target_queue)
    else:
        err_msg = f"❌ No workers ({schema.worker}) available and no static queue configured for {schema.id}"
        logging.error(f"{execution.log_prefix} {err_msg}")
        status_code, api_body = 500, {"error": err_msg}

    status_label = "accepted" if status_code == 202 else "error"
    start_log = executions.build_log_entry(
        status=status_label,
        partition_key=execution.partition_key,
        exec_id=execution.exec_id,
        row_key=execution.exec_id,
        requested_at=execution.requested_at,
        name=schema.name or "",
        schema_id=schema.id,
        runbook=schema.runbook,
        run_args=schema.run_args,
        worker=schema.worker,
        group=schema.group,
        log_msg=api_body,
        oncall=schema.oncall,
        initiator=execution.initiator,
        monitor_condition=execution.monitor_condition,
        severity=execution.severity,
        team=schema.team,
        resource_info=execution.resource_info,
    )
    log_table.set(json.dumps(start_log, ensure_ascii=False))

    if status_label != "accepted" and smart_routing.available:
        _notify_dispatch_failure(
            execution,
            schema,
            status_label,
            api_body,
            start_log,
            log_table,
            smart_routing,
        )

    return func.HttpResponse(
        build_response_body(
            status_code=status_code,
            schema=schema,
            partition_key=execution.partition_key,
            exec_id=execution.exec_id,
            api_json=api_body,
        ),
        status_code=status_code,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )
