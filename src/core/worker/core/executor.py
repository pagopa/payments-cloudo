"""Runbook process execution, including the optional AKS login."""

import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from subprocess import CompletedProcess
from typing import Any, Optional

from core.config import LOG_STREAM_UPDATE_SECONDS

from core import registry, reporting, runbook_source


def _clean_path(p: Optional[str]) -> Optional[str]:
    if p is None:
        return None
    s = str(p).strip().strip('"').strip("'")
    if not s:
        return None
    s = os.path.expanduser(s)
    s = os.path.normpath(s)
    return s


def run_aks_login(
    resource_info: dict,
    payload: dict = None,
    env: dict = None,
    temp_dir: Optional[str] = None,
) -> str:
    """
    Runs the local AKS login script:
      src/core/worker/utils/aks-login.sh <rg> <name> <namespace>
    Accepts resource_info as dict or JSON string.
    Streams stdout lines to Receiver if payload is provided.
    """

    exec_id = (payload or {}).get("exec_id", "unknown")

    if env is None:
        env = os.environ.copy()

    if isinstance(resource_info, str):
        try:
            resource_info = json.loads(resource_info)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"[{exec_id}] resource_info is not valid JSON: {e}"
            ) from e
    if not isinstance(resource_info, dict):
        raise RuntimeError(f"[{exec_id}] resource_info must be a dict")

    rg = (resource_info.get("resource_rg") or "").strip()
    name = (resource_info.get("resource_name") or "").strip()
    ns = (resource_info.get("aks_namespace") or "").strip()

    if not rg or not name:
        raise RuntimeError(
            f"[{exec_id}] resource_info requires non-empty 'resource_rg' and 'resource_name'"
        )

    script_path = os.path.normpath("utils/aks-login.sh")
    if not os.path.exists(script_path):
        raise FileNotFoundError(
            f"[{exec_id}] AKS login script not found: {script_path}"
        )

    with tempfile.NamedTemporaryFile(
        prefix="kube-", delete=False, dir=temp_dir
    ) as tmp_file:
        kubeconfig_path = tmp_file.name

    cmd = (
        [script_path, kubeconfig_path, rg, name, ns]
        if ns
        else [script_path, kubeconfig_path, rg, name]
    )
    logging.info(f"[{exec_id}] Running AKS login: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
        )

        collected_stdout = []
        if proc.stdout:
            for line in proc.stdout:
                if not line:
                    continue
                msg = line.rstrip()
                collected_stdout.append(line)
                logging.info(f"[{exec_id}] {msg}")

        stdout_data = "".join(collected_stdout)
        rc = proc.wait()
        if rc != 0:
            combined_output = stdout_data.strip() or "No output captured from AKS login"
            raise RuntimeError(
                f"[{exec_id}] AKS login error (rc={rc}). Full output:\n{combined_output}"
            )
    except OSError as e:
        raise RuntimeError(f"[{exec_id}] AKS login execution error: {e}") from e

    return kubeconfig_path


