# Python
# routing.py
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional, cast

# =========================
# Routing: models
# =========================


@dataclass
class Action:
    type: str  # "slack" | "jsm"
    channel: Optional[str] = None
    token: Optional[str] = None
    team: Optional[str] = None
    apiKey: Optional[str] = None


@dataclass
class RoutingDecision:
    actions: list[Action]
    matched_rule_index: Optional[int]
    matched_team: Optional[str]
    reason: str  # "matched" | "fallback_jsm"


_SAFE_TEAM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_CHANNEL_RE = re.compile(r"^#[A-Za-z0-9._-]{1,80}$")
_SAFE_STATUS_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SAFE_ENV_KEY_RE = re.compile(r"^[A-Z0-9_]{1,96}$")
_INVISIBLE_WHITESPACE_RE = re.compile(
    "[\u00a0\u1680\u2000-\u200b\u202f\u205f\u2028\u2029\u3000\ufeff]"
)
ROUTING_CONFIG_CACHE_TTL_SECONDS = int(
    os.getenv("ROUTING_CONFIG_CACHE_TTL_SECONDS", "60")
)
ROUTING_SETTINGS_CACHE_TTL_SECONDS = int(
    os.getenv("ROUTING_SETTINGS_CACHE_TTL_SECONDS", "60")
)
_routing_config_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}
_setting_cache: dict[str, tuple[Optional[str], float]] = {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_team(team: Optional[str]) -> Optional[str]:
    if team is None:
        return None
    t = str(team).strip()
    if not t:
        return None
    if _SAFE_TEAM_RE.fullmatch(t):
        return t
    logging.warning("Rejected unsafe team identifier")
    return None


def _sanitize_channel(channel: Optional[str]) -> Optional[str]:
    if channel is None:
        return None
    c = str(channel).strip()
    if not c:
        return None
    if _SAFE_CHANNEL_RE.fullmatch(c):
        return c
    logging.warning("Rejected unsafe Slack channel format")
    return None


def _safe_for_log(value: Any, max_len: int = 128) -> str:
    # Remove control chars/newlines to reduce log injection surface.
    txt = str(value or "")
    txt = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else "?" for ch in txt)
    return txt[:max_len]


# =========================
# Config loader
# =========================


def load_routing_config() -> dict[str, Any]:
    """
    Load routing configuration from Azure Table Storage (CloudoSettings/ROUTING_RULES).
    Fallback to env ROUTING_RULES (JSON).
    If both absent/invalid, return safe fallback with JSM default.
    Do NOT store secrets (tokens/keys) in config JSON: resolve via environment.
    """
    defaults = {
        "jsm": {"team": "default"},  # apiKey resolved via env
        "slack": {
            "channel": os.environ.get("SLACK_CHANNEL_DEFAULT", "#cloudo-default")
        },
    }
    fallback = {
        "version": 1,
        "defaults": defaults,
        "teams": {},
        "rules": [
            {
                "when": {"isAlert": "true", "statusIn": ["failed", "error", "routed"]},
                "then": [
                    {
                        "type": "jsm",
                        "statusIn": ["failed", "error", "routed"],
                    },
                    {"type": "slack"},
                ],
            },
            {
                "when": {"any": "*"},
                "then": [
                    {"type": "slack"},
                ],
            },
        ],
    }

    now = time.time()
    if _routing_config_cache.get("expires_at", 0.0) > now:
        cached_cfg = _routing_config_cache.get("value")
        if isinstance(cached_cfg, dict):
            return cached_cfg

    def _cache_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
        _routing_config_cache["value"] = cfg
        _routing_config_cache["expires_at"] = now + ROUTING_CONFIG_CACHE_TTL_SECONDS
        return cfg

    raw = ""
    # 1. Try Azure Table Storage
    try:
        from azure.data.tables import TableClient

        conn_str = os.environ.get("AzureWebJobsStorage")
        if conn_str:
            with TableClient.from_connection_string(
                conn_str, table_name="CloudoSettings"
            ) as table_client:
                entity = table_client.get_entity(
                    partition_key="GlobalConfig", row_key="ROUTING_RULES"
                )
                raw = entity.get("value", "")
    except Exception as e:
        logging.warning(f"Could not load ROUTING_RULES from Table Storage: {e}")

    # 2. Fallback to Env
    if not raw:
        raw = (os.environ.get("ROUTING_RULES") or "").strip()

    if not raw:
        logging.info("ROUTING_RULES not set: using fallback configuration")
        return _cache_cfg(fallback)
    try:
        cfg = json.loads(raw)
        # Soft-merge defaults to ensure required keys exist
        cfg.setdefault("defaults", {}).setdefault("jsm", {}).setdefault(
            "team", defaults["jsm"]["team"]
        )
        cfg.setdefault("defaults", {}).setdefault("slack", {}).setdefault(
            "channel", defaults["slack"]["channel"]
        )
        cfg.setdefault("teams", {})
        cfg.setdefault("rules", cfg.get("rules") or fallback["rules"])
        return _cache_cfg(cfg)
    except Exception as e:
        logging.error(f"Invalid ROUTING_RULES JSON: {e}")
        return _cache_cfg(fallback)


