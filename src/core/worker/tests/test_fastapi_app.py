import ast
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
    _parse_decorator_kwargs,
    _resolve_value,
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


def test_resolve_value():
    assert _resolve_value("STORAGE_CONNECTION") is not None
    assert _resolve_value("UNKNOWN_VALUE_XYZ") == "UNKNOWN_VALUE_XYZ"


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


def test_parse_decorator_kwargs():
    tree = ast.parse("route(route='jobs', methods=['POST'])")
    call_node = tree.body[0].value
    kwargs = _parse_decorator_kwargs(call_node)
    assert kwargs.get("route") == "jobs"
    assert kwargs.get("methods") == ["POST"]


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
