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

import importlib
import json

import azure.functions as func


def test_healthz_is_anonymous_and_reports_ok():
    worker = importlib.import_module("function_app")

    req = func.HttpRequest(
        "GET", "https://x/api/healthz", params={}, headers={}, body=b""
    )
    res = worker.heartbeat(req)

    assert res.status_code == 200
    body = json.loads(res.get_body())
    assert body["status"] == "ok"
    assert body["service"] == "RunbookTest"
    assert "time" in body


def test_is_authorized_request_requires_matching_secret_key(monkeypatch):
    worker = importlib.import_module("function_app")
    monkeypatch.setattr(worker, "_CLOUDO_SECRET_KEY", "test-secret")

    authorized_req = func.HttpRequest(
        "GET",
        "https://x/api/processes",
        params={},
        headers={"x-cloudo-key": "test-secret"},
        body=b"",
    )
    assert worker._is_authorized_request(authorized_req) is True

    wrong_key_req = func.HttpRequest(
        "GET",
        "https://x/api/processes",
        params={},
        headers={"x-cloudo-key": "wrong-secret"},
        body=b"",
    )
    assert worker._is_authorized_request(wrong_key_req) is False

    no_key_req = func.HttpRequest(
        "GET", "https://x/api/processes", params={}, headers={}, body=b""
    )
    assert worker._is_authorized_request(no_key_req) is False


def test_is_authorized_request_rejects_everything_when_no_secret_configured(
    monkeypatch,
):
    worker = importlib.import_module("function_app")
    monkeypatch.setattr(worker, "_CLOUDO_SECRET_KEY", "")

    req = func.HttpRequest(
        "GET",
        "https://x/api/processes",
        params={},
        headers={"x-cloudo-key": ""},
        body=b"",
    )
    assert worker._is_authorized_request(req) is False
