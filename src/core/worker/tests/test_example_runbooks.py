"""Keep the runbook examples valid, since nothing else in the worker executes them."""

import ast
import os
import subprocess
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parents[4] / "examples" / "runbooks"
BASH_SCRIPTS = sorted(EXAMPLES_DIR.rglob("*.sh"))
PYTHON_SCRIPTS = sorted(EXAMPLES_DIR.rglob("*.py"))


def _script_id(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR))


def test_examples_are_found():
    assert BASH_SCRIPTS
    assert PYTHON_SCRIPTS


@pytest.mark.parametrize("script", BASH_SCRIPTS, ids=_script_id)
def test_bash_example_syntax(script: Path):
    result = subprocess.run(
        ["bash", "-n", str(script)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", BASH_SCRIPTS, ids=_script_id)
def test_bash_example_is_executable(script: Path):
    assert os.access(script, os.X_OK)


@pytest.mark.parametrize("script", PYTHON_SCRIPTS, ids=_script_id)
def test_python_example_syntax(script: Path):
    ast.parse(script.read_text(encoding="utf-8"), filename=str(script))


@pytest.mark.parametrize("script", BASH_SCRIPTS + PYTHON_SCRIPTS, ids=_script_id)
def test_example_is_listed_in_readme(script: Path):
    readme = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
    assert _script_id(script) in readme
