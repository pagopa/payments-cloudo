# python
"""Characterization tests for the Receiver's smart-routing notification,
log persistence edge cases and AI triage placeholder.

Complements test_receiver.py by pinning the exact routing context, the
Slack/JSM payload handed to smart routing and where notification outcomes
are stored, so the receiver can be restructured safely.
"""

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import azure.functions as func
import escalation
import pytest
import smart_routing
from routes import receiver
from services import ai_triage, status_update

from core import log_blobs, storage


class RecordingOut(func.Out):
    def __init__(self):
        self.values = []

    def set(self, v):
        self.values.append(v)

    def get(self):
        return self.values[-1] if self.values else None

    def entities(self):
        return [json.loads(v) for v in self.values]


def make_body(**overrides) -> dict:
    body = {
        "exec_id": "exec-123",
        "status": "failed",
        "name": "My Rule",
        "id": "schema-1",
        "runbook": "rb.sh",
        "run_args": "--x 1",
        "worker": "worker-a",
        "group": "payments",
        "oncall": "true",
        "initiator": "tester",
        "monitor_condition": "Fired",
        "severity": "Sev1",
        "resource_info": {
            "resource_id": "/sub/rg/res",
            "resource_rg": "rg-1",
            "resource_name": "res-1",
            "aks_namespace": "ns-1",
            "team": "payments",
        },
        "routing_info": {"team": "payments", "slack_channel": "#pay"},
        "logs_b64": base64.b64encode(b"line1\nboom").decode(),
    }
    body.update(overrides)
    return body


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("NEXTJS_URL", "https://ui.example/")
    mocks = SimpleNamespace(
        decision=SimpleNamespace(
            actions=[SimpleNamespace(team="payments"), SimpleNamespace(team="ops")],
            reason="rule",
            matched_rule_index=1,
        ),
        execute_actions=MagicMock(return_value=[{"type": "slack", "status": "sent"}]),
        send_slack=MagicMock(return_value=True),
        send_jsm=MagicMock(return_value=True),
        upload=MagicMock(return_value="blobref://runbook-logs/x.log"),
        queue=MagicMock(),
        table=MagicMock(),
    )
    mocks.route_alert = MagicMock(return_value=mocks.decision)
    monkeypatch.setattr(smart_routing, "route_alert", mocks.route_alert)
    monkeypatch.setattr(smart_routing, "execute_actions", mocks.execute_actions)
    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="false"))
    monkeypatch.setattr(escalation, "send_slack_execution", mocks.send_slack)
    monkeypatch.setattr(escalation, "send_jsm_alert", mocks.send_jsm)
    monkeypatch.setattr(log_blobs, "upload_log_to_blob", mocks.upload)
    monkeypatch.setattr(
        storage, "get_queue_client", MagicMock(return_value=mocks.queue)
    )
    monkeypatch.setattr(
        storage, "get_table_client", MagicMock(return_value=mocks.table)
    )
    return mocks


def process(body):
    out = RecordingOut()
    status_update.process(body, out)
    return out


def routed_payload(env):
    return env.execute_actions.call_args.args[1]


# =========================
# Routing context
# =========================


def test_routing_context_falls_back_to_resource_info(env):
    process(make_body())

    ctx = env.route_alert.call_args.args[0]
    assert ctx == {
        "resourceId": "/sub/rg/res",
        "resourceGroup": "rg-1",
        "resourceName": "res-1",
        "schemaName": "My Rule",
        "severity": "Sev1",
        "namespace": "ns-1",
        "oncall": "true",
        "status": "failed",
        "execId": "exec-123",
        "name": "My Rule",
        "id": "schema-1",
        "team": "payments",
        "routing_info": {"team": "payments", "slack_channel": "#pay"},
    }


def test_routing_context_prefers_body_fields(env):
    process(
        make_body(
            resource_id="/body/res",
            resource_group="rg-body",
            resource_name="res-body",
            namespace="ns-body",
            team="Ops",
        )
    )

    ctx = env.route_alert.call_args.args[0]
    assert ctx["resourceId"] == "/body/res"
    assert ctx["resourceGroup"] == "rg-body"
    assert ctx["resourceName"] == "res-body"
    assert ctx["namespace"] == "ns-body"
    assert ctx["team"] == "ops"


