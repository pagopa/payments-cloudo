"""Smart routing: decide which Slack channels / JSM teams hear about an
execution, and deliver the notifications.

- config:   routing rules (Table Storage, env, built-in fallback)
- settings: setting lookups and per-team Slack/JSM credentials
- matching: rule `when` conditions
- router:   route_alert, the routing decision
- actions:  execute_actions, delivery with final JSM fallback
"""

from smart_routing import config, settings
from smart_routing.actions import execute_actions
from smart_routing.config import _routing_config_cache, load_routing_config
from smart_routing.matching import normalize_context
from smart_routing.models import Action, RoutingDecision
from smart_routing.router import route_alert
from smart_routing.settings import (
    _get_setting,
    _setting_cache,
    get_setting,
    resolve_jsm_apikey,
    resolve_slack_token,
)


def invalidate_caches() -> None:
    """Drop cached rules and settings (e.g. after an admin settings update)."""
    config.invalidate()
    settings.invalidate()


__all__ = [
    "Action",
    "RoutingDecision",
    "_get_setting",
    "_routing_config_cache",
    "_setting_cache",
    "execute_actions",
    "get_setting",
    "invalidate_caches",
    "load_routing_config",
    "normalize_context",
    "resolve_jsm_apikey",
    "resolve_slack_token",
    "route_alert",
]
