import json
from unittest.mock import MagicMock, patch

import azure.functions as func
import pytest
from routes import runbooks


def _list_runbooks(monkeypatch, prefix: str, tree_paths: list[str]) -> list[str]:
    monkeypatch.delenv("LOCAL_DEV", raising=False)
    monkeypatch.setattr(runbooks, "GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(runbooks, "GITHUB_BRANCH", "main")
    monkeypatch.setattr(runbooks, "GITHUB_TOKEN", None)
    monkeypatch.setattr(runbooks, "GITHUB_PATH_PREFIX", prefix)

    github_response = MagicMock(status_code=200)
    github_response.json.return_value = {"tree": [{"path": p} for p in tree_paths]}
    request = func.HttpRequest(
        method="GET", url="/api/runbooks/list", headers={}, params={}, body=b""
    )

    with patch("requests.get", return_value=github_response):
        response = runbooks.list_runbooks(request)

    assert response.status_code == 200
    return json.loads(response.get_body())["runbooks"]


@pytest.mark.parametrize("prefix", ["src/runbooks", "/src/runbooks/"])
def test_list_only_includes_scripts_under_the_prefix_folder(monkeypatch, prefix):
    listed = _list_runbooks(
        monkeypatch,
        prefix,
        [
            "src/runbooks/check_sys.sh",
            "src/runbooks/aks/restart.py",
            "src/runbooks/README.md",
            "src/runbooks-examples/sibling.sh",
            "examples/runbooks/templates/template.sh",
        ],
    )

    assert listed == ["aks/restart.py", "check_sys.sh"]


def test_list_without_prefix_includes_all_scripts(monkeypatch):
    listed = _list_runbooks(monkeypatch, "", ["run.sh", "examples/other.py", "a.md"])

    assert listed == ["examples/other.py", "run.sh"]
