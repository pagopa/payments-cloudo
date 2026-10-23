"""The execution request derived from an incoming trigger call (alert or manual run)."""

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import azure.functions as func
import detection
from services.smart_routing_api import SmartRoutingApi
from utils import format_requested_at, today_partition_key

from core import executions, notify


@dataclass
class ExecutionRequest:
    exec_id: str
    requested_at: str
    partition_key: str
    initiator: Optional[str]
    resource_name: str = ""
    resource_group: str = ""
    resource_id: str = ""
    monitor_condition: str = ""
    severity: str = ""
    schema_ids: list[str] = field(default_factory=list)
    resource_info: dict[str, Any] = field(default_factory=dict)
    routing_info: dict[str, Any] = field(default_factory=dict)

    @property
    def log_prefix(self) -> str:
        return executions.build_exec_log_prefix(self.exec_id, self.initiator)

    @property
    def primary_schema_id(self) -> str:
        return str(self.schema_ids[0]).strip() if self.schema_ids else ""

    @property
    def is_alert(self) -> bool:
        return bool(self.monitor_condition and self.severity)


def _schema_ids(req: func.HttpRequest, parsed_body: dict) -> list[str]:
    """Schema ids from the alert payload, overridden by an explicit ?id=."""
    schema_id = parsed_body.get("schema_id")
    if (req.params.get("id")) is not None:
        schema_id = detection.extract_schema_id_from_req(req) or schema_id
    if not isinstance(schema_id, list):
        schema_id = [str(schema_id)] if schema_id else []
    return schema_id


def _routing_info(
    req: func.HttpRequest, team: Optional[str], smart_routing: SmartRoutingApi
) -> dict[str, Any]:
    """Notification targets: request overrides, then team config, then defaults."""
    token, channel = notify.get_default_notification_settings()
    return {
        "team": team or "default",
        "slack_token": req.params.get("slack_token")
        or smart_routing.resolve_slack_token(team or "default")
        or token,
        "slack_channel": req.params.get("slack_channel")
        or channel
        or (os.environ.get("SLACK_CHANNEL") or "#cloudo-test").strip(),
        "jsm_token": req.params.get("jsm_api_key")
        or req.params.get("opsgenie_api_key")
        or smart_routing.resolve_jsm_apikey(team or "default"),
    }


def from_http(
    req: func.HttpRequest, session: dict, smart_routing: SmartRoutingApi
) -> ExecutionRequest:
    route_params = getattr(req, "route_params", {}) or {}
    team = route_params.get("team")
    execution = ExecutionRequest(
        exec_id=str(uuid.uuid4()),
        requested_at=format_requested_at(),
        partition_key=today_partition_key(),
        initiator=session.get("username"),
    )
    routing_info = _routing_info(req, team, smart_routing)

    parsed_body = detection.parse_resource_fields(req)
    execution.resource_name = parsed_body.get("resource_name") or ""
    execution.resource_group = parsed_body.get("resource_group") or ""
    execution.resource_id = parsed_body.get("resource_id") or ""
    execution.monitor_condition = parsed_body.get("monitorCondition") or ""
    execution.severity = parsed_body.get("severity") or ""
    execution.schema_ids = _schema_ids(req, parsed_body)
    execution.resource_info = {
        "_raw": parsed_body.get("_raw"),
        "schema_id": execution.schema_ids,
        "monitor_condition": execution.monitor_condition,
        "severity": execution.severity,
        "resource_name": execution.resource_name,
        "resource_rg": execution.resource_group,
        "resource_id": execution.resource_id,
        "aks_namespace": parsed_body.get("namespace"),
        "aks_pod": parsed_body.get("pod"),
        "aks_deployment": parsed_body.get("deployment"),
        "aks_job": parsed_body.get("job"),
        "aks_horizontalpodautoscaler": parsed_body.get("horizontalpodautoscaler"),
        "team": team,
        "payload": parsed_body.get("payload"),
    }
    execution.routing_info = routing_info
    logging.debug(f"{execution.log_prefix} Resource info: %s", execution.resource_info)
    return execution
