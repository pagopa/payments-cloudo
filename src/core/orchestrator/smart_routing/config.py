"""Routing rules: loaded from Table Storage, then env, then a built-in fallback."""

import json
import logging
import os
import time
from typing import Any

ROUTING_CONFIG_CACHE_TTL_SECONDS = int(
    os.getenv("ROUTING_CONFIG_CACHE_TTL_SECONDS", "60")
)
_routing_config_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}


def invalidate() -> None:
    _routing_config_cache["expires_at"] = 0.0


def _default_targets() -> dict[str, Any]:
    return {
        "jsm": {"team": "default"},  # apiKey resolved via env
        "slack": {
            "channel": os.environ.get("SLACK_CHANNEL_DEFAULT", "#cloudo-default")
        },
    }


def _fallback_config(defaults: dict[str, Any]) -> dict[str, Any]:
    """Page JSM and notify Slack on failed alerts; notify Slack on everything else."""
    return {
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


def _read_rules_from_table() -> str:
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
                return entity.get("value", "")
    except Exception as e:
        logging.warning(f"Could not load ROUTING_RULES from Table Storage: {e}")
    return ""


def load_routing_config() -> dict[str, Any]:
    """
    Load routing configuration from Azure Table Storage (CloudoSettings/ROUTING_RULES).
    Fallback to env ROUTING_RULES (JSON).
    If both absent/invalid, return safe fallback with JSM default.
    Do NOT store secrets (tokens/keys) in config JSON: resolve via environment.
    """
    defaults = _default_targets()
    fallback = _fallback_config(defaults)

    now = time.time()
    if _routing_config_cache.get("expires_at", 0.0) > now:
        cached_cfg = _routing_config_cache.get("value")
        if isinstance(cached_cfg, dict):
            return cached_cfg

    def _cache_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
        _routing_config_cache["value"] = cfg
        _routing_config_cache["expires_at"] = now + ROUTING_CONFIG_CACHE_TTL_SECONDS
        return cfg

    raw = _read_rules_from_table() or (os.environ.get("ROUTING_RULES") or "").strip()
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
