import json

import history


def test_compute_signature_is_stable_across_volatile_details():
    logs_a = "Error: timeout after 30s connecting to host 10.0.0.5 (req-id=abc123ef)"
    logs_b = "Error: timeout after 45s connecting to host 10.0.0.9 (req-id=999def01)"

    sig_a = history.compute_signature("check_sys.sh", logs_a)
    sig_b = history.compute_signature("check_sys.sh", logs_b)

    assert sig_a == sig_b


def test_compute_signature_differs_for_different_runbooks():
    logs = "Error: disk full"

    sig_a = history.compute_signature("check_sys.sh", logs)
    sig_b = history.compute_signature("other_runbook.sh", logs)

    assert sig_a != sig_b


def test_compute_signature_differs_for_different_errors():
    sig_a = history.compute_signature("check_sys.sh", "Error: disk full")
    sig_b = history.compute_signature("check_sys.sh", "Error: connection refused")

    assert sig_a != sig_b


def test_get_history_returns_none_without_storage_connection(monkeypatch):
    monkeypatch.delenv(history.utils.STORAGE_CONNECTION, raising=False)

    assert history.get_history("some-signature") is None


def test_record_occurrence_noop_without_storage_connection(monkeypatch):
    monkeypatch.delenv(history.utils.STORAGE_CONNECTION, raising=False)

    assert (
        history.record_occurrence(
            "sig", "runbook.sh", {"summary": "x"}, "exec-1", False
        )
        == 0
    )


def test_build_reused_analysis_enriches_and_marks_recurring():
    prior = {
        "last_analysis": json.dumps(
            {
                "summary": "Disk full",
                "probable_root_cause": "PVC exceeded quota",
                "recommended_actions": ["Expand PVC"],
                "confidence": "high",
            }
        ),
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-02T00:00:00+00:00",
    }

    enriched = history.build_reused_analysis(prior, occurrence_count=7)

    assert enriched["recurring"] is True
    assert enriched["occurrence_count"] == 7
    assert enriched["probable_root_cause"] == "PVC exceeded quota"
    assert "Recurring issue" in enriched["summary"]
    assert "Disk full" in enriched["summary"]


def test_build_reused_analysis_returns_none_without_prior_analysis():
    assert history.build_reused_analysis({}, occurrence_count=7) is None
    assert history.build_reused_analysis(None, occurrence_count=7) is None


def test_build_reused_analysis_returns_none_for_malformed_json():
    assert (
        history.build_reused_analysis({"last_analysis": "not-json"}, occurrence_count=7)
        is None
    )
