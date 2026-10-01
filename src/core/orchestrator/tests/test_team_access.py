import json

import azure.functions as func
import function_app


def make_request(query: str = "") -> func.HttpRequest:
    url = f"https://example.test/api/resource{query}"
    params = {}
    if query:
        key, value = query.lstrip("?").split("=", 1)
        params[key] = value
    return func.HttpRequest(method="GET", url=url, params=params, body=b"{}")


def test_default_and_own_team_are_visible_to_operator():
    request = make_request()
    entities = [
        {"team": "default", "id": "shared"},
        {"team": "payments", "id": "owned"},
        {"team": "platform", "id": "hidden"},
    ]

    visible = function_app._filter_entities_by_team(
        entities, request, {"role": "OPERATOR", "team": "payments"}
    )

    assert [entity["id"] for entity in visible] == ["shared", "owned"]


def test_only_admin_can_request_all_teams():
    request = make_request("?includeAllTeams=true")
    entities = [{"team": "payments"}, {"team": "platform"}]

    assert function_app._filter_entities_by_team(
        entities, request, {"role": "OPERATOR", "team": "payments"}
    ) == [{"team": "payments"}]
    assert (
        function_app._filter_entities_by_team(
            entities, request, {"role": "ADMIN", "team": "default"}
        )
        == entities
    )


def test_non_admin_cannot_assign_another_team():
    team, error = function_app._requested_write_team(
        {"team": "platform"}, {"role": "OPERATOR", "team": "payments"}
    )

    assert team is None
    assert error == "Only administrators can assign resources to another team"


def test_team_is_normalized_for_legacy_entities():
    assert function_app._entity_team({}) == "default"
    assert function_app._entity_team({"Team": "Payments"}) == "payments"


class _FakeSettingsTable:
    def __init__(self, stored):
        self._stored = stored

    def get_entity(self, partition_key, row_key):
        return {"value": json.dumps(self._stored)}


def test_operator_settings_update_is_scoped_to_own_team():
    stored = {
        "defaults": {"slack": {"channel": "#default"}},
        "teams": {"other": {"slack": {"channel": "#other"}}},
        "rules": [
            {
                "team": "other",
                "when": {"any": "*"},
                "then": [{"type": "jsm", "team": "other"}],
            },
            {"team": "ops", "when": {}, "then": []},
        ],
    }
    body = {
        "SLACK_TOKEN_DEFAULT": "stolen",
        "SLACK_TOKEN_OPS": "xoxb-ops",
        "ROUTING_RULES": json.dumps(
            {
                "defaults": {"slack": {"channel": "#hijack"}},
                "teams": {
                    "other": {"slack": {"channel": "#hijack"}},
                    "ops": {"slack": {"channel": "#ops-alerts", "token": "x"}},
                },
                "rules": [
                    {
                        "team": "other",
                        "when": {"statusIn": ["failed"]},
                        "then": [
                            {
                                "type": "slack",
                                "team": "other",
                                "channel": "#ops",
                                "token": "t",
                            }
                        ],
                    }
                ],
            }
        ),
    }

    updates = function_app._build_operator_settings_update(
        _FakeSettingsTable(stored), body, "ops", {"default", "ops", "other"}
    )

    assert "SLACK_TOKEN_DEFAULT" not in updates
    assert updates["SLACK_TOKEN_OPS"] == "xoxb-ops"
    assert updates["SLACK_CHANNEL_OPS"] == "#ops-alerts"
    result = json.loads(updates["ROUTING_RULES"])
    assert result["defaults"]["slack"]["channel"] == "#default"
    assert result["teams"]["other"]["slack"]["channel"] == "#other"
    assert result["teams"]["ops"]["slack"] == {"channel": "#ops-alerts"}
    assert [r["team"] for r in result["rules"]] == ["other", "ops"]
    assert result["rules"][1]["then"] == [
        {"type": "slack", "team": "other", "channel": "#ops"}
    ]


def test_team_approval_targets_prefer_team_config(monkeypatch):
    import smart_routing

    monkeypatch.setattr(
        smart_routing,
        "load_routing_config",
        lambda: {"teams": {"ops": {"slack": {"channel": "#ops-approvals"}}}},
    )
    monkeypatch.setattr(smart_routing, "resolve_slack_token", lambda team: "tok-ops")
    monkeypatch.setattr(smart_routing, "resolve_jsm_apikey", lambda team: "jsm-ops")

    assert function_app._team_approval_targets("ops", "t", "#c", "j") == (
        "tok-ops",
        "#ops-approvals",
        "jsm-ops",
    )
    assert function_app._team_approval_targets("default", "t", "#c", "j") == (
        "t",
        "#c",
        "j",
    )


class _FakeUsersTable:
    def __init__(self, keys):
        self._keys = set(keys)

    def get_entity(self, partition_key, row_key):
        if row_key not in self._keys:
            raise KeyError(row_key)
        return {"RowKey": row_key}


def test_user_row_key_is_case_insensitive_and_keeps_legacy_rows():
    assert function_app._resolve_user_row_key(_FakeUsersTable([]), " Fabio ") == "fabio"
    assert (
        function_app._resolve_user_row_key(_FakeUsersTable(["fabio"]), "FABIO")
        == "fabio"
    )
    assert (
        function_app._resolve_user_row_key(_FakeUsersTable(["Fabio"]), "Fabio")
        == "Fabio"
    )
