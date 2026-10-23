"""Runbook catalogue endpoints backed by the GitHub repository."""

import json
import logging
import os

import azure.functions as func
from core.config import (
    AUTH,
    GITHUB_BRANCH,
    GITHUB_PATH_PREFIX,
    GITHUB_REPO,
    GITHUB_TOKEN,
)
from utils import create_cors_response

bp = func.Blueprint()


@bp.route(
    route="runbooks/content",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def get_runbook_content(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    script_name = req.params.get("name")
    if not script_name:
        return func.HttpResponse(
            json.dumps({"error": "Query parameter 'name' is required"}),
            status_code=400,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    owner_repo = (GITHUB_REPO or "").strip()
    if not owner_repo or "/" not in owner_repo:
        return func.HttpResponse(
            json.dumps({"error": "GITHUB_REPO not configured correctly"}),
            status_code=500,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    branch = (GITHUB_BRANCH or "main").strip()
    prefix = (GITHUB_PATH_PREFIX or "").strip().strip("/")
    path_parts = [p for p in [prefix, script_name] if p]
    repo_path = "/".join(path_parts)

    content_text = None
    error_msg = "File not found"

    # LOCAL_DEV: read from local file system
    if os.getenv("LOCAL_DEV", "false").lower() == "true":
        try:
            # Check if a dev script path is explicitly set (e.g. in Docker)
            dev_script_path = os.getenv("DEV_SCRIPT_PATH")
            if dev_script_path:
                local_path = os.path.join(dev_script_path, script_name)
            else:
                # Fallback to relative path discovery for local development
                # We assume runbooks are in src/runbooks relative to project root.
                # The function app runs in src/core/orchestrator.
                # __file__ is src/core/orchestrator/function_app.py
                base_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                local_path = os.path.join(base_dir, "src", "runbooks", script_name)

            if os.path.exists(local_path):
                with open(local_path, encoding="utf-8") as f:
                    content_text = f.read()
                logging.debug(f"Loaded runbook from local path: {local_path}")
            else:
                logging.error(f"Local runbook not found at {local_path}")
        except Exception as e:
            logging.error(f"Error reading local runbook: {e}")

    if content_text is not None:
        return func.HttpResponse(
            json.dumps({"content": content_text}),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # We try both Contents API and Raw download
    import requests

    headers_list = []
    base_headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers_list.append({**base_headers, "Authorization": f"Bearer {GITHUB_TOKEN}"})
        headers_list.append({**base_headers, "Authorization": f"token {GITHUB_TOKEN}"})
    else:
        headers_list.append(base_headers)

    content_text = None
    error_msg = "File not found"

    # Try Contents API first
    api_url = f"https://api.github.com/repos/{owner_repo}/contents/{repo_path}"
    for h in headers_list:
        try:
            resp = requests.get(api_url, headers=h, params={"ref": branch}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if (
                    isinstance(data, dict)
                    and data.get("encoding") == "base64"
                    and "content" in data
                ):
                    import base64

                    content_text = base64.b64decode(
                        data["content"].replace("\n", "")
                    ).decode("utf-8")
                    break
            elif resp.status_code in (401, 403):
                error_msg = f"GitHub Auth Error: {resp.status_code}"
                continue
        except Exception as e:
            logging.error(f"GitHub API error: {e}")

    # Fallback to Raw
    if content_text is None:
        raw_url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{repo_path}"
        for h in headers_list:
            try:
                resp = requests.get(raw_url, headers=h, timeout=10)
                if resp.status_code == 200:
                    content_text = resp.text
                    break
            except Exception as e:
                logging.error(f"GitHub Raw error: {e}")

    if content_text is not None:
        return func.HttpResponse(
            json.dumps({"content": content_text}),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    else:
        return func.HttpResponse(
            json.dumps({"error": error_msg}),
            status_code=404,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )


@bp.route(
    route="runbooks/list",
    methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS],
    auth_level=AUTH,
)
def list_runbooks(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return create_cors_response()

    runbooks = []

    # LOCAL_DEV: list from local file system
    if os.getenv("LOCAL_DEV", "false").lower() == "true":
        try:
            dev_script_path = os.getenv("DEV_SCRIPT_PATH")
            if dev_script_path:
                local_dir = dev_script_path
            else:
                base_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                local_dir = os.path.join(base_dir, "src", "runbooks")

            if os.path.exists(local_dir):
                for root, dirs, files in os.walk(local_dir):
                    for file in files:
                        if file.endswith(".sh") or file.endswith(".py"):
                            rel_path = os.path.relpath(
                                os.path.join(root, file), local_dir
                            )
                            runbooks.append(rel_path)
                logging.info(f"Listed runbooks from local path: {local_dir}")
        except Exception as e:
            logging.error(f"Error listing local runbooks: {e}")

    # If no local runbooks found or not in DEV, try GitHub
    if not runbooks:
        owner_repo = (GITHUB_REPO or "").strip()
        branch = (GITHUB_BRANCH or "main").strip()
        prefix = (GITHUB_PATH_PREFIX or "").strip().strip("/")

        if owner_repo and "/" in owner_repo:
            import requests

            headers_list = []
            base_headers = {"Accept": "application/vnd.github.v3+json"}
            if GITHUB_TOKEN:
                headers_list.append(
                    {**base_headers, "Authorization": f"Bearer {GITHUB_TOKEN}"}
                )
                headers_list.append(
                    {**base_headers, "Authorization": f"token {GITHUB_TOKEN}"}
                )
            else:
                headers_list.append(base_headers)

            api_url = f"https://api.github.com/repos/{owner_repo}/git/trees/{branch}?recursive=1"
            for h in headers_list:
                try:
                    resp = requests.get(api_url, headers=h, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        tree = data.get("tree", [])
                        for item in tree:
                            path = item.get("path", "")
                            # Filter by prefix and extension
                            if path.startswith(prefix) and (
                                path.endswith(".sh") or path.endswith(".py")
                            ):
                                # If prefix is present, remove it from the path to get relative path
                                if prefix:
                                    prefix_len = len(prefix)
                                    rel_path = path[prefix_len:].lstrip("/")
                                    if rel_path:
                                        runbooks.append(rel_path)
                                else:
                                    runbooks.append(path)
                        break
                except Exception as e:
                    logging.error(f"GitHub API list error: {e}")

    return func.HttpResponse(
        json.dumps({"runbooks": sorted(list(set(runbooks)))}),
        status_code=200,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )
