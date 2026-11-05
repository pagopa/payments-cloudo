"""Runs real scripts through run_script, since the bug here lives in the OS pipes."""

import stat
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core import executor, registry, reporting

# Over the pipe buffer of both Linux (64 KB) and macOS.
LARGE_STDERR_BYTES = 1024 * 1024
WRITE_LARGE_STDERR = f"head -c {LARGE_STDERR_BYTES} /dev/zero | tr '\\0' x >&2"
HANG_TIMEOUT_SECONDS = 20


def _run(tmp_path: Path, body: str):
    """Runs a script, failing the test instead of hanging it if the script gets stuck."""
    script = tmp_path / "script.sh"
    script.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    exec_id = f"test-{tmp_path.name}"
    outcome = {}

    def target():
        try:
            outcome["result"] = executor.run_script(
                script_name=script.name,
                run_args=None,
                script_path=str(tmp_path),
                payload={"exec_id": exec_id},
                temp_dir=str(tmp_path),
            )
        except Exception as e:
            outcome["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(HANG_TIMEOUT_SECONDS)
    if thread.is_alive():
        proc = registry.PROCESS_BY_EXEC.get(exec_id)
        if proc:
            proc.kill()
        pytest.fail(f"run_script still blocked after {HANG_TIMEOUT_SECONDS}s")

    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


@pytest.fixture(autouse=True)
def no_log_upload():
    with patch.object(reporting, "update_running_log_blob") as upload:
        yield upload


def test_large_stderr_does_not_block_a_successful_script(tmp_path):
    result = _run(tmp_path, f"{WRITE_LARGE_STDERR}\necho done")

    assert result.returncode == 0
    assert result.stdout == "done\n"
    assert len(result.stderr) == LARGE_STDERR_BYTES


def test_large_stderr_is_reported_in_full_when_the_script_fails(tmp_path):
    with pytest.raises(subprocess.CalledProcessError) as failure:
        _run(tmp_path, f"echo before\n{WRITE_LARGE_STDERR}\nexit 3")

    assert failure.value.returncode == 3
    assert failure.value.output == "before\n"
    assert len(failure.value.stderr) == LARGE_STDERR_BYTES


def test_shell_tracing_does_not_block_the_script(tmp_path):
    result = _run(
        tmp_path, 'set -x\nfor i in $(seq 1 20000); do : "$i"; done\necho done'
    )

    assert result.returncode == 0
    assert result.stdout.endswith("done\n")


def test_stdout_is_streamed_while_stderr_is_being_written(tmp_path, no_log_upload):
    release = tmp_path / "release"
    first_line_streamed = threading.Event()

    def on_upload(_payload, log_text):
        if "first" in log_text and "second" not in log_text:
            first_line_streamed.set()
            release.touch()

    no_log_upload.side_effect = on_upload

    with patch.object(executor, "LOG_STREAM_UPDATE_SECONDS", 1e-9):
        result = _run(
            tmp_path,
            f"echo first\n{WRITE_LARGE_STDERR}\n"
            f"until [ -f '{release}' ]; do sleep 0.05; done\necho second",
        )

    assert first_line_streamed.is_set()
    assert result.stdout == "first\nsecond\n"
    assert len(result.stderr) == LARGE_STDERR_BYTES


def test_invalid_utf8_on_stderr_does_not_block_the_script(tmp_path):
    result = _run(
        tmp_path,
        f"printf '\\xff\\xfe bad bytes\\n' >&2\n{WRITE_LARGE_STDERR}\necho done",
    )

    assert result.returncode == 0
    assert result.stdout == "done\n"
    assert "bad bytes" in result.stderr


def test_invalid_utf8_on_stdout_does_not_fail_the_script(tmp_path):
    result = _run(tmp_path, "printf '\\xff\\xfe bad bytes\\n'\necho done")

    assert result.returncode == 0
    assert result.stdout.endswith("done\n")
