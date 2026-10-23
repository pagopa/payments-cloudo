"""Routing engine: turn an alert/execution context into Slack/JSM targets."""

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from smart_routing import config, settings
from smart_routing.matching import match_when, normalize_context
from smart_routing.models import Action, RoutingDecision
from smart_routing.sanitize import (
    SAFE_STATUS_RE,
    as_bool,
    safe_for_log,
    sanitize_channel,
    sanitize_team,
)

ALLOWED_STATUSES = {
    "accepted",
    "succeeded",
    "error",
    "failed",
    "timeout",
    "routed",
    "scheduled",
}
# Statuses that still page JSM when no rule matched.
FALLBACK_STATUSES = {"error", "failed", "timeout", "routed", "scheduled"}


@dataclass
class _Routing:
    """Everything a rule needs to resolve its targets."""

    ctx: dict[str, Any]
    defaults: dict[str, Any]
    teams_cfg: dict[str, Any]
    team: Optional[str]  # requester team (context, then routing_info)
    slack_channel: Optional[str]  # requester channel from routing_info
    slack_token: Optional[str]  # runtime secrets, only when explicitly allowed
    jsm_token: Optional[str]
    allow_inline_secrets: bool
    jsm_allowed: bool  # JSM pages people: only on-call executions may reach it
    status: str
    correlation_id: str

    def default_slack_channel(self) -> Optional[str]:
        return (self.defaults.get("slack", {}) or {}).get("channel")

    def default_jsm_team(self) -> Optional[str]:
        return (self.defaults.get("jsm", {}) or {}).get("team")


def _runtime_secret(routing_info: dict, key: str, allowed: bool) -> Optional[str]:
    # Secrets from runtime payload are disabled by default to avoid injection.
    return str(routing_info.get(key) or "").strip() or None if allowed else None


def _inline_secret(target: dict, key: str, allowed: bool) -> Optional[str]:
    return (str(target.get(key) or "").strip() or None) if allowed else None


def _strip_quotes(value: Optional[str]) -> Optional[str]:
    return str(value).strip().strip('"').strip("'") if value else value


def _prepare(raw_ctx: dict[str, Any]) -> tuple[_Routing, list]:
    cfg = config.load_routing_config()
    ctx = normalize_context(raw_ctx)
    routing_info = ctx.get("routing_info") or {}
    allow_runtime_secrets = as_bool(
        os.environ.get("ALLOW_ROUTING_INFO_SECRETS"), default=False
    )
    allow_inline_secrets = as_bool(
        os.environ.get("ALLOW_INLINE_ROUTING_SECRETS"), default=False
    )

    # Avoid logging sensitive information such as API keys or tokens
    safe_routing_info = {
        k: v for k, v in routing_info.items() if k not in {"slack_token", "jsm_token"}
    }
    logging.info(
        "Routing info (redacted): %s",
        {k: safe_for_log(v) for k, v in safe_routing_info.items()},
    )

    raw_status = (ctx.get("status") or "").strip().lower()
    safe_status = raw_status if SAFE_STATUS_RE.fullmatch(raw_status) else "unknown"
    return _Routing(
        ctx=ctx,
        defaults=cfg.get("defaults", {}),
        teams_cfg=cfg.get("teams", {}),
        team=sanitize_team(ctx.get("team")) or sanitize_team(routing_info.get("team")),
        slack_channel=sanitize_channel(routing_info.get("slack_channel")),
        slack_token=_runtime_secret(routing_info, "slack_token", allow_runtime_secrets),
        jsm_token=_runtime_secret(routing_info, "jsm_token", allow_runtime_secrets),
        allow_inline_secrets=allow_inline_secrets,
        jsm_allowed=as_bool(ctx.get("oncall"), default=False),
        status=safe_status if safe_status in ALLOWED_STATUSES else "unknown",
        correlation_id=uuid.uuid4().hex[:12],
    ), cfg.get("rules", [])


def _rules_to_evaluate(rules: list, routing: _Routing) -> list:
    """Global rules first, then the requester team's own rules."""
    team_rules = (
        (routing.teams_cfg.get(routing.team, {}) or {}).get("rules", [])
        if routing.team
        else []
    )
    rules_to_evaluate = list(rules)
    if isinstance(team_rules, list):
        rules_to_evaluate.extend(team_rules)
    return rules_to_evaluate


def _slack_action(
    target: dict, team_name: Optional[str], team_conf: dict, routing: _Routing
) -> Action:
    channel = sanitize_channel(
        sanitize_channel(target.get("channel"))
        or (team_conf.get("slack", {}) or {}).get("channel")
        or routing.default_slack_channel()
        or routing.slack_channel
    )
    token = (
        _inline_secret(target, "token", routing.allow_inline_secrets)
        or settings.resolve_slack_token(team_name)
        or routing.slack_token
    )
    return Action(type="slack", channel=channel, token=token, team=team_name)


def _jsm_action(
    target: dict, team_name: Optional[str], team_conf: dict, routing: _Routing
) -> Action:
    jsm_team = (
        team_name
        or sanitize_team((team_conf.get("jsm", {}) or {}).get("team"))
        or sanitize_team(routing.default_jsm_team())
        or routing.team
    )
    api_key = (
        _inline_secret(target, "apiKey", routing.allow_inline_secrets)
        or settings.resolve_jsm_apikey(jsm_team)
        or routing.jsm_token
    )
    return Action(type="jsm", team=jsm_team, apiKey=_strip_quotes(api_key))


