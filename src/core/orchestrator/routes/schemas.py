"""Runbook schema CRUD endpoints."""

import json
import logging
import uuid
from typing import Any

import azure.functions as func
from core.config import AUTH, STORAGE_CONN, TABLE_SCHEMAS
from utils import create_cors_response

from core import audit, security, storage, team_access

bp = func.Blueprint()


@bp.route(
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
@bp.table_input(arg_name="entities", table_name=TABLE_SCHEMAS, connection=STORAGE_CONN)
@bp.table_output(
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
