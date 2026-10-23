"""Routing decision model."""

from dataclasses import dataclass
from typing import Optional


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
    reason: str  # "matched" | "fallback_jsm" | "no_action_jsm_not_oncall" | "no_action_non_final"
    fallback_team: Optional[str] = None
    jsm_allowed: bool = True
