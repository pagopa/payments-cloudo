from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FailedRunbookAlert:
    """Normalized view of a failed/errored runbook execution."""

    exec_id: str
    id: str = ""
    name: str = ""
    status: str = ""
    runbook: str = ""
    run_args: str = ""
    monitor_condition: Optional[str] = None
    severity: Optional[str] = None
    initiator: Optional[str] = None
    resource_info: dict = field(default_factory=dict)
    routing_info: dict = field(default_factory=dict)
    logs: str = ""
    jsm_alias: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> "FailedRunbookAlert":
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")

        exec_id = str(payload.get("exec_id") or "").strip()
        if not exec_id:
            raise ValueError("payload.exec_id is required")

        resource_info = payload.get("resource_info")
        routing_info = payload.get("routing_info")
        schema_id = str(payload.get("id") or "").strip()

        return cls(
            exec_id=exec_id,
            id=schema_id,
            name=str(payload.get("name") or "").strip(),
            status=str(payload.get("status") or "").strip(),
            runbook=str(payload.get("runbook") or "").strip(),
            run_args=str(payload.get("run_args") or "").strip(),
            monitor_condition=payload.get("monitor_condition"),
            severity=payload.get("severity"),
            initiator=payload.get("initiator"),
            resource_info=resource_info if isinstance(resource_info, dict) else {},
            routing_info=routing_info if isinstance(routing_info, dict) else {},
            logs=str(payload.get("logs") or ""),
            # An explicit "jsm_alias" always wins; fall back to schema id,
            # then to exec_id so ad-hoc/manual calls to /analyze still work.
            jsm_alias=str(payload.get("jsm_alias") or "").strip() or schema_id,
        )

    @property
    def alias(self) -> str:
        return self.jsm_alias or self.exec_id
