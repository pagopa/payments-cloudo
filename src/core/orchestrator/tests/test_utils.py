import json
from datetime import datetime, timezone

from utils import (
    _truncate_for_table,
    create_cors_response,
    format_requested_at,
    is_cron_now,
    lower_keys,
    today_partition_key,
    utc_now_iso,
    utc_now_iso_seconds,
    utc_partition_key,
)


def test_lower_keys():
    data = {"KeyOne": "VAL", "Nested": {"INNER": [1, {"SUB": 2}]}}
    lowered = lower_keys(data)
    assert lowered == {"keyone": "VAL", "nested": {"inner": [1, {"sub": 2}]}}
    assert lower_keys("string") == "string"


def test_time_utils():
    assert isinstance(format_requested_at(), str)
    assert len(today_partition_key()) == 8
    assert "T" in utc_now_iso()
    assert len(utc_partition_key()) == 8
    assert isinstance(utc_now_iso_seconds(), str)


def test_truncate_for_table():
    assert _truncate_for_table(None, 10) == ("", False)
    assert _truncate_for_table("", 10) == ("", False)
    assert _truncate_for_table("short", 10) == "short"
    assert _truncate_for_table("a" * 20, 10) == "a" * 10


def test_create_cors_response():
    resp_empty = create_cors_response()
    assert resp_empty.status_code == 200
    assert resp_empty.headers["Access-Control-Allow-Origin"] == "*"

    resp_json = create_cors_response({"message": "ok"}, status_code=201)
    assert resp_json.status_code == 201
    assert json.loads(resp_json.get_body().decode("utf-8")) == {"message": "ok"}


def test_is_cron_now():
    # Invalid parts count
    assert not is_cron_now("* * *", datetime.now())

    # Friday Oct 2, 2026, 17:00:00 (weekday = 4, in formula (4+1)%7 = 5)
    test_dt = datetime(2026, 10, 2, 17, 0, 0, tzinfo=timezone.utc)

    # 0 0 17 2 10 5
    assert is_cron_now("0 0 17 2 10 5", test_dt)
    # Wildcards
    assert is_cron_now("0 * * * * *", test_dt)
    # Step */5
    assert is_cron_now("0 */5 * * * *", test_dt)
    # List
    assert is_cron_now("0 0,30 17 * * *", test_dt)
    # Range
    assert is_cron_now("0 0 15-18 * * *", test_dt)
    # Mismatch
    assert not is_cron_now("0 15 * * * *", test_dt)
