import concurrent.futures
import functools
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

import azure.functions as func
import function_app as legacy
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

API_PREFIX = os.getenv("API_PREFIX", "/api")
POLL_SECONDS = float(os.getenv("FASTAPI_QUEUE_POLL_SECONDS", "2"))
HEARTBEAT_SECONDS = int(os.getenv("FASTAPI_HEARTBEAT_SECONDS", "60"))
QUEUE_CONCURRENCY = max(1, int(os.getenv("FASTAPI_QUEUE_CONCURRENCY", "4")))
QUEUE_VISIBILITY_TIMEOUT = int(os.getenv("FASTAPI_QUEUE_VISIBILITY_TIMEOUT", "3600"))
QUEUE_BATCH_SIZE = max(1, int(os.getenv("FASTAPI_QUEUE_BATCH_SIZE", "8")))

# Renew the message lease well before it expires, otherwise long-running
# runbooks become visible again and get picked up a second time.
QUEUE_VISIBILITY_RENEW_SECONDS = max(
    1,
    int(
        os.getenv(
            "FASTAPI_QUEUE_VISIBILITY_RENEW_SECONDS",
            str(max(1, QUEUE_VISIBILITY_TIMEOUT // 2)),
        )
    ),
)

_STOP_EVENT = threading.Event()
_BACKGROUND_THREADS: list[threading.Thread] = []
_QUEUE_CLIENTS: dict[tuple[str, str], Any] = {}


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | worker       | %(name)s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"
# Third-party SDKs are chatty at INFO (full HTTP request/response dumps);
# keep them quiet so application logs aren't drowned out.
_NOISY_LOGGERS = ("azure", "urllib3", "openai", "httpx", "httpcore")


def _configure_runtime_logging() -> None:
    """Configure structured, leveled logging for the worker service."""
    level_name = os.getenv("WORKER_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).upper()
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


def _get_queue_client(queue_name: str):
    from azure.storage.queue import (
        QueueClient,
        TextBase64DecodePolicy,
        TextBase64EncodePolicy,
    )

    conn_str = (os.getenv(legacy.STORAGE_CONNECTION) or "").strip()
    if not conn_str:
        raise ValueError(
            f"Missing storage connection string in '{legacy.STORAGE_CONNECTION}'"
        )

    key = (conn_str, queue_name)
    client = _QUEUE_CLIENTS.get(key)
    if client is None:
        client = QueueClient.from_connection_string(
            conn_str=conn_str,
            queue_name=queue_name,
            message_encode_policy=TextBase64EncodePolicy(),
            message_decode_policy=TextBase64DecodePolicy(),
        )
        _QUEUE_CLIENTS[key] = client
    return client


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
    table = legacy._get_table_client(table_name)  # noqa: SLF001
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

    table = legacy._get_table_client(table_name)  # noqa: SLF001
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


def _flush_queue_output(binding: dict[str, Any], out: _OutBinding) -> None:
    queue_name = binding.get("queue_name")
    if not queue_name:
        return

    queue_client = _get_queue_client(queue_name)
    for raw in out.values():
        if raw is None:
            continue
        msg = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        queue_client.send_message(msg)


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


def _binding_spec(binding: Any) -> Optional[dict[str, Any]]:
    data = binding.get_dict_repr()
    is_input = str(getattr(data.get("direction"), "name", "")) == "IN"
    if data.get("type") == "table":
        return {
            "kind": "table_input" if is_input else "table_output",
            "arg_name": data.get("name"),
            "table_name": data.get("tableName"),
            "partition_key": data.get("partitionKey"),
            "row_key": data.get("rowKey"),
            "filter": data.get("filter"),
        }
    if data.get("type") == "queue" and not is_input:
        return {
            "kind": "queue_output",
            "arg_name": data.get("name"),
            "queue_name": data.get("queueName"),
        }
    return None


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
            for spec in (_binding_spec(b) for b in fn.get_bindings())
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
            elif kind in {"table_output", "queue_output"}:
                out = _OutBinding()
                kwargs[arg_name] = out
                outputs.append((binding, out))

        result = handler(req, **kwargs)

        for binding, out in outputs:
            kind = binding.get("kind")
            if kind == "table_output":
                _flush_table_output(binding, out)
            elif kind == "queue_output":
                _flush_queue_output(binding, out)

        return _to_fastapi_response(result)

    return endpoint


def _queue_message_to_legacy(content: str):
    class _Msg(func.QueueMessage):
        def __init__(self, value: str):
            self._value = value.encode("utf-8")

        def get_body(self) -> bytes:
            return self._value

    return _Msg(content)


def _process_queue_message(msg: Any) -> None:
    queue = _get_queue_client(legacy.QUEUE_NAME)
    lease = {"pop_receipt": msg.pop_receipt}
    done = threading.Event()

    def _keep_invisible() -> None:
        while not done.wait(QUEUE_VISIBILITY_RENEW_SECONDS):
            try:
                updated = queue.update_message(
                    msg.id,
                    lease["pop_receipt"],
                    visibility_timeout=QUEUE_VISIBILITY_TIMEOUT,
                )
                lease["pop_receipt"] = updated.pop_receipt
            except Exception as exc:
                logging.warning(
                    "Worker queue message %s lease renewal failed: %s", msg.id, exc
                )

    renewer = threading.Thread(
        target=_keep_invisible, name=f"worker-lease-{msg.id}", daemon=True
    )
    renewer.start()
    try:
        payload = msg.content or ""
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        if payload:
            legacy.process_runbook(_queue_message_to_legacy(payload))
    finally:
        done.set()
        renewer.join()
    queue.delete_message(msg.id, lease["pop_receipt"])


def _poll_runbook_queue() -> None:
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=QUEUE_CONCURRENCY, thread_name_prefix="worker-runbook"
    )
    in_flight: set[concurrent.futures.Future] = set()
    while not _STOP_EVENT.is_set():
        try:
            queue = _get_queue_client(legacy.QUEUE_NAME)

            done = {f for f in in_flight if f.done()}
            in_flight -= done

            for f in done:
                try:
                    f.result()
                except Exception as exc:  # noqa: PERF203
                    logging.warning("Worker queue message failed: %s", exc)

            available = max(0, QUEUE_CONCURRENCY - len(in_flight))
            if available <= 0:
                _STOP_EVENT.wait(0.5)
                continue

            messages = list(
                queue.receive_messages(
                    messages_per_page=min(QUEUE_BATCH_SIZE, available),
                    visibility_timeout=QUEUE_VISIBILITY_TIMEOUT,
                )
            )
            if not messages:
                _STOP_EVENT.wait(POLL_SECONDS)
                continue

            for msg in messages:
                in_flight.add(executor.submit(_process_queue_message, msg))
        except Exception as exc:
            logging.warning("Worker queue poll failed: %s", exc)
            _STOP_EVENT.wait(max(POLL_SECONDS, 5.0))

    executor.shutdown(wait=False, cancel_futures=True)


def _run_heartbeat_timer() -> None:
    class _Timer(func.TimerRequest):
        past_due = False

    while not _STOP_EVENT.is_set():
        try:
            legacy.heartbeat_trigger(_Timer())
        except Exception as exc:
            logging.warning("Worker heartbeat trigger failed: %s", exc)
        _STOP_EVENT.wait(HEARTBEAT_SECONDS)


def _start_background_workers() -> None:
    _STOP_EVENT.clear()
    for target, name in (
        (_poll_runbook_queue, "worker-queue-poller"),
        (_run_heartbeat_timer, "worker-heartbeat"),
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
\033[1;33m
   ______  __                   ______      ___
 .' ___  |[  |                 |_   _ `.  .'   `.
/ .'   \_| | |  .--.   __   _    | | `. \/  .-.  \
| |        | |/ .'`\ \[  | | |   | |  | || |   | |
\ `.___.'\ | || \__. | | \_/ |, _| |_.' /\  `-'  /
 `.____ .'[___]'.__.'  '.__.'_/|______.'  `.___.'
\033[0m
  service  : WORKER
  role     : Runbook queue processor
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


app = FastAPI(title="CloudDO Worker", version="fastapi-migration", lifespan=lifespan)


@app.get("/admin/warmup")
def _admin_warmup() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/admin/host/status")
def _admin_host_status() -> JSONResponse:
    return JSONResponse({"state": "Running"})


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
