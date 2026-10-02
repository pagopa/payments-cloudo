import utils


def test_write_analysis_result_noop_without_exec_id():
    # Should not raise even though no exec_id/storage is configured.
    utils.write_analysis_result(exec_id="", status="completed")


def test_write_analysis_result_noop_without_storage_connection(monkeypatch):
    monkeypatch.delenv(utils.STORAGE_CONNECTION, raising=False)

    # No AzureWebJobsStorage configured: should silently do nothing instead
    # of raising, so a misconfigured/local environment never breaks the
    # main triage flow.
    utils.write_analysis_result(
        exec_id="exec-1", status="completed", analysis={"summary": "x"}
    )


def test_sanitize_setting_value_removes_embedded_nbsp():
    dirty = "sk-abc\u00a0def"

    assert utils.sanitize_setting_value(dirty) == "sk-abc def"
    assert utils.sanitize_setting_value("") == ""


def test_decode_base64():
    assert utils.decode_base64("") == ""
    assert utils.decode_base64(None) == ""
    assert utils.decode_base64("aGVsbG8=") == "hello"
    assert utils.decode_base64("invalid_base64!!!") == ""


def test_get_setting_fallback_env(monkeypatch):
    monkeypatch.setenv("TEST_KEY_SETTING", "test-val")
    utils._settings_cache.clear()
    assert utils.get_setting("TEST_KEY_SETTING") == "test-val"
    assert utils.get_setting("INVALID KEY WITH SPACES", default="def") == "def"


def test_get_queue_client_missing_env(monkeypatch):
    monkeypatch.delenv(utils.STORAGE_CONNECTION, raising=False)
    import pytest

    with pytest.raises(ValueError, match="Missing storage connection string"):
        utils.get_queue_client("test-queue")
