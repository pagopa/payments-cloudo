import base64
import json
import logging
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from subprocess import CompletedProcess
from threading import Lock
from typing import Any, Optional

import azure.functions as func
import requests
from utils import _format_requested_at, _utc_now_iso, encode_logs

# =========================
# Constants and Utilities
# =========================

# Configuration constants
QUEUE_NAME = os.environ.get("QUEUE_NAME", "queue")
NOTIFICATION_QUEUE_NAME = os.environ.get(
    "NOTIFICATION_QUEUE_NAME", "cloudo-notification"
)
STORAGE_CONNECTION = "AzureWebJobsStorage"
MAX_INLINE_LOG_BYTES = int(
    os.environ.get("MAX_INLINE_LOG_BYTES", "48000")
)  # Keep queue message below Azure Table/queue limits when inlined
LOGS_BLOB_CONTAINER = os.environ.get("LOGS_BLOB_CONTAINER", "runbook-logs")
LOGS_REF_PREFIX = "blobref://"

# GitHub fallback configuration
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pagopa/payments-cloudo")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_PATH_PREFIX = os.environ.get("GITHUB_PATH_PREFIX", "src/runbooks")

_PROCESS_BY_EXEC: dict[str, subprocess.Popen] = {}

if os.getenv("LOCAL_DEV", "false").lower() != "true":
    AUTH = func.AuthLevel.FUNCTION
else:
    AUTH = func.AuthLevel.ANONYMOUS

app = func.FunctionApp()

# In-memory registry of ongoing executions (per instance)
_ACTIVE_RUNS = {}
_ACTIVE_LOCK = Lock()


def _build_status_headers(payload: dict, status: str, log_message: str) -> dict:
    """Build lightweight headers for the Receiver call; move logs into the JSON body."""
    return {
        "runbook": payload.get("runbook"),
        "run_args": payload.get("run_args"),
        "Id": payload.get("id"),
        "Name": payload.get("name"),
        "ExecId": payload.get("exec_id"),
        "Worker": payload.get("worker"),
        "Group": payload.get("group"),
        "Content-Type": "application/json",
        "Status": status,
        "OnCall": payload.get("oncall"),
        "MonitorCondition": payload.get("monitor_condition"),
        "Severity": payload.get("severity"),
        "ResourceInfo": payload.get("resource_info"),
        "RoutingInfo": payload.get("routing_info"),
    }


def _make_worker_log_blob_name(payload: dict, status: str) -> str:
    requested_at = str(payload.get("requestedAt") or "").strip()
    if (
        len(requested_at) >= 10
        and requested_at[4:5] == "-"
        and requested_at[7:8] == "-"
    ):
        partition_key = requested_at[:10].replace("-", "")
    else:
        partition_key = _format_requested_at()[:10].replace("-", "")

    safe_exec_id = str(payload.get("exec_id") or "unknown").strip() or "unknown"
    safe_status_raw = str(status or "UNKNOWN").strip().upper() or "UNKNOWN"
    safe_status = "".join(ch if ch.isalnum() else "_" for ch in safe_status_raw).strip(
        "_"
    )
    safe_status = safe_status or "UNKNOWN"
    return f"{partition_key}/{safe_exec_id}/{safe_status}_{safe_exec_id}.log"


