# python
"""Characterization tests for the Trigger endpoint.

They pin the current behaviour of every branch (auth, schema lookup, routed
alarms, disabled runbooks, approval gate, worker dispatch, smart-routing on
dispatch errors) so the endpoint can be restructured safely. All I/O
(storage, Slack/JSM, routing config) is mocked.
"""

import builtins
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import azure.functions as func
import detection
import escalation
import pytest
import smart_routing
import worker_routing
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from routes import trigger as trigger_routes

from core import audit, notify, security, storage

OPERATOR = {"username": "alice", "role": "OPERATOR", "team": "default"}


class FakeOut(func.Out):
    """Table output binding stub that records every write."""

    def __init__(self):
        self.values = []

    def set(self, v):
        self.values.append(v)

    def get(self):
        return self.values[-1] if self.values else None

    def entities(self):
        return [json.loads(v) for v in self.values]


def make_request(method="POST", params=None, headers=None, route_params=None):
    return func.HttpRequest(
        method=method,
        url="https://x/api/Trigger",
        params=params or {},
        headers=headers or {},
        route_params=route_params or {},
        body=b"",
    )


def schema_row(**overrides) -> dict:
    row = {
        "Id": "schema-1",
        "name": "My Rule",
        "description": "Restart the thing",
        "runbook": "rb.sh",
        "run_args": "--x 1",
        "worker": "pool-a",
        "group": "payments",
        "oncall": "false",
        "require_approval": "false",
        "aks_login_required": "true",
        "aks_cluster_name": "aks-schema",
        "aks_resource_group": "rg-schema",
        "aks_namespace": "ns-schema",
        "team": "default",
    }
    row.update(overrides)
    return row


def alert_fields(**overrides) -> dict:
    fields = {
        "_raw": {"raw": True},
        "resource_name": "res-1",
        "resource_group": "rg-1",
        "resource_id": "/subscriptions/s/resourceGroups/rg-1/res-1",
        "monitorCondition": "Fired",
        "severity": "Sev2",
        "schema_id": "schema-1",
        "namespace": "ns-1",
        "pod": "pod-1",
        "deployment": None,
        "job": None,
        "horizontalpodautoscaler": None,
        "payload": {"k": "v"},
    }
    fields.update(overrides)
    return fields


@pytest.fixture
def env(monkeypatch):
    """Mock every collaborator of Trigger and expose the mocks."""
    monkeypatch.setattr(trigger_routes, "_NOTIFICATION_QUEUE_READY", False)
    monkeypatch.setenv("NEXTJS_URL", "https://ui.example")
    monkeypatch.setenv("ORCHESTRATOR_BASE_URL", "https://orch.example/")
    monkeypatch.delenv("SLACK_CHANNEL", raising=False)

    queue = MagicMock()
    mocks = SimpleNamespace(
        queue=queue,
        get_queue_client=MagicMock(return_value=queue),
        enqueue=MagicMock(),
        log_audit=MagicMock(),
        auth=MagicMock(return_value=(dict(OPERATOR), None)),
        settings=MagicMock(return_value=("tok-default", "#default")),
        parse_fields=MagicMock(return_value=alert_fields()),
        worker_routing=MagicMock(return_value="worker-q"),
        route_alert=MagicMock(
            return_value=SimpleNamespace(
                actions=[SimpleNamespace(team="ops"), SimpleNamespace(team=None)],
                reason="rule",
                matched_rule_index=3,
            )
        ),
        execute_actions=MagicMock(return_value=[{"type": "slack", "status": "sent"}]),
        resolve_slack_token=MagicMock(return_value=None),
        resolve_jsm_apikey=MagicMock(return_value=None),
        send_slack=MagicMock(return_value=True),
        send_jsm=MagicMock(return_value=True),
    )
    monkeypatch.setattr(storage, "get_queue_client", mocks.get_queue_client)
    monkeypatch.setattr(storage, "enqueue_queue_payload", mocks.enqueue)
    monkeypatch.setattr(audit, "log_audit", mocks.log_audit)
    monkeypatch.setattr(security, "get_authenticated_user", mocks.auth)
    monkeypatch.setattr(notify, "get_default_notification_settings", mocks.settings)
    monkeypatch.setattr(detection, "parse_resource_fields", mocks.parse_fields)
    monkeypatch.setattr(worker_routing, "worker_routing", mocks.worker_routing)
    monkeypatch.setattr(smart_routing, "route_alert", mocks.route_alert)
    monkeypatch.setattr(smart_routing, "execute_actions", mocks.execute_actions)
    monkeypatch.setattr(smart_routing, "resolve_slack_token", mocks.resolve_slack_token)
    monkeypatch.setattr(smart_routing, "resolve_jsm_apikey", mocks.resolve_jsm_apikey)
    monkeypatch.setattr(escalation, "send_slack_execution", mocks.send_slack)
    monkeypatch.setattr(escalation, "send_jsm_alert", mocks.send_jsm)
    return mocks


