import json
import logging

import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# =========================
# ESCALATIONS Functions
# =========================


# =========================
# JSM (Jira Service Management)
# =========================

JSM_ALERTS_URL = "https://api.atlassian.com/jsm/ops/integration/v2/alerts"
_JSM_MAX_CHAIN = 4096


def jsm_chain_alias(alias: str, index: int) -> str:
    """Alias of the Nth alert opened for `alias` (1 -> `alias`, 2 -> `alias-2`...)."""
    return alias if index <= 1 else f"{alias}-{index}"


def _jsm_alert_state(alias: str, headers: dict):
    """Return 'open', 'closed', 'missing', or None when the lookup is unavailable."""
    try:
        resp = requests.get(
            f"{JSM_ALERTS_URL}/{alias}",
            headers=headers,
            params={"identifierType": "alias"},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"JSM: alert lookup failed (alias={alias}): {e}")
        return None
    if resp.status_code == 404:
        return "missing"
    if resp.status_code != 200:
        logging.warning(
            f"JSM: alert lookup returned {resp.status_code} (alias={alias})"
        )
        return None
    try:
        body = resp.json() or {}
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        status = str(data.get("status") or "").strip().lower()
    except Exception:
        return None
    return "closed" if status == "closed" else "open"


def resolve_jsm_alias(alias: str, headers: dict):
    """
    Find the latest alert of the alias chain (`alias`, `alias-2`, ...).
    Returns (index, state) where state is 'open', 'closed', 'missing' (no alert
    yet) or None (lookup unavailable: callers fall back to the plain alias).
    A closed alert is never reused: the next one is opened on the next index.
    """
    cache: dict = {}

    def state(i: int):
        if i not in cache:
            cache[i] = _jsm_alert_state(jsm_chain_alias(alias, i), headers)
        return cache[i]

    if state(1) in (None, "missing"):
        return 1, state(1)
    lo, hi = 1, 2
    while hi <= _JSM_MAX_CHAIN:
        s = state(hi)
        if s is None:
            return 1, None
        if s == "missing":
            break
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        s = state(mid)
        if s is None:
            return 1, None
        if s == "missing":
            hi = mid
        else:
            lo = mid
    return lo, state(lo)


