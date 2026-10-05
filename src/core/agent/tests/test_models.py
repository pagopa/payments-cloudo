import json

import pytest
from models import FailedRunbookAlert


def _base_payload(**overrides):
    payload = {
        "exec_id": "exec-123",
        "id": "schema-1",
        "name": "Runbook failed",
        "status": "failed",
        "runbook": "check_sys.sh",
        "run_args": "--flag x",
        "monitor_condition": "Fired",
        "severity": "Sev2",
        "initiator": "SYSTEM",
        "resource_info": {"resource_name": "aks-node-1"},
        "routing_info": {"team": "core"},
        "logs": "Traceback...\nConnection refused",
    }
    payload.update(overrides)
    return payload


def test_from_payload_builds_alert():
    alert = FailedRunbookAlert.from_payload(_base_payload())

    assert alert.exec_id == "exec-123"
    assert alert.status == "failed"
    assert alert.resource_info == {"resource_name": "aks-node-1"}
    # alias falls back to the schema id (matches the orchestrator's
    # smart-routing JSM alert alias), not exec_id.
    assert alert.alias == "schema-1"


def test_alias_prefers_explicit_jsm_alias():
    alert = FailedRunbookAlert.from_payload(_base_payload(jsm_alias="custom-alias"))

    assert alert.alias == "custom-alias"


def test_alias_falls_back_to_exec_id_when_no_schema_id():
    alert = FailedRunbookAlert.from_payload(_base_payload(id=""))

    assert alert.alias == "exec-123"


def test_from_payload_requires_exec_id():
    with pytest.raises(ValueError):
        FailedRunbookAlert.from_payload(_base_payload(exec_id=""))


def test_from_payload_rejects_non_dict():
    with pytest.raises(ValueError):
        FailedRunbookAlert.from_payload(json.dumps(_base_payload()))


def test_from_payload_defaults_dict_fields_when_not_dict():
    alert = FailedRunbookAlert.from_payload(
        _base_payload(resource_info="not-a-dict", routing_info=None)
    )

    assert alert.resource_info == {}
    assert alert.routing_info == {}
