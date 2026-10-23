"""Lookup of runbook schema rows coming from table input bindings."""

from typing import Optional


def get_entity_id(e: dict) -> str:
    return str(e.get("Id") or e.get("id") or "").strip()


def find_schema_entity(rows: list[dict], candidate_ids: list[str]) -> Optional[dict]:
    if not rows or not candidate_ids:
        return None

    if len(candidate_ids) == 1:
        target = candidate_ids[0]
        for e in rows:
            if get_entity_id(e) == target:
                return e
        return None

    entities_by_id: dict[str, dict] = {}
    for e in rows:
        sid = get_entity_id(e)
        if sid and sid not in entities_by_id:
            entities_by_id[sid] = e

    for sid in candidate_ids:
        if sid in entities_by_id:
            return entities_by_id[sid]
    return None
