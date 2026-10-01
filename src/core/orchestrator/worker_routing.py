import json
import logging
from datetime import datetime, timedelta, timezone
from random import choice


def get_active_workers(
    workers_list: list[dict], timeout_minutes: int = 5
) -> list[dict]:
    """
    Filters a list of workers, returning only those that sent an heartbeat
    within the last X minutes.

    Args:
        workers_list: List of dictionaries (Azure Table entities)
        timeout_minutes: Tolerance threshold (default 5 min)

    Returns:
        List of active workers.
    """
    if not workers_list:
        return []

    active_workers = []
    now = datetime.now(timezone.utc)
    threshold = timedelta(minutes=timeout_minutes)

    for w in workers_list:
        # 1. Safely extract LastSeen
        # Azure Table keys can be case-sensitive, check common variations
        last_seen_raw = w.get("LastSeen") or w.get("lastSeen") or w.get("last_seen")

        if not last_seen_raw:
            logging.warning(f"Worker '{w.get('RowKey')}' skipped: missing LastSeen")
            continue

        try:
            # 2. Date parsing
            # Azure Table usually saves as ISO string (e.g., '2023-10-25T10:00:00.123Z')
            # or as a native datetime object if using the Python SDK to read.
            if isinstance(last_seen_raw, datetime):
                last_seen_dt = last_seen_raw
            else:
                # Fix for 'Z' suffix which might be problematic for older fromisoformat versions
                clean_str = str(last_seen_raw).replace("Z", "+00:00")
                last_seen_dt = datetime.fromisoformat(clean_str)

            # Ensure it is timezone-aware for comparison
            if last_seen_dt.tzinfo is None:
                last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)

            # 3. Comparison
            elapsed = now - last_seen_dt

            if elapsed <= threshold:
                active_workers.append(w)
            else:
                # Log at INFO level (or DEBUG) to avoid cluttering logs
                logging.info(
                    f"Worker '{w.get('RowKey')}' is inactive (Last seen: {int(elapsed.total_seconds())}s ago)"
                )

        except Exception as e:
            logging.warning(f"Error checking worker '{w.get('RowKey')}': {e}")
            continue

    return active_workers


def worker_routing(workers, schema):
    # 1. Parse workers from binding string to list
    try:
        all_workers_list = json.loads(workers) if isinstance(workers, str) else workers
    except Exception:
        all_workers_list = []

    if not isinstance(all_workers_list, list):
        all_workers_list = []

    requested_team = (
        str(getattr(schema, "team", "default") or "default").strip().lower()
    )

    # Filter by capability and team. The default pool is shared with all teams.
    # Note: PartitionKey identifies the skill/alert type
    candidates = [
        w
        for w in all_workers_list
        if w.get("PartitionKey") == schema.worker
        and str(w.get("team") or "default").strip().lower()
        in {"default", requested_team}
    ]

    # 3. Filter Active using the helper function
    valid_workers = get_active_workers(candidates, timeout_minutes=3)

    if valid_workers:
        # 4. Load Balancing (Random Strategy)
        selected_worker = choice(valid_workers)
        target_queue = selected_worker.get("Queue")

        return target_queue

    return None
