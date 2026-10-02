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
_JSM_MAX_CHAIN = 4096


def _chain_alias(alias: str, index: int) -> str:
    return alias if index <= 1 else f"{alias}-{index}"


def _alert_state(alias: str, headers: dict, timeout: int):
    """'open' | 'closed' | 'missing' | None when the lookup is unavailable."""
    try:
        resp = requests.get(
            f"{JSM_ALERTS_URL}/{alias}",
            headers=headers,
            params={"identifierType": "alias"},
            timeout=timeout,
        )
        if resp.status_code == 404:
            return "missing"
        if resp.status_code != 200:
            return None
        body = resp.json() or {}
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        status = str(data.get("status") or "").strip().lower()
        return "closed" if status == "closed" else "open"
    except Exception:
        return None


def resolve_current_alias(api_key: str, alias: str, timeout: int = 10) -> str:
    """Alias of the latest alert opened for `alias`; falls back to `alias`."""
    headers = {"Authorization": f"GenieKey {api_key}", "Accept": "application/json"}
    cache: dict = {}

    def state(i: int):
        if i not in cache:
            cache[i] = _alert_state(_chain_alias(alias, i), headers, timeout)
        return cache[i]

    if state(1) in (None, "missing"):
        return alias
    lo, hi = 1, 2
    while hi <= _JSM_MAX_CHAIN:
        s = state(hi)
        if s is None:
            return alias
        if s == "missing":
            break
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        s = state(mid)
        if s is None:
            return alias
        if s == "missing":
            hi = mid
        else:
            lo = mid
    return _chain_alias(alias, lo)


def format_triage_note(analysis: dict) -> str:
    lines = ["AI triage (ClouDO Agent)"]
    if analysis.get("recurring"):
        lines.append(
            f"⚠ Recurring failure (seen {analysis.get('occurrence_count', '?')}x"
            f", first seen {analysis.get('first_seen') or 'n/a'})"
        )
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
    alias = resolve_current_alias(api_key, alias, timeout=timeout)
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