def _post_status(payload: dict, status: str, log_message: str) -> str:
    """POST execution status to the Receiver with logs in the JSON body (truncated if too large)."""
    headers = _build_status_headers(payload, status, log_message)

    log_text = log_message or ""
    log_bytes = encode_logs(log_text)
    log_ref = ""
    if len(log_bytes) > MAX_INLINE_LOG_BYTES:
        try:
            from azure.storage.blob import BlobServiceClient

            blob_name = _make_worker_log_blob_name(payload, status)
            conn_str = os.environ.get(STORAGE_CONNECTION)
            service = BlobServiceClient.from_connection_string(conn_str)
            container = service.get_container_client(LOGS_BLOB_CONTAINER)
            try:
                container.create_container()
            except Exception:
                pass
            blob = container.get_blob_client(blob_name)
            blob.upload_blob(log_text.encode("utf-8", errors="replace"), overwrite=True)
            log_ref = f"{LOGS_REF_PREFIX}{LOGS_BLOB_CONTAINER}/{blob_name}"
            log_bytes = b""
        except Exception as e:
            logging.warning(
                "[%s] worker blob upload failed, fallback to inline logs: %s: %s",
                payload.get("exec_id"),
                type(e).__name__,
                e,
            )
            log_bytes = log_bytes[:MAX_INLINE_LOG_BYTES]

    message = {
        "requestedAt": payload.get("requestedAt"),
        "id": headers.get("Id"),
        "name": headers.get("Name"),
        "exec_id": headers.get("ExecId"),
        "runbook": headers.get("runbook"),
        "run_args": headers.get("run_args"),
        "worker": headers.get("Worker"),
        "group": headers.get("Group"),
        "status": headers.get("Status"),
        "oncall": headers.get("OnCall"),
        "monitor_condition": headers.get("MonitorCondition"),
        "severity": headers.get("Severity"),
        "resource_info": headers.get("ResourceInfo"),
        "routing_info": headers.get("RoutingInfo"),
        "log_ref": log_ref,
        "logs_b64": log_bytes.decode("utf-8"),
        "content_type": "text/plain; charset=utf-8",
        "sent_at": _format_requested_at(),
    }
    return json.dumps(message, ensure_ascii=False)


def _github_auth_headers() -> list[dict]:
    """
    Build alternative auth headers for GitHub:
    - Prefer Bearer (fine-grained tokens)
    - Fallback to 'token' (classic PAT)
    Always include User-Agent and Accept.
    """
    base = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "azure-func-runbook/1.0",
    }
    headers_list: list[dict] = [base.copy()]
    if GITHUB_TOKEN:
        # Try Bearer first
        h1 = base.copy()
        h1["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        headers_list.insert(0, h1)
        # Then classic 'token' scheme
        h2 = base.copy()
        h2["Authorization"] = f"token {GITHUB_TOKEN}"
        headers_list.append(h2)
    return headers_list


def _create_inline_script_temp_file(
    script_content: str, script_type: str = "python", temp_dir: Optional[str] = None
) -> str:
    """
    Create a temporary file with the provided script content.
    For shell scripts, sets executable permissions.
    Returns the path to the temporary file.
    """
    import tempfile

    suffix = ".sh" if script_type == "shell" else ".py"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, dir=temp_dir or "/tmp"
    ) as f:
        f.write(script_content)
        temp_path = f.name

    # Make shell scripts executable
    if script_type == "shell":
        os.chmod(temp_path, 0o755)

    logging.debug("Created inline %s script: %s", script_type, temp_path)
    return temp_path


