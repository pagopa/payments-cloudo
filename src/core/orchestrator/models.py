from dataclasses import dataclass
from typing import Optional


# =========================
# Schema Model
# =========================
@dataclass
class Schema:
    id: str
    entity: Optional[dict] = None
    name: Optional[str] = None
    description: Optional[str] = None
    runbook: Optional[str] = None
    run_args: Optional[str] = None
    worker: Optional[str] = None
    oncall: Optional[str] = "false"
    monitor_condition: Optional[str] = None
    severity: Optional[str] = None
    require_approval: bool = False
    enabled: bool = True
    tags: Optional[list] = ""

    def __post_init__(self):
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Schema id must be a non-empty str")

        if not self.entity:
            raise ValueError(
                "Entity not provided: use table input binding to inject the table entity"
            )

        e = self.entity
        self.name = (e.get("name") or "").strip()
        self.description = (e.get("description") or "").strip() or None
        self.runbook = (e.get("runbook") or "").strip() or None
        self.run_args = (e.get("run_args") or "").strip() or ""
        self.worker = (e.get("worker") or "").strip() or ""
        self.oncall = (
            str(e.get("oncall", e.get("oncall", "false"))).strip().lower() or "false"
        )
        self.require_approval = (
            str(e.get("require_approval", "false")).strip().lower() == "true"
        )
        self.enabled = str(e.get("enabled", "true")).strip().lower() == "true"
        self.tags = (e.get("tags") or "").strip() or ""