def call(req=None, rows=None, workers="[]"):
    out = FakeOut()
    entities = json.dumps([schema_row()] if rows is None else rows)
    res = trigger_routes.Trigger(req or make_request(), out, entities, workers)
    return res, out


def body_of(res):
    return json.loads(res.get_body())


# =========================
# Preconditions
# =========================


def test_queue_client_failure_returns_500(env):
    env.get_queue_client.side_effect = RuntimeError("no storage")

    res, out = call()

    assert res.status_code == 500
    assert res.get_body() == b"Failed to initialize queue client"
    env.auth.assert_not_called()
    assert out.values == []


def test_notification_queue_is_created_only_once(env):
    call()
    call()

    env.queue.create_queue.assert_called_once()


def test_existing_notification_queue_marks_queue_ready(env):
    env.queue.create_queue.side_effect = ResourceExistsError("exists")

    call()
    call()

    env.queue.create_queue.assert_called_once()


def test_queue_creation_failure_is_retried_on_next_call(env):
    env.queue.create_queue.side_effect = RuntimeError("transient")

    res, _ = call()
    call()

    assert res.status_code == 202
    assert env.queue.create_queue.call_count == 2


def test_options_returns_cors_preflight_without_auth(env):
    res, _ = call(make_request(method="OPTIONS"))

    assert res.status_code == 200
    assert res.headers["Access-Control-Allow-Origin"] == "*"
    env.auth.assert_not_called()


def test_unauthenticated_request_returns_auth_error(env):
    denied = func.HttpResponse("nope", status_code=401)
    env.auth.return_value = (None, denied)

    res, out = call()

    assert res is denied
    assert out.values == []


def test_viewer_cannot_trigger(env):
    env.auth.return_value = ({"username": "v", "role": "VIEWER"}, None)

    res, out = call()

    assert res.status_code == 403
    assert "Viewer cannot trigger" in body_of(res)["error"]
    env.settings.assert_not_called()
    assert out.values == []


def test_unexpected_binding_format_returns_500(env):
    out = FakeOut()

    res = trigger_routes.Trigger(make_request(), out, "not-json", "[]")

    assert res.status_code == 500
    assert body_of(res) == {"error": "Unexpected table result format"}


# =========================
# Schema resolution
# =========================


def test_schema_of_another_team_is_forbidden(env):
    env.auth.return_value = (
        {"username": "bob", "role": "OPERATOR", "team": "ops"},
        None,
    )

    res, out = call(rows=[schema_row(team="payments")])

    assert res.status_code == 403
    assert body_of(res) == {"error": "Schema belongs to another team"}
    env.enqueue.assert_not_called()


def test_admin_including_all_teams_can_trigger_other_team_schema(env):
    env.auth.return_value = ({"username": "root", "role": "ADMIN", "team": "ops"}, None)

    res, _ = call(
        make_request(params={"includeAllTeams": "true"}),
        rows=[schema_row(team="payments")],
    )

    assert res.status_code == 202


def test_query_id_overrides_payload_schema_id(env):
    rows = [schema_row(), schema_row(Id="schema-2", name="Other")]

    res, _ = call(make_request(params={"id": "schema-2"}), rows=rows)

    assert body_of(res)["schema"]["id"] == "schema-2"


def test_unknown_schema_with_alert_is_routed(env):
    res, out = call(rows=[schema_row(Id="other")])

    assert res.status_code == 200
    assert "ROUTED" in body_of(res)["routed"]
    assert out.values == []
    env.enqueue.assert_not_called()

    message = json.loads(env.queue.send_message.call_args.args[0])
    assert message["status"] == "routed"
    assert message["id"] == "schema-1"
    assert message["name"] == "res-1"
    assert message["runbook"] == "alarm routed"
    assert message["worker"] == "No worker"
    assert message["oncall"] is True
    assert message["initiator"] == "alice"
    assert message["monitor_condition"] == "Fired"
    assert message["severity"] == "Sev2"
    assert message["resource_info"]["resource_rg"] == "rg-1"
    assert message["routing_info"]["slack_channel"] == "#default"


def test_unknown_schema_without_alert_is_ignored(env):
    env.parse_fields.return_value = alert_fields(monitorCondition="", severity="")

    res, out = call(rows=[schema_row(Id="other")])

    assert res.status_code == 204
    assert "No alert detected" in body_of(res)["ignored"]
    env.queue.send_message.assert_not_called()
    assert out.values == []


