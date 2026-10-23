# python
"""General worker HTTP surface tests: health check and shared auth guard.

Note: the worker no longer exposes a synchronous "Runbook" HTTP endpoint -
runbook executions are enqueued directly by the orchestrator's Trigger()
onto the worker's queue and processed by process_runbook() (queue trigger,
see tests/test_runbooks.py). This file previously tested a `worker.runbook`
HTTP handler that doesn't exist anymore; it now covers the endpoints that
actually exist: `healthz` and the shared `_is_authorized_request` guard used
by `processes` / `processes/stop`.
"""

import json

import azure.functions as func
from routes import system

from core import reporting, security


def test_healthz_is_anonymous_and_reports_ok():
    req = func.HttpRequest(
        "GET", "https://x/api/healthz", params={}, headers={}, body=b""
    )
    res = system.heartbeat(req)

    assert res.status_code == 200
    body = json.loads(res.get_body())
    assert body["status"] == "ok"
    assert body["service"] == "RunbookTest"
    assert "time" in body


def test_is_authorized_request_requires_matching_secret_key(monkeypatch):
    monkeypatch.setattr(security, "CLOUDO_SECRET_KEY", "test-secret")

    authorized_req = func.HttpRequest(
        "GET",
        "https://x/api/processes",
        params={},
        headers={"x-cloudo-key": "test-secret"},
        body=b"",
    )
    assert security.is_authorized_request(authorized_req) is True

    wrong_key_req = func.HttpRequest(
        "GET",
        "https://x/api/processes",
        params={},
        headers={"x-cloudo-key": "wrong-secret"},
        body=b"",
    )
    assert security.is_authorized_request(wrong_key_req) is False

    no_key_req = func.HttpRequest(
        "GET", "https://x/api/processes", params={}, headers={}, body=b""
    )
    assert security.is_authorized_request(no_key_req) is False


def test_is_authorized_request_rejects_everything_when_no_secret_configured(
    monkeypatch,
):
    monkeypatch.setattr(security, "CLOUDO_SECRET_KEY", "")

    req = func.HttpRequest(
        "GET",
        "https://x/api/processes",
        params={},
        headers={"x-cloudo-key": ""},
        body=b"",
    )
    assert security.is_authorized_request(req) is False


def test_post_status_propagates_team_to_receiver_payload():
    message = json.loads(
        reporting.post_status(
            {
                "requestedAt": "2026-10-01T10:00:00+00:00",
                "id": "schema-1",
                "name": "Team Runbook",
                "exec_id": "exec-1",
                "runbook": "run.sh",
                "team": "payments",
            },
            status="running",
            log_message="started",
        )
    )

    assert message["team"] == "payments"
