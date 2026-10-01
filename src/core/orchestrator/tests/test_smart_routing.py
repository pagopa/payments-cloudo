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
        {"team": "payments", "status": "failed", "severity": "Sev2"}
    )

    assert {(action.type, action.team) for action in decision.actions} == {
        ("jsm", "payments"),
        ("slack", "platform"),
        ("slack", "payments"),
    }
    assert decision.reason == "matched"
