"""A run folder of its own per test, a job runner that just runs the command, and the fake runtime as the binary."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))

RUNNER = """#!/usr/bin/env bash
# job-runner interface (tools/run/job.sh): MODE NAME CMD...; start detaches
mode=$1; shift 2
if [ "$mode" = start ]; then nohup "$@" > /dev/null 2>&1 < /dev/null & exit 0; fi
exec "$@"
"""


@pytest.fixture
def runenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if os.name == "nt":
        pytest.skip("FIFOs and /proc: the run layer is Linux's")
    runner = tmp_path / "runner.sh"
    runner.write_text(RUNNER)
    runner.chmod(0o755)
    for k in list(os.environ):
        if k.startswith(("MKW_", "RACE_", "FAKE_")) or k in ("CLAUDECODE", "WORLD_SECS"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("MKW_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("JOB_RUNNER", str(runner))
    monkeypatch.setenv("RUN_PERF", "0")
    monkeypatch.setenv("MKW_RUN_BIN", str(ROOT / "tests/run/fake_mkw.py"))
    monkeypatch.setenv("MKW_INPUT_SCRIPT", "none")
    return tmp_path / "run"
