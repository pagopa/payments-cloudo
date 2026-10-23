"""Audit trail persisted to the audit table."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from core.config import TABLE_AUDIT

from core import operators, storage, team_access


def log_audit(user: str, action: str, target: str, details: str = "", team: Any = None):
    """Log an action to the Audit table."""
    try:
        table_client = storage.get_table_client(TABLE_AUDIT)

        now = datetime.now(timezone.utc)
        entity = {
            "PartitionKey": now.strftime("%Y%m%d"),
            "RowKey": str(uuid.uuid4()),
            "timestamp": now.isoformat(),
            "operator": user,
            "action": action,
            "target": target,
            "details": details,
            "team": team_access.normalize_team(team)
            if team
            else operators.team_of_user(user),
        }
        table_client.create_entity(entity=entity)
    except Exception as e:
        logging.error(f"Failed to log audit: {e}")
