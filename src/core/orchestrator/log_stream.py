"""WebSocket streaming for the executions page"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import function_app as legacy
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceNotModifiedError
from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

POLL_SECONDS = float(os.getenv("LOG_STREAM_POLL_SECONDS", "1"))
HEARTBEAT_SECONDS = float(os.getenv("LOG_STREAM_HEARTBEAT_SECONDS", "20"))
MAX_STREAM_SECONDS = float(os.getenv("LOG_STREAM_MAX_SECONDS", "3600"))
EXECUTIONS_POLL_SECONDS = float(os.getenv("EXECUTIONS_STREAM_POLL_SECONDS", "2"))
EXECUTIONS_LOOKBACK_SECONDS = 30
EXECUTIONS_OVERLAP_SECONDS = 5
_EXEC_ID_BATCH_SIZE = 15
AUTH_TIMEOUT_SECONDS = 10

LIVE_STATUSES = {"accepted", "running"}

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_AUTH_TIMEOUT = 4408

_ENTRY_COLUMNS = [
    "PartitionKey",
    "RowKey",
    "ExecId",
    "Status",
    "RequestedAt",
    "ApprovalRequired",
    "ApprovalExpiresAt",
    "Name",
    "Id",
    "Runbook",
    "Run_Args",
    "Worker",
    "Group",
    "OnCall",
    "Initiator",
    "Severity",
    "MonitorCondition",
    "ResourceInfo",
    "team",
    "Notifications",
    "Log",
]


def _odata_escape(value: str) -> str:
    return str(value or "").replace("'", "''")


def _fetch_latest_entity(partition_key: str, exec_id: str) -> Optional[dict]:
    table = legacy._get_table_client(legacy.TABLE_NAME)  # noqa: SLF001
    query_filter = (
        f"PartitionKey eq '{_odata_escape(partition_key)}' "
        f"and ExecId eq '{_odata_escape(exec_id)}'"
    )
    rows = [
        dict(e)
        for e in table.query_entities(query_filter=query_filter, select=_ENTRY_COLUMNS)
    ]
    return legacy._latest_log_entity(rows)  # noqa: SLF001


def _can_view(entity: dict, session: dict, include_all_teams: bool) -> bool:
    if include_all_teams and legacy._is_admin(session):  # noqa: SLF001
        return True
    allowed = {legacy.DEFAULT_TEAM, legacy._session_team(session)}  # noqa: SLF001
    return legacy._entity_team(entity) in allowed  # noqa: SLF001


class _LogTail:
    """Resolves a row's Log field, skipping blob downloads when unchanged."""

    def __init__(self) -> None:
        self._ref: Optional[str] = None
        self._etag: Optional[str] = None

    def read(self, log_value: Optional[str]) -> Optional[str]:
        """Return the full log text, or None if the blob has not changed."""
        parsed = legacy._parse_blob_ref(log_value)  # noqa: SLF001
        if not parsed:
            self._ref, self._etag = None, None
            return log_value or ""

        if log_value != self._ref:
            self._ref, self._etag = log_value, None

        container, blob_name = parsed
        blob = legacy._get_blob_service(  # noqa: SLF001
            legacy.STORAGE_CONN
        ).get_blob_client(container=container, blob=blob_name)
        kwargs: dict[str, Any] = {}
        if self._etag:
            kwargs = {"etag": self._etag, "match_condition": MatchConditions.IfModified}
        try:
            downloader = blob.download_blob(**kwargs)
        except ResourceNotModifiedError:
            return None
        except HttpResponseError as e:
            if e.status_code == 304:
                return None
            raise
        self._etag = downloader.properties.etag
        return downloader.readall().decode("utf-8", errors="replace")


