import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import utils

# =========================
# Runbook resolution history
# =========================
#
HISTORY_TABLE_NAME = "CloudoRunbookHistory"
HISTORY_PARTITION_KEY = "RunbookHistory"
DEFAULT_REGEN_THRESHOLD = int(os.getenv("AGENT_HISTORY_REGEN_THRESHOLD", "6"))
SIGNATURE_LOG_CHARS = 1500

_VOLATILE_TOKEN_RE = re.compile(r"[0-9a-fA-F]{8,}|\d+")


def regen_threshold() -> int:
    try:
        return int(
            utils.get_setting(
                "AGENT_HISTORY_REGEN_THRESHOLD", str(DEFAULT_REGEN_THRESHOLD)
            )
        )
    except ValueError:
        return DEFAULT_REGEN_THRESHOLD


def _tail(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


def _normalize_error_text(text: str) -> str:
    text = utils.sanitize_setting_value(text or "")
    text = _VOLATILE_TOKEN_RE.sub("#", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def compute_signature(runbook: str, logs: str) -> str:
    """A stable identifier for "the same recurring failure" on a given
    runbook."""
    normalized_logs = _normalize_error_text(_tail(logs, SIGNATURE_LOG_CHARS))
    key = f"{(runbook or '').strip().lower()}::{normalized_logs}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _table_client():
    from azure.data.tables import TableClient

    conn_str = os.environ.get(utils.STORAGE_CONNECTION)
    if not conn_str:
        return None
    return TableClient.from_connection_string(conn_str, table_name=HISTORY_TABLE_NAME)


def get_history(signature: str) -> Optional[dict]:
    """Return the stored history entity for this signature, or None if it
    doesn't exist yet."""
    if not signature:
        return None
    try:
        client = _table_client()
        if client is None:
            return None
        with client:
            entity = client.get_entity(
                partition_key=HISTORY_PARTITION_KEY, row_key=signature
            )
            return dict(entity)
    except Exception:
        return None


def record_occurrence(
    signature: str,
    runbook: str,
    analysis: dict,
    exec_id: str,
    reused: bool,
) -> int:
    """Upsert the history entity: bump the occurrence counter and, only when
    a fresh LLM analysis was produced (reused=False), remember it as the
    "last_analysis" to potentially reuse on future occurrences.
    """
    if not signature:
        return 0
    try:
        client = _table_client()
        if client is None:
            return 0
        with client:
            try:
                entity = client.get_entity(
                    partition_key=HISTORY_PARTITION_KEY, row_key=signature
                )
            except Exception:
                entity = None

            now = datetime.now(timezone.utc).isoformat()
            occurrence_count = (
                int(entity.get("occurrence_count", 0)) + 1 if entity else 1
            )

            new_entity = {
                "PartitionKey": HISTORY_PARTITION_KEY,
                "RowKey": signature,
                "runbook": runbook or (entity.get("runbook") if entity else ""),
                "occurrence_count": occurrence_count,
                "first_seen": (entity.get("first_seen") if entity else now) or now,
                "last_seen": now,
                "last_exec_id": exec_id,
            }

            if not reused and analysis:
                new_entity["last_analysis"] = json.dumps(analysis, ensure_ascii=False)
            elif entity and entity.get("last_analysis"):
                new_entity["last_analysis"] = entity.get("last_analysis")

            client.upsert_entity(entity=new_entity)
            return occurrence_count
    except Exception as exc:
        logging.warning(
            "Failed to record runbook history for signature=%s: %s", signature, exc
        )
        return 0


def build_reused_analysis(prior: dict, occurrence_count: int) -> Optional[dict]:
    """Build an enriched analysis dict from a prior stored analysis."""
    raw = prior.get("last_analysis") if prior else None
    if not raw:
        return None
    try:
        analysis = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(analysis, dict):
        return None

    enriched = dict(analysis)
    enriched["recurring"] = True
    enriched["occurrence_count"] = occurrence_count
    enriched["first_seen"] = prior.get("first_seen")
    enriched["last_seen"] = prior.get("last_seen")
    summary = str(enriched.get("summary") or "").strip()
    enriched["summary"] = (
        f"[Recurring issue \u2014 seen {occurrence_count}x, reusing last AI analysis] {summary}"
    ).strip()
    return enriched
