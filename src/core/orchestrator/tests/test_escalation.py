import json
from unittest.mock import MagicMock, patch

import requests
from escalation import (
    _jsm_alert_state,
    format_jsm_description,
    jsm_chain_alias,
    resolve_jsm_alias,
    send_jsm_alert,
    send_slack_execution,
)


def test_jsm_chain_alias():
    assert jsm_chain_alias("my-alert", 1) == "my-alert"
    assert jsm_chain_alias("my-alert", 2) == "my-alert-2"
    assert jsm_chain_alias("my-alert", 5) == "my-alert-5"


@patch("escalation.requests.get")
def test_jsm_alert_state(mock_get):
    headers = {"Authorization": "GenieKey test"}
    # 404
    mock_get.return_value.status_code = 404
    assert _jsm_alert_state("a1", headers) == "missing"

    # 500
    mock_get.return_value.status_code = 500
    assert _jsm_alert_state("a1", headers) is None

    # Exception
    mock_get.side_effect = requests.RequestException("boom")
    assert _jsm_alert_state("a1", headers) is None
    mock_get.side_effect = None

    # 200 open
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"data": {"status": "open"}}
    assert _jsm_alert_state("a1", headers) == "open"

    # 200 closed
    mock_get.return_value.json.return_value = {"data": {"status": "closed"}}
    assert _jsm_alert_state("a1", headers) == "closed"


@patch("escalation._jsm_alert_state")
def test_resolve_jsm_alias(mock_state):
    headers = {"Authorization": "GenieKey test"}

    # Case 1: First is missing
    mock_state.return_value = "missing"
    assert resolve_jsm_alias("alias", headers) == (1, "missing")

    # Case 2: First is None (unavailable)
    mock_state.return_value = None
    assert resolve_jsm_alias("alias", headers) == (1, None)

    # Case 3: 1 is open, 2 is missing
    def state_lookup(alias, h):
        if alias == "alias":
            return "open"
        return "missing"

    mock_state.side_effect = state_lookup
    assert resolve_jsm_alias("alias", headers) == (1, "open")


@patch("escalation.resolve_jsm_alias")
@patch("escalation.requests.post")
def test_send_jsm_alert_create(mock_post, mock_resolve):
    mock_resolve.return_value = (1, "open")
    mock_post.return_value.status_code = 202

    # Empty apiKey
    assert not send_jsm_alert("", "msg")
    assert not send_jsm_alert(None, "msg")

    # Create new alert
    res = send_jsm_alert(
        api_key="my-key",
        message="alert message",
        description="detailed desc",
        priority="P1",
        alias="my-alias",
        tags=["t1", "t2"],
        details={"cluster": "aks-1", "empty": None, ":test": "val"},
        monitor_condition="Fired",
    )
    assert res is True
    assert mock_post.called


@patch("escalation.resolve_jsm_alias")
@patch("escalation.requests.post")
def test_send_jsm_alert_resolve(mock_post, mock_resolve):
    mock_resolve.return_value = (1, "open")
    mock_post.return_value.status_code = 200

    # Resolved without alias
    assert not send_jsm_alert(
        api_key="my-key", message="msg", monitor_condition="Resolved"
    )

    # Resolved with alias already closed
    mock_resolve.return_value = (1, "closed")
    assert (
        send_jsm_alert(
            api_key="my-key",
            message="msg",
            alias="my-alias",
            monitor_condition="Resolved",
        )
        is True
    )

    # Resolved with open alias
    mock_resolve.return_value = (1, "open")
    assert (
        send_jsm_alert(
            api_key="my-key",
            message="msg",
            alias="my-alias",
            monitor_condition="Resolved",
        )
        is True
    )


def test_format_jsm_description():
    raw_payload = json.dumps(
        {
            "data": {
                "essentials": {
                    "alertRule": "CPUHigh",
                    "severity": "Sev2",
                    "monitorCondition": "Fired",
                },
                "alertContext": {
                    "labels": {
                        "cluster": "cls-prod",
                        "namespace": "payments",
                        "deployment": "pay-svc",
                    }
                },
            }
        }
    )
    res_info = {
        "_raw": raw_payload,
        "resource_name": "res-1",
        "resource_rg": "rg-1",
        "aks_namespace": "payments",
        "team": "payments-team",
    }
    desc = format_jsm_description("exec-123", res_info, {"status": "ok"})
    assert "🚨 Alert: CPUHigh" in desc
    assert "📊 Severity: Sev2" in desc
    assert "🏗️ Cluster: cls-prod" in desc
    assert "Resource Name: `res-1`" in desc
    assert "ExecID: `exec-123`" in desc


@patch("escalation.WebClient")
def test_send_slack_execution(mock_web_client):
    client_instance = MagicMock()
    mock_web_client.return_value = client_instance
    client_instance.chat_postMessage.return_value = {"ok": True}

    # Missing token or channel
    assert not send_slack_execution("", "C123", "msg")
    assert not send_slack_execution("token", "", "msg")

    # Success with blocks and url fix
    blocks = [
        {"type": "actions", "elements": [{"type": "button", "url": "example.com/test"}]}
    ]
    res = send_slack_execution("xoxb-test", "C123", "Hello", blocks=blocks)
    assert res is True
    assert blocks[0]["elements"][0]["url"] == "http://example.com/test"
