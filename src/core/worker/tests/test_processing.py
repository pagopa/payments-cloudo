# python
import importlib
import json
from unittest.mock import MagicMock, patch

import azure.functions as func


class FakeOut(func.Out):
    """Minimal Out binding stub capturing the value passed to set()."""

    def __init__(self):
        self.value = None

    def set(self, v):
        self.value = v

    def get(self):
        return self.value


def _auth_headers(worker) -> dict:
    return {"x-cloudo-key": worker._CLOUDO_SECRET_KEY}


def test_list_processes_returns_active_runs(monkeypatch):
    worker = importlib.import_module("function_app")
    monkeypatch.setattr(worker, "_CLOUDO_SECRET_KEY", "test-secret")

    # Seed ACTIVE_RUNS with two items
    worker._ACTIVE_RUNS.clear()
    worker._ACTIVE_RUNS["e1"] = {
        "exec_id": "e1",
        "id": "s1",
        "name": "N1",
        "runbook": "a.sh",
        "requestedAt": "2025-01-01 10:00:00",
        "startedAt": "2025-01-01 10:01:00",
        "status": "running",
    }
    worker._ACTIVE_RUNS["e2"] = {
        "exec_id": "e2",
        "id": "s2",
        "name": "N2",
        "runbook": "b.sh",
        "requestedAt": "2025-01-01 10:00:10",
        "startedAt": "2025-01-01 10:02:00",
        "status": "running",
    }

    req = func.HttpRequest(
        "GET",
        "https://x/api/processes?q=N",
        params={},
        headers=_auth_headers(worker),
        body=b"",
    )
    res = worker.list_processes(req)
    assert res.status_code == 200
    data = json.loads(res.get_body())
    assert data["status"] == "ok"
    assert data["count"] == 2
    assert len(data["runs"]) == 2


def test_list_processes_rejects_unauthorized_request(monkeypatch):
    worker = importlib.import_module("function_app")
    monkeypatch.setattr(worker, "_CLOUDO_SECRET_KEY", "test-secret")

    req = func.HttpRequest(
        "GET", "https://x/api/processes", params={}, headers={}, body=b""
    )
    res = worker.list_processes(req)
    assert res.status_code == 401


def test_stop_process_terminates_and_reports(monkeypatch):
    worker = importlib.import_module("function_app")
    monkeypatch.setattr(worker, "_CLOUDO_SECRET_KEY", "test-secret")

    # Fake running process mapped by exec id
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # running
    worker._PROCESS_BY_EXEC.clear()
    worker._PROCESS_BY_EXEC["exec-999"] = fake_proc

    # Track run info to trigger _dispatch_status on stop
    worker._ACTIVE_RUNS.clear()
    worker._ACTIVE_RUNS["exec-999"] = {
        "exec_id": "exec-999",
        "id": "s1",
        "name": "N1",
        "runbook": "a.sh",
        "run_args": "--x 1",
        "requestedAt": "2025-01-01 10:00:00",
        "startedAt": "2025-01-01 10:01:00",
        "status": "running",
    }

    # Avoid any real HTTP/queue delivery: pretend the HTTP fast path succeeded.
    with patch.object(
        worker, "_send_status_to_receiver", return_value=True
    ) as send_status:
        req = func.HttpRequest(
            "DELETE",
            "https://x/api/processes/stop?exec_id=exec-999",
            params={"exec_id": "exec-999"},
            headers=_auth_headers(worker),
            body=b"",
        )
        out = FakeOut()
        res = worker.stop_process(req, out)

    assert res.status_code == 200
    body = json.loads(res.get_body())
    assert body["status"] == "stopped"
    assert body["exec_id"] == "exec-999"

    # Process termination sequence attempted
    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called_once()

    # A status update should have been dispatched
    send_status.assert_called_once()
    message_json, payload = send_status.call_args[0]
    assert json.loads(message_json)["status"] == "stopped"
    assert payload["exec_id"] == "exec-999"


def test_stop_process_rejects_unauthorized_request(monkeypatch):
    worker = importlib.import_module("function_app")
    monkeypatch.setattr(worker, "_CLOUDO_SECRET_KEY", "test-secret")

    req = func.HttpRequest(
        "DELETE",
        "https://x/api/processes/stop?exec_id=exec-999",
        params={"exec_id": "exec-999"},
        headers={},
        body=b"",
    )
    out = FakeOut()
    res = worker.stop_process(req, out)
    assert res.status_code == 401


def test_stop_process_returns_404_for_unknown_exec_id(monkeypatch):
    worker = importlib.import_module("function_app")
    monkeypatch.setattr(worker, "_CLOUDO_SECRET_KEY", "test-secret")

    worker._PROCESS_BY_EXEC.clear()
    req = func.HttpRequest(
        "DELETE",
        "https://x/api/processes/stop?exec_id=does-not-exist",
        params={"exec_id": "does-not-exist"},
        headers=_auth_headers(worker),
        body=b"",
    )
    out = FakeOut()
    res = worker.stop_process(req, out)
    assert res.status_code == 404
