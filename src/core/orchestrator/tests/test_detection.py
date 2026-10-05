import json
from unittest.mock import MagicMock

from detection import (
    SOURCE_AZURE_MONITOR,
    CloudoParser,
    ElasticParser,
    detect_source,
    extract_schema_id_from_req,
    parse_resource_fields,
)


def test_detect_source():
    assert detect_source(None) == SOURCE_AZURE_MONITOR
    assert detect_source({}) == SOURCE_AZURE_MONITOR
    assert detect_source({"source": "elastic"}) == "elastic"
    assert detect_source({"source": "CLOUDO"}) == "cloudo"
    assert (
        detect_source({"data": {"essentials": {"alertRule": "HighCPU"}}})
        == SOURCE_AZURE_MONITOR
    )


def test_elastic_parser():
    parser = ElasticParser()
    body = {
        "source": "elastic",
        "rule": "ElasticRule1",
        "severity": "Sev1",
        "monitorCondition": "Fired",
        "payload": {
            "type": "aks",
            "attributes": {
                "severity": "Sev2",
                "monitor_condition": "Resolved",
                "cluster_name": "aks-cluster",
                "cluster_rg_name": "rg-aks",
                "namespace": "payments",
                "deployment": "pay-deploy",
                "hpa": "pay-hpa",
                "job": "pay-job",
            },
        },
    }
    res = parser.parse(body)
    assert res["source"] == "elastic"
    assert res["severity"] == "Sev2"
    assert res["monitorCondition"] == "Resolved"
    assert res["resource_name"] == "aks-cluster"
    assert res["namespace"] == "payments"
    assert res["deployment"] == "pay-deploy"
    assert res["horizontalpodautoscaler"] == "pay-hpa"


def test_cloudo_parser():
    parser = CloudoParser()
    body = {"source": "cloudo", "rule": "CloudoRule", "payload": {"hello": "world"}}
    res = parser.parse(body)
    assert res["source"] == "cloudo"
    assert json.loads(res["payload"]) == {"hello": "world"}


def test_parse_resource_fields():
    # Non Azure Monitor
    req_dict = {"source": "elastic", "rule": "r1", "payload": {}}
    res = parse_resource_fields(req_dict)
    assert res["source"] == "elastic"

    # Azure Monitor request mock
    req = MagicMock()
    azure_payload = {
        "data": {
            "essentials": {
                "alertRule": "AzureRule",
                "severity": "Sev3",
                "monitorCondition": "Fired",
                "alertTargetIDs": [
                    "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1"
                ],
            },
            "alertContext": {
                "labels": {
                    "namespace": "ingress-nginx",
                    "pod": "nginx-ingress-1",
                    "deployment": "nginx-ingress",
                    "horizontalpodautoscaler": "nginx-hpa",
                    "job": "nginx-job",
                }
            },
        }
    }
    raw_bytes = json.dumps(azure_payload).encode("utf-8")
    req.get_body.return_value = raw_bytes
    req.get_json.return_value = azure_payload

    res_azure = parse_resource_fields(req)
    assert res_azure["source"] == SOURCE_AZURE_MONITOR
    assert res_azure["resource_name"] == "vm1"
    assert res_azure["resource_group"] == "rg1"
    assert res_azure["namespace"] == "ingress-nginx"
    assert res_azure["pod"] == "nginx-ingress-1"


def test_extract_schema_id_from_req():
    # Query parameter priority
    req = MagicMock()
    req.params = {"id": "/subscriptions/sub/my-schema"}
    assert extract_schema_id_from_req(req) == ["my-schema"]

    # Body essentials
    req.params = {}
    req.get_json.return_value = {
        "data": {"essentials": {"alertId": "alert-1", "alertRule": "/rules/rule-2"}}
    }
    assert extract_schema_id_from_req(req) == ["alert-1", "rule-2"]
