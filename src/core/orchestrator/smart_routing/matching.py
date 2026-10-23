"""Rule matching: does an alert/execution context satisfy a rule's `when`?"""

import logging
from typing import Any, Optional

# Statuses routed by default (rules opt in to others with finalOnly=false).
FINAL_STATUSES = {"succeeded", "error", "failed", "timeout", "routed"}


def sev_to_num(sev: Optional[str]) -> Optional[int]:
    """
    Normalize Azure severity "Sev0-Sev4" to integer 0..4.
    Lower is more critical (0=Critical, 4=Informational).
    """
    if not sev:
        return None
    s = str(sev).strip().lower()
    if s.startswith("sev"):
        s = s.replace("sev", "")
    try:
        return int(s)
    except Exception:
        return None


def _eq(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return False
    return str(a).strip().lower() == str(b).strip().lower()


def _starts(a: Optional[str], prefix: Optional[str]) -> bool:
    if a is None or prefix is None:
        return False
    return str(a).lower().startswith(str(prefix).lower())


def _subscription_from_resource_id(resource_id: Optional[str]) -> Optional[str]:
    try:
        parts = (resource_id or "").split("/")
        return (
            parts[2] if len(parts) > 2 and parts[1].lower() == "subscriptions" else None
        )
    except Exception:
        return None


def _mismatch(exec_id: Any, message: str) -> bool:
    logging.debug(f"[{exec_id}] Routing mismatch: {message}")
    return False


def _status_matches(when: dict[str, Any], status: str, exec_id: Any) -> bool:
    final_only = when.get("finalOnly", True)
    if final_only and status not in FINAL_STATUSES:
        return _mismatch(
            exec_id, f"status '{status}' not in final_statuses and finalOnly=True"
        )
    if "statusIn" in when:
        allowed = {str(x).strip().lower() for x in (when.get("statusIn") or [])}
        if allowed and status not in allowed:
            return _mismatch(exec_id, f"status '{status}' not in statusIn {allowed}")
    return True


def _field_equals(
    when: dict[str, Any], ctx: dict[str, Any], field: str, exec_id: Any
) -> bool:
    if field in when and not _eq(ctx.get(field), when[field]):
        return _mismatch(exec_id, f"{field} '{ctx.get(field)}' != '{when[field]}'")
    return True


def _attributes_match(when: dict[str, Any], ctx: dict[str, Any], exec_id: Any) -> bool:
    for field in ("resourceId", "resourceGroup", "resourceName"):
        if not _field_equals(when, ctx, field, exec_id):
            return False
    if "subscriptionId" in when:
        sub = _subscription_from_resource_id(ctx.get("resourceId"))
        if not _eq(sub, when["subscriptionId"]):
            return _mismatch(
                exec_id, f"subscriptionId '{sub}' != '{when['subscriptionId']}'"
            )
    if not _field_equals(when, ctx, "namespace", exec_id):
        return False
    if "schemaName" in when:
        logging.debug(
            f"[{exec_id}] Routing check: schemaName '{ctx.get('schemaName')}' != '{when['schemaName']}'"
        )
        if not _field_equals(when, ctx, "schemaName", exec_id):
            return False
    if "oncall" in when:
        if not _eq(str(ctx.get("oncall") or ""), str(when["oncall"])):
            return _mismatch(
                exec_id, f"oncall '{ctx.get('oncall')}' != '{when['oncall']}'"
            )
    if "resourceGroupPrefix" in when:
        if not _starts(ctx.get("resourceGroup"), when["resourceGroupPrefix"]):
            return _mismatch(
                exec_id,
                f"resourceGroup '{ctx.get('resourceGroup')}' does not start with '{when['resourceGroupPrefix']}'",
            )
    return True


def _severity_matches(
    when: dict[str, Any], sev: Optional[int], status: str, exec_id: Any
) -> bool:
    if "isAlert" in when:
        raw_val = when["isAlert"]
        # Handle string "true"/"false" vs actual boolean
        if isinstance(raw_val, str):
            should_be_alert = raw_val.strip().lower() == "true"
        else:
            should_be_alert = bool(raw_val)
        # An event is considered an alert if it has a valid severity
        # OR if it's in a failure status (error/failed/timeout)
        is_alert = (sev is not None) or (status in {"failed", "error", "timeout"})
        if should_be_alert != is_alert:
            return _mismatch(
                exec_id,
                f"isAlert requirement {should_be_alert} != actual {is_alert} (sev={sev}, status={status})",
            )
    if "severityMin" in when:
        minv = sev_to_num(when["severityMin"])
        if minv is not None and (sev is None or sev < minv):
            return _mismatch(exec_id, f"severity {sev} < severityMin {minv}")
    if "severityMax" in when:
        maxv = sev_to_num(when["severityMax"])
        if maxv is not None and (sev is None or sev > maxv):
            return _mismatch(exec_id, f"severity {sev} > severityMax {maxv}")
    return True


def match_when(when: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """
    Return True if context satisfies the rule's 'when' conditions.
    All conditions are AND-ed. Supports:
      - equality: resourceId, resourceGroup, resourceName, subscriptionId,
        namespace, schemaName, oncall
      - prefix: resourceGroupPrefix
      - severity ranges: severityMin, severityMax (SevN semantics); isAlert
      - wildcard: any="*"
      - status filters: finalOnly (default True), statusIn (list of allowed statuses)
    """
    exec_id = ctx.get("execId", "unknown")
    # Wildcard catch-all: only if any is Exactly "*"
    if when.get("any") == "*":
        return True
    status = (ctx.get("status") or "").strip().lower()
    return (
        _status_matches(when, status, exec_id)
        and _attributes_match(when, ctx, exec_id)
        and _severity_matches(when, sev_to_num(ctx.get("severity")), status, exec_id)
    )


def normalize_context(raw_ctx: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize the incoming alert context to a stable key set for rule matching.
    """
    routing_info = raw_ctx.get("routing_info") or {}
    resource_info = raw_ctx.get("resource_info") or {}
    return {
        "resourceId": raw_ctx.get("resourceId"),
        "resourceGroup": raw_ctx.get("resourceGroup"),
        "resourceName": raw_ctx.get("resourceName"),
        "schemaName": raw_ctx.get("schemaName"),
        "severity": raw_ctx.get("severity"),
        "namespace": raw_ctx.get("namespace"),
        "oncall": str(raw_ctx.get("oncall") or False),
        "status": raw_ctx.get("status"),
        "execId": raw_ctx.get("execId"),
        "name": raw_ctx.get("name"),
        "id": raw_ctx.get("id"),
        "team": raw_ctx.get("team")
        or (resource_info.get("team") if isinstance(resource_info, dict) else None)
        or (routing_info.get("team") if isinstance(routing_info, dict) else None),
        "resource_info": resource_info,
        "routing_info": routing_info,
    }
