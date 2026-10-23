import concurrent.futures
import functools
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable, Optional

import azure.functions as func
import function_app as legacy
from core.config import NOTIFICATION_QUEUE_NAME, TABLE_NAME
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from log_stream import stream_execution_logs, stream_executions
from routes import receiver, schedules, workers

from core import storage

API_PREFIX = os.getenv("API_PREFIX", "/api")
POLL_SECONDS = float(os.getenv("FASTAPI_QUEUE_POLL_SECONDS", "2"))
SCHEDULER_SECONDS = int(os.getenv("FASTAPI_SCHEDULER_SECONDS", "60"))
NOTIF_QUEUE_CONCURRENCY = max(
    1, int(os.getenv("FASTAPI_NOTIFICATION_CONCURRENCY", "4"))
)
NOTIF_QUEUE_BATCH_SIZE = max(1, int(os.getenv("FASTAPI_NOTIFICATION_BATCH_SIZE", "8")))
NOTIF_QUEUE_VISIBILITY_TIMEOUT = int(
    os.getenv("FASTAPI_NOTIFICATION_VISIBILITY_TIMEOUT", "300")
)

_STOP_EVENT = threading.Event()
_BACKGROUND_THREADS: list[threading.Thread] = []


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | orchestrator | %(name)s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"
# Third-party SDKs are chatty at INFO (full HTTP request/response dumps);
# keep them quiet so application logs aren't drowned out.
_NOISY_LOGGERS = ("azure", "urllib3", "openai", "httpx", "httpcore")


def _configure_runtime_logging() -> None:
    """Configure structured, leveled logging for the orchestrator service.

    Without an explicit basicConfig() call the root logger defaults to
    WARNING with no handler attached, so every logging.info()/debug() call
    in the codebase is silently dropped and only errors/warnings show up.
    """
    level_name = os.getenv(
        "ORCHESTRATOR_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")
    ).upper()
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


class _OutBinding:
    def __init__(self) -> None:
        self._values: list[Any] = []

    def set(self, value: Any) -> None:
        self._values.append(value)

    def values(self) -> list[Any]:
        return self._values


def _normalize_table_entity(entity: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in entity.items():
        if isinstance(value, (dict, list, tuple)):
            normalized[key] = json.dumps(value, ensure_ascii=False)
        else:
            normalized[key] = value
    return normalized


def _sanitize_method(method: Any) -> str:
    raw = str(getattr(method, "value", method) or "GET").strip().upper()
    if raw.startswith("HTTPMETHOD."):
        raw = raw.split(".", 1)[1]
    return raw


def _make_http_request(request: Request, route_params: dict[str, str], body: bytes):
    return func.HttpRequest(
        method=request.method,
        url=str(request.url),
        params=dict(request.query_params),
        headers=dict(request.headers),
        route_params=route_params,
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


def _query_table_rows(binding: dict[str, Any], params: dict[str, str]) -> str:
    table_name = binding.get("table_name")
    if not table_name:
        return "[]"

    partition_template = binding.get("partition_key")
    filter_template = binding.get("filter")
    filter_parts: list[str] = []

    if isinstance(partition_template, str) and partition_template:
        pk_value = partition_template.format(**params)
        filter_parts.append(f"PartitionKey eq '{pk_value}'")

    if isinstance(filter_template, str) and filter_template:
        filter_parts.append(filter_template.format(**params))

    query_filter = " and ".join(filter_parts) if filter_parts else None
    table = storage.get_table_client(table_name)
    rows = (
        list(table.query_entities(query_filter=query_filter))
        if query_filter
        else list(table.query_entities(query_filter="PartitionKey ne ''"))
    )
    return json.dumps(rows, ensure_ascii=False)


def _flush_table_output(binding: dict[str, Any], out: _OutBinding) -> None:
    table_name = binding.get("table_name")
    if not table_name:
        return

    table = storage.get_table_client(table_name)
    for raw in out.values():
        if raw is None:
            continue
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict):
            table.upsert_entity(entity=_normalize_table_entity(parsed))
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    table.upsert_entity(entity=_normalize_table_entity(item))


def _translate_route_pattern(route: str) -> list[str]:
    optional = re.findall(r"/\{([A-Za-z_][A-Za-z0-9_]*)\?\}", route)
    clean = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\?\}", r"{\1}", route)
    patterns = [clean]
    for name in optional:
        patterns.append(clean.replace(f"/{{{name}}}", "", 1))
    unique = list(dict.fromkeys(patterns))
    return unique or [route]


