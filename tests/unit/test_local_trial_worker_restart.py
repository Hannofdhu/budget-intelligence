import json
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import local_trial


@pytest.fixture
def restart_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state = {
        "database": local_trial.DATABASE,
        "mode": "local",
        "processes": [
            {"name": "worker", "pid": 12345, "signature": "-m apps.worker.main", "started": "old-start"},
            {"name": "api", "pid": 34567, "signature": "untouched", "started": "api-start"},
        ],
    }
    target = tmp_path / "processes.json"
    target.write_text(json.dumps(state))
    calls: dict[str, Any] = {"spawn": [], "kill": [], "state": target}
    monkeypatch.setattr(local_trial, "STATE", target)
    monkeypatch.setattr(local_trial, "RUNTIME", tmp_path)
    monkeypatch.setattr(local_trial, "environment", lambda: {"SYNTHETIC_TEST": "true"})
    monkeypatch.setattr(local_trial, "run", lambda *args, **kwargs: "new-start")

    def spawn(*args: Any, **kwargs: Any) -> Any:
        calls["spawn"].append((args, kwargs))
        return SimpleNamespace(pid=23456)

    def kill(pid: int, signum: int) -> None:
        calls["kill"].append((pid, signum))

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(os, "kill", kill)
    return calls


def test_missing_worker_is_recovered_without_signalling_unknown_pid(
    monkeypatch: pytest.MonkeyPatch,
    restart_fixture: dict[str, Any],
) -> None:
    monkeypatch.setattr(local_trial, "process_matches", lambda _: False)
    monkeypatch.setattr(local_trial, "process_exists", lambda _: False)
    local_trial.restart_worker()
    assert len(restart_fixture["spawn"]) == 1 and restart_fixture["kill"] == []
    saved = json.loads(restart_fixture["state"].read_text())
    assert saved["processes"][0]["pid"] == 23456 and saved["processes"][0]["started"] == "new-start"
    assert saved["processes"][1]["pid"] == 34567


def test_live_mismatched_or_reused_pid_is_never_killed_or_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    restart_fixture: dict[str, Any],
) -> None:
    original = restart_fixture["state"].read_bytes()
    monkeypatch.setattr(local_trial, "process_matches", lambda _: False)
    monkeypatch.setattr(local_trial, "process_exists", lambda _: True)
    with pytest.raises(RuntimeError, match="身份不匹配"):
        local_trial.restart_worker()
    assert restart_fixture["kill"] == [] and restart_fixture["spawn"] == []
    assert restart_fixture["state"].read_bytes() == original


def test_matching_worker_is_terminated_before_replacement(
    monkeypatch: pytest.MonkeyPatch,
    restart_fixture: dict[str, Any],
) -> None:
    matches = iter([True, False, False])
    monkeypatch.setattr(local_trial, "process_matches", lambda _: next(matches))
    local_trial.restart_worker()
    assert restart_fixture["kill"] == [(12345, signal.SIGTERM)]
    assert len(restart_fixture["spawn"]) == 1


@pytest.mark.parametrize("failure,expected", [(ProcessLookupError(), False), (PermissionError(), True)])
def test_process_existence_treats_inaccessible_pid_as_alive(
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    expected: bool,
) -> None:
    def probe(pid: int, signum: int) -> None:
        assert (pid, signum) == (12345, 0)
        raise failure

    monkeypatch.setattr(os, "kill", probe)
    assert local_trial.process_exists(12345) is expected


def test_invalid_pid_cannot_signal_process_group() -> None:
    with pytest.raises(RuntimeError, match="进程编号无效"):
        local_trial.process_exists(0)
