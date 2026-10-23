"""Deliver a routing decision to Slack/JSM, with a final JSM fallback."""

import logging
from typing import Any, Callable, Optional, cast

from smart_routing import settings
from smart_routing.models import Action, RoutingDecision
from smart_routing.sanitize import safe_for_log

_JSM_PAYLOAD_KEY = "jsm"


def _delivery(action: Action, ok: bool, error: Optional[str] = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": action.type,
        "team": action.team,
        "status": "sent" if ok else "failed",
    }
    if action.type == "slack" and action.channel:
        record["channel"] = action.channel
    if error:
        record["error"] = error[:200]
    return record


def _send(
    action: Action,
    payload: dict[str, Any],
    slack_sender: Optional[Callable[..., Any]],
    jsm_sender: Optional[Callable[..., Any]],
) -> Optional[dict[str, Any]]:
    """Deliver one target; raises on invalid targets. None for unknown types."""
    if action.type == "slack":
        if slack_sender is None:
            raise ValueError("Slack sender not configured")
        if not action.token:
            raise ValueError("Missing Slack token")
        if not action.channel:
            raise ValueError("Missing Slack channel")
        slack_payload = payload.get("slack")
        if not isinstance(slack_payload, dict):
            raise ValueError("Missing/invalid Slack payload")
        ok = slack_sender(token=action.token, channel=action.channel, **slack_payload)
        return _delivery(action, ok is not False)
    if action.type in ("jsm"):
        if jsm_sender is None:
            raise ValueError("JSM sender not configured")
        if not action.apiKey:
            raise ValueError("Missing JSM apiKey")
        jsm_payload = payload.get(_JSM_PAYLOAD_KEY)
        if not isinstance(jsm_payload, dict):
            raise ValueError("Missing/invalid JSM payload")
        ok = jsm_sender(api_key=action.apiKey, **jsm_payload)
        return _delivery(action, ok is not False)
    return None


def _final_jsm_fallback(
    decision: RoutingDecision,
    payload: dict[str, Any],
    jsm_sender: Optional[Callable[..., Any]],
    results: list[dict[str, Any]],
) -> None:
    """Every target failed: page the fallback team with the default JSM key."""
    if jsm_sender is None:
        logging.error("Final fallback skipped: JSM sender not configured")
        return
    jsm_sender_safe = cast(Callable[..., Any], jsm_sender)
    api_key = settings.resolve_jsm_apikey(decision.fallback_team)
    if api_key:
        logging.info(
            f"Attempting final JSM fallback (reason={decision.reason}, team={decision.fallback_team})"
        )
        try:
            jsm_payload = payload.get(_JSM_PAYLOAD_KEY)
            if not isinstance(jsm_payload, dict):
                raise ValueError("Missing/invalid JSM payload")
            ok = jsm_sender_safe(api_key=api_key, **jsm_payload)
            results.append(
                {
                    "type": "jsm",
                    "team": decision.fallback_team,
                    "status": "sent" if ok else "failed",
                    "fallback": True,
                }
            )
            if ok:
                logging.info(
                    f"Final JSM fallback sent (team={safe_for_log(decision.fallback_team, 64)})"
                )
            else:
                logging.error("Final JSM fallback did not confirm success")
        except Exception as send_err:
            results.append(
                {
                    "type": "jsm",
                    "team": decision.fallback_team,
                    "status": "failed",
                    "fallback": True,
                    "error": str(send_err)[:200],
                }
            )
            logging.error(f"Final JSM fallback failed during send: {send_err}")
    else:
        logging.error(
            f"Final fallback skipped: no JSM API key resolved for team={safe_for_log(decision.fallback_team, 64)}"
        )

    logging.warning(
        "Escalation finished with errors; JSM fallback attempted"
        if api_key
        else "Escalation finished with errors; JSM fallback skipped"
    )


def execute_actions(
    decision: RoutingDecision,
    payload: dict[str, Any],
    send_slack_fn: Optional[Callable[..., Any]] = None,
    send_jsm_fn: Optional[Callable[..., Any]] = None,
) -> list[dict[str, Any]]:
    """
    Execute the decided actions in order.
    - If any action succeeds, continue executing others (fan-out).
    - If all actions fail, attempt a final JSM fallback using a default env key.
    Returns one delivery record per attempted target (type, team, channel, status).
    """
    if send_slack_fn is None or not callable(send_slack_fn):
        logging.error("Invalid send_slack_fn: expected callable")
        send_slack_fn = None
    if send_jsm_fn is None or not callable(send_jsm_fn):
        logging.error("Invalid send_jsm_fn: expected callable")
        send_jsm_fn = None

    results: list[dict[str, Any]] = []
    any_success = False
    failed_actions = 0
    for action in decision.actions:
        try:
            record = _send(action, payload, send_slack_fn, send_jsm_fn)
        except Exception as e:
            failed_actions += 1
            results.append(_delivery(action, False, str(e)))
            logging.error(
                f"Routing action failed (type={action.type}, team={action.team}): {e}"
            )
            continue
        if record is not None:
            any_success = True
            results.append(record)

    if (
        not any_success
        and decision.jsm_allowed
        and decision.reason != "no_action_non_final"
    ):
        logging.warning(
            f"Routing: all actions failed ({failed_actions}/{len(decision.actions)}, "
            f"reason={decision.reason}, fallback_team={safe_for_log(decision.fallback_team, 64)})"
            " -> final JSM fallback"
        )
        try:
            _final_jsm_fallback(decision, payload, send_jsm_fn, results)
        except Exception as e:
            logging.error(f"Final JSM fallback handling encountered an error: {e}")
            logging.warning(
                "Escalation finished with errors; fallback handling error was logged"
            )
    return results
