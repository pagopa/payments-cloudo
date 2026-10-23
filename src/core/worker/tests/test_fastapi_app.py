import asyncio
import json
from unittest.mock import MagicMock, patch

import azure.functions as func
from fastapi import Response
from fastapi_app import (
    _admin_host_status,
    _admin_warmup,
    _configure_runtime_logging,
    _normalize_table_entity,
    _OutBinding,
    _read_route_specs,
    _sanitize_method,
    _to_fastapi_response,
    _translate_route_pattern,
    app,
    lifespan,
)


def test_configure_runtime_logging():
    _configure_runtime_logging()


def test_out_binding():
    ob = _OutBinding()
    assert ob.values() == []
    ob.set("msg1")
    assert ob.values() == ["msg1"]


def test_normalize_table_entity():
    data = {"name": "test", "items": [1, 2, 3]}
    norm = _normalize_table_entity(data)
    assert norm["name"] == "test"
    assert norm["items"] == json.dumps([1, 2, 3])


def test_sanitize_method():
    assert _sanitize_method("get") == "GET"
    mock_m = MagicMock()
    mock_m.value = "HTTPMETHOD.DELETE"
    assert _sanitize_method(mock_m) == "DELETE"


def test_to_fastapi_response():
    func_resp = func.HttpResponse("hello", status_code=200, mimetype="text/plain")
    res = _to_fastapi_response(func_resp)
    assert res.status_code == 200
    assert res.body == b"hello"

    fastapi_resp = Response("test")
    assert _to_fastapi_response(fastapi_resp) is fastapi_resp

    json_res = _to_fastapi_response({"status": "ok"})
    assert json_res.status_code == 200

    none_res = _to_fastapi_response(None)
    assert none_res.status_code == 204


def test_translate_route_pattern():
    assert _translate_route_pattern("jobs/{id?}") == ["jobs/{id}", "jobs"]
    assert _translate_route_pattern("ping") == ["ping"]


def test_read_route_specs_uses_function_metadata():
    specs = {spec["function_name"]: spec for spec in _read_route_specs()}

    assert specs["heartbeat"]["route"] == "healthz"
    assert callable(specs["heartbeat"]["handler"])

    stop = specs["stop_process"]
    assert [b["kind"] for b in stop["bindings"]] == ["queue_output"]
    assert stop["bindings"][0]["queue_name"]

    # Queue/timer triggers are consumed by background threads, not routes.
    assert "process_runbook" not in specs
    assert "heartbeat_trigger" not in specs


def test_admin_endpoints():
    warmup_resp = _admin_warmup()
    assert warmup_resp.status_code == 200
    assert json.loads(warmup_resp.body.decode()) == {"status": "ok"}

    host_resp = _admin_host_status()
    assert host_resp.status_code == 200
    assert json.loads(host_resp.body.decode()) == {"state": "Running"}


def test_startup_shutdown():
    async def _run():
        with patch("fastapi_app._start_background_workers") as mock_start, patch(
            "fastapi_app._stop_background_workers"
        ) as mock_stop:
            async with lifespan(app):
                assert mock_start.called
                assert not mock_stop.called
            assert mock_stop.called

    asyncio.run(_run())


def test_process_queue_message_renews_lease_until_done():
    import time

    import fastapi_app

    queue = MagicMock()
    queue.update_message.side_effect = [
        MagicMock(pop_receipt=f"receipt-{i}") for i in range(1, 100)
    ]
    msg = MagicMock(id="msg-1", pop_receipt="receipt-0", content='{"exec_id": "x"}')

    def slow_runbook(_msg):
        time.sleep(0.35)

    with (
        patch.object(fastapi_app, "_get_queue_client", return_value=queue),
        patch.object(fastapi_app, "QUEUE_VISIBILITY_RENEW_SECONDS", 0.1),
        patch.object(fastapi_app.runbooks, "process_runbook", side_effect=slow_runbook),
    ):
        fastapi_app._process_queue_message(msg)

    assert queue.update_message.call_count >= 2
    first = queue.update_message.call_args_list[0]
    assert first.args == ("msg-1", "receipt-0")
    assert first.kwargs["visibility_timeout"] == fastapi_app.QUEUE_VISIBILITY_TIMEOUT
    last_receipt = f"receipt-{queue.update_message.call_count}"
    queue.delete_message.assert_called_once_with("msg-1", last_receipt)
