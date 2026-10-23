"""Runbook queue consumer: deduplication, AKS login and execution."""

import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
from typing import Any, Optional

import azure.functions as func
from core.config import QUEUE_NAME, STORAGE_CONNECTION, WORKER_TEAM
from utils import _format_requested_at

from core import executor, registry, reporting

bp = func.Blueprint()


def _parse_resource_info(value: Any, exec_id: str) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            logging.warning("[%s] resource_info not valid JSON", exec_id)
    return {}


def _inspect_duplicate_runs(items: list[Any], payload: Any) -> Optional[str]:
    payload_group = str(payload.get("group") or "-").strip().lower()

    for item in items:
        item_group = str(item.get("group") or "-").strip().lower()

        # Non-default groups are mutually exclusive: only one run at a time.
        if payload_group != "-" and item_group == payload_group:
            return f"group '{payload_group}' is already running"

        # Keep duplicate run protection for identical requests.
        if (
            item.get("name") == payload.get("name")
            and item.get("runbook") == payload.get("runbook")
            and item.get("run_args") == payload.get("run_args")
            and item.get("resource_info") == payload.get("resource_info")
            and item_group == payload_group
        ):
            return "an identical execution is already in progress"

    return None


@bp.queue_trigger(arg_name="msg", queue_name=QUEUE_NAME, connection=STORAGE_CONNECTION)
def process_runbook(msg: func.QueueMessage) -> None:
    payload = json.loads(msg.get_body().decode("utf-8"))
    exec_id = payload.get("exec_id") or ""
    initiator = str(payload.get("initiator") or payload.get("oncall") or "SYSTEM")
    log_prefix = f"[{exec_id}] [initiator={initiator}]"

    logging.info(f"{log_prefix} Job started: %s", payload)

    started_at = _format_requested_at()

    # Check if this execution is already running
    with registry.ACTIVE_LOCK:
        items = list(registry.ACTIVE_RUNS.values())
        skip_reason = _inspect_duplicate_runs(items, payload)
        if skip_reason:
            log_msg = f"Execution {exec_id} skipped: {skip_reason}"
            logging.info(f"{log_prefix} {log_msg}")
            reporting.dispatch_status(payload, status="skipped", log_message=log_msg)
            return

    # Register the execution as "in progress" and notify
    with registry.ACTIVE_LOCK:
        registry.ACTIVE_RUNS[exec_id] = {
            "exec_id": exec_id,
            "id": payload.get("id"),
            "name": payload.get("name"),
            "runbook": payload.get("runbook"),
            "run_args": payload.get("run_args"),
            "worker": payload.get("worker"),
            "group": payload.get("group"),
            "initiator": payload.get("initiator"),
            "requestedAt": payload.get("requestedAt"),
            "startedAt": started_at,
            "resource_info": payload.get("resource_info") or {},
            "team": payload.get("team") or WORKER_TEAM,
            "status": "running",
        }

    log_msg = f"{log_prefix} Job {payload.get('name')} started"
    reporting.dispatch_status(payload, status="running", log_message=log_msg)
    logging.info(f"{log_prefix} Receiver response: status=running")

    # Local environment to avoid race conditions in multithreading
    env = os.environ.copy()
    execution_temp_dir = tempfile.mkdtemp(prefix=f"cloudo-{exec_id}-")

    try:
        info = _parse_resource_info(payload.get("resource_info"), exec_id)
        kubeconfig_path = None
        aks_login_required = (
            str(payload.get("aks_login_required", "false")).strip().lower() == "true"
        )

        invalid_aks_values = {"", "null", "none", "undefined"}
        for info_key, schema_key, env_key in (
            ("resource_name", "aks_cluster_name", "RESOURCE_NAME"),
            ("resource_rg", "aks_resource_group", "RESOURCE_RG"),
            ("aks_namespace", "aks_namespace", "AKS_NAMESPACE"),
        ):
            value = str(info.get(info_key) or "").strip()
            if value.lower() in invalid_aks_values:
                value = str(payload.get(schema_key) or "").strip()
            if value.lower() in invalid_aks_values:
                value = str(env.get(env_key) or "").strip()
            info[info_key] = value

        if aks_login_required:
            aks_integration_enabled = (
                os.environ.get("AKS_INTEGRATION_ENABLED", "").strip().lower()
            )
            if aks_integration_enabled == "false":
                err_msg = (
                    f"{log_prefix} AKS login failed: tf var AKS_INTEGRATION "
                    "not defined for this deployment"
                )
                reporting.dispatch_status(payload, status="error", log_message=err_msg)
                logging.error(err_msg)
                return

            required_aks_settings = {
                "resource_rg": "resource group (RESOURCE_RG)",
                "resource_name": "cluster name (RESOURCE_NAME)",
                "aks_namespace": "namespace (AKS_NAMESPACE)",
            }
            missing_settings = [
                label
                for key, label in required_aks_settings.items()
                if not str(info.get(key) or "").strip()
            ]
            if missing_settings:
                err_msg = (
                    f"{log_prefix} AKS login failed: missing "
                    f"{', '.join(missing_settings)} in resource_info, schema "
                    "definition, or worker environment"
                )
                reporting.dispatch_status(payload, status="error", log_message=err_msg)
                logging.error(err_msg)
                return

            try:
                kubeconfig_path = executor.run_aks_login(
                    info, payload, env=env, temp_dir=execution_temp_dir
                )
                logging.info(f"{log_prefix} AKS login completed successfully")
            except Exception as e:
                # Report error and stop processing
                err_msg = f"{log_prefix} AKS login failed: {type(e).__name__}: {e}"
                reporting.dispatch_status(payload, status="error", log_message=err_msg)
                logging.error(f"{err_msg}")
                return
        else:
            logging.info(f"{log_prefix} AKS login skipped: not required by schema")

        result = executor.run_script(
            script_name=payload.get("runbook"),
            script_path=os.getenv("DEV_SCRIPT_PATH"),
            run_args=payload.get("run_args"),
            resource_info=info,
            monitor_condition=payload.get("monitor_condition"),
            payload=payload,
            kubeconfig_path=kubeconfig_path,
            env=env,
            temp_dir=execution_temp_dir,
        )
        stopped = False
        try:
            if os.name != "nt" and result:
                stopped = result.returncode == -getattr(signal, "SIGTERM", 15)
        except Exception as e:
            logging.error(e)

        if not stopped:
            log_msg = f"{result.stdout.strip() if result else 'No output'}"
            reporting.dispatch_status(payload, status="succeeded", log_message=log_msg)

    except subprocess.CalledProcessError as e:
        error_message = f"Script failed. returncode={e.returncode} stderr={e.stderr.strip()} stdout={e.stdout.strip()}"
        try:
            reporting.dispatch_status(
                payload, status="failed", log_message=error_message
            )
            logging.error(
                f"{log_prefix} Receiver response: status=queued",
            )
        finally:
            logging.error(f"{log_prefix} {error_message}")
    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)}"
        try:
            reporting.dispatch_status(payload, status="error", log_message=err_msg)
            logging.error(
                f"{log_prefix} Receiver response: status=queued",
            )
        finally:
            logging.error(f"{log_prefix} Unexpected error: %s", err_msg)
    finally:
        try:
            if os.path.isdir(execution_temp_dir):
                shutil.rmtree(execution_temp_dir, ignore_errors=True)
        except Exception as e:
            logging.warning(
                f"{log_prefix} failed to cleanup temp dir {execution_temp_dir}: {e}"
            )

        # Remove from the registry: no longer "in progress"
        logging.info(
            "%s Job complete (requested at %s)",
            log_prefix,
            payload.get("requestedAt"),
        )
        with registry.ACTIVE_LOCK:
            registry.ACTIVE_RUNS.pop(exec_id, None)