async def _authenticate(websocket: WebSocket) -> Optional[tuple[dict, bool]]:
    try:
        message = await asyncio.wait_for(
            websocket.receive_json(), timeout=AUTH_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        await _close_with_error(websocket, CLOSE_AUTH_TIMEOUT, "Authentication timeout")
        return None
    except (ValueError, KeyError):
        await _close_with_error(websocket, CLOSE_UNAUTHORIZED, "Invalid auth message")
        return None

    if not isinstance(message, dict):
        message = {}
    ok, session = legacy._verify_session_token(  # noqa: SLF001
        str(message.get("token") or "")
    )
    if message.get("type") != "auth" or not ok:
        await _close_with_error(
            websocket,
            CLOSE_UNAUTHORIZED,
            "Unauthorized: Missing or invalid credentials",
        )
        return None
    return session, legacy._as_bool(message.get("includeAllTeams"))  # noqa: SLF001


async def _close_with_error(websocket: WebSocket, code: int, error: str) -> None:
    try:
        await websocket.send_json({"type": "error", "error": error})
        await websocket.close(code=code)
    except Exception:
        pass


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    """Drain client frames until the client goes away."""
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
    except Exception:
        return


class _StreamContext:
    def __init__(
        self, websocket: WebSocket, session: dict, include_all_teams: bool
    ) -> None:
        self.websocket = websocket
        self.session = session
        self.include_all_teams = include_all_teams
        self.started = self.last_sent = time.monotonic()
        self._disconnected = asyncio.create_task(_wait_for_disconnect(websocket))

    @property
    def disconnected(self) -> bool:
        return self._disconnected.done()

    def can_view(self, entity: dict) -> bool:
        return _can_view(entity, self.session, self.include_all_teams)

    async def send(self, payload: dict) -> None:
        await self.websocket.send_text(
            json.dumps(payload, ensure_ascii=False, default=str)
        )
        self.last_sent = time.monotonic()

    async def idle(self, seconds: float) -> None:
        """Heartbeat if due, then wait until the next poll or a disconnect."""
        if time.monotonic() - self.last_sent >= HEARTBEAT_SECONDS:
            await self.send({"type": "ping"})
        await asyncio.wait({self._disconnected}, timeout=seconds)

    def cancel(self) -> None:
        self._disconnected.cancel()


async def _serve(websocket: WebSocket, log_prefix: str, body) -> None:
    """Accept, authenticate, then run ``body(ctx)`` until it returns or the
    client goes away."""
    await websocket.accept()
    try:
        auth = await _authenticate(websocket)
    except WebSocketDisconnect:
        return
    if auth is None:
        return

    ctx = _StreamContext(websocket, *auth)
    try:
        await body(ctx)
    except WebSocketDisconnect:
        pass
    except Exception:
        logging.exception(f"{log_prefix} stream failed")
        await _close_with_error(websocket, 1011, "Stream failed")
    finally:
        ctx.cancel()


async def stream_execution_logs(
    websocket: WebSocket, partitionKey: str, execId: str
) -> None:
    tail = _LogTail()
    sent_log: Optional[str] = None
    sent_entry_key: Optional[tuple] = None

    async def body(ctx: _StreamContext) -> None:
        nonlocal sent_log, sent_entry_key
        while not ctx.disconnected:
            entity = await run_in_threadpool(_fetch_latest_entity, partitionKey, execId)
            if entity is None:
                await _close_with_error(websocket, CLOSE_NOT_FOUND, "Entity not found")
                return
            if not ctx.can_view(entity):
                await _close_with_error(
                    websocket, CLOSE_FORBIDDEN, "Execution belongs to another team"
                )
                return

            entry = {k: v for k, v in entity.items() if k != "Log"}
            entry_key = (entry.get("RowKey"), entry.get("Status"))
            if entry_key != sent_entry_key:
                await ctx.send({"type": "entry", "entry": entry})
                sent_entry_key = entry_key

            text = await run_in_threadpool(tail.read, entity.get("Log"))
            if text is not None and text != sent_log:
                if sent_log is not None and text.startswith(sent_log):
                    appended = text[len(sent_log):]  # fmt: skip
                    await ctx.send({"type": "append", "data": appended})
                else:
                    await ctx.send({"type": "replace", "log": text})
                sent_log = text

            status = str(entity.get("Status") or "").strip().lower()
            if status not in LIVE_STATUSES:
                await ctx.send({"type": "end", "status": status})
                await websocket.close(code=1000)
                return

            if time.monotonic() - ctx.started >= MAX_STREAM_SECONDS:
                await websocket.close(code=1000, reason="Max stream duration reached")
                return
            await ctx.idle(POLL_SECONDS)

    await _serve(websocket, f"[{execId}] [LogStream]", body)


def _fetch_changed_rows(partition_key: str, since: datetime) -> list[Any]:
    table = legacy._get_table_client(legacy.TABLE_NAME)  # noqa: SLF001
    return list(
        table.query_entities(
            query_filter="PartitionKey eq @pk and Timestamp ge @since",
            parameters={"pk": partition_key, "since": since},
            select=["RowKey", "ExecId", "Timestamp"],
        )
    )


def _fetch_latest_for_execs(partition_key: str, exec_ids: set[str]) -> list[dict]:
    table = legacy._get_table_client(legacy.TABLE_NAME)  # noqa: SLF001
    grouped: dict[str, list[dict]] = {}
    ids = sorted(exec_ids)
    for i in range(0, len(ids), _EXEC_ID_BATCH_SIZE):
        batch = ids[i:i + _EXEC_ID_BATCH_SIZE]  # fmt: skip
        params: dict[str, Any] = {"pk": partition_key}
        params.update({f"e{j}": exec_id for j, exec_id in enumerate(batch)})
        exec_filter = " or ".join(f"ExecId eq @e{j}" for j in range(len(batch)))
        for e in table.query_entities(
            # The SDK substitutes @params on whitespace-separated tokens, so the
            # parentheses must not touch them.
            query_filter=f"PartitionKey eq @pk and ( {exec_filter} )",
            parameters=params,
            select=_ENTRY_COLUMNS,
        ):
            grouped.setdefault(str(e.get("ExecId") or ""), []).append(dict(e))

    items = []
    for rows in grouped.values():
        latest = legacy._latest_log_entity(rows)  # noqa: SLF001
        try:
            items.append(legacy._hydrate_log_field(latest))  # noqa: SLF001
        except Exception as e:
            logging.error(f"Failed to hydrate log item {latest.get('RowKey')}: {e}")
            items.append(latest)
    return items


async def stream_executions(websocket: WebSocket, partitionKey: str) -> None:
    # Start a bit in the past: rows written between the client's initial query
    # and this subscription are re-sent (the client merge is idempotent)
    since = datetime.now(timezone.utc) - timedelta(seconds=EXECUTIONS_LOOKBACK_SECONDS)
    seen: dict[str, Any] = {}

    async def body(ctx: _StreamContext) -> None:
        nonlocal since, seen
        while not ctx.disconnected:
            rows = await run_in_threadpool(_fetch_changed_rows, partitionKey, since)
            changed: set[str] = set()
            newest = since
            current: dict[str, Any] = {}
            for e in rows:
                etag = e.metadata.get("etag")
                current[e["RowKey"]] = etag
                if seen.get(e["RowKey"]) != etag and e.get("ExecId"):
                    changed.add(str(e["ExecId"]))
                ts = e.metadata.get("timestamp")
                if ts and ts > newest:
                    newest = ts
            # Re-scan a small window so same-second writes are never skipped;
            # `seen` filters the rows already sent
            since = newest - timedelta(seconds=EXECUTIONS_OVERLAP_SECONDS)
            seen = current

            if changed:
                items = await run_in_threadpool(
                    _fetch_latest_for_execs, partitionKey, changed
                )
                items = [item for item in items if ctx.can_view(item)]
                if items:
                    await ctx.send({"type": "upsert", "items": items})

            await ctx.idle(EXECUTIONS_POLL_SECONDS)

    await _serve(websocket, f"[{partitionKey}] [ExecutionsStream]", body)
