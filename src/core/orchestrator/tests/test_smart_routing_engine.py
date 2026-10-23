# python
"""Characterization tests for the smart routing engine.

Rules come from the real config sources (ROUTING_RULES env / a fake Table
Storage client) and credentials from real setting lookups (env vars), so
these tests exercise the public API end to end and do not depend on how the
module is organized internally.
"""

import json
import logging
import sys
import types

import pytest
import smart_routing
from smart_routing import Action, RoutingDecision, execute_actions, route_alert

SETTING_ENV = (
    "SLACK_TOKEN_DEFAULT",
    "SLACK_TOKEN_PAYMENTS",
    "SLACK_TOKEN_OPS",
    "SLACK_TOKEN_TEAM_A",
    "JSM_API_KEY",
    "JSM_API_KEY_DEFAULT",
    "JSM_API_KEY_PAYMENTS",
    "JSM_API_KEY_OPS",
    "SLACK_CHANNEL_DEFAULT",
    "ROUTING_RULES",
    "ALLOW_ROUTING_INFO_SECRETS",
    "ALLOW_INLINE_ROUTING_SECRETS",
    "AzureWebJobsStorage",
    "SOME_FLAG",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in SETTING_ENV:
        monkeypatch.delenv(key, raising=False)
    smart_routing._routing_config_cache["value"] = None
    smart_routing._routing_config_cache["expires_at"] = 0.0
    smart_routing._setting_cache.clear()
    yield
    smart_routing._routing_config_cache["value"] = None
    smart_routing._routing_config_cache["expires_at"] = 0.0
    smart_routing._setting_cache.clear()


def use_rules(monkeypatch, cfg):
    monkeypatch.setenv("ROUTING_RULES", json.dumps(cfg))


def fake_table(monkeypatch, entities=None, error=None):
    """Install a fake azure.data.tables.TableClient serving CloudoSettings rows."""
    entities = entities or {}

    class FakeTableClient:
        @classmethod
        def from_connection_string(cls, conn_str, table_name):
            assert table_name == "CloudoSettings"
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_entity(self, partition_key, row_key):
            if error:
                raise error
            if row_key not in entities:
                raise KeyError(row_key)
            return {"value": entities[row_key]}

    module = types.ModuleType("azure.data.tables")
    module.TableClient = FakeTableClient
    monkeypatch.setitem(sys.modules, "azure.data.tables", module)
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")


def actions(decision):
    return [(a.type, a.team, a.channel, a.token, a.apiKey) for a in decision.actions]


# =========================
# Configuration loading
# =========================


def test_config_falls_back_to_builtin_rules(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNEL_DEFAULT", "#fallback")

    cfg = smart_routing.load_routing_config()

    assert cfg["defaults"] == {
        "jsm": {"team": "default"},
        "slack": {"channel": "#fallback"},
    }
    assert cfg["teams"] == {}
    assert [r["when"] for r in cfg["rules"]] == [
        {"isAlert": "true", "statusIn": ["failed", "error", "routed"]},
        {"any": "*"},
    ]


def test_config_from_env_is_soft_merged_with_defaults(monkeypatch):
    use_rules(monkeypatch, {"rules": [{"when": {"any": "*"}, "then": []}]})

    cfg = smart_routing.load_routing_config()

    assert cfg["defaults"] == {
        "jsm": {"team": "default"},
        "slack": {"channel": "#cloudo-default"},
    }
    assert cfg["teams"] == {}
    assert cfg["rules"] == [{"when": {"any": "*"}, "then": []}]


def test_config_without_rules_key_gets_fallback_rules(monkeypatch):
    use_rules(monkeypatch, {"teams": {"ops": {}}})

    cfg = smart_routing.load_routing_config()

    assert len(cfg["rules"]) == 2
    assert cfg["teams"] == {"ops": {}}


def test_config_with_empty_rules_keeps_them_empty(monkeypatch):
    use_rules(monkeypatch, {"rules": []})

    assert smart_routing.load_routing_config()["rules"] == []


def test_invalid_config_json_uses_fallback(monkeypatch):
    monkeypatch.setenv("ROUTING_RULES", "{not json")

    assert len(smart_routing.load_routing_config()["rules"]) == 2


def test_config_from_table_storage_wins_over_env(monkeypatch):
    use_rules(monkeypatch, {"rules": [{"when": {"any": "*"}, "then": []}]})
    fake_table(
        monkeypatch,
        {"ROUTING_RULES": json.dumps({"rules": [{"when": {"x": 1}, "then": []}]})},
    )

    assert smart_routing.load_routing_config()["rules"][0]["when"] == {"x": 1}


def test_table_storage_failure_falls_back_to_env(monkeypatch):
    use_rules(monkeypatch, {"rules": [{"when": {"any": "*"}, "then": []}]})
    fake_table(monkeypatch, error=RuntimeError("table down"))

    assert smart_routing.load_routing_config()["rules"][0]["when"] == {"any": "*"}


def test_config_is_cached(monkeypatch):
    use_rules(monkeypatch, {"rules": [{"when": {"a": 1}, "then": []}]})
    first = smart_routing.load_routing_config()
    use_rules(monkeypatch, {"rules": [{"when": {"b": 2}, "then": []}]})

    assert smart_routing.load_routing_config() is first

    smart_routing._routing_config_cache["expires_at"] = 0.0
    assert smart_routing.load_routing_config()["rules"][0]["when"] == {"b": 2}


# =========================
# Settings and credentials
# =========================


def test_setting_from_env_is_cleaned(monkeypatch):
    monkeypatch.setenv("SOME_FLAG", ' "va\u00a0lue" ')

    assert smart_routing.get_setting("SOME_FLAG") == "va lue"


def test_setting_from_table_wins_and_is_cleaned(monkeypatch):
    monkeypatch.setenv("SOME_FLAG", "env")
    fake_table(monkeypatch, {"SOME_FLAG": "'table\u200bvalue'"})

    assert smart_routing.get_setting("SOME_FLAG") == "table value"


def test_missing_table_row_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SOME_FLAG", "env")
    fake_table(monkeypatch, {})

    assert smart_routing.get_setting("SOME_FLAG") == "env"


def test_unknown_setting_is_none_and_cached(monkeypatch):
    assert smart_routing.get_setting("SOME_FLAG") is None
    monkeypatch.setenv("SOME_FLAG", "late")

    assert smart_routing.get_setting("SOME_FLAG") is None


def test_unsafe_setting_key_is_rejected(monkeypatch):
    monkeypatch.setenv("lower", "x")

    assert smart_routing.get_setting("lower") is None
    assert smart_routing.get_setting("") is None


def test_slack_token_resolution_order(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN_DEFAULT", "tok-default")
    monkeypatch.setenv("SLACK_TOKEN_TEAM_A", "tok-a")

    assert smart_routing.resolve_slack_token("team-a") == "tok-a"
    assert smart_routing.resolve_slack_token("other") == "tok-default"
    assert smart_routing.resolve_slack_token(None) == "tok-default"
    assert smart_routing.resolve_slack_token("bad team!") == "tok-default"


def test_jsm_apikey_resolution_order(monkeypatch):
    monkeypatch.setenv("JSM_API_KEY", "jsm-generic")
    assert smart_routing.resolve_jsm_apikey("payments") == "jsm-generic"

    monkeypatch.setenv("JSM_API_KEY_DEFAULT", "jsm-default")
    smart_routing._setting_cache.clear()
    assert smart_routing.resolve_jsm_apikey("payments") == "jsm-default"

    monkeypatch.setenv("JSM_API_KEY_PAYMENTS", "jsm-payments")
    smart_routing._setting_cache.clear()
    assert smart_routing.resolve_jsm_apikey("payments") == "jsm-payments"
    assert smart_routing.resolve_jsm_apikey("  ") == "jsm-default"


# =========================
# Context normalization
# =========================


def test_normalize_context_team_fallbacks():
    base = {"status": "failed", "oncall": None}

    assert smart_routing.normalize_context({**base, "team": "a"})["team"] == "a"
    assert (
        smart_routing.normalize_context({**base, "resource_info": {"team": "b"}})[
            "team"
        ]
        == "b"
    )
    assert (
        smart_routing.normalize_context({**base, "routing_info": {"team": "c"}})["team"]
        == "c"
    )
    ctx = smart_routing.normalize_context(base)
    assert ctx["team"] is None
    assert ctx["oncall"] == "False"
    assert ctx["routing_info"] == {} and ctx["resource_info"] == {}


# =========================
# Rule matching
# =========================

BASE_CTX = {
    "status": "failed",
    "severity": "Sev2",
    "resourceId": "/subscriptions/sub-1/resourceGroups/rg-pay-1/providers/x/res",
    "resourceGroup": "rg-pay-1",
    "resourceName": "res",
    "namespace": "ns",
    "schemaName": "Schema",
    "oncall": "true",
}


@pytest.mark.parametrize(
    "when, ctx_overrides, expected",
    [
        ({"any": "*"}, {"status": "running"}, True),
        ({}, {"status": "running"}, False),
        ({"finalOnly": False}, {"status": "running"}, True),
        ({}, {"status": "succeeded"}, True),
        ({}, {"status": "timeout"}, True),
        ({"statusIn": ["error"]}, {}, False),
        ({"statusIn": ["Failed "]}, {}, True),
        ({"statusIn": []}, {}, True),
        ({"resourceId": BASE_CTX["resourceId"].upper()}, {}, True),
        ({"resourceId": "/other"}, {}, False),
        ({"resourceGroup": "RG-PAY-1"}, {}, True),
        ({"resourceGroup": "rg-x"}, {}, False),
        ({"resourceGroup": "rg-x"}, {"resourceGroup": None}, False),
        ({"resourceName": "res"}, {}, True),
        ({"resourceName": "nope"}, {}, False),
        ({"subscriptionId": "SUB-1"}, {}, True),
        ({"subscriptionId": "sub-2"}, {}, False),
        ({"subscriptionId": "sub-1"}, {"resourceId": "relative/id"}, False),
        ({"namespace": "ns"}, {}, True),
        ({"namespace": "other"}, {}, False),
        ({"schemaName": "schema"}, {}, True),
        ({"schemaName": "other"}, {}, False),
        ({"oncall": True}, {}, True),
        ({"oncall": "false"}, {}, False),
        ({"resourceGroupPrefix": "RG-PAY"}, {}, True),
        ({"resourceGroupPrefix": "rg-core"}, {}, False),
        ({"isAlert": "true"}, {}, True),
        ({"isAlert": True}, {"severity": None, "status": "succeeded"}, False),
        ({"isAlert": "false"}, {"severity": None, "status": "succeeded"}, True),
        ({"isAlert": "true"}, {"severity": None, "status": "error"}, True),
        ({"isAlert": 0}, {}, False),
        ({"severityMin": "Sev1"}, {}, True),
        ({"severityMin": "Sev3"}, {}, False),
        ({"severityMin": "Sev3"}, {"severity": None}, False),
        ({"severityMin": "garbage"}, {"severity": None}, True),
        ({"severityMax": "sev2"}, {}, True),
        ({"severityMax": "1"}, {}, False),
        ({"severityMax": "1"}, {"severity": "SevX"}, False),
        (
            {
                "statusIn": ["failed"],
                "resourceGroupPrefix": "rg-pay",
                "severityMax": "Sev3",
            },
            {},
            True,
        ),
    ],
)
def test_rule_matching(monkeypatch, when, ctx_overrides, expected):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "#d"}},
            "rules": [{"when": when, "then": [{"type": "slack"}]}],
        },
    )
    monkeypatch.setenv("SLACK_TOKEN_DEFAULT", "tok")

    decision = route_alert({**BASE_CTX, **ctx_overrides})

    assert (decision.reason == "matched") is expected


