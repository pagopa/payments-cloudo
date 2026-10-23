# python
import json
import os
from unittest.mock import MagicMock, patch

import azure.functions as func
from routes import runbooks

from core import executor, registry, reporting


def make_queue_message(payload: dict) -> func.QueueMessage:
    # Minimal QueueMessage stub using azure.functions QueueMessage signature
    class QM(func.QueueMessage):
        def __init__(self, body: bytes):
            self._body = body

        def get_body(self) -> bytes:
            return self._body

    return QM(json.dumps(payload).encode("utf-8"))


def test_process_runbook_happy_path(monkeypatch):
    payload = {
        "requestedAt": "2025-01-01 10:00:00",
        "id": "schema-1",
        "name": "MyRule",
        "runbook": "script.py",
        "run_args": "--ok 1",
        "exec_id": "exec-abc",
        "oncall": "false",
        "monitor_condition": "Fired",
        "severity": "Sev2",
        "resource_info": json.dumps({"aks_namespace": "", "resource_name": "r1"}),
    }

    # Patch _send_status_to_receiver (the actual delivery function invoked by
    # _dispatch_status) to record calls instead of doing real HTTP/queue I/O.
    status_calls = []

    def fake_send_status_to_receiver(message_json, _payload):
        message = json.loads(message_json)
        status_calls.append((message["status"], message))
        return True

    # Patch _run_script to simulate success
    fake_completed = MagicMock()
    fake_completed.returncode = 0
    fake_completed.stdout = "all good\n"
    fake_completed.stderr = ""

    with patch.object(
        reporting, "_send_status_to_receiver", side_effect=fake_send_status_to_receiver
    ):
        with patch.object(executor, "run_script", return_value=fake_completed):
            # Ensure internal registries are clean
            registry.ACTIVE_RUNS.clear()
            registry.PROCESS_BY_EXEC.clear()

            msg = make_queue_message(payload)
            runbooks.process_runbook(msg)

            # Verify status transitions: running -> succeeded
            assert status_calls[0][0] == "running"
            assert status_calls[-1][0] == "succeeded"

            # Ensure the run is no longer tracked
            assert payload["exec_id"] not in registry.ACTIVE_RUNS


def test_process_runbook_uses_schema_aks_settings_unless_resource_info_overrides(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("RESOURCE_NAME", raising=False)
    monkeypatch.delenv("RESOURCE_RG", raising=False)
    monkeypatch.delenv("AKS_NAMESPACE", raising=False)
    monkeypatch.setenv("AKS_INTEGRATION_ENABLED", "true")

    payload = {
        "name": "MyRule",
        "runbook": "script.py",
        "exec_id": "exec-schema-aks",
        "aks_login_required": True,
        "aks_cluster_name": "aks-schema",
        "aks_resource_group": "rg-schema",
        "aks_namespace": "ns-schema",
        "resource_info": {"resource_name": "aks-payload"},
    }
    login_calls = []
    script_calls = []

    def fake_aks_login(info, payload, env, temp_dir):
        login_calls.append(info)
        return str(tmp_path / "kubeconfig")

    def fake_run_script(**kwargs):
        script_calls.append(kwargs)
        result = MagicMock()
        result.returncode = 0
        result.stdout = "all good\n"
        result.stderr = ""
        return result

    with patch.object(
        reporting, "_send_status_to_receiver", return_value=True
    ), patch.object(
        executor, "run_aks_login", side_effect=fake_aks_login
    ), patch.object(executor, "run_script", side_effect=fake_run_script):
        registry.ACTIVE_RUNS.clear()
        runbooks.process_runbook(make_queue_message(payload))

    expected_info = {
        "resource_name": "aks-payload",
        "resource_rg": "rg-schema",
        "aks_namespace": "ns-schema",
    }
    assert login_calls == [expected_info]
    assert script_calls[0]["resource_info"] == expected_info
    assert script_calls[0]["kubeconfig_path"] == str(tmp_path / "kubeconfig")


def test_process_runbook_skips_aks_login_when_schema_does_not_require_it(
    monkeypatch,
):
    payload = {
        "name": "MyRule",
        "runbook": "script.py",
        "exec_id": "exec-no-aks",
        "resource_info": {
            "resource_name": "aks-demo",
            "resource_rg": "rg-demo",
            "aks_namespace": "demo-ns",
        },
    }

    result = MagicMock(returncode=0, stdout="all good\n", stderr="")
    with patch.object(
        reporting, "_send_status_to_receiver", return_value=True
    ), patch.object(executor, "run_aks_login") as login, patch.object(
        executor, "run_script", return_value=result
    ):
        registry.ACTIVE_RUNS.clear()
        runbooks.process_runbook(make_queue_message(payload))

    login.assert_not_called()


def test_process_runbook_skips_if_already_running(monkeypatch):
    payload = {
        "requestedAt": "2025-01-01 10:00:00",
        "id": "schema-dup",
        "name": "MyRule",
        "runbook": "script.py",
        "run_args": "--ok 1",
        "resource_info": None,
        "exec_id": "exec-1",
    }

    # _inspect_duplicate_runs matches on name+runbook+run_args+resource_info+group
    # (not on "id"), so pre-populate an in-progress run with identical fields
    # to trigger the "identical execution already in progress" skip branch.
    registry.ACTIVE_RUNS.clear()
    registry.ACTIVE_RUNS["some-other-exec"] = {
        "id": "schema-dup",
        "name": "MyRule",
        "runbook": "script.py",
        "run_args": "--ok 1",
        "resource_info": None,
        "group": "-",
    }

    status_calls = []

    def fake_send_status_to_receiver(message_json, _payload):
        status_calls.append(json.loads(message_json)["status"])
        return True

    with patch.object(
        reporting, "_send_status_to_receiver", side_effect=fake_send_status_to_receiver
    ):
        msg = make_queue_message(payload)
        runbooks.process_runbook(msg)

    # Should report "skipped" and not attempt to run
    assert "skipped" in status_calls


def test_run_aks_login_error_includes_full_output(tmp_path):
    os.environ["FEATURE_DEV"] = "true"

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(
                [
                    'WARNING: Merged "cluster" as current context\n',
                    "ERROR: forbidden: cannot create token\n",
                ]
            )

        def wait(self):
            return 1

    with patch("core.executor.os.path.exists", return_value=True), patch(
        "core.executor.subprocess.Popen", return_value=FakePopen()
    ):
        try:
            executor.run_aks_login(
                {
                    "resource_rg": "rg-demo",
                    "resource_name": "aks-demo",
                    "aks_namespace": "demo-ns",
                },
                payload={"exec_id": "exec-aks-1"},
                env={},
                temp_dir=str(tmp_path),
            )
            assert False, "Expected RuntimeError from AKS login"
        except RuntimeError as exc:
            message = str(exc)

    assert "AKS login error (rc=1). Full output:" in message
    assert 'WARNING: Merged "cluster" as current context' in message
    assert "ERROR: forbidden: cannot create token" in message
