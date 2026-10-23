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
    ob.set("val1")
    ob.set("val2")
    assert ob.values() == ["val1", "val2"]


def test_normalize_table_entity():
    data = {"str": "hello", "dict": {"a": 1}, "list": [1, 2]}
    norm = _normalize_table_entity(data)
    assert norm["str"] == "hello"
    assert norm["dict"] == json.dumps({"a": 1})
    assert norm["list"] == json.dumps([1, 2])


def test_sanitize_method():
    assert _sanitize_method("get") == "GET"
    mock_method = MagicMock()
    mock_method.value = "HTTPMETHOD.POST"
    assert _sanitize_method(mock_method) == "POST"


def test_to_fastapi_response():
    # Azure func response
    func_resp = func.HttpResponse(
        "body content", status_code=200, mimetype="text/plain"
    )
    res = _to_fastapi_response(func_resp)
    assert res.status_code == 200
    assert res.body == b"body content"

    # Already FastAPI response
    fastapi_resp = Response("resp")
    assert _to_fastapi_response(fastapi_resp) is fastapi_resp

    # Dict/list
    json_res = _to_fastapi_response({"key": "val"})
    assert json_res.status_code == 200

    # None
    none_res = _to_fastapi_response(None)
    assert none_res.status_code == 204


def test_translate_route_pattern():
    assert _translate_route_pattern("users/{id?}") == ["users/{id}", "users"]
    assert _translate_route_pattern("health") == ["health"]


def test_read_route_specs_uses_function_metadata():
    specs = {spec["function_name"]: spec for spec in _read_route_specs()}

    get_log = specs["get_log"]
    assert get_log["route"] == "logs/{partitionKey}/{execId}"
    assert callable(get_log["handler"])
    assert get_log["bindings"] == [
        {
            "kind": "table_input",
            "arg_name": "log_entity",
            "table_name": "RunbookLogs",
            "partition_key": "{partitionKey}",
            "row_key": None,
            "filter": "ExecId eq '{execId}'",
        }
    ]

    receiver = specs["ReceiverHttp"]
    assert [b["kind"] for b in receiver["bindings"]] == ["table_output"]

    # Non-HTTP functions (queue/timer triggers) are not exposed as routes.
    assert "Receiver" not in specs
    assert "scheduler_engine" not in specs


def test_app_endpoints():
    resp = _admin_warmup()
    assert resp.status_code == 200
    assert json.loads(resp.body.decode()) == {"status": "ok"}

    resp = _admin_host_status()
    assert resp.status_code == 200
    assert json.loads(resp.body.decode()) == {"state": "Running"}


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
