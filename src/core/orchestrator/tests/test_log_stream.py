from unittest.mock import MagicMock, patch

import function_app
import log_stream
import pytest
from azure.core.exceptions import ResourceNotModifiedError
from fastapi.testclient import TestClient
from fastapi_app import app
from starlette.websockets import WebSocketDisconnect

WS_PATH = "/api/ws/logs/20260101/exec-1"
SESSION = {"username": "alice", "role": "OPERATOR", "team": "payments"}


def _row(
    status, log, team="payments", row_key="r1", requested_at="2026-01-01 10:00:00"
):
    return {
        "PartitionKey": "20260101",
        "RowKey": row_key,
        "ExecId": "exec-1",
        "Status": status,
        "RequestedAt": requested_at,
        "team": team,
        "Log": log,
    }


@pytest.fixture
def client():
    with (
        patch("fastapi_app._start_background_workers"),
        patch("fastapi_app._stop_background_workers"),
        patch.object(log_stream, "POLL_SECONDS", 0.01),
    ):
        with TestClient(app) as c:
            yield c


@pytest.fixture
def valid_token():
    with patch.object(
        function_app, "_verify_session_token", return_value=(True, SESSION)
    ):
        yield


def _collect(ws):
    messages = []
    while True:
        try:
            messages.append(ws.receive_json())
        except WebSocketDisconnect as e:
            return messages, e.code


def test_rejects_invalid_token(client):
    with patch.object(function_app, "_verify_session_token", return_value=(False, {})):
        with client.websocket_connect(WS_PATH) as ws:
            ws.send_json({"type": "auth", "token": "bad"})
            messages, code = _collect(ws)
    assert messages[0]["type"] == "error"
    assert code == log_stream.CLOSE_UNAUTHORIZED


def test_rejects_non_auth_first_message(client, valid_token):
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_json({"type": "hello"})
        _, code = _collect(ws)
    assert code == log_stream.CLOSE_UNAUTHORIZED


def test_streams_appends_until_terminal_status(client, valid_token):
    rows = [
        _row("running", "line1\n"),
        _row("running", "line1\n"),
        _row("running", "line1\nline2\n"),
        _row("succeeded", "line1\nline2\ndone\n", row_key="r2"),
    ]
    with patch.object(log_stream, "_fetch_latest_entity", side_effect=rows):
        with client.websocket_connect(WS_PATH) as ws:
            ws.send_json({"type": "auth", "token": "ok"})
            messages, code = _collect(ws)

    assert code == 1000
    assert [m["type"] for m in messages] == [
        "entry",
        "replace",
        "append",
        "entry",
        "append",
        "end",
    ]
    assert messages[0]["entry"]["Status"] == "running"
    assert "Log" not in messages[0]["entry"]
    assert messages[1]["log"] == "line1\n"
    assert messages[2]["data"] == "line2\n"
    assert messages[3]["entry"]["Status"] == "succeeded"
    assert messages[4]["data"] == "done\n"
    assert messages[5]["status"] == "succeeded"


def test_replaces_log_when_not_a_prefix(client, valid_token):
    rows = [_row("running", "partial"), _row("failed", "rewritten", row_key="r2")]
    with patch.object(log_stream, "_fetch_latest_entity", side_effect=rows):
        with client.websocket_connect(WS_PATH) as ws:
            ws.send_json({"type": "auth", "token": "ok"})
            messages, _ = _collect(ws)
    replaces = [m for m in messages if m["type"] == "replace"]
    assert [m["log"] for m in replaces] == ["partial", "rewritten"]


def test_forbidden_for_other_team(client, valid_token):
    with patch.object(
        log_stream, "_fetch_latest_entity", return_value=_row("running", "x", "other")
    ):
        with client.websocket_connect(WS_PATH) as ws:
            ws.send_json({"type": "auth", "token": "ok"})
            messages, code = _collect(ws)
    assert code == log_stream.CLOSE_FORBIDDEN
    assert messages == [{"type": "error", "error": "Execution belongs to another team"}]


def test_admin_include_all_teams_can_view_other_team(client):
    admin = {"username": "root", "role": "ADMIN", "team": "platform"}
    with (
        patch.object(function_app, "_verify_session_token", return_value=(True, admin)),
        patch.object(
            log_stream,
            "_fetch_latest_entity",
            return_value=_row("succeeded", "x", "other"),
        ),
    ):
        with client.websocket_connect(WS_PATH) as ws:
            ws.send_json({"type": "auth", "token": "ok", "includeAllTeams": True})
            messages, code = _collect(ws)
    assert code == 1000
    assert messages[-1] == {"type": "end", "status": "succeeded"}