def test_routing_context_uses_none_for_missing_resource_fields(env):
    process(make_body(resource_info={}, routing_info=None, oncall=None))

    ctx = env.route_alert.call_args.args[0]
    assert ctx["resourceId"] is None
    assert ctx["resourceGroup"] is None
    assert ctx["resourceName"] is None
    assert ctx["namespace"] is None
    assert ctx["oncall"] is False
    assert ctx["team"] == "default"
    assert ctx["routing_info"] == {}


def test_routing_info_json_string_is_parsed(env):
    process(make_body(routing_info=json.dumps({"team": "x"})))

    assert env.route_alert.call_args.args[0]["routing_info"] == {"team": "x"}


# =========================
# Notification payload
# =========================


@pytest.mark.parametrize(
    "status, label, emoji",
    [
        ("completed", "succeeded", "✅"),
        ("running", "running", "🏃"),
        ("skipped", "skipped", "⏭️"),
        ("routed", "routed", "🧭"),
        ("error", "error", "❌"),
        ("failed", "failed", "❌"),
        ("accepted", "accepted", "📩"),
        ("pending", "pending", "⏳"),
        ("weird", "weird", "ℹ️"),
    ],
)
def test_slack_message_reflects_status(env, status, label, emoji):
    process(make_body(status=status))

    slack = routed_payload(env)["slack"]
    assert slack["message"] == f"[exec-123] {emoji} {label.upper()}: My Rule"
    assert slack["blocks"][0]["text"]["text"] == f"{emoji}\tMy Rule\texecution"


def test_slack_blocks_content(env):
    process(make_body())

    blocks = routed_payload(env)["slack"]["blocks"]
    assert blocks[1]["text"]["text"] == (
        "Execution *My Rule* transitioned to: *FAILED*."
    )
    assert [f["text"] for f in blocks[2]["fields"]] == [
        "*SchemaId:* `schema-1`",
        "*Severity:* `Sev1`",
        "*Worker:* `worker-a`",
        "*Group:* `payments`",
        "*Team:* `payments`",
        "*Runbook:* `rb.sh`",
        "*Initiator:* `tester`",
        "*On Call:* `true`",
    ]
    assert blocks[3]["text"]["text"].startswith("*Resource Context:*\n")
    assert blocks[4]["text"]["text"] == "*Arguments:*\n```--x 1```"
    assert blocks[5]["text"]["text"] == "*Telemetry Output:*\n```line1\nboom```"
    url = blocks[6]["elements"][0]["url"]
    assert url.startswith("https://ui.example/executions?execId=exec-123&partitionKey=")
    assert blocks[7]["elements"][0]["text"].startswith(
        "ExecId: `exec-123` | Event: <!date^"
    )


def test_slack_blocks_defaults_and_truncation(env, monkeypatch):
    monkeypatch.setenv("NEXTJS_URL", "ui.example")
    body = make_body(
        worker="",
        group="",
        initiator="",
        oncall="",
        severity="",
        run_args="a" * 900,
        resource_info={"resource_name": "r" * 700},
        logs_b64=base64.b64encode(b"l" * 1200).decode(),
    )
    body["team"] = ""

    process(body)

    blocks = routed_payload(env)["slack"]["blocks"]
    assert [f["text"] for f in blocks[2]["fields"]][1:] == [
        "*Severity:* `-`",
        "*Worker:* `unknown`",
        "*Group:* `default`",
        "*Team:* `default`",
        "*Runbook:* `rb.sh`",
        "*Initiator:* `SYSTEM`",
        "*On Call:* `-`",
    ]
    assert blocks[3]["text"]["text"].endswith("...")
    assert blocks[4]["text"]["text"].endswith("\n... (truncated)```")
    assert blocks[5]["text"]["text"] == (
        "*Telemetry Output:*\n```" + "l" * 1000 + "\n... (truncated)```"
    )
    assert blocks[6]["elements"][0]["url"].startswith("https://ui.example/executions")


def test_slack_blocks_without_resource_context_or_output(env):
    process(make_body(resource_info={}, run_args="", logs_b64=""))

    blocks = routed_payload(env)["slack"]["blocks"]
    assert [b["text"]["text"].split("\n")[0] for b in blocks[3:5]] == [
        "*Arguments:*",
        "*Telemetry Output:*",
    ]
    assert blocks[3]["text"]["text"] == "*Arguments:*\n```None```"
    assert (
        blocks[4]["text"]["text"] == "*Telemetry Output:*\n```No telemetry available```"
    )


