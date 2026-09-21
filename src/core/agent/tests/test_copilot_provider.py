import copilot_provider
import pytest


@pytest.fixture(autouse=True)
def _reset_runtime():
    """copilot_provider keeps a process-wide singleton; make sure tests don't
    leak state into each other."""
    copilot_provider._runtime = None
    yield
    copilot_provider._runtime = None


def test_invoke_requires_token():
    with pytest.raises(ValueError):
        copilot_provider.invoke("hello", token="", model="")


def test_get_runtime_reuses_instance_for_same_config():
    r1 = copilot_provider.get_runtime(token="tok", model="gpt-4o")
    r2 = copilot_provider.get_runtime(token="tok", model="gpt-4o")

    assert r1 is r2


def test_get_runtime_restarts_on_token_change():
    r1 = copilot_provider.get_runtime(token="tok-a", model="")
    r2 = copilot_provider.get_runtime(token="tok-b", model="")

    assert r1 is not r2


def test_get_runtime_restarts_on_model_change():
    r1 = copilot_provider.get_runtime(token="tok", model="gpt-4o")
    r2 = copilot_provider.get_runtime(token="tok", model="o3")

    assert r1 is not r2


def test_extract_text_raises_on_missing_event():
    with pytest.raises(ValueError):
        copilot_provider._CopilotRuntime._extract_text(None)


def test_extract_text_reads_plain_string_content():
    class _Data:
        content = "hello world"

    class _Event:
        data = _Data()

    assert copilot_provider._CopilotRuntime._extract_text(_Event()) == "hello world"


def test_extract_text_joins_list_content_blocks():
    class _Data:
        content = [{"text": "foo"}, {"text": "bar"}]

    class _Event:
        data = _Data()

    assert copilot_provider._CopilotRuntime._extract_text(_Event()) == "foobar"