def _download_from_github(script_name: str, temp_dir: Optional[str] = None) -> str:
    """
    Download a script from GitHub using the Contents API with proper auth.
    Tries both Bearer and 'token' schemes and falls back to raw download.
    Returns the local temporary file path.
    """
    owner_repo = (GITHUB_REPO or "").strip()
    if not owner_repo or "/" not in owner_repo:
        raise RuntimeError(
            "GITHUB_REPO must be set as 'owner/repo' (e.g., 'pagopa/payments-cloudo')"
        )
    branch = (GITHUB_BRANCH or "main").strip()
    prefix = (GITHUB_PATH_PREFIX or "").strip().strip("/")

    path_parts = [p for p in [prefix, script_name] if p]
    repo_path = "/".join(path_parts)

    api_url = f"https://api.github.com/repos/{owner_repo}/contents/{repo_path}"
    params = {"ref": branch}

    last_resp = None
    data = None

    # Try Contents API with multiple auth headers
    for headers in _github_auth_headers():
        try:
            resp = requests.get(api_url, headers=headers, params=params, timeout=30)
            last_resp = resp
            logging.debug("GitHub GET %s -> %s", resp.url, resp.status_code)
            if resp.status_code == 200:
                data = resp.json()
                break
            # If unauthorized/forbidden, try next header variant
            if resp.status_code in (401, 403):
                continue
            # For 404, don't immediately fail; we will also try raw fallback below
        except requests.RequestException as e:
            logging.warning("GitHub request error: %s", e)
            continue

    content_bytes: Optional[bytes] = None
    if (
        isinstance(data, dict)
        and data.get("encoding") == "base64"
        and "content" in data
    ):
        b64 = data.get("content")
        if not isinstance(b64, str):
            raise RuntimeError("Missing or invalid 'content' (expected base64 string)")
        try:
            content_bytes = base64.b64decode(b64.replace("\n", ""))
        except Exception as e:
            raise RuntimeError(f"Failed to decode GitHub content: {e}") from e

    if content_bytes is None:
        # Raw fallback: https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}
        raw_url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{repo_path}"
        raw_ok = False
        for headers in _github_auth_headers():
            # Raw supports same auth headers
            try:
                raw_resp = requests.get(raw_url, headers=headers, timeout=30)
                logging.debug("GitHub RAW %s -> %s", raw_url, raw_resp.status_code)
                if raw_resp.status_code == 200:
                    content_bytes = raw_resp.content
                    raw_ok = True
                    break
                if raw_resp.status_code in (401, 403):
                    continue
            except requests.RequestException as e:
                logging.warning("GitHub raw request error: %s", e)
                continue

        if not raw_ok:
            # Build meaningful error based on last response
            status = getattr(last_resp, "status_code", "n/a")
            url = getattr(last_resp, "url", api_url)
            raise FileNotFoundError(
                f"GitHub file not found or not accessible: {owner_repo}/{repo_path}@{branch} "
                f"(last status={status}, url={url}). "
                "Check token scopes (repo or fine-grained: Contents Read, Metadata Read) and SSO authorization."
            )

    suffix = ".py" if script_name.lower().endswith(".py") else ""
    fd, tmp_path = tempfile.mkstemp(prefix="runbook_", suffix=suffix, dir=temp_dir)
    with os.fdopen(fd, "wb") as f:
        f.write(content_bytes)

    try:
        st = os.stat(tmp_path)
        os.chmod(tmp_path, st.st_mode | stat.S_IEXEC)
    except Exception:
        pass

    return tmp_path


def _clean_path(p: Optional[str]) -> Optional[str]:
    if p is None:
        return None
    s = str(p).strip().strip('"').strip("'")
    if not s:
        return None
    s = os.path.expanduser(s)
    s = os.path.normpath(s)
    return s


