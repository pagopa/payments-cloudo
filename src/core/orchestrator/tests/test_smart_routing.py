import smart_routing


def test_route_alert_fans_out_all_matching_global_and_team_rules(monkeypatch):
    monkeypatch.setattr(
        smart_routing,
        "load_routing_config",
        lambda: {
            "defaults": {"jsm": {"team": "default"}, "slack": {"channel": "#default"}},
            "teams": {
                "payments": {
                    "rules": [
                        {"when": {"statusIn": ["failed"]}, "then": [{"type": "slack"}]}
                    ]
                }
            },
            "rules": [
                {
                    "when": {"statusIn": ["failed"]},
                    "then": [{"type": "jsm", "team": "payments"}],
                },
                {
                    "when": {"statusIn": ["failed"]},
                    "then": [{"type": "slack", "team": "platform"}],
                },
            ],
        },
    )
    monkeypatch.setattr(
        smart_routing, "resolve_slack_token", lambda team: f"slack-{team}"
    )
    monkeypatch.setattr(smart_routing, "resolve_jsm_apikey", lambda team: f"jsm-{team}")

    decision = smart_routing.route_alert(
        {"team": "payments", "status": "failed", "severity": "Sev2", "oncall": "true"}
    )

    assert {(action.type, action.team) for action in decision.actions} == {
        ("jsm", "payments"),
        ("slack", "platform"),
        ("slack", "payments"),
    }
    assert decision.reason == "matched"


def test_execute_actions_reports_delivery_per_target():
    from smart_routing import Action, RoutingDecision, execute_actions

    decision = RoutingDecision(
        actions=[
            Action(type="slack", channel="#core", token="t", team="core"),
            Action(type="jsm", team="infra", apiKey=None),
        ],
        matched_rule_index=0,
        matched_team="infra",
        reason="matched",
        fallback_team="infra",
    )
    payload = {"slack": {"message": "m"}, "jsm": {"message": "m"}}

    results = execute_actions(
        decision,
        payload,
        send_slack_fn=lambda token, channel, **kw: True,
        send_jsm_fn=lambda api_key, **kw: True,
    )

    assert results[0] == {
        "type": "slack",
        "team": "core",
        "status": "sent",
        "channel": "#core",
    }
    assert results[1]["type"] == "jsm"
    assert results[1]["status"] == "failed"
    assert results[1]["error"] == "Missing JSM apiKey"


def test_jsm_targets_are_dropped_when_execution_is_not_oncall(monkeypatch):
    import smart_routing

    monkeypatch.setattr(
        smart_routing,
        "load_routing_config",
        lambda: {
            "defaults": {"slack": {"channel": "#d"}, "jsm": {"team": "default"}},
            "teams": {},
            "rules": [
                {
                    "when": {"any": "*"},
                    "then": [{"type": "jsm", "team": "infra"}],
                }
            ],
        },
    )
    monkeypatch.setattr(smart_routing, "resolve_jsm_apikey", lambda team: "key")

    ctx = {"status": "failed", "team": "infra", "oncall": "false"}
    off = smart_routing.route_alert(ctx)
    assert off.actions == []
    assert off.jsm_allowed is False

    on = smart_routing.route_alert({**ctx, "oncall": "true"})
    assert [a.type for a in on.actions] == ["jsm"]

    calls = []
    smart_routing.execute_actions(
        off,
        {"jsm": {"message": "m"}},
        send_jsm_fn=lambda api_key, **kw: calls.append(1),
    )
    assert calls == []
