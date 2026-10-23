"""Development-only endpoint to test-run a runbook without a schema."""

import json
import logging
import os
import uuid

import azure.functions as func
from core.config import AUTH, STORAGE_CONN, TABLE_NAME, TABLE_WORKERS_SCHEMAS

from core import audit, executions, security, storage

bp = func.Blueprint()


@bp.route(route="dev/testrun", auth_level=AUTH)
@bp.table_output(
    arg_name="log_table",
    table_name=TABLE_NAME,
    connection=STORAGE_CONN,
)
@bp.table_input(
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
