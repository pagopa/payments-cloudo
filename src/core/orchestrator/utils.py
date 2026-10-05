import json
from datetime import datetime, timezone
from typing import Any, Optional, Union
from zoneinfo import ZoneInfo

import azure.functions as func

# =========================
# UTILS Functions
# =========================


def lower_keys(obj: Any) -> Any:
    """Recursively lower-case dict keys."""
    if isinstance(obj, dict):
        return {str(k).lower(): lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [lower_keys(x) for x in obj]
    return obj


def format_requested_at() -> str:
    # Human-readable UTC timestamp for logs (e.g., 2025-09-15 12:34:56)
    return (
        datetime.now(timezone.utc)
        .astimezone(ZoneInfo("Europe/Rome"))
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def today_partition_key() -> str:
    # Compact UTC date used as PartitionKey (e.g., 20250915)
    return (
        datetime.now(timezone.utc)
        .astimezone(ZoneInfo("Europe/Rome"))
        .strftime("%Y%m%d")
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_iso_seconds() -> str:
    # Generate a UTC timestamp in ISO 8601 format with seconds precision
    return (
        datetime.now(timezone.utc)
        .astimezone(ZoneInfo("Europe/Rome"))
        .isoformat(timespec="seconds")
    )


def utc_partition_key() -> str:
    # Generate a compact UTC date for PartitionKey (e.g., 20250915)
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _truncate_for_table(
    s: Optional[str], max_chars: int
) -> Union[str, tuple[str, bool]]:
    if not s:
        return "", False
    return s if len(s) <= max_chars else (s[:max_chars])


def create_cors_response(body=None, status_code=200, mimetype="application/json"):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, x-cloudo-key, x-cloudo-user, x-Approver",
    }

    if body is None and status_code == 200:
        return func.HttpResponse(status_code=status_code, headers=headers)

    return func.HttpResponse(
        body=json.dumps(body, ensure_ascii=False)
        if isinstance(body, (dict, list))
        else body,
        status_code=status_code,
        mimetype=mimetype,
        headers=headers,
    )


def is_cron_now(cron_str: str, now: datetime) -> bool:
    """
    Very simplified cron parser for Azure 6-field cron expressions:
    {second} {minute} {hour} {day} {month} {day-of-week}
    Example: 0 */10 * * * *
    """
    try:
        parts = cron_str.split()
        if len(parts) != 6:
            return False

        # now components (Azure cron uses UTC usually, but here we use what's passed)
        # 0: second, 1: minute, 2: hour, 3: day, 4: month, 5: day-of-week (0-6, 0=Sunday)

        # We only check minute, hour, day, month, day-of-week for this simplified version
        # as the scheduler itself runs every minute (at second 0).

        # Azure TimerTrigger: {second} {minute} {hour} {day} {month} {day-of-week}
        # Sunday is 0.

        dt_parts = [
            now.second,
            now.minute,
            now.hour,
            now.day,
            now.month,
            (now.weekday() + 1) % 7,  # weekday() is 0=Monday, so +1 % 7 -> 0=Sunday
        ]

        for i, part in enumerate(parts):
            if part == "*":
                continue

            # Handle */n (step)
            if part.startswith("*/"):
                step = int(part[2:])
                if dt_parts[i] % step != 0:
                    return False
                continue

            # Handle list (e.g. 1,2,3)
            if "," in part:
                allowed_values = [int(x) for x in part.split(",")]
                if dt_parts[i] not in allowed_values:
                    return False
                continue

            # Handle range (e.g. 1-5)
            if "-" in part:
                start_range, end_range = (int(x) for x in part.split("-"))
                if not (start_range <= dt_parts[i] <= end_range):
                    return False
                continue

            # Handle single value
            if int(part) != dt_parts[i]:
                return False

        return True
    except Exception:
        return False