def _run_aks_login(
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
    import tempfile

    if env is None:
        env = os.environ.copy()

    if isinstance(resource_info, str):
        try:
            resource_info = json.loads(resource_info)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"[{payload.get('exec_id')}] resource_info is not valid JSON: {e}"
            ) from e
    if not isinstance(resource_info, dict):
        raise RuntimeError(f"[{payload.get('exec_id')}] resource_info must be a dict")

    rg = (resource_info.get("resource_rg") or "").strip()
    name = (resource_info.get("resource_name") or "").strip()
    ns = (resource_info.get("aks_namespace") or "").strip()

    if not rg or not name:
        raise RuntimeError(
            f"[{payload.get('exec_id')}] resource_info requires non-empty 'resource_rg' and 'resource_name'"
        )

    script_path = os.path.normpath("utils/aks-login.sh")
    if not os.path.exists(script_path):
        raise FileNotFoundError(
            f"[{payload.get('exec_id')}] AKS login script not found: {script_path}"
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
    logging.info(f"[{payload.get('exec_id')}] Running AKS login: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
                logging.info(f"[{payload.get('exec_id')}] {msg}")

        stdout_data = "".join(collected_stdout)
        stderr_data = ""
        if proc.stderr:
            stderr_data = proc.stderr.read() or ""

        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(
                f"[{payload.get('exec_id')}] AKS login error (rc={rc}): {stderr_data.strip() or stdout_data.strip()}"
            )
    except OSError as e:
        raise RuntimeError(
            f"[{payload.get('exec_id')}] AKS login execution error: {e}"
        ) from e

    return kubeconfig_path


def _run_script(
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
        env["CLOUDO_PAYLOAD"] = json.dumps(payload)
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
        tmp_path = _create_inline_script_temp_file(
            script_content, script_type, temp_dir=temp_dir
        )
        script_path = tmp_path
    # GitHub if not found locally
    elif script_path is None:
        try:
            github_tmp_path = _download_from_github(script_name, temp_dir=temp_dir)
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
                with _ACTIVE_LOCK:
                    _PROCESS_BY_EXEC[payload["exec_id"]] = proc
        except Exception as e:
            logging.error(f"[{payload.get('exec_id')}] cannot record process: {e}")

        collected_stdout = []
        if proc.stdout:
            for line in proc.stdout:
                if not line:
                    continue
                collected_stdout.append(line)
                logging.debug(f"[{payload.get('exec_id')}] {line.rstrip()}")

        stdout_data = "".join(collected_stdout)
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
                with _ACTIVE_LOCK:
                    _PROCESS_BY_EXEC.pop(payload["exec_id"], None)
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
                f"[{payload.get('exec_id')}] remove kubeconfig path error ({p}): {e}"
            )


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


@app.queue_trigger(arg_name="msg", queue_name=QUEUE_NAME, connection=STORAGE_CONNECTION)
def process_runbook(msg: func.QueueMessage) -> None:
    payload = json.loads(msg.get_body().decode("utf-8"))
    logging.info(f"[{payload.get('exec_id')}] Job started: %s", payload)

    started_at = _format_requested_at()
    exec_id = payload.get("exec_id") or ""

    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    q_client = QueueClient.from_connection_string(
        conn_str=os.environ.get(STORAGE_CONNECTION),
        queue_name=NOTIFICATION_QUEUE_NAME,
        message_encode_policy=TextBase64EncodePolicy(),
    )

    # Check if this execution is already running
    with _ACTIVE_LOCK:
        items = list(_ACTIVE_RUNS.values())
        skip_reason = _inspect_duplicate_runs(items, payload)
        if skip_reason:
            log_msg = f"Execution {exec_id} skipped: {skip_reason}"
            logging.info(f"[{payload.get('exec_id')}] {log_msg}")
            q_client.send_message(
                _post_status(payload, status="skipped", log_message=log_msg)
            )
            return

    # Register the execution as "in progress" and notify
    with _ACTIVE_LOCK:
        _ACTIVE_RUNS[exec_id] = {
            "exec_id": exec_id,
            "id": payload.get("id"),
            "name": payload.get("name"),
            "runbook": payload.get("runbook"),
            "run_args": payload.get("run_args"),
            "worker": payload.get("worker"),
            "group": payload.get("group"),
            "requestedAt": payload.get("requestedAt"),
            "startedAt": started_at,
            "resource_info": payload.get("resource_info") or {},
            "status": "running",
        }

    log_msg = f"[{exec_id}] Job {payload.get('name')} started"
    q_client.send_message(_post_status(payload, status="running", log_message=log_msg))
    logging.info(f"[{exec_id}] Receiver response: status=running")

    # Local environment to avoid race conditions in multithreading
    env = os.environ.copy()
    execution_temp_dir = tempfile.mkdtemp(prefix=f"cloudo-{exec_id}-")

    try:
        info_raw = payload.get("resource_info")
        info: dict = {}
        if isinstance(info_raw, str):
            try:
                parsed = json.loads(info_raw)
                if isinstance(parsed, dict):
                    info = parsed
            except json.JSONDecodeError:
                logging.warning("[%s] resource_info not valid JSON", exec_id)
        elif isinstance(info_raw, dict):
            info = info_raw

        ns_val = str(info.get("aks_namespace", "")).strip().lower() if info else ""
        has_valid_ns = bool(ns_val) and ns_val not in {"null", "none", "undefined"}

        kubeconfig_path = None
        if info and has_valid_ns:
            try:
                kubeconfig_path = _run_aks_login(
                    info, payload, env=env, temp_dir=execution_temp_dir
                )
                logging.info(f"[{exec_id}] AKS login completed successfully")
            except Exception as e:
                # Report error and stop processing
                err_msg = f"[{exec_id}] AKS login failed: {type(e).__name__}: {e}"
                q_client.send_message(
                    _post_status(payload, status="error", log_message=err_msg)
                )
                logging.error(f"{err_msg}")
                return

        script_path = os.getenv("DEV_SCRIPT_PATH")

        result = _run_script(
            script_name=payload.get("runbook"),
            script_path=script_path,
            run_args=payload.get("run_args"),
            resource_info=payload.get("resource_info"),
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
            q_client.send_message(
                _post_status(payload, status="succeeded", log_message=log_msg)
            )

    except subprocess.CalledProcessError as e:
        error_message = f"Script failed. returncode={e.returncode} stderr={e.stderr.strip()} stdout={e.stdout.strip()}"
        try:
            q_client.send_message(
                _post_status(payload, status="failed", log_message=error_message)
            )
            logging.error(
                f"[{exec_id}] Receiver response: status=queued",
            )
        finally:
            logging.error(f"[{exec_id}] {error_message}")
    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)}"
        try:
            q_client.send_message(
                _post_status(payload, status="error", log_message=err_msg)
            )
            logging.error(
                f"[{exec_id}] Receiver response: status=queued",
            )
        finally:
            logging.error(f"[{exec_id}] Unexpected error: %s", err_msg)
    finally:
        try:
            if os.path.isdir(execution_temp_dir):
                shutil.rmtree(execution_temp_dir, ignore_errors=True)
        except Exception as e:
            logging.warning(
                f"[{exec_id}] failed to cleanup temp dir {execution_temp_dir}: {e}"
            )

        # Remove from the registry: no longer "in progress"
        logging.info(
            "[%s] Job complete (requested at %s)",
            exec_id,
            payload.get("requestedAt"),
        )
        with _ACTIVE_LOCK:
            _ACTIVE_RUNS.pop(exec_id, None)


# =========================
# Heartbeat
# =========================


@app.route(route="healthz", auth_level=func.AuthLevel.ANONYMOUS)
def heartbeat(req: func.HttpRequest) -> func.HttpResponse:
    now_utc = _utc_now_iso()
    body = json.dumps(
        {
            "status": "ok",
            "time": now_utc,
            "service": "RunbookTest",
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(body, status_code=200, mimetype="application/json")


# =========================
# HTTPS: Running Process
# =========================


@app.route(
    route="processes",
    methods=[func.HttpMethod.GET],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def list_processes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Lists the "in progress" runs of the RunbookTest endpoint (jobs not yet completed).
    Parameters:
    - q: text filter on exec_id, id, name, runbook (optional)

    Example:
    - GET /api/processes — returns only the “running” runs of RunbookTest on this instance
    - GET /api/processes?q=python — Filter by text on exec_id, id, name, runbook

    """
    expected_key = os.environ.get("CLOUDO_SECRET_KEY")
    request_key = req.headers.get("x-cloudo-key")

    if not expected_key or request_key != expected_key:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    q = (req.params.get("q") or "").lower().strip()
    with _ACTIVE_LOCK:
        items = list(_ACTIVE_RUNS.values())

    if q:

        def match(item: dict) -> bool:
            return any(
                (str(item.get(k) or "").lower().find(q) != -1)
                for k in ("exec_id", "id", "name", "runbook")
            )

        items = [i for i in items if match(i)]

    # Order by startedAt desc
    items.sort(key=lambda x: x.get("startedAt") or "", reverse=True)

    body = json.dumps(
        {
            "status": "ok",
            "time": _utc_now_iso(),
            "count": len(items),
            "runs": items,
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(
        body,
        status_code=200,
        mimetype="application/json",
        headers={
            **{"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        },
    )


@app.route(
    route="processes/stop",
    methods=[func.HttpMethod.DELETE],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@app.queue_output(
    arg_name="cloudo_notification_q",
    queue_name=NOTIFICATION_QUEUE_NAME,
    connection=STORAGE_CONNECTION,
)
def stop_process(
    req: func.HttpRequest, cloudo_notification_q: func.Out[str]
) -> func.HttpResponse:
    """
    Stop a job by exec_id.
    example: POST /api/processes/stop?exec_id=123
    """

    expected_key = os.environ.get("CLOUDO_SECRET_KEY")
    request_key = req.headers.get("x-cloudo-key")

    if not expected_key or request_key != expected_key:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized"}, ensure_ascii=False),
            status_code=401,
            mimetype="application/json",
        )

    exec_id = (req.params.get("exec_id") or req.headers.get("ExecId") or "").strip()
    if not exec_id:
        return func.HttpResponse(
            json.dumps({"error": "exec_id missing"}, ensure_ascii=False),
            status_code=400,
            mimetype="application/json",
        )

    with _ACTIVE_LOCK:
        proc = _PROCESS_BY_EXEC.get(exec_id)
        run_info = _ACTIVE_RUNS.get(exec_id)

    if not proc:
        return func.HttpResponse(
            json.dumps({"status": "not_found", "exec_id": exec_id}, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
        )

    try:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as e:
                logging.error(f"[{exec_id}] terminate error: {e}")
            try:
                proc.wait(timeout=10)
            except Exception:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception as e:
                        logging.error(f"[{exec_id}] kill error: {e}")
        status = "stopped"
        code = 200
    except Exception as e:
        status = f"error: {type(e).__name__}: {e}"
        code = 500

    try:
        if run_info:
            run_info["status"] = "stopped"
            payload = {
                "runbook": run_info.get("runbook"),
                "run_args": run_info.get("run_args"),
                "id": run_info.get("id"),
                "name": run_info.get("name"),
                "exec_id": run_info.get("exec_id"),
                "worker": run_info.get("worker"),
                "group": run_info.get("group"),
                "oncall": None,
                "monitor_condition": None,
                "severity": None,
                "requestedAt": run_info.get("requestedAt"),
            }
            cloudo_notification_q.set(
                _post_status(
                    payload,
                    status="stopped",
                    log_message=f"Execution {exec_id} stopped by request",
                )
            )
    except Exception:
        logging.warning("[%s] Unable to send status stop", exec_id)

    return func.HttpResponse(
        json.dumps({"status": status, "exec_id": exec_id}, ensure_ascii=False),
        status_code=code,
        mimetype="application/json",
    )


@app.schedule(
    schedule="0 */1 * * * *",
    arg_name="HeartBeatTimer",
    run_on_startup=True,
    use_monitor=False,
)
def heartbeat_trigger(HeartBeatTimer: func.TimerRequest) -> None:
    if HeartBeatTimer.past_due:
        logging.debug("The timer is past due!")

    url = os.getenv("ORCHESTRATOR_URL", "http://orchestrator/api/workers/register")
    key = os.getenv("CLOUDO_SECRET_KEY")

    payload = {
        "capability": os.getenv("WORKER_CAPABILITY", "local"),
        "worker_id": os.getenv("WEBSITE_SITE_NAME", "azure-func-worker"),
        "queue": QUEUE_NAME,
        "region": os.getenv("REGION_NAME", "azure-cloud"),
    }

    try:
        r = requests.post(url, json=payload, headers={"x-cloudo-key": key}, timeout=10)
        logging.debug(f"request {r.status_code}")
        logging.debug("Heartbeat sent successfully")
    except Exception as e:
        logging.error(f"Failed to send heartbeat: {e}")