def _rule_targets(rule: dict, routing: _Routing) -> tuple[list[Action], Optional[str]]:
    """Resolve a matching rule's `then` targets; returns (actions, matched team)."""
    resolved: list[Action] = []
    matched_team: Optional[str] = None
    for target in rule.get("then", []):
        atype = target.get("type")
        # support legacy "opsgenie" type in existing configs
        if atype == "opsgenie":
            atype = "jsm"
        if atype not in ("slack", "jsm"):
            logging.warning(f"Ignoring unsupported action type: {atype}")
            continue
        if atype == "jsm" and not routing.jsm_allowed:
            logging.info(
                f"[{routing.correlation_id}] Routing: JSM target skipped (execution is not on-call)"
            )
            continue
        logging.info(
            "Executing action: %s for %s",
            safe_for_log(atype, max_len=16),
            safe_for_log(target.get("team"), max_len=64),
        )
        team_name = sanitize_team(target.get("team")) or routing.team
        team_conf = routing.teams_cfg.get(team_name, {}) if team_name else {}
        matched_team = matched_team or team_name
        build = _slack_action if atype == "slack" else _jsm_action
        resolved.append(build(target, team_name, team_conf, routing))
    return resolved, matched_team


def _add_requester_team_targets(resolved: list[Action], routing: _Routing) -> None:
    """The requester team also hears about alerts routed to other teams."""
    action_types = {a.type for a in resolved}
    team = routing.team

    if "slack" in action_types and team:
        if not any(a.type == "slack" and a.team == team for a in resolved):
            team_conf = routing.teams_cfg.get(team, {})
            channel = (
                routing.slack_channel
                or (team_conf.get("slack", {}) or {}).get("channel")
                or routing.default_slack_channel()
            )
            token = routing.slack_token or settings.resolve_slack_token(team)
            if channel or token:
                resolved.append(
                    Action(type="slack", channel=channel, token=token, team=team)
                )

    if "jsm" in action_types and (team or routing.jsm_token):
        jsm_team = team or routing.default_jsm_team()
        if not any(a.type == "jsm" and a.team == jsm_team for a in resolved):
            api_key = routing.jsm_token or settings.resolve_jsm_apikey(jsm_team)
            if api_key:
                resolved.append(
                    Action(type="jsm", team=jsm_team, apiKey=_strip_quotes(api_key))
                )


def _merge_unique(all_actions: list[Action], new_actions: list[Action]) -> None:
    for action in new_actions:
        duplicate = any(
            existing.type == action.type
            and existing.team == action.team
            and existing.channel == action.channel
            for existing in all_actions
        )
        if not duplicate:
            all_actions.append(action)


def _no_match_decision(routing: _Routing, evaluated: int) -> RoutingDecision:
    status = routing.status
    if status in FALLBACK_STATUSES and not routing.jsm_allowed:
        logging.info(
            f"[{routing.correlation_id}] Routing: no action and execution is not on-call, JSM fallback skipped"
        )
        return RoutingDecision(
            actions=[],
            matched_rule_index=None,
            matched_team=None,
            reason="no_action_jsm_not_oncall",
            fallback_team=routing.team,
            jsm_allowed=False,
        )
    if status in FALLBACK_STATUSES:
        jsm_team = routing.team or routing.default_jsm_team()
        api_key = routing.jsm_token or settings.resolve_jsm_apikey(jsm_team)
        logging.warning(
            f"[{routing.correlation_id}] Routing FALLBACK: no rule matched "
            f"(evaluated={evaluated}, status={safe_for_log(status, 16)}, "
            f"team={safe_for_log(routing.team, 64)}) -> JSM fallback "
            f"(jsm_team={safe_for_log(jsm_team, 64)}, api_key_resolved={bool(api_key)})"
        )
        return RoutingDecision(
            actions=[Action(type="jsm", team=jsm_team, apiKey=api_key)],
            matched_rule_index=None,
            matched_team=None,
            reason="fallback_jsm",
            fallback_team=jsm_team,
        )
    logging.warning(
        f"[{routing.correlation_id}] Routing: non-final status and no rule matched, no actions executed"
    )
    return RoutingDecision(
        actions=[],
        matched_rule_index=None,
        matched_team=None,
        reason="no_action_non_final",
        fallback_team=routing.team,
    )


def route_alert(raw_ctx: dict[str, Any]) -> RoutingDecision:
    """
    Decide the actions to execute (Slack/JSM) based on routing rules.
    Every matching rule contributes its targets (de-duplicated).
    If nothing matches, returns JSM fallback (only for final outcomes).
    """
    routing, rules = _prepare(raw_ctx)
    rules_to_evaluate = _rules_to_evaluate(rules, routing)
    logging.info(
        f"[{routing.correlation_id}] Routing: evaluating {len(rules_to_evaluate)} rules"
    )

    all_actions: list[Action] = []
    first_matched_rule_index: Optional[int] = None
    matched_team: Optional[str] = None

    for idx, rule in enumerate(rules_to_evaluate):
        rule_team = sanitize_team(rule.get("team"))
        if rule_team and rule_team != routing.team:
            continue
        if not match_when(rule.get("when", {}), routing.ctx):
            continue

        resolved, matched_team = _rule_targets(rule, routing)
        _add_requester_team_targets(resolved, routing)
        if resolved:
            logging.info(
                f"[{routing.correlation_id}] Routing: matched rule #{idx} with {len(resolved)} action(s)"
            )
            if first_matched_rule_index is None:
                first_matched_rule_index = idx
            matched_team = matched_team or routing.team
            _merge_unique(all_actions, resolved)

    if all_actions:
        logging.info(
            f"[{routing.correlation_id}] Routing: matched {len(all_actions)} unique action(s) across all matching rules"
        )
        return RoutingDecision(
            actions=all_actions,
            matched_rule_index=first_matched_rule_index,
            matched_team=matched_team,
            reason="matched",
            fallback_team=routing.team,
            jsm_allowed=routing.jsm_allowed,
        )
    return _no_match_decision(routing, len(rules_to_evaluate))
