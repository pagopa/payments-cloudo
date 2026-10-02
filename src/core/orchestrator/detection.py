import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import azure.functions as func
from utils import lower_keys

SOURCE_AZURE_MONITOR = "azure_monitor"


# =============================================================================
# Abstract base
# =============================================================================


class AlertParser(ABC):
    """Abstract base for all alert-source parsers."""

    @abstractmethod
    def parse(self, req_or_body) -> dict[str, Any]:
        """
        Parse the incoming alert and return a standardised resource-fields dict.

        Returned keys (always present, in order):
          _raw, source, resourceName, resourceGroup, resourceId, schema_id,
          namespace, pod, deployment, horizontalpodautoscaler, job,
          monitorCondition, severity, payload
        """
        ...

    @staticmethod
    def _base_result(
        source: str,
        raw: str,
        payload: dict,
        schemaId: list[str],
        severity: str,
        condition: str,
    ) -> dict[str, Any]:
        """Return the standard result skeleton with all fields at their zero values."""
        return {
            "_raw": raw,
            "source": source,
            "resource_name": None,
            "resource_rg": None,
            "resource_id": None,
            "schema_id": schemaId,
            "namespace": None,
            "pod": None,
            "deployment": None,
            "horizontalpodautoscaler": None,
            "job": None,
            "monitorCondition": condition,
            "severity": severity,
            "payload": payload,
        }


# =============================================================================
# Azure Monitor parser
# =============================================================================


class AzureMonitorParser(AlertParser):
    """
    Parser for Azure Monitor alerts (Common Alert Schema).

    ARM resourceId resolution priority:
      1) data.essentials.alertTargetIDs (list of ARM IDs)
      2) data.alertContext.labels["microsoft.resourceid"]
      3) data.alertContext.resourceId

    Fallbacks (when no ARM ID is available):
      - resourceName from essentials.configurationItems[0] or alertContext.resourceName
      - resourceGroup from alertContext.resourceGroup
      - resourceId from alertContext.resourceId

    Kubernetes fields extracted when present:
      namespace, pod, deployment, horizontalpodautoscaler, job
    """

    def parse(self, req) -> dict[str, Any]:
        # --- raw body ---
        try:
            raw_body = req.get_body() or b""
        except Exception:
            raw_body = b""
        raw_text = raw_body.decode("utf-8", "ignore")
        try:
            parsed = json.loads(raw_text)
            compact_raw = json.dumps(parsed, separators=(",", ":"))
        except Exception:
            compact_raw = raw_text.replace("\r", "").replace("\n", "")

        # --- parsed JSON ---
        try:
            data = req.get_json() or {}
            lower = lower_keys(data)
        except ValueError:
            lower = {}
        except AttributeError as e:
            logging.warning("Failed to parse JSON body: %s - Fallback on req", e)
            lower = lower_keys(req or {})

        e = lower.get("data", {}) or {}
        essentials = e.get("essentials", {}) or {}
        ctx = e.get("alertcontext", {}) or {}
        labels = ctx.get("labels") or {}
        annotations = ctx.get("annotations") or {}

        # --- ARM resource ID ---
        candidates: list[str] = []
        alert_target_ids = essentials.get("alerttargetids") or []
        candidates.extend(x for x in alert_target_ids if isinstance(x, str))
        mrid = labels.get("microsoft.resourceid")
        if isinstance(mrid, str):
            candidates.append(mrid)
        rid = ctx.get("resourceid")
        if isinstance(rid, str):
            candidates.append(rid)

        resource_id: Optional[str] = next(
            (
                x
                for x in candidates
                if isinstance(x, str) and x.startswith("/subscriptions/")
            ),
            None,
        )

        schema_ids = extract_schema_id_from_req(req)

        resource_group: Optional[str] = None
        resource_name: Optional[str] = None

        if resource_id:
            parts_l = [p.lower() for p in resource_id.strip("/").split("/")]
            try:
                resource_group = parts_l[parts_l.index("resourcegroups") + 1]
            except Exception:
                resource_group = None
            resource_name = parts_l[-1] if parts_l else None
        else:
            config_items = essentials.get("configurationitems") or []
            if config_items and isinstance(config_items, list):
                resource_name = config_items[0]
            resource_name = (
                resource_name
                or essentials.get("targetresourcename")
                or ctx.get("resourcename")
                or labels.get("resourcename")
            )
            resource_group = (
                ctx.get("resourcegroup")
                or labels.get("resourcegroup")
                or essentials.get("targetresourcegroup")
            )
            resource_id = ctx.get("resourceid") or labels.get("resourceid")

        # --- Kubernetes fields ---
        namespace = (
            labels.get("namespace")
            or labels.get("kubernetes_namespace")
            or annotations.get("namespace")
            or annotations.get("kubernetes_namespace")
        )
        pod = (
            labels.get("pod")
            or labels.get("kubernetes_pod_name")
            or annotations.get("pod")
            or annotations.get("kubernetes_pod_name")
        )
        deployment = (
            labels.get("deployment")
            or labels.get("kubernetes_deployment")
            or annotations.get("deployment")
            or annotations.get("kubernetes_deployment")
        )
        horizontalpodautoscaler = (
            labels.get("horizontalpodautoscaler")
            or labels.get("kubernetes_horizontalpodautoscaler")
            or annotations.get("horizontalpodautoscaler")
            or annotations.get("kubernetes_horizontalpodautoscaler")
        )
        # Prefer k8s-specific job keys; avoid Prometheus "job" label collision
        job = (
            labels.get("kubernetes_job_name")
            or annotations.get("kubernetes_job_name")
            or labels.get("job_name")
            or annotations.get("job_name")
        )
        if not job:
            cand = labels.get("job") or annotations.get("job")
            if cand and cand != "kube-state-metrics":
                job = cand

        result = self._base_result(
            SOURCE_AZURE_MONITOR,
            compact_raw,
            {},
            schema_ids,
            essentials.get("severity") or "Sev4",
            essentials.get("monitorcondition") or "Fired",
        )
        result.update(
            {
                "resource_name": resource_name,
                "resource_group": resource_group,
                "resource_id": resource_id,
                "namespace": namespace,
                "pod": pod,
                "deployment": deployment,
                "horizontalpodautoscaler": horizontalpodautoscaler,
                "job": job,
            }
        )

        return result


