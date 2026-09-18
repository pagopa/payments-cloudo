import json

import function_app


def _payload(**overrides):
    payload = {
        "exec_id": "exec-123",
        "id": "schema-1",
        "name": "Runbook failed",
        "status": "failed",
        "runbook": "check_sys.sh",
        "monitor_condition": "Fired",
        "severity": "Sev2",
        "resource_info": {"resource_name": "aks-node-1"},
        "logs": "boom: connection refused",
    }
    payload.update(overrides)
    return payload


def test_analyze_failed_runbook_payload_happy_path(monkeypatch):
    monkeypatch.setattr(function_app, "JSM_API_KEY_DEFAULT", "secret")
    monkeypatch.setattr(function_app.history, "get_history", lambda signature: None)
    monkeypatch.setattr(function_app.history, "record_occurrence", lambda *a, **k: 1)

    def _fake_analyze(alert):
        from analyzer import AnalysisResult

        return AnalysisResult(
            summary="Disk full",
            probable_root_cause="PVC exceeded quota",
            recommended_actions=["Expand PVC"],
            confidence="high",
        )

    posted_calls = {}
    written_calls = {}

    def _fake_add_note(api_key, alias, note):
        posted_calls["api_key"] = api_key
        posted_calls["alias"] = alias
        posted_calls["note"] = note
        return True

    def _fake_write_analysis_result(exec_id, status, analysis=None, error=None):
        written_calls["exec_id"] = exec_id
        written_calls["status"] = status
        written_calls["analysis"] = analysis
        written_calls["error"] = error

    monkeypatch.setattr(function_app, "analyze", _fake_analyze)
    monkeypatch.setattr(function_app, "add_jsm_alert_note", _fake_add_note)
    monkeypatch.setattr(
        function_app.utils, "write_analysis_result", _fake_write_analysis_result
    )

    result = function_app.analyze_failed_runbook_payload(json.dumps(_payload()))

    assert result["exec_id"] == "exec-123"
    assert result["analysis"]["confidence"] == "high"
    assert result["jsm_note_posted"] is True
    assert posted_calls["api_key"] == "secret"
    assert posted_calls["alias"] == "schema-1"
    assert written_calls["exec_id"] == "exec-123"
    assert written_calls["status"] == "completed"
    assert written_calls["error"] is None


def test_analyze_failed_runbook_payload_marks_total_failure_as_error(monkeypatch):
    monkeypatch.setattr(function_app, "JSM_API_KEY_DEFAULT", "secret")
    monkeypatch.setattr(function_app.history, "get_history", lambda signature: None)
    monkeypatch.setattr(function_app.history, "record_occurrence", lambda *a, **k: 1)

    def _fake_analyze(alert):
        from analyzer import AnalysisResult

        return AnalysisResult(
            summary="AI analysis unavailable.",
            probable_root_cause="",
            recommended_actions=[],
            confidence="low",
            error="no credentials",
        )

    written_calls = {}

    def _fake_write_analysis_result(exec_id, status, analysis=None, error=None):
        written_calls["status"] = status
        written_calls["error"] = error

    monkeypatch.setattr(function_app, "analyze", _fake_analyze)
    monkeypatch.setattr(function_app, "add_jsm_alert_note", lambda **kwargs: False)
    monkeypatch.setattr(
        function_app.utils, "write_analysis_result", _fake_write_analysis_result
    )

    function_app.analyze_failed_runbook_payload(json.dumps(_payload()))

    assert written_calls["status"] == "error"
    assert written_calls["error"] == "no credentials"


def test_analyze_failed_runbook_payload_reuses_recurring_analysis(monkeypatch):
    monkeypatch.setattr(function_app, "JSM_API_KEY_DEFAULT", "secret")

    prior_analysis = {
        "summary": "Disk full",
        "probable_root_cause": "PVC exceeded quota",
        "recommended_actions": ["Expand PVC"],
        "confidence": "high",
    }
    prior_entity = {
        "occurrence_count": 6,
        "last_analysis": json.dumps(prior_analysis),
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-02T00:00:00+00:00",
    }

    analyze_calls = []
    record_calls = {}

    def _fake_analyze(alert):
        analyze_calls.append(alert)
        raise AssertionError("analyze() must not be called when reusing history")

    def _fake_record_occurrence(signature, runbook, analysis, exec_id, reused):
        record_calls["reused"] = reused
        record_calls["analysis"] = analysis
        return 7

    monkeypatch.setattr(function_app, "analyze", _fake_analyze)
    monkeypatch.setattr(function_app, "add_jsm_alert_note", lambda **kwargs: True)
    monkeypatch.setattr(
        function_app.utils, "write_analysis_result", lambda **kwargs: None
    )
    monkeypatch.setattr(
        function_app.history, "get_history", lambda signature: prior_entity
    )
    monkeypatch.setattr(
        function_app.history, "record_occurrence", _fake_record_occurrence
    )

    result = function_app.analyze_failed_runbook_payload(json.dumps(_payload()))

    assert not analyze_calls
    assert result["reused_from_history"] is True
    assert result["analysis"]["recurring"] is True
    assert result["analysis"]["occurrence_count"] == 7
    assert "Recurring issue" in result["analysis"]["summary"]
    assert record_calls["reused"] is True


def test_analyze_failed_runbook_payload_rejects_invalid_json():
    result = function_app.analyze_failed_runbook_payload("not-json")

    assert "error" in result


def test_analyze_failed_runbook_payload_rejects_missing_exec_id():
    result = function_app.analyze_failed_runbook_payload(
        json.dumps(_payload(exec_id=""))
    )

    assert "error" in result


def test_healthz_returns_ok():
    import azure.functions as func

    req = func.HttpRequest(
        method="GET", url="https://x/api/healthz", headers={}, params={}, body=b""
    )
    res = function_app.Healthz(req)

    assert res.status_code == 200
    body = json.loads(res.get_body())
    assert body["status"] == "ok"
