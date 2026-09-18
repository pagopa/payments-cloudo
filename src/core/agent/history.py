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

# Volatile tokens that legitimately differ between otherwise-identical runs
# of the *same* failure and must be collapsed before hashing
_VOLATILE_TOKEN_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
    r"|\b[0-9a-fA-F]{7,}\b"  # long hex-only run
    r"|\b(?=[a-zA-Z0-9]*\d)(?=[a-zA-Z0-9]*[a-zA-Z])[a-zA-Z0-9]{5,}\b"  # random-looking mixed token
    r"|\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"  # IPv4
    r"|\d+"  # any remaining digit run
)

# Environment/system variables whose *value* is inherently host/container
_VOLATILE_ENV_LINE_RE = re.compile(
    r"(?im)^\s*("
    r"HOSTNAME|HOST|POD_NAME|POD_IP|CONTAINER_ID|CONTAINER_NAME|INSTANCE_ID|"
    r"WEBSITE_INSTANCE_ID|SESSION_ID|REQUEST_ID|TRACE_ID|CORRELATION_ID|"
    r"PID|PPID|PORT|RANDOM|SHLVL|_|PWD|OLDPWD|TERM_SESSION_ID"
    r")\s*=.*$"
)


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
    text = _VOLATILE_ENV_LINE_RE.sub(lambda m: f"{m.group(1)}=#", text)
    text = _VOLATILE_TOKEN_RE.sub("#", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def compute_signature(runbook: str, logs: str) -> str:
    """A stable identifier for "the same recurring failure" on a given
    runbook."""
    tail = _tail(logs, SIGNATURE_LOG_CHARS)
    normalized_logs = _normalize_error_text(tail)
    key = f"{(runbook or '').strip().lower()}::{normalized_logs}"
    signature = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    logging.debug(
        "History: computed signature=%s for runbook=%s "
        "(raw_log_tail_chars=%d, normalized_chars=%d, normalized_preview=%r)",
        signature,
        runbook,
        len(tail),
        len(normalized_logs),
        normalized_logs[:200],
    )
    return signature


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
        logging.debug("History: get_history called with empty signature, skipping")
        return None
    try:
        client = _table_client()
        if client is None:
            logging.debug(
                "History: table client unavailable (no storage connection "
                "configured); treating signature=%s as first occurrence",
                signature,
            )
            return None
        with client:
            entity = client.get_entity(
                partition_key=HISTORY_PARTITION_KEY, row_key=signature
            )
            logging.debug(
                "History: found existing entry for signature=%s "
                "(occurrence_count=%s, first_seen=%s, last_seen=%s, has_last_analysis=%s)",
                signature,
                entity.get("occurrence_count"),
                entity.get("first_seen"),
                entity.get("last_seen"),
                bool(entity.get("last_analysis")),
            )
            return dict(entity)
    except Exception as exc:
        logging.debug(
            "History: no existing entry for signature=%s (%s)", signature, exc
        )
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
        logging.debug(
            "History: record_occurrence called with empty signature, skipping"
        )
        return 0
    try:
        client = _table_client()
        if client is None:
            logging.debug(
                "History: table client unavailable, cannot record occurrence "
                "for signature=%s (exec_id=%s)",
                signature,
                exec_id,
            )
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
            logging.debug(
                "History: recorded occurrence #%d for signature=%s "
                "(runbook=%s, exec_id=%s, reused=%s, stored_last_analysis=%s)",
                occurrence_count,
                signature,
                runbook,
                exec_id,
                reused,
                "last_analysis" in new_entity,
            )
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
        logging.debug(
            "History: no last_analysis stored for prior entry, cannot reuse "
            "(occurrence_count=%s)",
            occurrence_count,
        )
        return None
    try:
        analysis = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logging.debug(
            "History: failed to parse stored last_analysis as JSON, cannot "
            "reuse: %s",
            exc,
        )
        return None
    if not isinstance(analysis, dict):
        logging.debug(
            "History: stored last_analysis is not a JSON object (type=%s), "
            "cannot reuse",
            type(analysis).__name__,
        )
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
    logging.debug(
        "History: built reused/enriched analysis (occurrence_count=%s, "
        "first_seen=%s, last_seen=%s)",
        occurrence_count,
        enriched["first_seen"],
        enriched["last_seen"],
    )
    return enriched
