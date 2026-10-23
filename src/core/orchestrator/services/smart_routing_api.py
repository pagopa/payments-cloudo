"""Optional access to the smart routing module.

Execution flows keep working when smart routing cannot be imported: routing
is skipped and team-specific Slack/JSM credentials resolve to None.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class SmartRoutingApi:
    route_alert: Optional[Callable[..., Any]]
    execute_actions: Optional[Callable[..., Any]]
    resolve_slack_token: Callable[[Any], Optional[str]]
    resolve_jsm_apikey: Callable[[Any], Optional[str]]

    @property
    def available(self) -> bool:
        return bool(self.route_alert and self.execute_actions)


def load() -> SmartRoutingApi:
    try:
        from smart_routing import (
            execute_actions,
            resolve_jsm_apikey,
            resolve_slack_token,
            route_alert,
        )
    except ImportError:
        return SmartRoutingApi(
            route_alert=None,
            execute_actions=None,
            resolve_slack_token=lambda _: None,
            resolve_jsm_apikey=lambda _: None,
        )
    return SmartRoutingApi(
        route_alert=route_alert,
        execute_actions=execute_actions,
        resolve_slack_token=resolve_slack_token,
        resolve_jsm_apikey=resolve_jsm_apikey,
    )