def test_jsm_payload(env):
    process(make_body())

    jsm = routed_payload(env)["jsm"]
    assert jsm["message"] == "[FAILED] [schema-1] [Sev1] My Rule"
    assert jsm["priority"] == "P2"
    assert jsm["alias"] == "schema-1"
    assert jsm["monitor_condition"] == "Fired"
    assert jsm["details"] == {
        "Name": "My Rule",
        "Id": "schema-1",
        "ExecId": "exec-123",
        "Status": "failed",
        "Runbook": "rb.sh",
        "Run_Args": "--x 1",
        "Worker": "worker-a",
        "Group": "payments",
        "Team": "payments",
        "OnCall": "true",
        "MonitorCondition": "Fired",
        "Severity": "Sev1",
        "Teams:": "payments, ops",
    }
    assert "line1" in jsm["description"]


def test_jsm_priority_defaults_to_p5_without_severity(env):
    process(make_body(severity=None))

    assert routed_payload(env)["jsm"]["priority"] == "P5"


def test_senders_delegate_to_escalation(env):
    process(make_body())

    kwargs = env.execute_actions.call_args.kwargs
    kwargs["send_slack_fn"]("t", "#c", message="m")
    kwargs["send_jsm_fn"]("k", message="m")
    env.send_slack.assert_called_once_with(token="t", channel="#c", message="m")
    env.send_jsm.assert_called_once_with(api_key="k", message="m")


# =========================
# Persistence of notification outcomes
# =========================


def test_notification_outcomes_are_written_back_to_the_log(env):
    out = process(make_body())

    first, second = out.entities()
    assert first.get("Notifications") is None
    assert second["RowKey"] == first["RowKey"]
    assert json.loads(second["Notifications"]) == {
        "reason": "rule",
        "rule": 1,
        "targets": [{"type": "slack", "status": "sent"}],
    }
    assert first["Log"] == "blobref://runbook-logs/x.log"
    assert first["team"] == "payments"


def test_no_notification_outcomes_means_single_write(env):
    env.execute_actions.return_value = None

    out = process(make_body())

    assert len(out.values) == 1


# =========================
# Edge cases
# =========================


def test_non_dict_body_is_dropped(env):
    out = process(["not", "a", "dict"])

    assert out.values == []
    env.route_alert.assert_not_called()


def test_undecodable_logs_are_treated_as_empty(env, monkeypatch):
    monkeypatch.setattr(
        log_blobs, "decode_base64", MagicMock(side_effect=RuntimeError("bad"))
    )

    out = process(make_body())

    assert out.entities()[0]["Log"] == ""
    env.upload.assert_not_called()


def test_body_is_normalized_in_place(env):
    body = make_body(team=None, status=["", " failed "])

    process(body)

    assert body["status"] == "failed"
    assert body["team"] == "payments"


def test_ai_triage_placeholder_and_payload(env, monkeypatch):
    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="true"))

    process(make_body())

    entity = env.table.upsert_entity.call_args.kwargs["entity"]
    assert entity["PartitionKey"] == "AiAnalysis"
    assert entity["RowKey"] == "exec-123"
    assert entity["team"] == "payments"
    assert entity["status"] == "processing"
    message = json.loads(env.queue.send_message.call_args.args[0])
    assert message["jsm_alias"] == "schema-1"
    assert message["routing_info"] == {"team": "payments"}
    assert message["logs"] == "line1\nboom"


def test_ai_triage_falls_back_to_env_when_settings_unavailable(env, monkeypatch):
    monkeypatch.setattr(
        smart_routing, "get_setting", MagicMock(side_effect=RuntimeError("down"))
    )
    monkeypatch.setenv("AI_AGENT_ENABLED", "true")

    process(make_body())

    env.queue.send_message.assert_called_once()


def test_ai_placeholder_skipped_without_exec_id(env):
    ai_triage.write_placeholder("", "payments")

    env.table.upsert_entity.assert_not_called()


def test_ai_placeholder_failure_is_swallowed(env):
    env.table.upsert_entity.side_effect = RuntimeError("table down")

    ai_triage.write_placeholder("exec-1", "payments")


def test_receiver_http_unexpected_error_returns_500(monkeypatch):
    monkeypatch.setattr(
        status_update, "process", MagicMock(side_effect=RuntimeError("x"))
    )
    req = func.HttpRequest(
        "POST", "https://x/api/receiver", body=json.dumps({"a": 1}).encode()
    )

    res = receiver.ReceiverHttp(req, RecordingOut())

    assert res.status_code == 500
    assert json.loads(res.get_body()) == {"error": "Internal error: RuntimeError"}