def test_not_found(client, valid_token):
    with patch.object(log_stream, "_fetch_latest_entity", return_value=None):
        with client.websocket_connect(WS_PATH) as ws:
            ws.send_json({"type": "auth", "token": "ok"})
            _, code = _collect(ws)
    assert code == log_stream.CLOSE_NOT_FOUND


def test_log_tail_uses_etag_for_blob_refs():
    blob = MagicMock()
    downloader = MagicMock()
    downloader.properties.etag = "etag-1"
    downloader.readall.return_value = b"hello"
    blob.download_blob.side_effect = [downloader, ResourceNotModifiedError()]
    service = MagicMock()
    service.get_blob_client.return_value = blob
    ref = "blobref://runbook-logs/20260101/exec-1/RUNNING_exec-1.log"

    tail = log_stream._LogTail()
    with patch.object(function_app, "_get_blob_service", return_value=service):
        assert tail.read(ref) == "hello"
        assert tail.read(ref) is None

    second_call = blob.download_blob.call_args_list[1]
    assert second_call.kwargs["etag"] == "etag-1"
    assert "etag" not in blob.download_blob.call_args_list[0].kwargs


def test_log_tail_returns_inline_text():
    assert log_stream._LogTail().read("plain log") == "plain log"
    assert log_stream._LogTail().read(None) == ""


def test_latest_log_entity_prefers_priority_then_time_then_rowkey():
    rows = [
        _row("running", "", row_key="a", requested_at="2026-01-01 10:00:05"),
        _row("accepted", "", row_key="b", requested_at="2026-01-01 10:00:09"),
        _row("succeeded", "", row_key="c", requested_at="2026-01-01 10:00:01"),
    ]
    assert function_app._latest_log_entity(rows)["RowKey"] == "c"

    same_priority = [
        _row("running", "", row_key="a", requested_at="2026-01-01 10:00:05"),
        _row("running", "", row_key="b", requested_at="2026-01-01 10:00:01"),
        _row("running", "", row_key="c", requested_at=""),
    ]
    assert function_app._latest_log_entity(same_priority)["RowKey"] == "a"

    tie = [_row("running", "", row_key="a"), _row("running", "", row_key="b")]
    assert function_app._latest_log_entity(tie)["RowKey"] == "b"
    assert function_app._latest_log_entity([]) is None


class _Entity(dict):
    def __init__(self, metadata, **values):
        super().__init__(**values)
        self.metadata = metadata


def _changed(row_key, exec_id, etag, ts):
    return _Entity({"etag": etag, "timestamp": ts}, RowKey=row_key, ExecId=exec_id)


def test_executions_stream_sends_only_changed_executions(client, valid_token):
    from datetime import datetime, timedelta, timezone

    t0 = datetime.now(timezone.utc)
    ticks = [
        [_changed("r1", "e1", "a", t0), _changed("r2", "e2", "b", t0)],
        [_changed("r1", "e1", "a", t0), _changed("r2", "e2", "b", t0)],
        [
            _changed("r1", "e1", "a2", t0 + timedelta(seconds=9)),
            _changed("r2", "e2", "b", t0),
        ],
    ]
    since_calls = []

    def fake_changed(pk, since):
        since_calls.append(since)
        return ticks.pop(0) if ticks else []

    def fake_latest(pk, exec_ids):
        return [
            {"ExecId": e, "Status": "running", "team": "payments", "Log": "x"}
            for e in sorted(exec_ids)
        ] + [{"ExecId": "hidden", "Status": "running", "team": "other"}]

    with (
        patch.object(log_stream, "EXECUTIONS_POLL_SECONDS", 0.01),
        patch.object(log_stream, "_fetch_changed_rows", side_effect=fake_changed),
        patch.object(
            log_stream, "_fetch_latest_for_execs", side_effect=fake_latest
        ) as latest,
        client.websocket_connect("/api/ws/executions/20260101") as ws,
    ):
        ws.send_json({"type": "auth", "token": "ok"})
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "upsert"
    assert [i["ExecId"] for i in first["items"]] == ["e1", "e2"]
    assert [i["ExecId"] for i in second["items"]] == ["e1"]
    assert [c.args[1] for c in latest.call_args_list[:2]] == [{"e1", "e2"}, {"e1"}]
    # Window advances to the newest Timestamp seen minus the overlap.
    overlap = timedelta(seconds=log_stream.EXECUTIONS_OVERLAP_SECONDS)
    assert since_calls[0] < t0 - overlap
    assert since_calls[1] == t0 - overlap


def test_executions_stream_requires_auth(client):
    with (
        patch.object(function_app, "_verify_session_token", return_value=(False, {})),
        client.websocket_connect("/api/ws/executions/20260101") as ws,
    ):
        ws.send_json({"type": "auth", "token": "bad"})
        _, code = _collect(ws)
    assert code == log_stream.CLOSE_UNAUTHORIZED
