import json

import analyzer
from models import FailedRunbookAlert


def _alert(**overrides):
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
    return FailedRunbookAlert.from_payload(payload)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


def test_build_prompt_includes_key_fields():
    prompt = analyzer.build_prompt(_alert())

    assert "check_sys.sh" in prompt
    assert "Fired" in prompt
    assert "connection refused" in prompt


def test_build_prompt_truncates_long_logs():
    long_logs = "x" * 100
    alert = _alert(logs=long_logs)

    prompt = analyzer.build_prompt(alert)

    tail = prompt.split("Execution logs (tail):\n", 1)[1]
    assert len(tail.strip("\n")) <= analyzer.MAX_LOG_CHARS


def test_analyze_parses_valid_json_response(monkeypatch):
    valid_json = json.dumps(
        {
            "summary": "Disk full on node",
            "probable_root_cause": "PVC exceeded quota",
            "recommended_actions": ["Expand PVC", "Restart pod"],
            "confidence": "high",
        }
    )
    monkeypatch.setattr(analyzer, "_get_client", lambda: _FakeClient(valid_json))

    result = analyzer.analyze(_alert())

    assert result.summary == "Disk full on node"
    assert result.confidence == "high"
    assert result.recommended_actions == ["Expand PVC", "Restart pod"]
    assert result.error is None


def test_analyze_handles_unparsable_response(monkeypatch):
    monkeypatch.setattr(analyzer, "_get_client", lambda: _FakeClient("not json"))

    result = analyzer.analyze(_alert())

    assert result.error is not None
    assert result.confidence == "low"


def test_analyze_strips_markdown_json_fence(monkeypatch):
    valid_json = json.dumps(
        {
            "summary": "Runbook failed",
            "probable_root_cause": "exit code 1",
            "recommended_actions": ["Check logs"],
            "confidence": "medium",
        }
    )
    fenced = f"```json\n{valid_json}\n```"
    monkeypatch.setattr(analyzer, "_get_client", lambda: _FakeClient(fenced))

    result = analyzer.analyze(_alert())

    assert result.error is None
    assert result.summary == "Runbook failed"
    assert result.confidence == "medium"


def test_analyze_never_raises_on_client_failure(monkeypatch):
    def _boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(analyzer, "_get_client", _boom)

    result = analyzer.analyze(_alert())

    assert result.error == "no credentials"
    assert result.summary == "AI analysis unavailable."


def test_analyze_uses_copilot_provider_when_configured(monkeypatch):
    monkeypatch.setattr(analyzer, "_llm_provider", lambda: "copilot_sdk")
    monkeypatch.setattr(analyzer, "_copilot_token", lambda: "ghp_fake_token")
    monkeypatch.setattr(analyzer, "_copilot_model", lambda: "")

    valid_json = json.dumps(
        {
            "summary": "Disk full on node",
            "probable_root_cause": "PVC exceeded quota",
            "recommended_actions": ["Expand PVC"],
            "confidence": "high",
        }
    )
    captured = {}

    def _fake_invoke(prompt, token, model):
        captured["prompt"] = prompt
        captured["token"] = token
        captured["model"] = model
        return valid_json

    monkeypatch.setattr(analyzer.copilot_provider, "invoke", _fake_invoke)

    result = analyzer.analyze(_alert())

    assert result.summary == "Disk full on node"
    assert result.error is None
    assert captured["token"] == "ghp_fake_token"
    assert analyzer.SYSTEM_PROMPT in captured["prompt"]


def test_analyze_copilot_provider_never_raises_without_token(monkeypatch):
    monkeypatch.setattr(analyzer, "_llm_provider", lambda: "copilot_sdk")
    monkeypatch.setattr(analyzer, "_copilot_token", lambda: "")

    result = analyzer.analyze(_alert())

    assert result.error is not None
    assert result.summary == "AI analysis unavailable."