def test_disabled_schema_is_routed(env):
    res, out = call(rows=[schema_row(enabled="false")])

    assert res.status_code == 200
    assert "schema-1 is disabled" in body_of(res)["routed"]
    message = json.loads(env.queue.send_message.call_args.args[0])
    assert message["status"] == "routed"
    assert message["runbook"] == "rb.sh"
    assert message["worker"] == "pool-a"
    assert "group" not in message  # post_status does not forward the group
    assert message["oncall"] is False
    env.enqueue.assert_not_called()
    assert out.values == []


# =========================
# Routing info
# =========================


def test_routing_info_prefers_request_overrides(env):
    req = make_request(
        params={
            "slack_token": "tok-q",
            "slack_channel": "#q",
            "jsm_api_key": "jsm-q",
        },
        route_params={"team": "payments"},
    )

    call(req)

    queue_payload = env.enqueue.call_args.args[1]
    assert queue_payload["routing_info"] == {
        "team": "payments",
        "slack_token": "tok-q",
        "slack_channel": "#q",
        "jsm_token": "jsm-q",
    }
    assert queue_payload["resource_info"]["team"] == "payments"


def test_routing_info_falls_back_to_team_then_default_settings(env):
    env.resolve_slack_token.return_value = "tok-team"
    env.resolve_jsm_apikey.return_value = "jsm-team"

    call()

    routing = env.enqueue.call_args.args[1]["routing_info"]
    assert routing == {
        "team": "default",
        "slack_token": "tok-team",
        "slack_channel": "#default",
        "jsm_token": "jsm-team",
    }
    env.resolve_slack_token.assert_called_with("default")


# =========================
# Worker dispatch
# =========================


def test_dispatch_enqueues_payload_and_logs_accepted(env):
    res, out = call(make_request(params={"id": "schema-1"}))

    assert res.status_code == 202
    body = body_of(res)
    assert body["status"] == 202
    assert body["schema"]["id"] == "schema-1"
    assert body["schema"]["team"] == "default"
    assert body["response"] == {"status": "accepted", "queue": "worker-q"}
    assert set(body["log"]) == {"partitionKey", "exec_id"}

    queue_name, payload = env.enqueue.call_args.args
    assert queue_name == "worker-q"
    assert payload["exec_id"] == body["log"]["exec_id"]
    assert payload["runbook"] == "rb.sh"
    assert payload["run_args"] == "--x 1"
    assert payload["aks_login_required"] is True
    assert payload["aks_cluster_name"] == "aks-schema"
    assert payload["aks_resource_group"] == "rg-schema"
    assert payload["aks_namespace"] == "ns-schema"
    assert payload["initiator"] == "alice"
    assert payload["worker"] == "pool-a"
    assert payload["resource_info"]["aks_pod"] == "pod-1"

    (log,) = out.entities()
    assert log["Status"] == "accepted"
    assert log["RowKey"] == log["ExecId"] == body["log"]["exec_id"]
    assert log["PartitionKey"] == body["log"]["partitionKey"]
    assert json.loads(log["ResourceInfo"])["resource_name"] == "res-1"
    assert log["Initiator"] == "alice"

    env.log_audit.assert_called_once()
    assert env.log_audit.call_args.kwargs["action"] == "RUNBOOK_EXECUTE"
    env.route_alert.assert_not_called()
    env.execute_actions.assert_not_called()


def test_no_available_worker_logs_error_and_runs_smart_routing(env):
    env.worker_routing.return_value = None

    res, out = call()

    assert res.status_code == 500
    error = body_of(res)["response"]["error"]
    assert "No workers (pool-a) available" in error
    env.enqueue.assert_not_called()
    env.log_audit.assert_not_called()

    ctx = env.route_alert.call_args.args[0]
    assert ctx["status"] == "error"
    assert ctx["resourceName"] == "res-1"
    assert ctx["namespace"] == "ns-1"
    assert ctx["team"] == "default"

    decision, payload = env.execute_actions.call_args.args[:2]
    assert decision is env.route_alert.return_value
    assert payload["jsm"]["priority"] == "P3"
    assert payload["jsm"]["alias"] == "schema-1"
    assert payload["jsm"]["details"]["Teams:"] == "ops"
    assert payload["slack"]["message"].endswith("Status: error: My Rule")

    first, second = out.entities()
    assert first["Status"] == second["Status"] == "error"
    assert first.get("Notifications") is None
    assert json.loads(second["Notifications"]) == {
        "reason": "rule",
        "rule": 3,
        "targets": [{"type": "slack", "status": "sent"}],
    }


