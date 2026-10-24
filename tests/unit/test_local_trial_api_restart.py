"""Recover an exited managed API after graceful shutdown; never reseed or kill unknown PIDs."""

import json
import signal
from typing import Any

import pytest

from scripts import local_trial
from tests.unit.test_local_trial_worker_restart import restart_fixture as restart_fixture


@pytest.fixture
def api_restart(monkeypatch: pytest.MonkeyPatch, restart_fixture: dict[str, Any]) -> dict[str, Any]:
    fixture = restart_fixture["state"].parent / "fixture.json"
    fixture.write_text(json.dumps({"department_id": "synthetic-fixture"}))
    monkeypatch.setattr(local_trial, "FIXTURE", fixture)
    monkeypatch.setattr(local_trial, "free_port", lambda _: True)
    monkeypatch.setattr(local_trial, "wait_url", lambda _: None)
    return restart_fixture


def test_exited_api_can_be_recovered_without_seed_or_signalling_old_pid(
    monkeypatch: pytest.MonkeyPatch, api_restart: dict[str, Any]
) -> None:
    monkeypatch.setattr(local_trial, "process_matches", lambda _: False)
    monkeypatch.setattr(local_trial, "process_exists", lambda _: False)
    local_trial.restart_api()
    assert api_restart["kill"] == [] and len(api_restart["spawn"]) == 1
    saved = json.loads(api_restart["state"].read_text())
    assert saved["processes"][1]["pid"] == 23456
    assert saved["processes"][0]["pid"] == 12345  # Worker is untouched.
    command = api_restart["spawn"][0][0][0]
    assert command[1:3] == ["apps.api.main:app", "--host"]


def test_reused_api_pid_or_unknown_port_is_never_stopped_or_overwritten(
    monkeypatch: pytest.MonkeyPatch, api_restart: dict[str, Any]
) -> None:
    original = api_restart["state"].read_bytes()
    monkeypatch.setattr(local_trial, "process_matches", lambda _: False)
    monkeypatch.setattr(local_trial, "process_exists", lambda _: True)
    with pytest.raises(RuntimeError, match="身份不匹配"):
        local_trial.restart_api()
    monkeypatch.setattr(local_trial, "process_exists", lambda _: False)
    monkeypatch.setattr(local_trial, "free_port", lambda _: False)
    with pytest.raises(RuntimeError, match="未知进程"):
        local_trial.restart_api()
    assert api_restart["kill"] == [] and api_restart["spawn"] == []
    assert api_restart["state"].read_bytes() == original


def test_matching_api_is_stopped_before_replacement_without_touching_worker(
    monkeypatch: pytest.MonkeyPatch, api_restart: dict[str, Any]
) -> None:
    monkeypatch.setattr(local_trial, "process_matches", lambda _: True)
    local_trial.restart_api()
    assert api_restart["kill"] == [(34567, signal.SIGTERM)]
    assert len(api_restart["spawn"]) == 1
