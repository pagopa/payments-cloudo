"""Runbook script resolution: inline scripts and GitHub downloads."""

import base64
import logging
import os
import stat
import tempfile
from typing import Optional

import requests
from core.config import GITHUB_BRANCH, GITHUB_PATH_PREFIX, GITHUB_REPO, GITHUB_TOKEN


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


def create_inline_script_temp_file(
    script_content: str, script_type: str = "python", temp_dir: Optional[str] = None
) -> str:
    """
    Create a temporary file with the provided script content.
    For shell scripts, sets executable permissions.
    Returns the path to the temporary file.
    """
    import tempfile

    suffix = ".sh" if script_type.lower() in {"shell", "bash", "sh"} else ".py"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, dir=temp_dir or "/tmp"
    ) as f:
        f.write(script_content)
        temp_path = f.name

    # Make shell scripts executable (owner only)
    if script_type.lower() in {"shell", "bash", "sh"}:
        os.chmod(temp_path, 0o700)

    logging.debug("Created inline %s script: %s", script_type, temp_path)
    return temp_path


def download_from_github(script_name: str, temp_dir: Optional[str] = None) -> str:
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
