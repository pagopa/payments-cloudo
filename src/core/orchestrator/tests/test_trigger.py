# python
import json
import os
from unittest.mock import MagicMock, patch

import azure.functions as func


def make_request(
    method="POST", url="https://x/api/Trigger", params=None, headers=None, body=b""
):
    # Inject team via querystring to avoid setting route_params (which is read-only)
    params = params or {}
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}team=core"
    return func.HttpRequest(
        method=method,
        url=url,
        params=params,
        headers=headers or {},
        body=body,
    )


class FakeOut(func.Out):
    # Minimal table output stub that captures the last value passed to set()
    def __init__(self):
        self.value = None

    def set(self, v):
        self.value = v

    def get(self):
        return self.value


def test_trigger_happy_path(monkeypatch):
    # Ensure anonymous auth in tests
    os.environ["FEATURE_DEV"] = "true"
    # Authenticate via the global secret key (simplest path through
    # _get_authenticated_user for an OPERATOR-level caller).
    os.environ["CLOUDO_SECRET_KEY"] = "test-secret"

    # Table binding mocked content: a single schema row
    schema_row = {
        "id": "schema-1",
        "Id": "schema-1",
        "name": "My Rule",
        "url": "https://runbook.example/exec",
        "runbook": "rb.sh",
        "run_args": "--x 1",
        "oncall": "false",
        "require_approval": "false",
    }
    entities = json.dumps([schema_row])

    import function_app

    # Trigger's execution path is fully queue-based (no direct HTTP call to
    # the worker): it (1) ensures the notification queue exists, (2) asks
    # worker_routing for a target queue, then (3) enqueues the payload.
    # Mock all three so the test never touches real Azure Storage.
    monkeypatch.setattr(
        function_app, "_get_queue_client", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(function_app, "_enqueue_queue_payload", MagicMock())

    with patch("worker_routing.worker_routing", return_value="worker-a-queue"):
        # Routing is not executed for status 202 ("accepted"), but we patch
        # defensively in case that assumption ever changes.
        with patch("smart_routing.route_alert", return_value=MagicMock(actions=[])):
            with patch("smart_routing.execute_actions") as exec_actions:
                from function_app import Trigger

                # Build request and fake table output
                req = make_request(
                    params={"id": "schema-1"},
                    headers={"x-cloudo-key": "test-secret"},
                )
                out = FakeOut()

                # Invoke function
                res = Trigger(req, out, entities, workers="[]")

                # Assert HTTP response
                assert res.status_code == 202
                body = json.loads(res.get_body())
                assert body["schema"]["id"] == "schema-1"

                # Assert a coherent log entity is written
                log = json.loads(out.get())
                assert log["Status"] == "accepted"
                assert log["Id"] == "schema-1"
                assert log["Runbook"] == "rb.sh"

                # Ensure routing actions are not executed on "accepted"
                exec_actions.assert_not_called()

                # The payload was handed off to the worker via the queue,
                # not via a direct HTTP call.
                function_app._enqueue_queue_payload.assert_called_once()
                queue_name, queue_payload = (
                    function_app._enqueue_queue_payload.call_args.args
                )
                assert queue_name == "worker-a-queue"
                assert queue_payload["id"] == "schema-1"
                assert queue_payload["runbook"] == "rb.sh"
