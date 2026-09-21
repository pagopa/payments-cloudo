import jsm_notes


class _FakeResponse:
    def __init__(self, status_code=202, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)


def test_format_triage_note_includes_all_sections():
    analysis = {
        "summary": "Disk full",
        "probable_root_cause": "PVC exceeded quota",
        "recommended_actions": ["Expand PVC", "Restart pod"],
        "confidence": "high",
    }

    note = jsm_notes.format_triage_note(analysis)

    assert "Disk full" in note
    assert "PVC exceeded quota" in note
    assert "- Expand PVC" in note
    assert "- Restart pod" in note
    assert "high" in note


def test_add_jsm_alert_note_skips_without_api_key():
    assert jsm_notes.add_jsm_alert_note(api_key="", alias="exec-1", note="hi") is False


def test_add_jsm_alert_note_skips_without_alias():
    assert jsm_notes.add_jsm_alert_note(api_key="key", alias="", note="hi") is False


def test_add_jsm_alert_note_posts_to_expected_url(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, params, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["params"] = params
        return _FakeResponse(202)

    monkeypatch.setattr(jsm_notes.requests, "post", _fake_post)

    ok = jsm_notes.add_jsm_alert_note(api_key="secret", alias="exec-123", note="hello")

    assert ok is True
    assert captured["url"] == f"{jsm_notes.JSM_ALERTS_URL}/exec-123/notes"
    assert captured["json"] == {"user": jsm_notes.JSM_NOTE_AUTHOR, "note": "hello"}
    assert captured["headers"]["Authorization"] == "GenieKey secret"
    assert captured["params"] == {"identifierType": "alias"}


def test_add_jsm_alert_note_returns_false_on_http_error(monkeypatch):
    def _fake_post(*args, **kwargs):
        return _FakeResponse(500, text="boom")

    monkeypatch.setattr(jsm_notes.requests, "post", _fake_post)

    ok = jsm_notes.add_jsm_alert_note(api_key="secret", alias="exec-123", note="hello")

    assert ok is False
