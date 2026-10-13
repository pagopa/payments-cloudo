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
    group: Optional[str] = "-"
    oncall: bool = False
    monitor_condition: Optional[str] = None
    severity: Optional[str] = None
    require_approval: bool = False
    aks_login_required: bool = False
    aks_cluster_name: Optional[str] = None
    aks_resource_group: Optional[str] = None
    aks_namespace: Optional[str] = None
    enabled: bool = True
    tags: Optional[list] = None
    team: str = "default"

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
        self.group = (e.get("group") or "-").strip() or "-"
        self.oncall = str(e.get("oncall", "false")).strip().lower() == "true"
        self.require_approval = (
            str(e.get("require_approval", "false")).strip().lower() == "true"
        )
        self.aks_login_required = (
            str(e.get("aks_login_required", "false")).strip().lower() == "true"
        )
        self.aks_cluster_name = (e.get("aks_cluster_name") or "").strip() or None
        self.aks_resource_group = (e.get("aks_resource_group") or "").strip() or None
        self.aks_namespace = (e.get("aks_namespace") or "").strip() or None
        self.enabled = str(e.get("enabled", "true")).strip().lower() == "true"
        self.tags = e.get("tags") or None
        self.team = (e.get("team") or "default").strip() or "default"


# =========================
# Schema Model
# =========================
@dataclass
class User:
    username: str
    password: str
    email: Optional[str] = None
    role: Optional[str] = "VIEWER"
    team: Optional[str] = "default"
    is_active: bool = True
    created_at: Optional[str] = None
    sso_provider: Optional[str] = None
    picture: Optional[str] = None
    api_token: Optional[str] = None

    def __post_init__(self):
        if not self.username or not isinstance(self.username, str):
            raise ValueError("Username must be a non-empty string")
        if not self.password or not isinstance(self.password, str):
            raise ValueError("Password must be a non-empty string")

        self.username = self.username.strip()
        self.password = self.password.strip()
        self.email = (self.email or "").strip() or None
        self.role = (self.role or "VIEWER").strip().upper() or "VIEWER"
        self.team = (self.team or "default").strip() or "default"
        self.is_active = bool(self.is_active)
        self.created_at = (self.created_at or "").strip() or None
        self.sso_provider = (self.sso_provider or "").strip() or None
        self.picture = (self.picture or "").strip() or None
        self.api_token = (self.api_token or "").strip() or None