# =========================
# Action resolution
# =========================


def test_slack_and_jsm_targets_resolve_channels_and_credentials(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "#default"}, "jsm": {"team": "default"}},
            "teams": {"payments": {"slack": {"channel": "#payments"}}},
            "rules": [
                {
                    "when": {"any": "*"},
                    "then": [
                        {"type": "slack", "team": "payments"},
                        {"type": "slack", "team": "ops", "channel": "#ops-alerts"},
                        {"type": "opsgenie", "team": "payments"},
                        {"type": "email"},
                    ],
                }
            ],
        },
    )
    monkeypatch.setenv("SLACK_TOKEN_PAYMENTS", "tok-pay")
    monkeypatch.setenv("SLACK_TOKEN_OPS", "tok-ops")
    monkeypatch.setenv("JSM_API_KEY_PAYMENTS", '"jsm-pay"')

    decision = route_alert({"status": "failed", "oncall": "true"})

    assert actions(decision) == [
        ("slack", "payments", "#payments", "tok-pay", None),
        ("slack", "ops", "#ops-alerts", "tok-ops", None),
        ("jsm", "payments", None, None, "jsm-pay"),
    ]
    assert decision.matched_rule_index == 0
    assert decision.matched_team == "payments"
    assert decision.fallback_team is None
    assert decision.jsm_allowed is True