# =============================================================================
# Generic source parser (base for all {source, payload} shaped alerts)
# =============================================================================


class GenericSourceParser(AlertParser):
    """
    Base parser for sources that send alerts in the envelope format:
      { "source": "<name>", "payload": { <arbitrary key-values> } }

    Subclasses should override ``_parse_payload()`` to map source-specific
    payload fields onto the standard result dict.  The default implementation
    leaves all resource fields as None and stores the raw payload as-is.
    """

    def parse(self, body: dict) -> dict[str, Any]:
        body = body if isinstance(body, dict) else {}
        lower_body = lower_keys(body)

        source = str(
            body.get("source") or lower_body.get("source") or "unknown"
        ).lower()
        rule = body.get("rule") or lower_body.get("rule") or ""
        severity = body.get("severity") or lower_body.get("severity") or "Sev4"
        monitor_condition = (
            body.get("monitorCondition")
            or body.get("monitor_condition")
            or lower_body.get("monitorcondition")
            or lower_body.get("monitor_condition")
            or "Fired"
        )
        payload = body.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        compact_raw = json.dumps(body, separators=(",", ":"))
        result = self._base_result(
            source, compact_raw, payload, [str(rule)], severity, monitor_condition
        )
        return self._parse_payload(payload, result)

    def _parse_payload(self, payload: dict, result: dict) -> dict[str, Any]:
        """
        Hook for source-specific field extraction.

        Override in subclasses to populate result fields from *payload*.
        Must return the (mutated or replaced) result dict.
        """
        return result


# =============================================================================
# Concrete source parsers
# =============================================================================


class ElasticParser(GenericSourceParser):
    """
    Parser for alerts originating from Elastic.

    Override ``_parse_payload()`` here once the Elastic payload structure
    is known, mapping fields onto the standard result dict.
    """

    def _parse_payload(self, payload: dict, result: dict) -> dict[str, Any]:
        alert_attributes = payload.get("attributes")
        if not isinstance(alert_attributes, dict):
            alert_attributes = {}

        elastic_data = {
            "type": payload.get("type"),
            "attributes": alert_attributes,
        }

        payload_severity = alert_attributes.get("severity") or payload.get("severity")
        if payload_severity:
            result["severity"] = payload_severity

        payload_monitor_condition = (
            alert_attributes.get("monitorCondition")
            or alert_attributes.get("monitor_condition")
            or payload.get("monitorCondition")
            or payload.get("monitor_condition")
        )
        if payload_monitor_condition:
            result["monitorCondition"] = payload_monitor_condition

        if payload.get("type") == "aks":
            alert_attributes = payload.get("attributes", {})
            aks_data = {
                "namespace": alert_attributes.pop("namespace", ""),
                "resource_name": alert_attributes.pop("cluster_name", ""),
                "resource_group": alert_attributes.pop("cluster_rg_name", ""),
                "deployment": alert_attributes.pop("deployment", ""),
                "horizontalpodautoscaler": alert_attributes.pop("hpa", ""),
                "job": alert_attributes.pop("job", ""),
            }
            result.update(aks_data)

        result.update(elastic_data)

        return result


