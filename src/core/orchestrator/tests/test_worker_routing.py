from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from worker_routing import get_active_workers, worker_routing


def test_get_active_workers_empty():
    assert get_active_workers([]) == []
    assert get_active_workers(None) == []


def test_get_active_workers_filtering():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=1)).isoformat()
    old = (now - timedelta(minutes=10)).isoformat()
    naive_recent = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")

    workers = [
        {"RowKey": "w1", "LastSeen": recent, "Queue": "q1"},
        {"RowKey": "w2", "lastSeen": old, "Queue": "q2"},
        {"RowKey": "w3", "last_seen": naive_recent, "Queue": "q3"},
        {
            "RowKey": "w4",
            "LastSeen": now - timedelta(seconds=30),
            "Queue": "q4",
        },  # datetime object
        {"RowKey": "w5", "Queue": "q5"},  # missing LastSeen
        {"RowKey": "w6", "LastSeen": "invalid-date", "Queue": "q6"},  # invalid date
    ]

    active = get_active_workers(workers, timeout_minutes=5)
    active_keys = [w["RowKey"] for w in active]
    assert "w1" in active_keys
    assert "w3" in active_keys
    assert "w4" in active_keys
    assert "w2" not in active_keys
    assert "w5" not in active_keys
    assert "w6" not in active_keys


def test_worker_routing_success():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=1)).isoformat()

    schema = MagicMock()
    schema.worker = "my-worker"
    schema.team = "payments"

    workers = [
        {
            "PartitionKey": "my-worker",
            "RowKey": "w1",
            "LastSeen": recent,
            "Queue": "q-payments",
            "team": "payments",
        },
        {
            "PartitionKey": "my-worker",
            "RowKey": "w2",
            "LastSeen": recent,
            "Queue": "q-default",
            "team": "default",
        },
        {
            "PartitionKey": "other-worker",
            "RowKey": "w3",
            "LastSeen": recent,
            "Queue": "q-other",
            "team": "payments",
        },
        {
            "PartitionKey": "my-worker",
            "RowKey": "w4",
            "LastSeen": recent,
            "Queue": "q-other-team",
            "team": "orders",
        },
    ]

    target = worker_routing(workers, schema)
    assert target in ["q-payments", "q-default"]


def test_worker_routing_json_string():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=1)).isoformat()

    schema = MagicMock()
    schema.worker = "my-worker"
    schema.team = "default"

    import json

    workers_json = json.dumps(
        [
            {
                "PartitionKey": "my-worker",
                "RowKey": "w1",
                "LastSeen": recent,
                "Queue": "q1",
                "team": "default",
            }
        ]
    )

    target = worker_routing(workers_json, schema)
    assert target == "q1"


def test_worker_routing_invalid_input():
    schema = MagicMock()
    schema.worker = "my-worker"
    assert worker_routing("invalid json", schema) is None
    assert worker_routing(None, schema) is None
    assert worker_routing([], schema) is None
