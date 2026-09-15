import logging

import requests

# =========================
# JSM (Jira Service Management) — asynchronous triage notes
# =========================
#
# ClouDO's orchestrator already creates/closes JSM Ops alerts using the
# runbook exec_id as alias.

JSM_ALERTS_URL = "https://api.atlassian.com/jsm/ops/integration/v2/alerts"
JSM_NOTE_AUTHOR = "cloudo-ai-agent"


def format_triage_note(analysis: dict) -> str:
    lines = ["AI triage (ClouDO Agent)"]
    if analysis.get("summary"):
        lines.append(f"Summary: {analysis['summary']}")
    if analysis.get("probable_root_cause"):
        lines.append(f"Probable root cause: {analysis['probable_root_cause']}")
    actions = analysis.get("recommended_actions") or []
    if actions:
        lines.append("Recommended actions:")
        lines.extend(f"- {a}" for a in actions)
    if analysis.get("confidence"):
        lines.append(f"Confidence: {analysis['confidence']}")
    return "\n".join(lines)


def add_jsm_alert_note(api_key: str, alias: str, note: str, timeout: int = 10) -> bool:
    """Attach an asynchronous triage note to an existing JSM Ops alert."""
    if not api_key or not str(api_key).strip():
        logging.warning("JSM: missing or empty apiKey, skipping note.")
        return False
    if not alias:
        logging.warning("JSM: cannot add a note without alias.")
        return False

    api_key = str(api_key).strip().strip('"').strip("'")
    url = f"{JSM_ALERTS_URL}/{alias}/notes"
    headers = {
        "Authorization": f"GenieKey {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"user": JSM_NOTE_AUTHOR, "note": note}

    logging.info(
        "JSM: posting triage note to alert alias=%s (note length=%d chars)",
        alias,
        len(note or ""),
    )
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            params={"identifierType": "alias"},
            timeout=timeout,
        )
        resp.raise_for_status()
        logging.info(
            "JSM: note added to alert alias=%s (status=%s)", alias, resp.status_code
        )
        return True
    except requests.HTTPError as exc:
        logging.error(
            "JSM: HTTP error while adding note to alias=%s: status=%s body=%s",
            alias,
            exc.response.status_code if exc.response is not None else "?",
            exc.response.text if exc.response is not None else "",
        )
        return False
    except Exception as exc:
        logging.error(
            "JSM: unexpected error while adding note to alias=%s: %s",
            alias,
            exc,
            exc_info=True,
        )
        return False