def test_unsafe_channels_and_teams_are_rejected(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "no-hash"}},
            "rules": [
                {
                    "when": {"any": "*"},
                    "then": [{"type": "slack", "team": "bad team", "channel": "#ok"}],
                }
            ],
        },
    )

    decision = route_alert({"status": "failed"})

    assert actions(decision) == [("slack", None, "#ok", None, None)]


def test_jsm_team_falls_back_to_team_config_then_defaults(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "defaults": {"jsm": {"team": "fallback-team"}},
            "teams": {},
            "rules": [{"when": {"any": "*"}, "then": [{"type": "jsm"}]}],
        },
    )
    monkeypatch.setenv("JSM_API_KEY_DEFAULT", "jsm-d")

    decision = route_alert({"status": "failed", "oncall": True})

    assert actions(decision) == [("jsm", "fallback-team", None, None, "jsm-d")]


def test_context_team_gets_its_own_slack_and_jsm_targets(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "#d"}, "jsm": {"team": "default"}},
            "teams": {"ops": {"slack": {"channel": "#ops"}}},
            "rules": [
                {
                    "when": {"any": "*"},
                    "then": [
                        {"type": "slack", "team": "payments"},
                        {"type": "jsm", "team": "payments"},
                    ],
                }
            ],
        },
    )
    monkeypatch.setenv("SLACK_TOKEN_OPS", "tok-ops")
    monkeypatch.setenv("JSM_API_KEY_OPS", "jsm-ops")

    decision = route_alert(
        {
            "status": "failed",
            "oncall": "true",
            "team": "ops",
            "routing_info": {"slack_channel": "#runtime"},
        }
    )

    assert [(a.type, a.team, a.channel) for a in decision.actions] == [
        ("slack", "payments", "#d"),
        ("jsm", "payments", None),
        ("slack", "ops", "#runtime"),
        ("jsm", "ops", None),
    ]
    assert decision.matched_team == "payments"
    assert decision.fallback_team == "ops"


