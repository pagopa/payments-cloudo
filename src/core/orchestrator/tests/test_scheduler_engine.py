"""Tests for how the scheduler claims a run before dispatching it."""

import json
from unittest.mock import MagicMock

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableEntity, UpdateMode
from core.config import TABLE_NAME, TABLE_SCHEDULES, TABLE_WORKERS_SCHEMAS
from routes import schedules

from core import storage


def schedule_row(**overrides) -> TableEntity:
    row = TableEntity(
        PartitionKey="Schedule",
        RowKey="sched-1",
        name="Nightly",
        cron="* * * * * *",
        runbook="nightly.sh",
        enabled=True,
        **overrides,
    )
    row._metadata = {"etag": 'W/"etag-1"'}
    return row


@pytest.fixture
def engine(monkeypatch):
    tables = {
        TABLE_SCHEDULES: MagicMock(),
        TABLE_NAME: MagicMock(),
        TABLE_WORKERS_SCHEMAS: MagicMock(),
    }
    tables[TABLE_WORKERS_SCHEMAS].query_entities.return_value = []
    queue = MagicMock()
    monkeypatch.setattr(storage, "get_table_client", lambda name: tables[name])
    monkeypatch.setattr(storage, "get_queue_client", lambda name: queue)
    return tables[TABLE_SCHEDULES], tables[TABLE_NAME], queue


def test_claims_with_etag_and_writes_only_last_run_before_dispatching(engine):
    schedule_table, _, queue = engine
    schedule_table.query_entities.return_value = [schedule_row()]
    calls = MagicMock()
    calls.attach_mock(schedule_table.update_entity, "claim")
    calls.attach_mock(queue.send_message, "dispatch")

    schedules.scheduler_engine(None)

    assert [c[0] for c in calls.mock_calls] == ["claim", "dispatch"]
    kwargs = schedule_table.update_entity.call_args.kwargs
    assert set(kwargs["entity"]) == {"PartitionKey", "RowKey", "last_run"}
    assert kwargs["mode"] == UpdateMode.MERGE
    assert kwargs["etag"] == 'W/"etag-1"'
    assert kwargs["match_condition"] == MatchConditions.IfNotModified
    assert json.loads(queue.send_message.call_args.args[0])["id"] == "sched-1"


@pytest.mark.parametrize("error", [ResourceModifiedError, ResourceNotFoundError])
def test_lost_claim_does_not_dispatch_or_log_the_run(engine, error):
    schedule_table, log_table, queue = engine
    schedule_table.query_entities.return_value = [schedule_row()]
    schedule_table.update_entity.side_effect = error("claimed elsewhere")

    schedules.scheduler_engine(None)

    queue.send_message.assert_not_called()
    log_table.create_entity.assert_not_called()


def test_lost_claim_does_not_stop_the_other_schedules(engine):
    schedule_table, _, queue = engine
    other = schedule_row()
    other["RowKey"] = "sched-2"
    schedule_table.query_entities.return_value = [schedule_row(), other]
    schedule_table.update_entity.side_effect = [ResourceModifiedError("taken"), None]

    schedules.scheduler_engine(None)

    queue.send_message.assert_called_once()
    assert json.loads(queue.send_message.call_args.args[0])["id"] == "sched-2"


def test_recent_last_run_is_not_claimed(engine):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    schedule_table, _, queue = engine
    last_run = datetime.now(ZoneInfo("Europe/Rome")).isoformat()
    schedule_table.query_entities.return_value = [schedule_row(last_run=last_run)]

    schedules.scheduler_engine(None)

    schedule_table.update_entity.assert_not_called()
    queue.send_message.assert_not_called()
