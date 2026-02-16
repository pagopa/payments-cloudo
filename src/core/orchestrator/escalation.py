import json
import logging
import os

from opsgenie_sdk import (
    AlertApi,
    ApiClient,
    CloseAlertPayload,
    Configuration,
    CreateAlertPayload,
)
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# =========================
# ESCALATIONS Functions
# =========================


# =========================
# OPSGENIE
# =========================
def send_opsgenie_alert(
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
    Create or close an Opsgenie alert using the Opsgenie SDK.
    - On 'Resolved', close the existing alert by alias (requires alias).
    - Otherwise, create (or de-duplicate) the alert.
    """
    if not api_key or not str(api_key).strip():
        logging.warning("Opsgenie: missing or empty apiKey, skipping alert send.")
        return False

    # Defensive trim and sanitization
    api_key = str(api_key).strip().strip('"').strip("'")

    # Validation and logging (safe)
    if len(api_key) < 10:
        logging.error(f"Opsgenie: apiKey is suspiciously short ({len(api_key)} chars)")
    else:
        logging.info(
            f"Opsgenie: sending alert with apiKey len={len(api_key)}, prefix={api_key[:4]}... suffix=...{api_key[-4:]}"
        )

    try:
        conf = Configuration()
        # Handle EU region if the key prefix suggests it or via environment
        if (
            api_key.startswith("eu_")
            or os.environ.get("OPSGENIE_REGION", "").upper() == "EU"
        ):
            conf.host = "https://api.eu.opsgenie.com"
            logging.info("Opsgenie: using EU region endpoint")

        conf.api_key["Authorization"] = api_key
        client = ApiClient(configuration=conf)
        alert_api = AlertApi(api_client=client)

        # Close path for resolved signals
        if (monitor_condition or "").strip().lower() == "resolved":
            if not alias:
                logging.warning(
                    "Opsgenie: cannot close alert without alias when resolved."
                )
                return False
            try:
                cap = CloseAlertPayload(user="cloudo", note="Auto-closed on resolve")
                alert_api.close_alert_with_http_info(
                    identifier=alias, identifier_type="alias", close_alert_payload=cap
                )
                logging.info(f"Opsgenie: closed alert with alias={alias}")
                return True
            except Exception as e:
                logging.error(f"Opsgenie: close_alert failed for alias={alias}: {e}")
                return False

        # Create alert (de-dup su alias se presente)
        body = CreateAlertPayload(
            message=message,
            description=description,
            priority=priority,
            alias=alias,
            tags=tags or [],
            details=details or {},
        )
        response = alert_api.create_alert(body)
        return True if response else False

    except Exception as e:
        logging.error(
            f"Opsgenie: unexpected error while sending/closing alert: {str(e)}"
        )
        return False


def format_opsgenie_description(exec_id: str, resource_info: dict, api_body) -> str:
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
        logging.error(f"Slack API Error: {error_code}. Response: {e.response}")

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
