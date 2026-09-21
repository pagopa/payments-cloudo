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