def test_team_rules_are_evaluated_and_team_scoped_rules_filtered(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "#d"}},
            "teams": {
                "ops": {"rules": [{"when": {"any": "*"}, "then": [{"type": "slack"}]}]}
            },
            "rules": [
                {"team": "payments", "when": {"any": "*"}, "then": [{"type": "slack"}]},
                {"when": {"statusIn": ["error"]}, "then": [{"type": "slack"}]},
            ],
        },
    )

    decision = route_alert({"status": "failed", "team": "ops"})

    assert decision.matched_rule_index == 2
    assert [(a.type, a.team) for a in decision.actions] == [("slack", "ops")]


def test_duplicate_targets_across_rules_are_merged(monkeypatch):
    rule = {"when": {"any": "*"}, "then": [{"type": "slack", "team": "payments"}]}
    second = {"when": {"any": "*"}, "then": [{"type": "slack", "team": "ops"}]}
    use_rules(
        monkeypatch,
        {"defaults": {"slack": {"channel": "#d"}}, "rules": [rule, rule, second]},
    )

    decision = route_alert({"status": "failed"})

    assert [a.team for a in decision.actions] == ["payments", "ops"]
    assert decision.matched_rule_index == 0
    assert decision.matched_team == "ops"


def test_jsm_targets_are_skipped_when_not_oncall(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "#d"}},
            "rules": [
                {"when": {"any": "*"}, "then": [{"type": "jsm"}, {"type": "slack"}]}
            ],
        },
    )

    decision = route_alert({"status": "failed", "oncall": "false"})

    assert [a.type for a in decision.actions] == ["slack"]
    assert decision.jsm_allowed is False


