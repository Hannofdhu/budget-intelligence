import copy
import json
from pathlib import Path
from typing import Any

import pytest

from packages.forecast.store import ForecastStore, ForecastStoreError, fingerprint


def version(
    store: ForecastStore,
    request: str = "request-1",
    parent: str | None = None,
    ready: bool = True,
    department: str = "department",
) -> dict[str, Any]:
    return store.append_version(
        department=department,
        actor="owner",
        request_id=request,
        request_hash=fingerprint(request),
        parent_id=parent,
        baseline_hash="immutable-budget",
        snapshot={"as_of": "2026-10-03", "rows": []},
        assumptions={"states": {"暑假": "FINALIZED"}},
        calculation={"ready": ready, "annual": {"gap": "999999"}},
        reason="trial",
    )


def test_append_confirm_and_revision_invalidate_confirmation(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path)
    first = version(store)["version"]
    before = copy.deepcopy(first)
    confirmed = store.confirm(
        department="department",
        actor="owner",
        version_id=first["id"],
        request_id="confirm-1",
        request_hash="confirmation",
        reason="approved",
    )
    assert confirmed["version"]["status"] == "CONFIRMED"  # A revenue gap is NOT a confirmation blocker.
    second = version(store, request="request-2", parent=first["id"])
    assert second["version"]["status"] == "DRAFT"
    assert second["history"][1]["status"] == "CONFIRMED"
    assert first == before
    assert second["version"]["snapshot_hash"] == fingerprint(before["snapshot"])


def test_idempotency_replays_original_cutoff_not_newer_version(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path)
    first = version(store)
    version(store, request="request-2", parent=first["version"]["id"])
    assert version(store) == first
    assert store.request_result("department", "request-1", fingerprint("request-1")) == first
    with pytest.raises(ForecastStoreError, match="不同操作"):
        store.request_result("department", "request-1", "other content")


def test_compare_and_append_rejects_stale_and_wrong_baseline(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path)
    first = version(store)
    with pytest.raises(ForecastStoreError, match="已变化"):
        version(store, request="request-2")
    assert len(store.current("department")["history"]) == 1
    with pytest.raises(ForecastStoreError, match="当前预测"):
        store.confirm(
            department="department",
            actor="owner",
            version_id="stale",
            request_id="confirm-1",
            request_hash="confirmation",
            reason="approved",
        )
    assert first["version"]["status"] == "DRAFT"


def test_blocking_data_and_department_isolation(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path)
    first = version(store, ready=False)
    with pytest.raises(ForecastStoreError, match="数据阻断"):
        store.confirm(
            department="department",
            actor="owner",
            version_id=first["version"]["id"],
            request_id="confirm-1",
            request_hash="confirmation",
            reason="approved",
        )
    assert store.current("another")["version"] is None
    version(store, request="another-request", department="another")
    assert len(store.current("department")["history"]) == 1
    assert len(store.current("another")["history"]) == 1


def test_trial_is_never_confirmable_even_when_calculation_says_ready(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path)
    result = store.append_version(
        department="department",
        actor="owner",
        request_id="trial-request",
        request_hash="trial",
        parent_id=None,
        baseline_hash="immutable-budget",
        snapshot={"as_of": "2026-10-03", "rows": []},
        assumptions={"states": {"暑假": "FINALIZED"}, "trial_mode": True},
        calculation={"ready": True, "trial_ready": True},
        reason="trial",
    )
    before = {path: path.read_bytes() for path in tmp_path.glob("event-*.json")}
    with pytest.raises(ForecastStoreError, match="试用预测不能正式确认"):
        store.confirm(
            department="department",
            actor="owner",
            version_id=result["version"]["id"],
            request_id="trial-confirm",
            request_hash="confirmation",
            reason="not official",
        )
    assert {path: path.read_bytes() for path in tmp_path.glob("event-*.json")} == before


def test_content_tampering_and_deleted_event_are_detected(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path)
    first = version(store)
    version(store, request="request-2", parent=first["version"]["id"])
    files = list(tmp_path.glob("event-*.json"))
    event_file = next(p for p in files if json.loads(p.read_text())["event"]["event_no"] == 1)
    payload = json.loads(event_file.read_text())
    payload["event"]["version"]["as_of"] = "other"
    event_file.write_text(json.dumps(payload))
    with pytest.raises(ForecastStoreError, match="完整性"):
        store.current("department")
    event_file.unlink()
    with pytest.raises(ForecastStoreError, match="不完整"):
        store.current("department")