class CloudoParser(GenericSourceParser):
    """
    Parser for alerts originating from Cloudo.

    Override ``_parse_payload()`` here once the Cloudo payload structure
    is known, mapping fields onto the standard result dict.
    """

    def _parse_payload(self, payload: dict, result: dict) -> dict[str, Any]:
        result.update({"payload": json.dumps(payload)})
        return result


# =============================================================================
# Registry and public entry points
# =============================================================================

_PARSER_REGISTRY: dict[str, AlertParser] = {
    SOURCE_AZURE_MONITOR: AzureMonitorParser(),
    "elastic": ElasticParser(),
    "cloudo": CloudoParser(),
}

# Fallback for unknown non-Azure-Monitor sources
_GENERIC_PARSER = GenericSourceParser()


def detect_source(body: dict) -> str:
    """
    Determine the originating system of an incoming alert payload.

    Detection rules (in priority order):
      1. Top-level "source" key present → return its value (e.g. "elastic").
      2. Body contains "data.essentials" → "azure_monitor".
      3. Default fallback → "azure_monitor" (backward compatibility).
    """
    if not isinstance(body, dict):
        return SOURCE_AZURE_MONITOR

    lower_body = lower_keys(body)

    if "source" in lower_body:
        return str(lower_body["source"]).lower()

    data = lower_body.get("data") or {}
    if isinstance(data, dict) and "essentials" in data:
        return SOURCE_AZURE_MONITOR

    return SOURCE_AZURE_MONITOR


def parse_resource_fields(req) -> dict[str, Any]:
    """
    Parse incoming alert fields, dispatching to the correct parser based on source.

    Resolves the source with ``detect_source()``, looks up the matching parser
    in ``_PARSER_REGISTRY``, and delegates to it.  Unknown non-Azure-Monitor
    sources fall back to ``GenericSourceParser``.

    Returns a dict with keys (always in this order):
      _raw, source, resourceName, resourceGroup, resourceId, schema_id,
      namespace, pod, deployment, horizontalpodautoscaler, job,
      monitorCondition, severity, payload
    """
    try:
        body = req.get_json() or {}
    except (AttributeError, ValueError):
        body = req if isinstance(req, dict) else {}

    source = detect_source(body)

    if source == SOURCE_AZURE_MONITOR:
        return _PARSER_REGISTRY[SOURCE_AZURE_MONITOR].parse(req)

    logging.info("Detected non-Azure-Monitor source: %s", source)
    parser = _PARSER_REGISTRY.get(source, _GENERIC_PARSER)
    return parser.parse(body)


def extract_schema_id_from_req(req: func.HttpRequest) -> Optional[list[str]]:
    """
    Resolve schema_id from the incoming request:
      - Query string (?id=...)
      - JSON body: data.essentials.alertId and data.essentials.alertRule

    Normalization:
      - If the value contains '/', return the trailing segment.
      - De-duplicate while preserving order.
    """

    def normalize(val) -> str:
        s = str(val).strip()
        if "/" in s:
            last = s.strip("/").split("/")[-1]
            return last or s
        return s

    candidates: list[str] = []

    try:
        q_id = req.params.get("id")
        if q_id:
            logging.info("Resolving schema_id: %s", q_id)
            candidates.append(normalize(q_id))
            return candidates
    except Exception as e:
        logging.warning("Error parsing id query parameters: %s", e)

    try:
        body = req.get_json()
    except AttributeError:
        body = req
    except ValueError:
        body = None

    if isinstance(body, dict):
        essentials = body.get("data", {}).get("essentials", {}) or {}
        raw_candidates = [
            essentials.get("alertId"),
            essentials.get("alertRule"),
        ]
        for c in raw_candidates:
            if c:
                candidates.append(normalize(c))

    seen = set()
    unique_candidates: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    return unique_candidates or None