@pytest.mark.parametrize(
    "allow_runtime, allow_inline, expected_slack, expected_jsm",
    [
        (None, None, "tok-env", "jsm-env"),
        ("true", None, "tok-env", "jsm-env"),
        (None, "true", "tok-inline", "jsm-inline"),
        ("true", "true", "tok-inline", "jsm-inline"),
    ],
)
def test_secret_sources_are_opt_in(
    monkeypatch, allow_runtime, allow_inline, expected_slack, expected_jsm
):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "#d"}},
            "rules": [
                {
                    "when": {"any": "*"},
                    "then": [
                        {"type": "slack", "token": "tok-inline"},
                        {"type": "jsm", "apiKey": "jsm-inline"},
                    ],
                }
            ],
        },
    )
    monkeypatch.setenv("SLACK_TOKEN_DEFAULT", "tok-env")
    monkeypatch.setenv("JSM_API_KEY", "jsm-env")
    if allow_runtime:
        monkeypatch.setenv("ALLOW_ROUTING_INFO_SECRETS", allow_runtime)
    if allow_inline:
        monkeypatch.setenv("ALLOW_INLINE_ROUTING_SECRETS", allow_inline)

    decision = route_alert(
        {
            "status": "failed",
            "oncall": "true",
            "routing_info": {"slack_token": "tok-runtime", "jsm_token": "jsm-runtime"},
        }
    )

    slack, jsm = decision.actions
    assert slack.token == expected_slack
    assert jsm.apiKey == expected_jsm


def test_runtime_secrets_are_used_only_as_last_resort(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {"channel": "#d"}},
            "rules": [
                {"when": {"any": "*"}, "then": [{"type": "slack"}, {"type": "jsm"}]}
            ],
        },
    )
    monkeypatch.setenv("ALLOW_ROUTING_INFO_SECRETS", "true")

    decision = route_alert(
        {
            "status": "failed",
            "oncall": "true",
            "routing_info": {"slack_token": " tok-rt ", "jsm_token": "jsm-rt"},
        }
    )

    assert [(a.type, a.token, a.apiKey) for a in decision.actions] == [
        ("slack", "tok-rt", None),
        ("jsm", None, "jsm-rt"),
    ]


def test_routing_info_is_logged_without_secrets(monkeypatch, caplog):
    use_rules(monkeypatch, {"rules": []})
    caplog.set_level(logging.INFO)

    route_alert(
        {
            "status": "failed",
            "routing_info": {
                "slack_token": "SECRET",
                "jsm_token": "SECRET",
                "x": "a\nb",
            },
        }
    )

    assert "SECRET" not in caplog.text
    assert "a?b" in caplog.text


# =========================
# Fallback decisions
# =========================


@pytest.mark.parametrize(
    "status, oncall, reason, n_actions",
    [
        ("failed", "true", "fallback_jsm", 1),
        ("routed", "true", "fallback_jsm", 1),
        ("scheduled", "true", "fallback_jsm", 1),
        ("failed", "false", "no_action_jsm_not_oncall", 0),
        ("succeeded", "true", "no_action_non_final", 0),
        ("running", "true", "no_action_non_final", 0),
        ("weird status!", "true", "no_action_non_final", 0),
    ],
)
def test_no_matching_rule(monkeypatch, status, oncall, reason, n_actions):
    use_rules(monkeypatch, {"defaults": {"jsm": {"team": "core"}}, "rules": []})
    monkeypatch.setenv("JSM_API_KEY", "jsm-generic")

    decision = route_alert({"status": status, "oncall": oncall, "team": "ops"})

    assert decision.reason == reason
    assert len(decision.actions) == n_actions
    assert decision.matched_rule_index is None
    assert decision.matched_team is None
    assert decision.fallback_team == "ops"
    if n_actions:
        assert actions(decision) == [("jsm", "ops", None, None, "jsm-generic")]


