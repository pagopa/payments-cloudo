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