@functools.cache
def _get_legacy_functions() -> list[Any]:
    # The SDK validates function names on every get_functions() call and
    # rejects names it has already seen, so it must be called once per app.
    return legacy.app.get_functions()


def _table_binding_spec(binding: Any) -> Optional[dict[str, Any]]:
    data = binding.get_dict_repr()
    if data.get("type") != "table":
        return None
    is_input = str(getattr(data.get("direction"), "name", "")) == "IN"
    return {
        "kind": "table_input" if is_input else "table_output",
        "arg_name": data.get("name"),
        "table_name": data.get("tableName"),
        "partition_key": data.get("partitionKey"),
        "row_key": data.get("rowKey"),
        "filter": data.get("filter"),
    }


def _read_route_specs() -> list[dict[str, Any]]:
    """Derive the HTTP routes from the Azure Functions app metadata.

    Functions registered on the app directly or through Blueprints are
    discovered the same way, regardless of the module they are defined in.
    """
    specs: list[dict[str, Any]] = []
    for fn in _get_legacy_functions():
        if not fn.is_http_function():
            continue
        trigger = fn.get_trigger().get_dict_repr()
        bindings = [
            spec
            for spec in (_table_binding_spec(b) for b in fn.get_bindings())
            if spec is not None
        ]
        specs.append(
            {
                "function_name": fn.get_function_name(),
                "handler": fn.get_user_function(),
                "route": str(trigger.get("route") or "").strip(),
                "methods": trigger.get("methods") or ["GET", "POST"],
                "bindings": bindings,
            }
        )
    return specs


def _build_endpoint(handler: Callable, bindings: list[dict[str, Any]]):
    async def endpoint(request: Request):
        path_params = dict(request.path_params)
        body = await request.body()
        req = _make_http_request(request, path_params, body)
        kwargs: dict[str, Any] = {}
        outputs: list[tuple[dict[str, Any], _OutBinding]] = []

        for binding in bindings:
            arg_name = binding.get("arg_name")
            if not arg_name:
                continue
            kind = binding.get("kind")
            if kind == "table_input":
                kwargs[arg_name] = _query_table_rows(binding, path_params)
            elif kind == "table_output":
                out = _OutBinding()
                kwargs[arg_name] = out
                outputs.append((binding, out))

        result = handler(req, **kwargs)

        for binding, out in outputs:
            _flush_table_output(binding, out)

        return _to_fastapi_response(result)

    return endpoint


def _queue_message_to_legacy(content: str):
    class _Msg(func.QueueMessage):
        def __init__(self, value: str):
            self._value = value.encode("utf-8")

        def get_body(self) -> bytes:
            return self._value

    return _Msg(content)


def _process_notification_message(msg: Any) -> None:
    payload = msg.content or ""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if payload:
        # The SDK queue client already applies TextBase64DecodePolicy, so
        # `payload` should be plain JSON text here. Still route through the
        # legacy Receiver's own resilient decode (JSON first, base64 fallback)
        # so both consumption paths (classic host trigger / FastAPI polling)
        # behave identically even if the message wasn't decoded upstream.
        out = _OutBinding()
        receiver.Receiver(_queue_message_to_legacy(payload), out)
        _flush_table_output({"table_name": TABLE_NAME}, out)
    queue = storage.get_queue_client(NOTIFICATION_QUEUE_NAME)
    queue.delete_message(msg.id, msg.pop_receipt)