def test_jsm_fallback_without_team_uses_default_jsm_team(monkeypatch):
    use_rules(monkeypatch, {"defaults": {"jsm": {"team": "core"}}, "rules": []})

    decision = route_alert({"status": "error", "oncall": "true"})

    assert actions(decision) == [("jsm", "core", None, None, None)]
    assert decision.fallback_team == "core"


# =========================
# Action execution
# =========================


def decision_with(*acts, reason="matched", fallback_team="ops", jsm_allowed=True):
    return RoutingDecision(
        actions=list(acts),
        matched_rule_index=0,
        matched_team="ops",
        reason=reason,
        fallback_team=fallback_team,
        jsm_allowed=jsm_allowed,
    )


PAYLOAD = {"slack": {"message": "m", "blocks": []}, "jsm": {"message": "j"}}


def test_execute_sends_to_every_target():
    sent = []
    results = execute_actions(
        decision_with(
            Action(type="slack", channel="#c", token="t", team="ops"),
            Action(type="jsm", team="ops", apiKey="k"),
        ),
        PAYLOAD,
        send_slack_fn=lambda **kw: sent.append(("slack", kw)) or True,
        send_jsm_fn=lambda **kw: sent.append(("jsm", kw)) or None,
    )

    assert sent == [
        ("slack", {"token": "t", "channel": "#c", "message": "m", "blocks": []}),
        ("jsm", {"api_key": "k", "message": "j"}),
    ]
    assert results == [
        {"type": "slack", "team": "ops", "status": "sent", "channel": "#c"},
        {"type": "jsm", "team": "ops", "status": "sent"},
    ]


def test_sender_returning_false_is_reported_failed_without_fallback():
    jsm_calls = []

    results = execute_actions(
        decision_with(Action(type="slack", channel="#c", token="t", team="ops")),
        PAYLOAD,
        send_slack_fn=lambda **kw: False,
        send_jsm_fn=lambda **kw: jsm_calls.append(kw),
    )

    assert results == [
        {"type": "slack", "team": "ops", "status": "failed", "channel": "#c"}
    ]
    assert jsm_calls == []


@pytest.mark.parametrize(
    "action, payload, error",
    [
        (
            Action(type="slack", channel="#c", team="ops"),
            PAYLOAD,
            "Missing Slack token",
        ),
        (Action(type="slack", token="t", team="ops"), PAYLOAD, "Missing Slack channel"),
        (
            Action(type="slack", channel="#c", token="t", team="ops"),
            {"jsm": {}},
            "Missing/invalid Slack payload",
        ),
        (Action(type="jsm", team="ops"), PAYLOAD, "Missing JSM apiKey"),
        (
            Action(type="jsm", team="ops", apiKey="k"),
            {"slack": {}},
            "Missing/invalid JSM payload",
        ),
    ],
)
def test_invalid_targets_are_reported(action, payload, error):
    results = execute_actions(
        decision_with(action, jsm_allowed=False),
        payload,
        send_slack_fn=lambda **kw: True,
        send_jsm_fn=lambda **kw: True,
    )

    assert results[0]["status"] == "failed"
    assert results[0]["error"] == error


def test_missing_senders_are_reported():
    results = execute_actions(
        decision_with(
            Action(type="slack", channel="#c", token="t"),
            Action(type="jsm", apiKey="k"),
            jsm_allowed=False,
        ),
        PAYLOAD,
        send_slack_fn="not callable",
    )

    assert [r["error"] for r in results] == [
        "Slack sender not configured",
        "JSM sender not configured",
    ]


def test_sender_exception_is_truncated_in_the_record():
    def boom(**kw):
        raise RuntimeError("x" * 500)

    results = execute_actions(
        decision_with(Action(type="slack", channel="#c", token="t"), jsm_allowed=False),
        PAYLOAD,
        send_slack_fn=boom,
    )

    assert results[0]["error"] == "x" * 200


