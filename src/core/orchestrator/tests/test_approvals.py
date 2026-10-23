# python
"""Characterization tests for the approval gate endpoints (approve / reject).

They pin the current behaviour of both endpoints (auth, signed link checks,
"already decided" guard, schema and team checks, worker dispatch on approve,
notifications) so the module can be restructured safely. Links are signed
for real with security.sign_payload_b64; all I/O is mocked.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import azure.functions as func
import pytest
import smart_routing
import worker_routing
from routes import approvals

from core import audit, notify, security, storage

OPERATOR = {"username": "alice", "role": "OPERATOR", "team": "default"}
EXEC_ID = "exec-1"


def signed_query(**overrides) -> dict:
    payload = {
        "execId": EXEC_ID,
        "schemaId": "schema-1",
        "exp": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "initiator": "requester",
        "resource_info": {
            "resource_id": "/sub/rg/res",
            "resource_rg": "rg-1",
            "resource_name": "res-1",
            "aks_namespace": "ns-1",
        },
        "routing_info": {"team": "default", "slack_channel": "#c"},
        "monitorCondition": "Fired",
        "severity": "Sev2",
        "team": "default",
    }
    payload.update(overrides)
    p = security.b64url_encode(json.dumps(payload).encode("utf-8"))
    return {"p": p, "s": security.sign_payload_b64(p)}


def make_request(method="GET", params=None, headers=None, exec_id=EXEC_ID):
    return func.HttpRequest(
        method=method,
        url=f"https://x/api/approvals/20260101/{exec_id}/approve",
        params=signed_query() if params is None else params,
        headers=headers or {},
        route_params={"partitionKey": "20260101", "execId": exec_id},
        body=b"",
    )


def schema_row(**overrides) -> dict:
    row = {
        "Id": "schema-1",
        "name": "My Rule",
        "runbook": "rb.sh",
        "run_args": "--x 1",
        "worker": "pool-a",
        "group": "payments",
        "oncall": "true",
        "require_approval": "true",
        "aks_login_required": "false",
        "team": "default",
    }
    row.update(overrides)
    return row


PENDING = [{"ExecId": EXEC_ID, "Status": "pending"}]


class RecordingOut(func.Out):
    def __init__(self):
        self.values = []

    def set(self, v):
        self.values.append(v)

    def get(self):
        return self.values[-1] if self.values else None

    def entities(self):
        return [json.loads(v) for v in self.values]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("LOCAL_DEV", raising=False)
    mocks = SimpleNamespace(
        auth=MagicMock(return_value=(dict(OPERATOR), None)),
        enqueue=MagicMock(),
        log_audit=MagicMock(),
        notify_decision=MagicMock(),
        worker_routing=MagicMock(return_value="worker-q"),
        decision=SimpleNamespace(actions=[]),
        execute_actions=MagicMock(),
    )
    mocks.route_alert = MagicMock(return_value=mocks.decision)
    monkeypatch.setattr(security, "get_authenticated_user", mocks.auth)
    monkeypatch.setattr(storage, "enqueue_queue_payload", mocks.enqueue)
    monkeypatch.setattr(audit, "log_audit", mocks.log_audit)
    monkeypatch.setattr(notify, "notify_slack_decision", mocks.notify_decision)
    monkeypatch.setattr(worker_routing, "worker_routing", mocks.worker_routing)
    monkeypatch.setattr(smart_routing, "route_alert", mocks.route_alert)
    monkeypatch.setattr(smart_routing, "execute_actions", mocks.execute_actions)
    return mocks


def approve(req=None, schemas=None, today_logs=None, workers="[]"):
    out = RecordingOut()
    res = approvals.approve(
        req or make_request(),
        out,
        json.dumps([schema_row()]) if schemas is None else schemas,
        json.dumps(PENDING) if today_logs is None else today_logs,
        workers,
    )
    return res, out


def reject(req=None, schemas=None, today_logs=None):
    out = RecordingOut()
    res = approvals.reject(
        req or make_request(),
        out,
        json.dumps([schema_row()]) if schemas is None else schemas,
        json.dumps(PENDING) if today_logs is None else today_logs,
    )
    return res, out


def body_of(res):
    return json.loads(res.get_body())


def hide_smart_routing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "smart_routing":
            raise ImportError("no smart_routing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


ENDPOINTS = pytest.mark.parametrize(
    "call", [approve, reject], ids=["approve", "reject"]
)


# =========================
# Shared preconditions
# =========================


@ENDPOINTS
def test_options_returns_cors_preflight(env, call):
    res, _ = call(make_request(method="OPTIONS"))

    assert res.status_code == 200
    env.auth.assert_not_called()


@ENDPOINTS
def test_unauthenticated_request_is_rejected(env, call):
    denied = func.HttpResponse("nope", status_code=401)
    env.auth.return_value = (None, denied)

    res, out = call()

    assert res is denied
    assert out.values == []


@pytest.mark.parametrize(
    "call, verb", [(approve, "approve"), (reject, "reject")], ids=["approve", "reject"]
)
def test_viewer_cannot_decide(env, call, verb):
    env.auth.return_value = ({"username": "v", "role": "VIEWER"}, None)

    res, _ = call()

    assert res.status_code == 403
    assert body_of(res) == {"error": f"Unauthorized: Viewer cannot {verb} executions"}
    assert res.headers["Access-Control-Allow-Origin"] == "*"


@ENDPOINTS
def test_missing_exec_id_returns_400(env, call):
    res, _ = call(make_request(exec_id=""))

    assert res.status_code == 400
    assert body_of(res) == {"error": "Missing execId in route"}


@ENDPOINTS
def test_invalid_signature_returns_401(env, call):
    query = signed_query()
    query["s"] = "0" * 64

    res, out = call(make_request(params=query))

    assert res.status_code == 401
    assert body_of(res) == {"error": "Invalid or expired payload"}
    assert out.values == []


@ENDPOINTS
def test_expired_link_returns_401(env, call):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    res, _ = call(make_request(params=signed_query(exp=expired)))

    assert res.status_code == 401


@ENDPOINTS
def test_already_decided_execution_returns_409(env, call):
    decided = PENDING + [{"ExecId": EXEC_ID, "Status": "accepted"}]

    res, out = call(today_logs=json.dumps(decided))

    assert res.status_code == 409
    assert body_of(res) == {"message": "Already decided or executed for this ExecId"}
    assert out.values == []


def test_reject_checks_already_decided_before_the_signature(env):
    decided = PENDING + [{"ExecId": EXEC_ID, "Status": "rejected"}]

    res, _ = reject(make_request(params={}), today_logs=json.dumps(decided))

    assert res.status_code == 409
    assert res.headers["Access-Control-Allow-Origin"] == "*"


def test_approve_checks_the_signature_before_already_decided(env):
    decided = PENDING + [{"ExecId": EXEC_ID, "Status": "rejected"}]

    res, _ = approve(make_request(params={}), today_logs=json.dumps(decided))

    assert res.status_code == 401


@ENDPOINTS
def test_unavailable_schemas_return_500(env, call):
    res, _ = call(schemas="not-json")

    assert res.status_code == 500
    assert body_of(res) == {"error": "Schemas not available"}


@ENDPOINTS
def test_unknown_schema_returns_404(env, call):
    res, _ = call(schemas=json.dumps([schema_row(Id="other")]))

    assert res.status_code == 404
    assert body_of(res) == {"error": "Schema not found"}


@ENDPOINTS
def test_operator_of_another_team_is_forbidden(env, call):
    env.auth.return_value = ({"username": "o", "role": "OPERATOR", "team": "ops"}, None)

    res, out = call(make_request(params=signed_query(team="payments")))

    assert res.status_code == 403
    assert body_of(res) == {"error": "Approval belongs to another team"}
    assert out.values == []


@ENDPOINTS
def test_admin_can_decide_for_any_team(env, call):
    env.auth.return_value = ({"username": "root", "role": "ADMIN", "team": "ops"}, None)

    res, _ = call(make_request(params=signed_query(team="payments")))

    assert res.status_code == 200


@ENDPOINTS
def test_operator_can_decide_default_team_resources(env, call):
    env.auth.return_value = ({"username": "o", "role": "OPERATOR", "team": "ops"}, None)

    res, _ = call()

    assert res.status_code == 200


@ENDPOINTS
def test_local_dev_prefers_session_user_over_headers(env, call, monkeypatch):
    monkeypatch.setenv("LOCAL_DEV", "true")

    call(make_request(headers={"x-cloudo-user": "header-user"}))

    assert env.log_audit.call_args.kwargs["user"] == "alice"


# =========================
# Approve
# =========================


def test_approve_dispatches_to_worker_and_logs(env):
    res, out = approve()

    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/json"
    body = body_of(res)
    assert body["message"] == "Approved and executed by alice"
    assert body["response"]["status"] == "accepted"
    assert body["response"]["queue"] == "worker-q"
    assert body["resource_info"]["resource_rg"] == "rg-1"

    queue_name, payload = env.enqueue.call_args.args
    assert queue_name == "worker-q"
    assert body["response"]["payload"] == payload
    assert payload == {
        "runbook": "rb.sh",
        "run_args": "--x 1",
        "id": "schema-1",
        "name": "My Rule",
        "requestedAt": payload["requestedAt"],
        "exec_id": EXEC_ID,
        "oncall": True,
        "aks_login_required": False,
        "aks_cluster_name": None,
        "aks_resource_group": None,
        "aks_namespace": None,
        "monitor_condition": "Fired",
        "severity": "Sev2",
        "team": "default",
        "worker": "pool-a",
        "group": "payments",
        "initiator": "requester",
        "resource_info": body["resource_info"],
        "routing_info": {"team": "default", "slack_channel": "#c"},
    }

    (log,) = out.entities()
    assert log["Status"] == "accepted"
    assert log["ExecId"] == EXEC_ID
    assert log["Initiator"] == "requester"
    assert log["ApprovalRequired"] is True
    assert log["ApprovalDecisionBy"] == "alice"
    assert json.loads(log["Log"])["message"] == "Approved and executed by alice"
    assert json.loads(log["ResourceInfo"])["resource_name"] == "res-1"

    assert env.log_audit.call_args.kwargs == {
        "user": "alice",
        "action": "RUNBOOK_APPROVE",
        "target": EXEC_ID,
        "details": "Runbook: rb.sh, Schema: schema-1, Approver: alice",
    }


def test_approve_routing_and_notifications(env):
    _, out = approve()

    ctx = env.route_alert.call_args.args[0]
    assert ctx == {
        "resourceId": "/sub/rg/res",
        "resourceGroup": "",
        "resourceName": "res-1",
        "schemaName": "My Rule",
        "severity": "Sev2",
        "namespace": "",
        "oncall": True,
        "status": "accepted",
        "execId": EXEC_ID,
        "name": "My Rule",
        "id": "schema-1",
        "team": "default",
        "routing_info": {"team": "default", "slack_channel": "#c"},
    }
    assert env.notify_decision.call_args.kwargs == {
        "exec_id": EXEC_ID,
        "schema_id": "schema-1",
        "decision": "approved",
        "approver": "alice",
        "routing_info": {"team": "default", "slack_channel": "#c"},
        "team": "default",
        "partition_key": out.entities()[0]["PartitionKey"],
    }
    env.execute_actions.assert_called_once_with(
        env.decision, {"slack": None}, send_slack_fn=None
    )


def test_approve_without_available_worker_reports_error_in_200(env):
    env.worker_routing.return_value = None

    res, out = approve()

    assert res.status_code == 200
    assert "No workers (pool-a) available" in body_of(res)["response"]["error"]
    assert out.entities()[0]["Status"] == "error"
    assert env.route_alert.call_args.args[0]["status"] == "error"
    env.enqueue.assert_not_called()


def test_approve_enqueue_failure_reports_error_in_200(env):
    env.enqueue.side_effect = RuntimeError("queue full")

    res, out = approve()

    assert res.status_code == 200
    assert body_of(res)["response"] == {"error": "queue full"}
    assert out.entities()[0]["Status"] == "error"


def test_approve_smart_routing_failure_still_succeeds(env):
    env.execute_actions.side_effect = RuntimeError("jsm down")

    res, _ = approve()

    assert res.status_code == 200


def test_approve_without_smart_routing_skips_decision_notification(env, monkeypatch):
    hide_smart_routing(monkeypatch)

    res, _ = approve()

    assert res.status_code == 200
    env.notify_decision.assert_not_called()
    env.execute_actions.assert_not_called()


def test_approve_unexpected_error_logs_and_notifies(env):
    env.worker_routing.side_effect = RuntimeError("boom")

    res, out = approve()

    assert res.status_code == 500
    assert body_of(res) == {"error": "boom"}
    (log,) = out.entities()
    assert log["Status"] == "error"
    assert log["Log"] == "Approve failed: boom"
    assert log["ApprovalDecisionBy"] == "alice"
    assert log["MonitorCondition"] is None
    assert log["Initiator"] is None
    args = env.notify_decision.call_args
    assert args.args == (EXEC_ID, "schema-1", f"approved {EXEC_ID} by alice", "alice")
    assert args.kwargs == {
        "extra": "*Error:* boom",
        "routing_info": {"team": "default", "slack_channel": "#c"},
        "team": "default",
        "partition_key": log["PartitionKey"],
    }


# =========================
# Reject
# =========================


def test_reject_logs_audits_and_notifies(env):
    res, out = reject()

    assert res.status_code == 200
    assert body_of(res) == {"message": "Rejected by approver: alice"}
    assert res.headers["Content-Type"] == "application/json"
    env.enqueue.assert_not_called()

    (log,) = out.entities()
    assert log["Status"] == "rejected"
    assert json.loads(log["Log"]) == {"message": "Rejected by approver: alice"}
    assert log["Initiator"] == "requester"
    assert log["ApprovalDecisionBy"] == "alice"
    assert log["MonitorCondition"] == "Fired"

    assert env.log_audit.call_args.kwargs == {
        "user": "alice",
        "action": "RUNBOOK_REJECT",
        "target": EXEC_ID,
        "details": "Runbook: rb.sh, Schema: schema-1",
    }
    assert env.notify_decision.call_args.kwargs == {
        "exec_id": EXEC_ID,
        "schema_id": "schema-1",
        "decision": "rejected",
        "approver": "alice",
        "routing_info": {"team": "default", "slack_channel": "#c"},
        "team": "default",
        "partition_key": log["PartitionKey"],
    }
    ctx = env.route_alert.call_args.args[0]
    assert ctx["status"] == "rejected"
    assert ctx["resourceGroup"] == ""
    assert ctx["namespace"] == ""
    env.execute_actions.assert_called_once_with(
        env.decision, {"slack": None}, send_slack_fn=None
    )


def test_reject_smart_routing_failure_still_succeeds(env):
    env.execute_actions.side_effect = RuntimeError("jsm down")

    res, _ = reject()

    assert res.status_code == 200


def test_reject_without_smart_routing_still_notifies_decision(env, monkeypatch):
    hide_smart_routing(monkeypatch)

    res, _ = reject()

    assert res.status_code == 200
    env.notify_decision.assert_called_once()
    env.route_alert.assert_not_called()