def run_script(
    script_name: str,
    run_args: Optional[str],
    script_path: Optional[str] = None,
    resource_info: Optional[dict] = None,
    monitor_condition: Optional[str] = "",
    payload: Optional[dict] = None,
    kubeconfig_path: Optional[str] = None,
    env: Optional[dict] = None,
    temp_dir: Optional[str] = None,
) -> Optional[CompletedProcess[str]]:
    """Run the requested script fetching it from Blob Storage, falling back to the local folder, then GitHub."""
    tmp_path: Optional[str] = None
    github_tmp_path: Optional[str] = None
    github_error: Optional[Exception] = None

    from utils import get_sanitized_env

    def to_str(x) -> str:
        return "" if x is None else str(x)

    # ClouDO Execution Standard Variables
    if env is None:
        env = os.environ.copy()

    env["MONITOR_CONDITION"] = to_str(monitor_condition)
    env["CLOUDO_ENVIRONMENT"] = os.getenv("CLOUDO_ENVIRONMENT", "unknown")
    env["CLOUDO_ENVIRONMENT_SHORT"] = os.getenv("CLOUDO_ENVIRONMENT", "0")[0]

    if payload:
        env["CLOUDO_PAYLOAD"] = json.dumps(payload.get("resource_info") or {})
        env["CLOUDO_EXEC_ID"] = to_str(payload.get("exec_id"))
        env["CLOUDO_REQUESTED_AT"] = to_str(
            payload.get("requestedAt") or payload.get("requested_at")
        )
        env["CLOUDO_NAME"] = to_str(payload.get("name"))
        env["CLOUDO_RUNBOOK"] = to_str(payload.get("runbook"))
        env["CLOUDO_WORKER"] = to_str(payload.get("worker"))
        env["CLOUDO_ONCALL"] = to_str(payload.get("oncall")).lower()

    TERMINATED_CODES = {-signal.SIGTERM} if hasattr(signal, "SIGTERM") else set()

    try:

        def normalize_aks_info(val) -> dict[str, Any]:
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    logging.warning("AKS info string non JSON: %r", val)
            return {}

        info = normalize_aks_info(resource_info)
        logging.debug(f"AKS info: {info}")

        env["RESOURCE_NAME"] = to_str(info.get("resource_name"))
        env["RESOURCE_RG"] = to_str(info.get("resource_rg"))
        env["RESOURCE_ID"] = to_str(info.get("resource_id"))
        env["AKS_NAMESPACE"] = to_str(info.get("aks_namespace"))
        env["AKS_POD"] = to_str(info.get("aks_pod"))
        env["AKS_DEPLOYMENT"] = to_str(info.get("aks_deployment"))
        env["AKS_JOB"] = to_str(info.get("aks_job"))
        env["AKS_HPA"] = to_str(info.get("aks_horizontalpodautoscaler"))
    except Exception as e:
        logging.warning("AKS set env failed: %s", e)

    # Check if script content is provided in payload (for dev/test runs)
    script_content = None
    script_type = "python"  # Default to python
    if payload and isinstance(payload, dict):
        script_content = payload.get("script")
        script_type = payload.get("script_type", "python").lower()

    # If we have inline script content, use it directly (dev/test feature)
    if script_content:
        tmp_path = runbook_source.create_inline_script_temp_file(
            script_content, script_type, temp_dir=temp_dir
        )
        script_path = tmp_path
    # GitHub if not found locally
    elif script_path is None:
        try:
            github_tmp_path = runbook_source.download_from_github(
                script_name, temp_dir=temp_dir
            )
            logging.debug("Downloaded script from GitHub: %s", github_tmp_path)
            script_path = github_tmp_path
        except Exception as e:
            github_error = e
    else:
        base = (_clean_path(script_path) or "").strip()
        name = _clean_path(script_name) or script_name
        if os.path.isabs(name):
            script_path = name
        else:
            script_path = os.path.join(base, name)

    if script_path is None or not os.path.exists(script_path):
        details = []
        if github_error:
            details.append(f"GitHub: {type(github_error).__name__}: {github_error}")
        raise FileNotFoundError(
            f"Script '{script_name}' not found. Checked GitHub. "
            f"Details: {' | '.join(details) if details else 'no extra details'}"
        )

    # Execute
    cmd = (
        [sys.executable, script_path]
        if script_path.lower().endswith(".py")
        else [script_path]
    )
    try:
        if run_args is not None:
            cmd = cmd + shlex.split(run_args)

        script_env = get_sanitized_env(env)
        if kubeconfig_path:
            script_env["KUBECONFIG"] = kubeconfig_path

        execution_cwd = temp_dir or os.path.dirname(script_path) or None

        logging.info("Running script: %s", cmd)
        # STREAMING
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,
            close_fds=True,
            env=script_env,
            cwd=execution_cwd,
        )

        # Record the process to be stopped
        try:
            if payload and payload.get("exec_id"):
                with registry.ACTIVE_LOCK:
                    registry.PROCESS_BY_EXEC[payload["exec_id"]] = proc
        except Exception as e:
            logging.error(f"[{payload.get('exec_id')}] cannot record process: {e}")

        collected_stdout = []
        last_stream_push = time.monotonic()
        streamed_chars = 0
        last_pushed_chars = 0
        running_blob_push_failed = False

        def _push_running_status_if_due(force: bool = False) -> None:
            nonlocal last_stream_push, last_pushed_chars, running_blob_push_failed
            if not payload:
                return
            if LOG_STREAM_UPDATE_SECONDS <= 0 and not force:
                return
            if streamed_chars <= 0:
                return
            if streamed_chars == last_pushed_chars:
                return
            now = time.monotonic()
            if not force and (now - last_stream_push) < LOG_STREAM_UPDATE_SECONDS:
                return
            try:
                reporting.update_running_log_blob(payload, "".join(collected_stdout))
                running_blob_push_failed = False
            except Exception as e:
                # Keep a single warning until the next successful upload.
                if not running_blob_push_failed:
                    logging.warning(
                        "[%s] Failed to update running log blob: %s",
                        payload.get("exec_id"),
                        e,
                    )
                    running_blob_push_failed = True
            finally:
                last_stream_push = now
                last_pushed_chars = streamed_chars

        if proc.stdout:
            for line in proc.stdout:
                if not line:
                    continue
                collected_stdout.append(line)
                streamed_chars += len(line)
                logging.debug(f"[{payload.get('exec_id')}] {line.rstrip()}")
                _push_running_status_if_due()

        stdout_data = "".join(collected_stdout)
        _push_running_status_if_due(force=True)
        stderr_data = ""
        if proc.stderr:
            stderr_data = proc.stderr.read() or ""

        returncode = proc.wait()
        if returncode != 0 and returncode not in TERMINATED_CODES:
            raise subprocess.CalledProcessError(
                returncode=returncode, cmd=cmd, output=stdout_data, stderr=stderr_data
            )
        else:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=returncode,
                stdout=stdout_data,
                stderr=stderr_data,
            )
    finally:
        # Clean up only if paths are valid files
        try:
            if payload and payload.get("exec_id"):
                with registry.ACTIVE_LOCK:
                    registry.PROCESS_BY_EXEC.pop(payload["exec_id"], None)
        except Exception as e:
            logging.error(f"[{payload.get('exec_id')}] remove lock error: {e}")
        for p in (tmp_path, github_tmp_path):
            try:
                if isinstance(p, str) and p and os.path.exists(p):
                    os.remove(p)
            except Exception as e:
                logging.error(
                    f"[{payload.get('exec_id')}] remove path error ({p}): {e}"
                )
        try:
            if kubeconfig_path and os.path.exists(kubeconfig_path):
                os.remove(kubeconfig_path)
        except Exception as e:
            logging.warning(
                f"[{payload.get('exec_id')}] remove kubeconfig path error ({kubeconfig_path}): {e}"
            )