def test_unknown_action_type_is_ignored():
    assert execute_actions(decision_with(Action(type="email")), PAYLOAD) == []


def test_all_failed_triggers_final_jsm_fallback(monkeypatch):
    monkeypatch.setenv("JSM_API_KEY_OPS", "jsm-ops")
    calls = []

    results = execute_actions(
        decision_with(Action(type="slack", channel="#c", team="ops")),
        PAYLOAD,
        send_slack_fn=lambda **kw: True,
        send_jsm_fn=lambda **kw: calls.append(kw) or True,
    )

    assert calls == [{"api_key": "jsm-ops", "message": "j"}]
    assert results[-1] == {
        "type": "jsm",
        "team": "ops",
        "status": "sent",
        "fallback": True,
    }


@pytest.mark.parametrize(
    "sender_result, payload, expected",
    [
        (False, PAYLOAD, {"status": "failed"}),
        (None, PAYLOAD, {"status": "failed"}),
        (
            True,
            {"slack": {}},
            {"status": "failed", "error": "Missing/invalid JSM payload"},
        ),
    ],
)
def test_final_fallback_failures_are_recorded(
    monkeypatch, sender_result, payload, expected
):
    monkeypatch.setenv("JSM_API_KEY", "jsm")

    results = execute_actions(
        decision_with(reason="fallback_jsm"),
        payload,
        send_jsm_fn=lambda **kw: sender_result,
    )

    assert results == [{"type": "jsm", "team": "ops", "fallback": True, **expected}]


def test_final_fallback_sender_exception_is_recorded(monkeypatch):
    monkeypatch.setenv("JSM_API_KEY", "jsm")

    def boom(**kw):
        raise RuntimeError("jsm down")

    results = execute_actions(decision_with(), PAYLOAD, send_jsm_fn=boom)

    assert results == [
        {
            "type": "jsm",
            "team": "ops",
            "status": "failed",
            "fallback": True,
            "error": "jsm down",
        }
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"jsm_allowed": False},
        {"reason": "no_action_non_final"},
    ],
)
def test_final_fallback_is_skipped(monkeypatch, kwargs):
    monkeypatch.setenv("JSM_API_KEY", "jsm")
    calls = []

    results = execute_actions(
        decision_with(**kwargs), PAYLOAD, send_jsm_fn=lambda **kw: calls.append(kw)
    )

    assert results == [] and calls == []


def test_final_fallback_needs_sender_and_key():
    assert execute_actions(decision_with(), PAYLOAD) == []
    assert (
        execute_actions(decision_with(), PAYLOAD, send_jsm_fn=lambda **kw: True) == []
    )


def test_final_fallback_resolution_error_is_contained(monkeypatch):
    def broken(key):
        raise RuntimeError("settings down")

    monkeypatch.setattr(smart_routing, "_get_setting", broken, raising=False)
    for name in ("smart_routing.settings",):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "_get_setting", broken)

    assert (
        execute_actions(decision_with(), PAYLOAD, send_jsm_fn=lambda **kw: True) == []
    )


def test_empty_and_unsafe_runtime_channels_are_ignored(monkeypatch):
    # An empty default channel lets the runtime channel decide.
    monkeypatch.setenv("SLACK_CHANNEL_DEFAULT", "")
    use_rules(
        monkeypatch,
        {
            "defaults": {"slack": {}},
            "rules": [
                {"when": {"any": "*"}, "then": [{"type": "slack", "channel": ""}]}
            ],
        },
    )

    for channel in ("", "   ", "bad channel"):
        decision = route_alert(
            {"status": "failed", "routing_info": {"slack_channel": channel}}
        )
        assert [a.channel for a in decision.actions] == [None]


def test_prefix_rule_does_not_match_without_resource_group(monkeypatch):
    use_rules(
        monkeypatch,
        {
            "rules": [
                {"when": {"resourceGroupPrefix": "rg"}, "then": [{"type": "slack"}]}
            ]
        },
    )

    assert route_alert({"status": "failed"}).reason != "matched"