def test_smart_routing_senders_delegate_to_escalation(env):
    env.worker_routing.return_value = None

    call()

    kwargs = env.execute_actions.call_args.kwargs
    kwargs["send_slack_fn"]("t", "#c", message="m")
    kwargs["send_jsm_fn"]("k", message="m")
    env.send_slack.assert_called_once_with(token="t", channel="#c", message="m")
    env.send_jsm.assert_called_once_with(api_key="k", message="m")


def test_smart_routing_failure_keeps_error_response(env):
    env.worker_routing.return_value = None
    env.execute_actions.side_effect = RuntimeError("slack down")

    res, out = call()

    assert res.status_code == 500
    assert len(out.values) == 1


def test_enqueue_failure_logs_error(env):
    env.enqueue.side_effect = RuntimeError("queue full")

    res, out = call()

    assert res.status_code == 500
    assert body_of(res)["response"] == {"error": "queue full"}
    assert out.entities()[0]["Status"] == "error"
    env.route_alert.assert_called_once()


def test_unexpected_error_writes_error_log(env):
    env.worker_routing.side_effect = RuntimeError("boom")

    res, out = call()

    assert res.status_code == 500
    body = body_of(res)
    assert body["response"] == {"error": "boom"}
    assert body["schema"]["id"] == "schema-1"
    (log,) = out.entities()
    assert log["Status"] == "error"
    assert log["Log"] == "boom"
    assert log["Initiator"] is None
    assert log["ResourceInfo"] is None


