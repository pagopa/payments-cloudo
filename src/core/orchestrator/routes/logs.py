"""Execution log and AI analysis query endpoints."""

import json
import logging
from datetime import datetime
from typing import Any

import azure.functions as func
from core.config import (
    AUTH,
    DEFAULT_TEAM,
    STORAGE_CONNECTION,
    TABLE_AI_ANALYSIS,
    TABLE_NAME,
)
from utils import create_cors_response

from core import executions, log_blobs, security, storage, team_access

bp = func.Blueprint()


@bp.route(route="logs/{partitionKey}/{execId}", auth_level=AUTH)
@bp.table_input(
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


@bp.route(route="ai-analysis/{execId}", auth_level=AUTH)
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
@bp.route(
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
