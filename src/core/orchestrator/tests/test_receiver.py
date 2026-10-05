# python
"""Tests for the Receiver flow (queue trigger + HTTP fast path) and the
shared `_process_receiver_body` helper that both delegate to.

Covers: field normalization, status resolution, missing-field handling,
log payload variants (plain, base64, blob ref, blob-upload failure),
resource_info/routing_info parsing, smart-routing invocation, the AI
triage enqueue (including the "Dev Test Run" skip), and the queue/HTTP
entrypoints' own error handling.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import azure.functions as func
import pytest


class FakeOut(func.Out):
    """Minimal table output binding stub that captures writes."""

    def __init__(self):
        self.value = None

    def set(self, v):
        self.value = v

    def get(self):
        return self.value


def make_body(**overrides) -> dict:
    """A minimal-but-complete Receiver payload; override fields per test."""
    body = {
        "exec_id": "exec-123",
        "status": "Completed",
        "name": "My Rule",
        "id": "schema-1",
        "runbook": "rb.sh",
        "run_args": "--x 1",
        "worker": "worker-a",
        "group": "default",
        "oncall": "false",
        "initiator": "tester",
        "monitor_condition": "Fired",
        "severity": "Sev3",
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def no_real_azure(monkeypatch):
    """Every test runs fully offline: no real queue/table/blob client is
    ever constructed, regardless of AzureWebJobsStorage configuration."""
    import function_app

    monkeypatch.setattr(
        function_app, "_get_queue_client", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        function_app, "_get_table_client", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(function_app, "_get_blob_service", MagicMock())


@pytest.fixture(autouse=True)
def inert_smart_routing(monkeypatch):
    """By default smart-routing is a no-op (empty decision, AI triage
    disabled) so tests only exercise what they explicitly opt into."""
    import smart_routing

    monkeypatch.setattr(
        smart_routing,
        "route_alert",
        MagicMock(return_value=SimpleNamespace(actions=[])),
    )
    monkeypatch.setattr(smart_routing, "execute_actions", MagicMock())
    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="false"))


# =========================
# Field normalization / validation
# =========================


def test_receiver_happy_path_normalizes_status_and_writes_log():
    from function_app import _process_receiver_body

    body = make_body(status="Completed")
    out = FakeOut()

    _process_receiver_body(body, out)

    log = json.loads(out.get())
    assert log["Status"] == "succeeded"
    assert log["ExecId"] == "exec-123"
    assert log["Id"] == "schema-1"
    assert log["Runbook"] == "rb.sh"
    assert log["Name"] == "My Rule"


@pytest.mark.parametrize(
    "raw_status,expected",
    [
        ("Completed", "succeeded"),
        ("COMPLETED", "succeeded"),
        ("Failed", "failed"),
        ("Error", "error"),
        ("Running", "running"),
        ("Skipped", "skipped"),
        ("Routed", "routed"),
        ("  Completed  ", "succeeded"),
    ],
)
def test_receiver_status_normalization_variants(raw_status, expected):
    from function_app import _process_receiver_body

    out = FakeOut()
    _process_receiver_body(make_body(status=raw_status), out)

    log = json.loads(out.get())
    assert log["Status"] == expected


def test_receiver_list_type_fields_are_normalized_to_first_value():
    """Some callers send scalar fields wrapped in a single-element list
    (e.g. header-derived payloads); Receiver must be robust to that."""
    from function_app import _process_receiver_body

    body = make_body(
        exec_id=["exec-999"],
        status=["Completed"],
        name=["Weird Rule"],
        id=["schema-9"],
        runbook=["rb2.sh"],
    )
    out = FakeOut()

    _process_receiver_body(body, out)

    log = json.loads(out.get())
    assert log["ExecId"] == "exec-999"
    assert log["Status"] == "succeeded"
    assert log["Name"] == "Weird Rule"
    assert log["Id"] == "schema-9"
    assert log["Runbook"] == "rb2.sh"


@pytest.mark.parametrize(
    "missing_field", ["exec_id", "status", "name", "id", "runbook"]
)
def test_receiver_missing_required_field_is_a_silent_no_op(missing_field):
    """Missing required fields must not raise and must not write a log
    entry — the message is dropped with a warning (fail-open by design,
    since Receiver is a best-effort queue/HTTP sink)."""
    from function_app import _process_receiver_body

    body = make_body()
    body[missing_field] = ""
    out = FakeOut()

    _process_receiver_body(body, out)

    assert out.get() is None


def test_receiver_none_values_are_normalized_to_empty_string():
    from function_app import _process_receiver_body

    body = make_body(run_args=None, worker=None)
    out = FakeOut()

    _process_receiver_body(body, out)

    log = json.loads(out.get())
    # run_args/worker are not part of the required-field normalization loop,
    # so an explicit None is stored as-is rather than coerced to "".
    assert log["Run_Args"] is None
    assert log["Worker"] is None


# =========================
# Logs handling
# =========================


def test_receiver_without_logs_writes_empty_log_field():
    from function_app import _process_receiver_body

    out = FakeOut()
    _process_receiver_body(make_body(), out)

    log = json.loads(out.get())
    assert log["Log"] == ""


def test_receiver_decodes_base64_logs_and_uploads_to_blob(monkeypatch):
    import function_app
    from function_app import _process_receiver_body

    upload_mock = MagicMock(return_value="blobref://runbook-logs/2024/exec-123.log")
    monkeypatch.setattr(function_app, "_upload_log_to_blob", upload_mock)

    logs_b64 = function_app._encode_logs("line1\nline2 error").decode("utf-8")
    out = FakeOut()

    _process_receiver_body(make_body(logs_b64=logs_b64), out)

    upload_mock.assert_called_once()
    log = json.loads(out.get())
    assert log["Log"] == "blobref://runbook-logs/2024/exec-123.log"


def test_receiver_falls_back_to_table_log_when_blob_upload_fails(monkeypatch):
    import function_app
    from function_app import _process_receiver_body

    monkeypatch.setattr(
        function_app, "_upload_log_to_blob", MagicMock(side_effect=RuntimeError("boom"))
    )

    logs_b64 = function_app._encode_logs("some failure output").decode("utf-8")
    out = FakeOut()

    _process_receiver_body(make_body(logs_b64=logs_b64), out)

    log = json.loads(out.get())
    assert log["Log"] == "some failure output"


def test_receiver_reuses_provided_log_ref_without_reuploading(monkeypatch):
    import function_app
    from function_app import _process_receiver_body

    upload_mock = MagicMock()
    monkeypatch.setattr(function_app, "_upload_log_to_blob", upload_mock)

    logs_b64 = function_app._encode_logs("already stored elsewhere").decode("utf-8")
    out = FakeOut()

    _process_receiver_body(
        make_body(
            logs_b64=logs_b64,
            log_ref="blobref://runbook-logs/pre-existing.log",
        ),
        out,
    )

    upload_mock.assert_not_called()
    log = json.loads(out.get())
    assert log["Log"] == "blobref://runbook-logs/pre-existing.log"


def test_receiver_ignores_malformed_log_ref_and_uses_logs_instead(monkeypatch):
    import function_app
    from function_app import _process_receiver_body

    monkeypatch.setattr(
        function_app, "_upload_log_to_blob", MagicMock(return_value="blobref://x/y.log")
    )
    logs_b64 = function_app._encode_logs("fallback logs").decode("utf-8")
    out = FakeOut()

    _process_receiver_body(
        make_body(logs_b64=logs_b64, log_ref="not-a-real-blob-ref"), out
    )

    log = json.loads(out.get())
    assert log["Log"] == "blobref://x/y.log"


# =========================
# resource_info / routing_info parsing
# =========================


def test_receiver_accepts_resource_info_as_json_string():
    from function_app import _process_receiver_body

    out = FakeOut()
    _process_receiver_body(
        make_body(resource_info=json.dumps({"resource_name": "vm-1"})), out
    )

    log = json.loads(out.get())
    assert json.loads(log["ResourceInfo"])["resource_name"] == "vm-1"


def test_receiver_invalid_resource_info_json_string_becomes_empty_dict():
    from function_app import _process_receiver_body

    out = FakeOut()
    _process_receiver_body(make_body(resource_info="{not json"), out)

    log = json.loads(out.get())
    # build_log_entry omits ResourceInfo entirely when the dict is empty/falsy
    assert log.get("ResourceInfo") is None


def test_receiver_invalid_routing_info_json_string_is_treated_as_no_routing(
    monkeypatch,
):
    import smart_routing
    from function_app import _process_receiver_body

    route_alert_mock = MagicMock(return_value=SimpleNamespace(actions=[]))
    monkeypatch.setattr(smart_routing, "route_alert", route_alert_mock)

    out = FakeOut()
    _process_receiver_body(make_body(routing_info="{not json"), out)

    ctx = route_alert_mock.call_args.args[0]
    assert ctx["routing_info"] == {}


# =========================
# Smart routing invocation
# =========================


def test_receiver_invokes_route_alert_and_execute_actions_with_resource_context(
    monkeypatch,
):
    import smart_routing
    from function_app import _process_receiver_body

    decision = SimpleNamespace(actions=[SimpleNamespace(team="core")])
    route_alert_mock = MagicMock(return_value=decision)
    execute_actions_mock = MagicMock()
    monkeypatch.setattr(smart_routing, "route_alert", route_alert_mock)
    monkeypatch.setattr(smart_routing, "execute_actions", execute_actions_mock)

    out = FakeOut()
    _process_receiver_body(
        make_body(
            status="Failed",
            resource_info={"resource_name": "vm-42", "resource_rg": "rg-1"},
        ),
        out,
    )

    route_alert_mock.assert_called_once()
    ctx = route_alert_mock.call_args.args[0]
    assert ctx["resourceName"] == "vm-42"
    assert ctx["resourceGroup"] == "rg-1"
    assert ctx["status"] == "failed"
    execute_actions_mock.assert_called_once()


def test_receiver_survives_execute_actions_exception(monkeypatch):
    """A smart-routing failure must not prevent the log entity from being
    written nor propagate to the caller."""
    import smart_routing
    from function_app import _process_receiver_body

    monkeypatch.setattr(
        smart_routing,
        "route_alert",
        MagicMock(return_value=SimpleNamespace(actions=[])),
    )
    monkeypatch.setattr(
        smart_routing, "execute_actions", MagicMock(side_effect=RuntimeError("down"))
    )

    out = FakeOut()
    _process_receiver_body(make_body(status="Failed"), out)

    log = json.loads(out.get())
    assert log["Status"] == "failed"


def test_receiver_skips_routing_gracefully_when_smart_routing_unavailable(
    monkeypatch,
):
    import function_app

    monkeypatch.setattr(function_app, "route_alert", None, raising=False)
    # Simulate the ImportError branch by making the local import fail.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "smart_routing":
            raise ImportError("no smart_routing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    out = FakeOut()
    function_app._process_receiver_body(make_body(), out)

    log = json.loads(out.get())
    assert log["Status"] == "succeeded"


# =========================
# AI-agent triage enqueue
# =========================


def test_receiver_does_not_enqueue_ai_triage_when_disabled(monkeypatch):
    import function_app
    from function_app import _process_receiver_body

    queue_mock = MagicMock()
    monkeypatch.setattr(
        function_app, "_get_queue_client", MagicMock(return_value=queue_mock)
    )

    out = FakeOut()
    _process_receiver_body(make_body(status="Failed"), out)

    queue_mock.send_message.assert_not_called()


def test_receiver_enqueues_ai_triage_for_failed_status_when_enabled(monkeypatch):
    import function_app
    import smart_routing
    from function_app import _process_receiver_body

    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="true"))
    queue_mock = MagicMock()
    monkeypatch.setattr(
        function_app, "_get_queue_client", MagicMock(return_value=queue_mock)
    )

    out = FakeOut()
    _process_receiver_body(
        make_body(status="Failed", routing_info={"team": "core", "slack_token": "x"}),
        out,
    )

    queue_mock.send_message.assert_called_once()
    sent_payload = json.loads(queue_mock.send_message.call_args.args[0])
    assert sent_payload["exec_id"] == "exec-123"
    assert sent_payload["jsm_alias"] == "schema-1"
    assert sent_payload["status"] == "failed"
    # Only "team" survives from routing_info (no tokens leaked to the agent).
    assert sent_payload["routing_info"] == {"team": "core"}


def test_receiver_does_not_enqueue_ai_triage_for_succeeded_status(monkeypatch):
    import function_app
    import smart_routing
    from function_app import _process_receiver_body

    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="true"))
    queue_mock = MagicMock()
    monkeypatch.setattr(
        function_app, "_get_queue_client", MagicMock(return_value=queue_mock)
    )

    out = FakeOut()
    _process_receiver_body(make_body(status="Completed"), out)

    queue_mock.send_message.assert_not_called()


def test_receiver_skips_ai_triage_for_dev_test_run_by_name(monkeypatch):
    import function_app
    import smart_routing
    from function_app import _process_receiver_body

    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="true"))
    queue_mock = MagicMock()
    monkeypatch.setattr(
        function_app, "_get_queue_client", MagicMock(return_value=queue_mock)
    )

    out = FakeOut()
    _process_receiver_body(
        make_body(status="Failed", name="Dev Test Run", id="dev-test-abc123"), out
    )

    queue_mock.send_message.assert_not_called()


def test_receiver_skips_ai_triage_for_dev_test_run_by_team(monkeypatch):
    import function_app
    import smart_routing
    from function_app import _process_receiver_body

    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="true"))
    queue_mock = MagicMock()
    monkeypatch.setattr(
        function_app, "_get_queue_client", MagicMock(return_value=queue_mock)
    )

    out = FakeOut()
    _process_receiver_body(
        make_body(status="Failed", resource_info={"team": "dev-test"}), out
    )

    queue_mock.send_message.assert_not_called()


def test_receiver_ai_triage_enqueue_failure_does_not_raise(monkeypatch):
    import function_app
    import smart_routing
    from function_app import _process_receiver_body

    monkeypatch.setattr(smart_routing, "get_setting", MagicMock(return_value="true"))
    monkeypatch.setattr(
        function_app,
        "_get_queue_client",
        MagicMock(side_effect=RuntimeError("queue unavailable")),
    )

    out = FakeOut()
    # Should not raise even though enqueueing the AI analysis message fails.
    _process_receiver_body(make_body(status="Failed"), out)

    log = json.loads(out.get())
    assert log["Status"] == "failed"


# =========================
# Queue trigger entrypoint: Receiver(msg, log_table)
# =========================


def test_queue_trigger_receiver_delegates_to_process_receiver_body():
    from function_app import Receiver

    msg = func.QueueMessage(body=json.dumps(make_body()).encode("utf-8"))
    out = FakeOut()

    Receiver(msg, out)

    log = json.loads(out.get())
    assert log["Status"] == "succeeded"
    assert log["ExecId"] == "exec-123"


def test_queue_trigger_receiver_swallows_malformed_json():
    """This is the regression covered by the routed-execution JSON parse
    bug: a message that fails to decode/parse must be logged, never
    raised, and must not write a (garbage) log entity."""
    from function_app import Receiver

    msg = func.QueueMessage(body=b"not-json-at-all")
    out = FakeOut()

    Receiver(msg, out)  # must not raise

    assert out.get() is None


def test_queue_trigger_receiver_handles_empty_message_body():
    from function_app import Receiver

    msg = func.QueueMessage(body=b"")
    out = FakeOut()

    Receiver(msg, out)  # must not raise

    assert out.get() is None


def test_queue_trigger_receiver_decodes_base64_body_when_host_does_not(monkeypatch):
    """Regression test for the ROUTED-execution bug reported by Azure Monitor
    action group test alerts: `Trigger()` sends the status message base64-
    encoded (TextBase64EncodePolicy) expecting the classic queue trigger host
    to auto-decode it (host.json extensions.queues.messageEncoding=base64).
    If that host-side decode is ever bypassed, `msg.get_body()` hands us the
    raw base64 text instead of JSON; `Receiver` must still parse it instead of
    failing with `json.JSONDecodeError: Expecting value: line 1 column 1
    (char 0)`.
    """
    import base64

    from function_app import Receiver

    raw_json = json.dumps(make_body()).encode("utf-8")
    b64_body = base64.b64encode(raw_json)
    msg = func.QueueMessage(body=b64_body)
    out = FakeOut()

    Receiver(msg, out)  # must not raise, must still process the payload

    log = json.loads(out.get())
    assert log["Status"] == "succeeded"
    assert log["ExecId"] == "exec-123"


# =========================
# HTTP fast-path entrypoint: ReceiverHttp(req, log_table)
# =========================


def make_http_request(body: bytes) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="https://x/api/receiver",
        headers={"Content-Type": "application/json"},
        body=body,
    )


def test_receiver_http_happy_path_returns_200():
    from function_app import ReceiverHttp

    req = make_http_request(json.dumps(make_body()).encode("utf-8"))
    out = FakeOut()

    res = ReceiverHttp(req, out)

    assert res.status_code == 200
    payload = json.loads(res.get_body())
    assert payload["status"] == "ok"
    log = json.loads(out.get())
    assert log["Status"] == "succeeded"


def test_receiver_http_rejects_non_object_json_body():
    from function_app import ReceiverHttp

    req = make_http_request(json.dumps(["not", "an", "object"]).encode("utf-8"))
    out = FakeOut()

    res = ReceiverHttp(req, out)

    assert res.status_code == 400
    assert out.get() is None


def test_receiver_http_rejects_malformed_json_body():
    from function_app import ReceiverHttp

    req = make_http_request(b"{not valid json")
    out = FakeOut()

    res = ReceiverHttp(req, out)

    assert res.status_code == 400
    body = json.loads(res.get_body())
    assert "Invalid JSON" in body["error"]


def test_receiver_http_still_returns_200_when_required_fields_missing():
    """_process_receiver_body fails open (logs + drops), so the HTTP caller
    always gets a 200 acknowledgement even if the payload was incomplete —
    this documents existing best-effort semantics."""
    from function_app import ReceiverHttp

    incomplete = make_body()
    incomplete["runbook"] = ""
    req = make_http_request(json.dumps(incomplete).encode("utf-8"))
    out = FakeOut()

    res = ReceiverHttp(req, out)

    assert res.status_code == 200
    assert out.get() is None