def _poll_notification_queue() -> None:
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=NOTIF_QUEUE_CONCURRENCY,
        thread_name_prefix="orchestrator-notification",
    )
    in_flight: set[concurrent.futures.Future] = set()
    while not _STOP_EVENT.is_set():
        try:
            queue = storage.get_queue_client(NOTIFICATION_QUEUE_NAME)

            done = {f for f in in_flight if f.done()}
            in_flight -= done

            for f in done:
                try:
                    f.result()
                except Exception as exc:  # noqa: PERF203
                    logging.warning("Orchestrator queue message failed: %s", exc)

            available = max(0, NOTIF_QUEUE_CONCURRENCY - len(in_flight))
            if available <= 0:
                _STOP_EVENT.wait(0.2)
                continue

            messages = list(
                queue.receive_messages(
                    messages_per_page=min(NOTIF_QUEUE_BATCH_SIZE, available),
                    visibility_timeout=NOTIF_QUEUE_VISIBILITY_TIMEOUT,
                )
            )
            if not messages:
                _STOP_EVENT.wait(POLL_SECONDS)
                continue

            for msg in messages:
                in_flight.add(executor.submit(_process_notification_message, msg))
        except Exception as exc:
            logging.warning("Orchestrator queue poll failed: %s", exc)
            _STOP_EVENT.wait(max(POLL_SECONDS, 5.0))

    executor.shutdown(wait=False, cancel_futures=True)


def _run_scheduler_timers() -> None:
    class _Timer(func.TimerRequest):
        @property
        def past_due(self) -> bool:
            return False

    def _wait_until_next_minute() -> None:
        """Sleep until the next minute boundary so now.second ≈ 0 on each call,
        matching the Azure cron format where the first field is seconds."""
        now = datetime.now()
        secs = 60 - now.second - now.microsecond / 1_000_000
        if secs < 1:
            secs += 60
        _STOP_EVENT.wait(secs)

    _wait_until_next_minute()

    while not _STOP_EVENT.is_set():
        try:
            schedules.scheduler_engine(_Timer())
        except Exception:
            logging.exception("Scheduler engine failed")
        try:
            workers.worker_cleanup(_Timer())
        except Exception:
            logging.exception("Worker cleanup failed")
        # Recalculate sleep to next minute boundary after each run,
        # so drift from execution time never accumulates.
        _wait_until_next_minute()


def _start_background_workers() -> None:
    _STOP_EVENT.clear()
    for target, name in (
        (_poll_notification_queue, "orchestrator-queue-poller"),
        (_run_scheduler_timers, "orchestrator-scheduler"),
    ):
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        _BACKGROUND_THREADS.append(thread)


def _stop_background_workers() -> None:
    _STOP_EVENT.set()
    for thread in _BACKGROUND_THREADS:
        thread.join(timeout=1.0)
    _BACKGROUND_THREADS.clear()


BANNER = r"""
\033[1;36m
   ______  __                   ______      ___
 .' ___  |[  |                 |_   _ `.  .'   `.
/ .'   \_| | |  .--.   __   _    | | `. \/  .-.  \
| |        | |/ .'`\ \[  | | |   | |  | || |   | |
\ `.___.'\ | || \__. | | \_/ |, _| |_.' /\  `-'  /
 `.____ .'[___]'.__.'  '.__.'_/|______.'  `.___.'
\033[0m
  service  : ORCHESTRATOR
  role     : Event-driven orchestration engine
  port     : 80
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_runtime_logging()
    print(BANNER.replace("\\033", "\033"))
    _start_background_workers()
    try:
        yield
    finally:
        _stop_background_workers()


app = FastAPI(
    title="CloudDO Orchestrator", version="fastapi-migration", lifespan=lifespan
)


@app.get("/admin/warmup")
def _admin_warmup() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/admin/host/status")
def _admin_host_status() -> JSONResponse:
    return JSONResponse({"state": "Running"})


app.add_api_websocket_route(
    f"{API_PREFIX}/ws/logs/{{partitionKey}}/{{execId}}",
    stream_execution_logs,
    name="stream_execution_logs",
)
app.add_api_websocket_route(
    f"{API_PREFIX}/ws/executions/{{partitionKey}}",
    stream_executions,
    name="stream_executions",
)


for spec in _read_route_specs():
    route = spec["route"]
    if not route:
        continue
    handler = spec["handler"]
    methods = [_sanitize_method(m) for m in spec["methods"]]
    endpoint = _build_endpoint(handler, spec["bindings"])
    for pattern in _translate_route_pattern(route):
        app.add_api_route(
            f"{API_PREFIX}/{pattern.lstrip('/')}",
            endpoint=endpoint,
            methods=methods,
            name=spec["function_name"],
        )