def test_missing_smart_routing_module_skips_routing(env, monkeypatch):
    env.worker_routing.return_value = None
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "smart_routing":
            raise ImportError("no smart_routing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    res, out = call()

    assert res.status_code == 500
    env.execute_actions.assert_not_called()
    assert len(out.values) == 1


# =========================
# Approval gate
# =========================


def _approval_rows(**overrides):
    return [schema_row(require_approval="true", **overrides)]


def test_approval_required_creates_pending_execution(env):
    env.resolve_jsm_apikey.return_value = "jsm-team"

    res, out = call(rows=_approval_rows(oncall="true"))

    assert res.status_code == 202
    body = body_of(res)
    assert body["message"] == "Job is pending approval"
    assert body["notification_status"] == "ok"
    assert body["notification_warnings"] == []
    exec_id = body["exec_id"]

    approve = urlsplit(body["approve"])
    assert approve.scheme == "https" and approve.netloc == "orch.example"
    assert approve.path.endswith(f"/{exec_id}/approve")
    assert body["reject"].split("?")[0].endswith(f"/{exec_id}/reject")
    query = parse_qs(approve.query)
    ok, signed = security.verify_signed_payload(exec_id, query["p"][0], query["s"][0])
    assert ok
    assert signed["schemaId"] == "schema-1"
    assert signed["initiator"] == "alice"
    assert signed["exp"] == body["expires_at (UTC)"]
    assert signed["routing_info"]["jsm_token"] == "jsm-team"

    env.enqueue.assert_not_called()
    assert env.log_audit.call_args.kwargs["action"] == "RUNBOOK_GATE_SCHEDULE"

    slack_kwargs = env.send_slack.call_args.kwargs
    assert slack_kwargs["token"] == "tok-default"
    assert slack_kwargs["channel"] == "#default"
    assert "APPROVAL REQUIRED: My Rule" in slack_kwargs["message"]
    assert slack_kwargs["blocks"][-2]["elements"][0]["url"] == (
        f"https://ui.example/executions?execId={exec_id}"
        f"&partitionKey={approve.path.split('/')[-3]}"
    )

    jsm_kwargs = env.send_jsm.call_args.kwargs
    assert jsm_kwargs["api_key"] == "jsm-team"
    assert jsm_kwargs["priority"] == "P2"
    assert jsm_kwargs["alias"] == exec_id
    assert jsm_kwargs["tags"] == ["approval-required", "payments"]

    (log,) = out.entities()
    assert log["Status"] == "pending"
    assert log["ApprovalRequired"] is True
    assert log["ApprovalExpiresAt"] == body["expires_at (UTC)"]
    assert json.loads(log["Log"])["approve"] == body["approve"]
    assert json.loads(log["Notifications"]) == {
        "reason": "approval_request",
        "rule": None,
        "targets": [
            {
                "type": "slack",
                "team": "default",
                "channel": "#default",
                "status": "sent",
            },
            {"type": "jsm", "team": "default", "status": "sent"},
        ],
    }


def test_approval_without_slack_config_and_not_oncall_warns(env):
    env.settings.return_value = ("", "")

    res, out = call(rows=_approval_rows())

    body = body_of(res)
    assert body["notification_status"] == "warning"
    assert body["notification_warnings"] == [
        "Slack approval notify skipped: missing token or channel"
    ]
    env.send_slack.assert_not_called()
    env.send_jsm.assert_not_called()
    log_payload = json.loads(out.entities()[0]["Log"])
    assert log_payload["notification_status"] == "warning"
    assert out.entities()[0]["Notifications"] is None


def test_approval_oncall_without_jsm_token_warns(env):
    res, _ = call(rows=_approval_rows(oncall="true"))

    assert body_of(res)["notification_warnings"] == [
        "JSM approval notify skipped: missing token"
    ]


def test_approval_notification_failures_are_reported(env):
    env.resolve_jsm_apikey.return_value = "jsm-team"
    env.send_slack.side_effect = RuntimeError("slack down")
    env.send_jsm.return_value = False

    res, out = call(rows=_approval_rows(oncall="true"))

    assert body_of(res)["notification_warnings"] == [
        "Slack approval notify failed: slack down"
    ]
    targets = json.loads(out.entities()[0]["Notifications"])["targets"]
    assert targets == [
        {
            "type": "slack",
            "team": "default",
            "channel": "#default",
            "status": "failed",
            "error": "slack down",
        },
        {"type": "jsm", "team": "default", "status": "failed"},
    ]


def test_approval_uses_schema_team_notification_targets(env, monkeypatch):
    monkeypatch.setattr(
        smart_routing,
        "load_routing_config",
        lambda: {"teams": {"ops": {"slack": {"channel": "#ops"}}}},
    )
    env.resolve_slack_token.side_effect = lambda team: f"tok-{team}"
    env.auth.return_value = ({"username": "o", "role": "OPERATOR", "team": "ops"}, None)

    res, out = call(rows=_approval_rows(team="ops"))

    assert res.status_code == 202
    assert env.send_slack.call_args.kwargs["token"] == "tok-ops"
    assert env.send_slack.call_args.kwargs["channel"] == "#ops"
    targets = json.loads(out.entities()[0]["Notifications"])["targets"]
    assert targets[0]["team"] == "ops"


def test_approval_links_fall_back_to_website_hostname(env, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_BASE_URL")
    monkeypatch.setenv("WEBSITE_HOSTNAME", "localhost:7071")
    monkeypatch.setenv("NEXTJS_URL", "ui.example")

    res, _ = call(rows=_approval_rows())

    assert body_of(res)["approve"].startswith("http://localhost:7071/api/approvals/")
    actions = env.send_slack.call_args.kwargs["blocks"][-2]
    assert actions["elements"][0]["url"].startswith("https://ui.example/executions")


def test_approval_slack_message_truncates_long_fields(env):
    rows = _approval_rows(description="d" * 500, run_args="a" * 900)
    env.parse_fields.return_value = alert_fields(resource_name="r" * 700)

    call(rows=rows)

    blocks = env.send_slack.call_args.kwargs["blocks"]
    assert blocks[1]["text"]["text"].endswith("d" * 400 + "...")
    assert blocks[3]["text"]["text"].endswith("...")
    assert blocks[4]["text"]["text"].endswith("\n... (truncated)```")


def test_approval_jsm_exception_is_reported(env):
    env.resolve_jsm_apikey.return_value = "jsm-team"
    env.send_jsm.side_effect = RuntimeError("jsm down")

    res, out = call(rows=_approval_rows(oncall="true"))

    assert body_of(res)["notification_warnings"] == [
        "JSM approval notify failed: jsm down"
    ]
    targets = json.loads(out.entities()[0]["Notifications"])["targets"]
    assert targets[1] == {
        "type": "jsm",
        "team": "default",
        "status": "failed",
        "error": "jsm down",
    }


def test_approval_team_targets_fall_back_when_routing_config_fails(env, monkeypatch):
    def broken_config():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(smart_routing, "load_routing_config", broken_config)
    env.auth.return_value = ({"username": "o", "role": "OPERATOR", "team": "ops"}, None)

    call(rows=_approval_rows(team="ops"))

    assert env.send_slack.call_args.kwargs["token"] == "tok-default"
    assert env.send_slack.call_args.kwargs["channel"] == "#default"


def test_routed_alarm_tolerates_missing_notification_queue(env):
    env.queue.send_message.side_effect = ResourceNotFoundError("gone")

    res, _ = call(rows=[schema_row(Id="other")])

    assert res.status_code == 200
