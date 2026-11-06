"""Tests for the name given to Studio dev test runs."""

import json
from unittest.mock import MagicMock

import azure.functions as func
import pytest
import worker_routing
from routes import dev_testrun

from core import audit, security, storage

EXEC_ID = "a1b2c3d4-0000-0000-0000-000000000000"


class FakeOut(func.Out):
    def __init__(self):
        self.value = None

    def set(self, v):
        self.value = v

    def get(self):
        return self.value


@pytest.mark.parametrize(
    ("label", "script_type", "expected"),
    [
        ("restart-pod", "python", "dev-test-restart-pod-a1b2c3d4"),
        ("  Check HPA_v2.1! ", "shell", "dev-test-check-hpa-v2-1-a1b2c3d4"),
        ("", "python", "dev-test-python-a1b2c3d4"),
        (None, "shell", "dev-test-shell-a1b2c3d4"),
        ("!!!", "python", "dev-test-python-a1b2c3d4"),
        ("../../etc/passwd", "python", "dev-test-etc-passwd-a1b2c3d4"),
        (None, "$(id)", "dev-test-id-a1b2c3d4"),
        (None, None, "dev-test-script-a1b2c3d4"),
        (123, "python", "dev-test-123-a1b2c3d4"),
    ],
)
def test_dev_test_name(label, script_type, expected):
    assert dev_testrun._dev_test_name(label, script_type, EXEC_ID) == expected


def test_dev_test_name_truncates_long_labels_without_trailing_dash():
    label = f"{'a' * 39}-{'b' * 20}"

    assert dev_testrun._dev_test_name(label, "python", EXEC_ID) == (
        f"dev-test-{'a' * 39}-a1b2c3d4"
    )


@pytest.fixture
def dispatch(monkeypatch):
    monkeypatch.setenv("FEATURE_DEV", "true")
    monkeypatch.setattr(
        security,
        "get_authenticated_user",
        lambda req: ({"username": "alice", "team": "default"}, None),
    )
    monkeypatch.setattr(worker_routing, "worker_routing", lambda *_: "worker-queue")
    monkeypatch.setattr(audit, "log_audit", MagicMock())
    enqueue = MagicMock()
    monkeypatch.setattr(storage, "enqueue_queue_payload", enqueue)
    return enqueue


def post_test_run(**fields):
    body = {"script": "echo hi", "capability": "cap", "body": {}, **fields}
    log_table = FakeOut()
    response = dev_testrun.dev_test_run(
        func.HttpRequest(
            method="POST",
            url="https://x/api/dev/testrun",
            body=json.dumps(body).encode(),
        ),
        log_table,
        "[]",
    )
    return response, json.loads(log_table.value)


def test_label_names_the_run_and_keeps_the_dev_test_run_display_name(dispatch):
    response, log_entry = post_test_run(label="Restart Pod", scriptType="shell")

    assert response.status_code == 202
    result = json.loads(response.get_body())
    queued = dispatch.call_args.args[1]
    expected = f"dev-test-restart-pod-{result['exec_id'][:8]}"
    assert result["script"] == expected
    assert queued["runbook"] == expected
    assert queued["id"] == expected
    assert log_entry["Runbook"] == expected
    # AI triage is skipped for dev runs by matching this display name.
    assert queued["name"] == "Dev Test Run"


def test_missing_label_falls_back_to_script_type(dispatch):
    response, _ = post_test_run(scriptType="shell")

    result = json.loads(response.get_body())
    assert result["script"] == f"dev-test-shell-{result['exec_id'][:8]}"
