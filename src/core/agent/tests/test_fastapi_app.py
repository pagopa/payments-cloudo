import asyncio
import json
from unittest.mock import patch

import azure.functions as func
from fastapi import Response
from fastapi_app import (
    _admin_host_status,
    _admin_warmup,
    _configure_runtime_logging,
    _to_fastapi_response,
    app,
    lifespan,
)


def test_configure_runtime_logging():
    _configure_runtime_logging()


def test_to_fastapi_response():
    func_resp = func.HttpResponse("ok", status_code=200, mimetype="text/plain")
    res = _to_fastapi_response(func_resp)
    assert res.status_code == 200
    assert res.body == b"ok"

    fastapi_resp = Response("direct")
    assert _to_fastapi_response(fastapi_resp) is fastapi_resp

    json_res = _to_fastapi_response({"status": "ok"})
    assert json_res.status_code == 200

    none_res = _to_fastapi_response(None)
    assert none_res.status_code == 204


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