def send_jsm_alert(
    api_key: str,
    message: str,
    description: str = None,
    priority: str = "P3",
    alias: str = None,
    tags: list = None,
    details: dict = None,
    monitor_condition: str = None,
) -> bool:
    """
    Create or close a JSM Ops alert via the official v2 integration API.
    - On 'Resolved', close the existing alert by alias (requires alias).
    - Otherwise, create (or de-duplicate) the alert.
    """
    if not api_key or not str(api_key).strip():
        logging.warning("JSM: missing or empty apiKey, skipping alert send.")
        return False

    api_key = str(api_key).strip().strip('"').strip("'")

    headers = {
        "Authorization": f"GenieKey {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        if (monitor_condition or "").strip().lower() == "resolved":
            if not alias:
                logging.warning("JSM: cannot close alert without alias when resolved.")
                return False
            index, state = resolve_jsm_alias(alias, headers)
            if state in ("closed", "missing"):
                logging.info(
                    f"JSM: no open alert to close (alias={alias}, state={state})"
                )
                return True
            alias = jsm_chain_alias(alias, index)
            url = f"{JSM_ALERTS_URL}/{alias}/close"
            payload = {"user": "cloudo", "note": "Auto-closed on resolve"}
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                params={"identifierType": "alias"},
                timeout=10,
            )
            resp.raise_for_status()
            logging.info(f"JSM: closed alert with alias={alias}")
            return True

        payload = {
            "message": str(message or "")[:130],
            "priority": priority,
            "source": "cloudo",
        }
        if description:
            payload["description"] = str(description)[:15000]
        if alias:
            index, state = resolve_jsm_alias(alias, headers)
            if state == "closed":
                index += 1
            alias = jsm_chain_alias(alias, index)
            payload["alias"] = alias[:512]
        if tags:
            payload["tags"] = [str(t)[:50] for t in tags][:20]
        if details:
            # JSM details must be a flat map of non-null strings.
            payload["details"] = {
                str(k).strip(": ")[:8000]: str(v)[:8000]
                for k, v in details.items()
                if v is not None and str(k).strip(": ")
            }

        resp = requests.post(JSM_ALERTS_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logging.info(f"JSM: alert created/updated (alias={alias})")
        return True

    except requests.HTTPError as e:
        # Response is falsy for 4xx/5xx, so compare with None to log the body.
        body = e.response.text if e.response is not None else ""
        logging.error(f"JSM: HTTP error while sending/closing alert: {e} — {body}")
        return False
    except Exception as e:
        logging.error(f"JSM: unexpected error while sending/closing alert: {e}")
        return False


def format_jsm_description(exec_id: str, resource_info: dict, api_body) -> str:
    # deprecated: use format_jsm_description instead
    raw_val = resource_info.get("_raw") or ""
    alert_data = {}
    try:
        alert_data = json.loads(raw_val)
    except Exception:
        pass

    if isinstance(api_body, (dict, list)):
        result_text = json.dumps(api_body, indent=2, ensure_ascii=False)
    else:
        result_text = str(api_body)

    # Extract key fields for a user-friendly summary
    summary_parts = []
    resource_details = []
    try:
        data = alert_data.get("data", {})
        essentials = data.get("essentials", {})
        context = data.get("alertContext", {})
        labels = context.get("labels", {})

        alert_name = essentials.get("alertRule") or labels.get("alertname")
        severity = essentials.get("severity")
        monitor_condition = essentials.get("monitorCondition")

        if alert_name:
            summary_parts.append(f"🚨 Alert: {alert_name}")
        if severity:
            summary_parts.append(f"📊 Severity: {severity}")
        if monitor_condition:
            summary_parts.append(f"🔍 Condition: {monitor_condition}")

        cluster = labels.get("cluster")
        namespace = labels.get("namespace")
        deployment = labels.get("deployment")

        if cluster:
            summary_parts.append(f"🏗️ Cluster: {cluster}")
        if namespace and namespace != "nonamespace":
            summary_parts.append(f"📦 Namespace: {namespace}")
        if deployment:
            summary_parts.append(f"🚀 Deployment: {deployment}")

        # Build Resource Info list from available fields
        field_mapping = {
            "resource_name": "Resource Name",
            "resource_rg": "Resource Group",
            "resource_id": "Resource ID",
            "aks_namespace": "AKS Namespace",
            "aks_pod": "AKS Pod",
            "aks_deployment": "AKS Deployment",
            "aks_job": "AKS Job",
            "aks_horizontalpodautoscaler": "AKS HPA",
            "team": "Team",
        }

        for key, label in field_mapping.items():
            val = resource_info.get(key)
            if val and str(val).lower() != "nonamespace":
                resource_details.append(f"- {label}: `{val}`")

    except Exception as e:
        logging.debug(f"Could not extract summary fields: {e}")

    summary_section = ""
    if summary_parts:
        summary_section = "### SUMMARY\n" + "\n".join(summary_parts) + "\n\n"

    resource_section = ""
    if resource_details:
        resource_section = (
            "#### 📋 RESOURCE INFO\n" + "\n".join(resource_details) + "\n\n"
        )

    raw_json_block = ""
    if raw_val:
        try:
            pretty_json = json.dumps(alert_data, indent=2, ensure_ascii=False)
            raw_json_block = (
                f"\n\n---\n#### 📄 RAW ALARM DATA\n```json\n{pretty_json}\n```"
            )
        except Exception:
            raw_json_block = f"\n\n---\n#### 📄 RAW ALARM DATA\n```\n{raw_val}\n```"

    return (
        f"{summary_section}"
        f"{resource_section}"
        f"#### ⚙️ EXECUTION RESULT\n"
        f"ExecID: `{exec_id}`\n"
        f"```\n"
        f"{result_text}\n"
        f"```"
        f"{raw_json_block}"
    )


# =========================
# SLACK
# =========================


def send_slack_execution(
    token: str, channel: str, message: str, blocks: list = None
) -> bool:
    """
    Send an alert to a Slack channel using the Slack SDK.
    Includes fallback to plain text if blocks are invalid.
    """
    if not token or not str(token).strip():
        logging.warning("Slack: missing or empty token, skipping message send.")
        return False
    if not channel or not str(channel).strip():
        logging.warning("Slack: missing or empty channel, skipping message send.")
        return False

    try:
        client = WebClient(token=token)

        # Validate URLs in blocks (if any)
        if blocks:
            for block in blocks:
                if block.get("type") == "actions":
                    for element in block.get("elements", []):
                        if element.get("type") == "button" and "url" in element:
                            url = str(element["url"])
                            if not url.startswith("http"):
                                logging.warning(
                                    "Slack: fixed invalid button URL by adding http fallback"
                                )
                                element["url"] = (
                                    f"http://{url}" if url else "http://localhost:3000"
                                )

        # Try sending with blocks
        response = client.chat_postMessage(channel=channel, text=message, blocks=blocks)
        return True if response["ok"] else False

    except SlackApiError as e:
        error_code = e.response.get("error")
        # Log the full error for debugging
        logging.error(
            f"[{channel}] Slack API Error: {error_code}. Response: {e.response}"
        )

        # Fallback if blocks are invalid
        if error_code == "invalid_blocks" and blocks:
            logging.warning(
                f"Retrying Slack send for channel {channel} without blocks due to 'invalid_blocks' error. Message: {message[:100]}..."
            )
            try:
                response = client.chat_postMessage(channel=channel, text=message)
                return True if response["ok"] else False
            except Exception as retry_err:
                logging.error(f"Slack retry failed: {retry_err}")
                return False
        return False
    except Exception as e:
        logging.error(f"Unexpected error sending Slack alert: {str(e)}")
        return False