# =========================
# Severity and matching utils
# =========================


def _sev_to_num(sev: Optional[str]) -> Optional[int]:
    """
    Normalize Azure severity "Sev0-Sev4" to integer 0..4.
    Lower is more critical (0=Critical, 4=Informational).
    """
    if not sev:
        return None
    s = str(sev).strip().lower()
    if s.startswith("sev"):
        s = s.replace("sev", "")
    try:
        return int(s)
    except Exception:
        return None


def _eq(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return False
    return str(a).strip().lower() == str(b).strip().lower()


def _starts(a: Optional[str], prefix: Optional[str]) -> bool:
    if a is None or prefix is None:
        return False
    return str(a).lower().startswith(str(prefix).lower())


def _subscription_from_resource_id(resource_id: Optional[str]) -> Optional[str]:
    try:
        parts = (resource_id or "").split("/")
        return (
            parts[2] if len(parts) > 2 and parts[1].lower() == "subscriptions" else None
        )
    except Exception:
        return None


def _match_when(when: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """
    Return True if context satisfies the rule's 'when' conditions.
    All conditions are AND-ed. Supports:
      - equality: resourceId, resourceGroup, subscriptionId, namespace, schemaName, oncall
      - prefix: resourceGroupPrefix
      - severity ranges: severityMin, severityMax (SevN semantics)
      - wildcard: any="*"
      - status filters: finalOnly (default True), statusIn (list of allowed statuses)
    """
    exec_id = ctx.get("execId", "unknown")

    # Wildcard catch-all: only if any is Exactly "*"
    if when.get("any") == "*":
        return True

    # Status filtering (centralized)
    status = (ctx.get("status") or "").strip().lower()
    # By default, only route final outcomes
    final_only = when.get("finalOnly", True)
    final_statuses = {"succeeded", "error", "failed", "timeout", "routed"}
    if final_only and status not in final_statuses:
        logging.debug(
            f"[{exec_id}] Routing mismatch: status '{status}' not in final_statuses and finalOnly=True"
        )
        return False
    if "statusIn" in when:
        allowed = {str(x).strip().lower() for x in (when.get("statusIn") or [])}
        if allowed and status not in allowed:
            logging.debug(
                f"[{exec_id}] Routing mismatch: status '{status}' not in statusIn {allowed}"
            )
            return False

    # Equality
    if "resourceId" in when:
        if not _eq(ctx.get("resourceId"), when["resourceId"]):
            logging.debug(
                f"[{exec_id}] Routing mismatch: resourceId '{ctx.get('resourceId')}' != '{when['resourceId']}'"
            )
            return False
    if "resourceGroup" in when:
        if not _eq(ctx.get("resourceGroup"), when["resourceGroup"]):
            logging.debug(
                f"[{exec_id}] Routing mismatch: resourceGroup '{ctx.get('resourceGroup')}' != '{when['resourceGroup']}'"
            )
            return False
    if "resourceName" in when:
        if not _eq(ctx.get("resourceName"), when["resourceName"]):
            logging.debug(
                f"[{exec_id}] Routing mismatch: resourceName '{ctx.get('resourceName')}' != '{when['resourceName']}'"
            )
            return False
    if "subscriptionId" in when:
        sub = _subscription_from_resource_id(ctx.get("resourceId"))
        if not _eq(sub, when["subscriptionId"]):
            logging.debug(
                f"[{exec_id}] Routing mismatch: subscriptionId '{sub}' != '{when['subscriptionId']}'"
            )
            return False
    if "namespace" in when:
        if not _eq(ctx.get("namespace"), when["namespace"]):
            logging.debug(
                f"[{exec_id}] Routing mismatch: namespace '{ctx.get('namespace')}' != '{when['namespace']}'"
            )
            return False
    if "schemaName" in when:
        logging.debug(
            f"[{exec_id}] Routing check: schemaName '{ctx.get('schemaName')}' != '{when['schemaName']}'"
        )
        if not _eq(ctx.get("schemaName"), when["schemaName"]):
            logging.debug(
                f"[{exec_id}] Routing mismatch: schemaName '{ctx.get('schemaName')}' != '{when['schemaName']}'"
            )
            return False
    if "oncall" in when:
        if not _eq(str(ctx.get("oncall") or ""), str(when["oncall"])):
            logging.debug(
                f"[{exec_id}] Routing mismatch: oncall '{ctx.get('oncall')}' != '{when['oncall']}'"
            )
            return False

    # Prefix
    if "resourceGroupPrefix" in when:
        if not _starts(ctx.get("resourceGroup"), when["resourceGroupPrefix"]):
            logging.debug(
                f"[{exec_id}] Routing mismatch: resourceGroup '{ctx.get('resourceGroup')}' does not start with '{when['resourceGroupPrefix']}'"
            )
            return False

    # Severity range
    sev = _sev_to_num(ctx.get("severity"))

    if "isAlert" in when:
        raw_val = when["isAlert"]
        # Handle string "true"/"false" vs actual boolean
        if isinstance(raw_val, str):
            should_be_alert = raw_val.strip().lower() == "true"
        else:
            should_be_alert = bool(raw_val)

        # An event is considered an alert if it has a valid severity
        # OR if it's in a failure status (error/failed/timeout)
        is_alert = (sev is not None) or (status in {"failed", "error", "timeout"})

        if should_be_alert != is_alert:
            logging.debug(
                f"[{exec_id}] Routing mismatch: isAlert requirement {should_be_alert} != actual {is_alert} (sev={sev}, status={status})"
            )
            return False

    if "severityMin" in when:
        minv = _sev_to_num(when["severityMin"])
        if minv is not None and (sev is None or sev < minv):
            logging.debug(
                f"[{exec_id}] Routing mismatch: severity {sev} < severityMin {minv}"
            )
            return False
    if "severityMax" in when:
        maxv = _sev_to_num(when["severityMax"])
        if maxv is not None and (sev is None or sev > maxv):
            logging.debug(
                f"[{exec_id}] Routing mismatch: severity {sev} > severityMax {maxv}"
            )
            return False

    return True


# =========================
# Team credential resolution
# =========================


def _get_setting(key: str) -> Optional[str]:
    """
    Helper to get a setting from Azure Table Storage or Environment.
    """
    # Prevent unsafe environment/table key lookups from dynamic input.
    if not _SAFE_ENV_KEY_RE.fullmatch(str(key or "")):
        logging.warning("Rejected unsafe setting key lookup")
        return None

    now = time.time()
    cached = _setting_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    # Try Table Storage
    resolved: Optional[str] = None
    try:
        from azure.data.tables import TableClient

        conn_str = os.environ.get("AzureWebJobsStorage")
        if conn_str:
            with TableClient.from_connection_string(
                conn_str, table_name="CloudoSettings"
            ) as table_client:
                entity = table_client.get_entity(
                    partition_key="GlobalConfig", row_key=key
                )
                val = entity.get("value")
                if val:
                    resolved = _INVISIBLE_WHITESPACE_RE.sub(
                        " ", str(val).strip().strip('"').strip("'")
                    ).strip()
    except Exception:
        pass

    if resolved is None:
        val = os.environ.get(key)
        if val:
            resolved = _INVISIBLE_WHITESPACE_RE.sub(
                " ", str(val).strip().strip('"').strip("'")
            ).strip()

    _setting_cache[key] = (
        resolved,
        now + ROUTING_SETTINGS_CACHE_TTL_SECONDS,
    )
    return resolved


def get_setting(key: str) -> Optional[str]:
    """Public wrapper around _get_setting for callers outside this module
    (e.g. function_app.py) that need a Table Storage-backed, env-fallback
    configuration value, such as feature flags configurable from the admin UI.
    """
    return _get_setting(key)


def resolve_jsm_apikey(team: Optional[str]) -> Optional[str]:
    """
    Resolve JSM apiKey from table storage or env using naming convention:
      - JSM_API_KEY_<TEAM> (preferred)
      - JSM_API_KEY_DEFAULT (fallback 1)
      - JSM_API_KEY (fallback 2)
    """
    team = _sanitize_team(team)
    if team:
        key_name = f"JSM_API_KEY_{team}".upper().replace("-", "_")
        key = _get_setting(key_name)
        if key:
            return key

    return _get_setting("JSM_API_KEY_DEFAULT") or _get_setting("JSM_API_KEY")


def resolve_slack_token(team: Optional[str]) -> Optional[str]:
    """
    Resolve Slack token from table storage or env using naming convention:
      - SLACK_TOKEN_<TEAM> (preferred)
      - SLACK_TOKEN_DEFAULT (default)
    """
    team = _sanitize_team(team)
    if team:
        key_name = f"SLACK_TOKEN_{team}".upper().replace("-", "_")
        tok = _get_setting(key_name)
        if tok:
            return tok
    return _get_setting("SLACK_TOKEN_DEFAULT")


# =========================
# Context normalization
# =========================


def normalize_context(raw_ctx: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize the incoming alert context to a stable key set for rule matching.
    """
    return {
        "resourceId": raw_ctx.get("resourceId"),
        "resourceGroup": raw_ctx.get("resourceGroup"),
        "resourceName": raw_ctx.get("resourceName"),
        "schemaName": raw_ctx.get("schemaName"),
        "severity": raw_ctx.get("severity"),
        "namespace": raw_ctx.get("namespace"),
        "oncall": str(raw_ctx.get("oncall") or False),
        "status": raw_ctx.get("status"),
        "execId": raw_ctx.get("execId"),
        "name": raw_ctx.get("name"),
        "id": raw_ctx.get("id"),
        "routing_info": raw_ctx.get("routing_info") or {},
    }


# =========================
# Routing engine
# =========================


def route_alert(raw_ctx: dict[str, Any]) -> RoutingDecision:
    """
    Decide the actions to execute (Slack/JSM) based on routing rules.
    Returns a RoutingDecision with the ordered list of actions.
    If nothing matches, returns JSM fallback (only for final outcomes).
    """
    cfg = load_routing_config()
    ctx = normalize_context(raw_ctx)
    rules = cfg.get("rules", [])
    defaults = cfg.get("defaults", {})
    teams_cfg = cfg.get("teams", {})

    routing_info = ctx.get("routing_info") or {}
    allow_runtime_secrets = _as_bool(
        os.environ.get("ALLOW_ROUTING_INFO_SECRETS"), default=False
    )
    allow_inline_rule_secrets = _as_bool(
        os.environ.get("ALLOW_INLINE_ROUTING_SECRETS"), default=False
    )

    # Avoid logging sensitive information such as API keys or tokens
    safe_routing_info = {
        k: v for k, v in routing_info.items() if k not in {"slack_token", "jsm_token"}
    }
    logging.info(
        "Routing info (redacted): %s",
        {k: _safe_for_log(v) for k, v in safe_routing_info.items()},
    )
    ri_team = _sanitize_team(routing_info.get("team"))
    ri_slack_channel = _sanitize_channel(routing_info.get("slack_channel"))
    # Secrets from runtime payload are disabled by default to avoid injection.
    ri_slack_token = (
        str(routing_info.get("slack_token") or "").strip() or None
        if allow_runtime_secrets
        else None
    )
    ri_jsm_token = (
        str(routing_info.get("jsm_token") or "").strip() or None
        if allow_runtime_secrets
        else None
    )

    raw_status = (ctx.get("status") or "").strip().lower()
    safe_status = raw_status if _SAFE_STATUS_RE.fullmatch(raw_status) else "unknown"
    allowed_statuses = {
        "accepted",
        "succeeded",
        "error",
        "failed",
        "timeout",
        "routed",
        "scheduled",
    }
    status = safe_status if safe_status in allowed_statuses else "unknown"

    log_correlation_id = uuid.uuid4().hex[:12]
    logging.info(f"[{log_correlation_id}] Routing: evaluating {len(rules)} rules")

    for idx, rule in enumerate(rules):
        when = rule.get("when", {})
        if not _match_when(when, ctx):
            continue

        resolved_actions: list[Action] = []
        matched_team: Optional[str] = None

        for t in rule.get("then", []):
            atype = t.get("type")
            # support legacy "opsgenie" type in existing configs
            if atype == "opsgenie":
                atype = "jsm"
            if atype not in ("slack", "jsm"):
                logging.warning(f"Ignoring unsupported action type: {atype}")
                continue
            logging.info(
                "Executing action: %s for %s",
                _safe_for_log(atype, max_len=16),
                _safe_for_log(t.get("team"), max_len=64),
            )

            team_name = _sanitize_team(t.get("team")) or ri_team
            team_conf = teams_cfg.get(team_name, {}) if team_name else {}
            matched_team = matched_team or team_name

            if atype == "slack":
                channel = (
                    _sanitize_channel(t.get("channel"))
                    or (team_conf.get("slack", {}) or {}).get("channel")
                    or (defaults.get("slack", {}) or {}).get("channel")
                    or ri_slack_channel
                )
                channel = _sanitize_channel(channel)
                inline_rule_token = (
                    (str(t.get("token") or "").strip() or None)
                    if allow_inline_rule_secrets
                    else None
                )
                token = (
                    inline_rule_token
                    or resolve_slack_token(team_name)
                    or ri_slack_token
                )
                resolved_actions.append(
                    Action(type="slack", channel=channel, token=token, team=team_name)
                )

            elif atype == "jsm":
                jsm_team = (
                    team_name
                    or _sanitize_team((team_conf.get("jsm", {}) or {}).get("team"))
                    or _sanitize_team((defaults.get("jsm", {}) or {}).get("team"))
                    or ri_team
                )
                inline_rule_apikey = (
                    (str(t.get("apiKey") or "").strip() or None)
                    if allow_inline_rule_secrets
                    else None
                )
                api_key = (
                    inline_rule_apikey or resolve_jsm_apikey(jsm_team) or ri_jsm_token
                )
                if api_key:
                    api_key = str(api_key).strip().strip('"').strip("'")

                resolved_actions.append(
                    Action(type="jsm", team=jsm_team, apiKey=api_key)
                )

        action_types_in_rule = {a.type for a in resolved_actions}

        if "slack" in action_types_in_rule and ri_team:
            already_slack_for_team = any(
                a.type == "slack" and a.team == ri_team for a in resolved_actions
            )
            if not already_slack_for_team:
                ri_team_conf = teams_cfg.get(ri_team, {})
                extra_channel = (
                    ri_slack_channel
                    or (ri_team_conf.get("slack", {}) or {}).get("channel")
                    or (defaults.get("slack", {}) or {}).get("channel")
                )
                extra_token = ri_slack_token or resolve_slack_token(ri_team)
                if extra_channel or extra_token:
                    resolved_actions.append(
                        Action(
                            type="slack",
                            channel=extra_channel,
                            token=extra_token,
                            team=ri_team,
                        )
                    )

        if "jsm" in action_types_in_rule and (ri_team or ri_jsm_token):
            jsm_extra_team = ri_team or (defaults.get("jsm", {}) or {}).get("team")
            already_jsm_for_team = any(
                a.type == "jsm" and a.team == jsm_extra_team for a in resolved_actions
            )
            if not already_jsm_for_team:
                extra_api_key = ri_jsm_token or resolve_jsm_apikey(jsm_extra_team)
                if extra_api_key:
                    extra_api_key = str(extra_api_key).strip().strip('"').strip("'")
                    resolved_actions.append(
                        Action(type="jsm", team=jsm_extra_team, apiKey=extra_api_key)
                    )

        if resolved_actions:
            logging.info(
                f"[{log_correlation_id}] Routing: matched rule #{idx} with {len(resolved_actions)} action(s)"
            )
            return RoutingDecision(
                actions=resolved_actions,
                matched_rule_index=idx,
                matched_team=matched_team,
                reason="matched",
            )

    # Fallback only for final outcomes
    final_statuses = {"error", "failed", "timeout", "routed", "scheduled"}
    if status in final_statuses:
        jsm_team = ri_team or (defaults.get("jsm", {}) or {}).get("team")
        api_key = ri_jsm_token or resolve_jsm_apikey(jsm_team)
        logging.info(
            f"[{log_correlation_id}] Routing: no rule matched, using JSM fallback (final outcome)"
        )
        return RoutingDecision(
            actions=[Action(type="jsm", team=jsm_team, apiKey=api_key)],
            matched_rule_index=None,
            matched_team=None,
            reason="fallback_jsm",
        )

    logging.warning(
        f"[{log_correlation_id}] Routing: non-final status and no rule matched, no actions executed"
    )
    return RoutingDecision(
        actions=[],
        matched_rule_index=None,
        matched_team=None,
        reason="no_action_non_final",
    )


# =========================
# Action execution with fallback
# =========================


def execute_actions(
    decision: RoutingDecision,
    payload: dict[str, Any],
    send_slack_fn: Optional[Callable[..., Any]] = None,
    send_jsm_fn: Optional[Callable[..., Any]] = None,
) -> None:
    """
    Execute the decided actions in order.
    - If any action succeeds, continue executing others (fan-out).
    - If all actions fail, attempt a final JSM fallback using a default env key.
    """
    any_success = False
    jsm_payload_key = "jsm"

    if send_slack_fn is None or not callable(send_slack_fn):
        logging.error("Invalid send_slack_fn: expected callable")
        send_slack_fn = None
    if send_jsm_fn is None or not callable(send_jsm_fn):
        logging.error("Invalid send_jsm_fn: expected callable")
        send_jsm_fn = None

    slack_sender = send_slack_fn
    jsm_sender = send_jsm_fn

    for a in decision.actions:
        try:
            if a.type == "slack":
                if slack_sender is None:
                    raise ValueError("Slack sender not configured")
                slack_sender_safe = cast(Callable[..., Any], slack_sender)
                if not a.token:
                    raise ValueError("Missing Slack token")
                if not a.channel:
                    raise ValueError("Missing Slack channel")
                slack_payload = payload.get("slack")
                if not isinstance(slack_payload, dict):
                    raise ValueError("Missing/invalid Slack payload")
                slack_sender_safe(token=a.token, channel=a.channel, **slack_payload)
                any_success = True

            elif a.type in ("jsm"):
                if jsm_sender is None:
                    raise ValueError("JSM sender not configured")
                jsm_sender_safe = cast(Callable[..., Any], jsm_sender)
                if not a.apiKey:
                    raise ValueError("Missing JSM apiKey")
                jsm_payload = payload.get(jsm_payload_key)
                if not isinstance(jsm_payload, dict):
                    raise ValueError("Missing/invalid JSM payload")
                jsm_sender_safe(api_key=a.apiKey, **jsm_payload)
                any_success = True

        except Exception as e:
            logging.error(f"Routing action failed (type={a.type}, team={a.team}): {e}")
            continue

    if not any_success and decision.reason != "no_action_non_final":
        try:
            if jsm_sender is None:
                logging.error("Final fallback skipped: JSM sender not configured")
                return
            jsm_sender_safe = cast(Callable[..., Any], jsm_sender)
            api_key = resolve_jsm_apikey(None)
            if api_key:
                logging.info(
                    f"Attempting final JSM fallback (reason={decision.reason})"
                )
                try:
                    jsm_payload = payload.get(jsm_payload_key)
                    if not isinstance(jsm_payload, dict):
                        raise ValueError("Missing/invalid JSM payload")
                    ok = jsm_sender_safe(api_key=api_key, **jsm_payload)
                    if not ok:
                        logging.error("Final JSM fallback did not confirm success")
                except Exception as send_err:
                    logging.error(f"Final JSM fallback failed during send: {send_err}")
            else:
                logging.error("Final fallback skipped: JSM_API_KEY not set")

            status_msg = (
                "Escalation finished with errors; JSM fallback attempted"
                if api_key
                else "Escalation finished with errors; JSM fallback skipped"
            )
            logging.warning(status_msg)
        except Exception as e:
            logging.error(f"Final JSM fallback handling encountered an error: {e}")
            logging.warning(
                "Escalation finished with errors; fallback handling error was logged"
            )
