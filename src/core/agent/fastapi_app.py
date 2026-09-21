import logging
import os
import threading
from typing import Any

import azure.functions as func
import function_app as legacy
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

API_PREFIX = os.getenv("API_PREFIX", "/api")
POLL_SECONDS = float(os.getenv("FASTAPI_QUEUE_POLL_SECONDS", "5"))
QUEUE_VISIBILITY_TIMEOUT = int(os.getenv("FASTAPI_QUEUE_VISIBILITY_TIMEOUT", "300"))
QUEUE_BATCH_SIZE = max(1, int(os.getenv("FASTAPI_QUEUE_BATCH_SIZE", "4")))

_STOP_EVENT = threading.Event()
_BACKGROUND_THREADS: list[threading.Thread] = []


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | agent        | %(name)s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"
# Third-party SDKs are chatty at INFO (full HTTP request/response dumps);
# keep them quiet so application logs aren't drowned out.
_NOISY_LOGGERS = ("azure", "urllib3", "openai", "httpx", "httpcore")


def _configure_runtime_logging() -> None:
    """Configure structured, leveled logging for the agent service."""
    level_name = os.getenv("AGENT_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATEFMT,
        force=True,
    )

    for noisy_logger in _NOISY_LOGGERS:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Disable per-request access logs (GET/POST lines) to reduce noise.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True


def _make_http_request(
    request: Request, path_params: dict, body: bytes
) -> func.HttpRequest:
    return func.HttpRequest(
        method=request.method,
        url=str(request.url),
        params=dict(request.query_params),
        headers=dict(request.headers),
        route_params=path_params,
        body=body,
    )


def _to_fastapi_response(result: Any) -> Response:
    if isinstance(result, func.HttpResponse):
        headers = dict(result.headers or {})
        body = result.get_body()
        media_type = result.mimetype or headers.get("content-type")
        return Response(
            content=body,
            status_code=result.status_code,
            headers=headers,
            media_type=media_type,
        )
    if isinstance(result, Response):
        return result
    if isinstance(result, (dict, list)):
        return JSONResponse(result)
    if result is None:
        return Response(status_code=204)
    return Response(content=str(result))


# =========================
# Background queue poller (equivalent of the Azure Functions queue trigger
# when this service runs as a plain FastAPI/uvicorn container in AKS).
# =========================


def _poll_analysis_queue() -> None:
    while not _STOP_EVENT.is_set():
        try:
            queue = legacy._get_queue_client(legacy.AI_ANALYSIS_QUEUE_NAME)
            messages = list(
                queue.receive_messages(
                    messages_per_page=QUEUE_BATCH_SIZE,
                    visibility_timeout=QUEUE_VISIBILITY_TIMEOUT,
                )
            )
            if not messages:
                _STOP_EVENT.wait(POLL_SECONDS)
                continue

            for msg in messages:
                except Exception as exc:
                    logging.warning("Agent: AI analysis message failed: %s", exc)
                    continue
                queue.delete_message(msg.id, msg.pop_receipt)
        except Exception as exc:
            logging.warning("Agent: AI analysis queue poll failed: %s", exc)
            _STOP_EVENT.wait(max(POLL_SECONDS, 5.0))


def _start_background_workers() -> None:
    _STOP_EVENT.clear()
    thread = threading.Thread(
        target=_poll_analysis_queue, name="agent-queue-poller", daemon=True
    )
    thread.start()
    _BACKGROUND_THREADS.append(thread)


def _stop_background_workers() -> None:
    _STOP_EVENT.set()
    for thread in _BACKGROUND_THREADS:
        thread.join(timeout=1.0)
    _BACKGROUND_THREADS.clear()


BANNER = r"""
\033[1;33m
   ______  __                   ______      ___
 .' ___  |[  |                 |_   _ `.  .'   `.
/ .'   \_| | |  .--.   __   _    | | `. \/  .-.  \
| |        | |/ .'`\ \[  | | |   | |  | || |   | |
\ `.___.'\ | || \__. | | \_/ |, _| |_.' /\  `-'  /
 `.____ .'[___]'.__.'  '.__.'_/|______.'  `.___.'
\033[0m
  service  : AGENT
  role     : AI triage for alerts & failed runbooks
  port     : 80
"""


app = FastAPI(title="CloudDO Agent", version="fastapi-migration")


@app.get("/admin/warmup")
def _admin_warmup() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/admin/host/status")
def _admin_host_status() -> JSONResponse:
    return JSONResponse({"state": "Running"})


@app.on_event("startup")
def _on_startup() -> None:
    _configure_runtime_logging()
    print(BANNER.replace("\\033", "\033"))
    _start_background_workers()


@app.on_event("shutdown")
def _on_shutdown() -> None:
    _stop_background_workers()


async def _analyze_endpoint(request: Request):
    body = await request.body()
    req = _make_http_request(request, dict(request.path_params), body)
    result = legacy.Analyze(req)
    return _to_fastapi_response(result)


async def _healthz_endpoint(request: Request):
    body = await request.body()
    req = _make_http_request(request, dict(request.path_params), body)
    result = legacy.Healthz(req)
    return _to_fastapi_response(result)


app.add_api_route(
    f"{API_PREFIX}/analyze",
    endpoint=_analyze_endpoint,
    methods=["POST"],
    name="Analyze",
)
app.add_api_route(
    f"{API_PREFIX}/healthz", endpoint=_healthz_endpoint, methods=["GET"], name="Healthz"
)
