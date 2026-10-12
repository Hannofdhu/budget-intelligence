import json
import stat
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from packages.forecast.store import fingerprint
from packages.next_year.store import NextYearStore, NextYearStoreError

Json = dict[str, Any]


def _source(name: str = "forecast-a") -> Json:
    return {"id": name, "calculation_hash": fingerprint(name), "baseline_hash": "immutable"}


def _append(
    store: NextYearStore,
    *,
    request: str = "prepare-1",
    parent: str | None = None,
    source: Json | None = None,
    inputs: Json | None = None,
    operation: str = "PREPARE",
    department: str = "department",
    observed: Json | None = None,
    source_snapshot: Json | None = None,
) -> Json:
    basis = _source() if source is None else source
    return store.append_draft(
        department=department,
        actor="owner",
        request_id=request,
        request_hash=fingerprint(request),
        parent_id=parent,
        source=basis,
        inputs={"fiscal_year": 2028, "target_revenue_yuan": "100", "frozen": {"students": 10}}
        if inputs is None
        else inputs,
        calculation={"annual": {"forecast_revenue": "99"}},
        reason="prepare or revise",
        operation=operation,
        source_snapshot=(
            {"forecast": {"id": basis["id"], "snapshot": {"private": "evidence"}}, "baseline": "immutable"}
            if operation == "PREPARE" and source_snapshot is None
            else source_snapshot
        ),
        observed_source=observed,
    )


def _files(path: Path) -> dict[str, bytes]:
    return {item.name: item.read_bytes() for item in path.glob("event-*.json")}


def test_empty_and_first_draft_keep_full_frozen_basis(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    assert store.current("department") == {
        "source": None,
        "draft": None,
        "source_changed": False,
        "history": [],
    }
    result = _append(store)
    draft = result["draft"]
    assert draft["status"] == "DRAFT"
    assert draft["sequence"] == 1
    assert draft["parent_id"] is None
    assert draft["prepared_inputs"] == draft["inputs"]
    assert draft["source_snapshot"]["forecast"]["snapshot"]["private"] == "evidence"
    for value, digest in (
        ("inputs", "inputs_hash"),
        ("prepared_inputs", "prepared_inputs_hash"),
        ("source_snapshot", "source_snapshot_hash"),
        ("calculation", "calculation_hash"),
        ("source", "source_hash"),
    ):
        assert fingerprint(draft[value]) == draft[digest]
    assert NextYearStore(tmp_path).current("department") == result


def test_revise_keeps_prepared_inputs_and_source_snapshot(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    first = _append(store)
    inputs = {**first["draft"]["inputs"], "target_revenue_yuan": "200"}
    before = _files(tmp_path)
    second = _append(
        store, request="revise-2", parent=first["draft"]["id"], inputs=inputs, operation="REVISE"
    )
    assert second["draft"]["inputs"]["target_revenue_yuan"] == "200"
    assert second["draft"]["prepared_inputs"] == first["draft"]["inputs"]
    assert second["draft"]["source_snapshot"] == first["draft"]["source_snapshot"]
    assert second["draft"]["source"] == first["draft"]["source"]
    assert second["draft"]["parent_id"] == first["draft"]["id"]
    assert second["draft"]["sequence"] == 2
    assert [item["id"] for item in second["history"]] == [second["draft"]["id"], first["draft"]["id"]]
    assert all(_files(tmp_path)[name] == content for name, content in before.items())


def test_initial_source_repreparation_is_explicit_parented_draft(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    first = _append(store)
    with pytest.raises(NextYearStoreError, match="已变化"):
        _append(store, request="new-prepare", source=_source("forecast-b"))
    second = _append(store, request="new-prepare", parent=first["draft"]["id"], source=_source("forecast-b"))
    assert second["draft"]["source"]["id"] == "forecast-b"
    assert first["draft"]["source"]["id"] == "forecast-a"
    assert second["draft"]["source_snapshot"] != first["draft"]["source_snapshot"]


def test_replay_restores_original_cutoff_and_observed_source(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    first = _append(store)
    second = _append(
        store,
        request="revise-2",
        parent=first["draft"]["id"],
        operation="REVISE",
        observed=_source("forecast-b"),
    )
    assert second["source_changed"] is True
    assert second["source"]["id"] == "forecast-b"
    before = _files(tmp_path)
    assert _append(store) == first
    assert store.request_result("department", "prepare-1", fingerprint("prepare-1")) == first
    assert _files(tmp_path) == before
    with pytest.raises(ValueError, match="不同操作"):
        store.request_result("department", "prepare-1", "changed")


@pytest.mark.parametrize("mutation", ["source", "snapshot", "parent"])
def test_revision_cannot_replace_source_or_use_stale_parent(tmp_path: Path, mutation: str) -> None:
    store = NextYearStore(tmp_path)
    first = _append(store)
    before = _files(tmp_path)
    kwargs: Json = {"operation": "REVISE", "request": "revise-2", "parent": first["draft"]["id"]}
    if mutation == "source":
        kwargs["source"] = _source("other")
    elif mutation == "snapshot":
        kwargs["source_snapshot"] = {"forecast": "replacement"}
    else:
        kwargs["parent"] = "stale"
    with pytest.raises(NextYearStoreError):
        _append(store, **kwargs)
    assert _files(tmp_path) == before


def test_only_drafts_no_confirm_or_revise_without_parent(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    with pytest.raises(NextYearStoreError, match="先准备"):
        _append(store, operation="REVISE")
    with pytest.raises(NextYearStoreError, match="不能确认或发布"):
        store.confirm(department="department", actor="owner", version_id="id", reason="approve")
    with pytest.raises(NextYearStoreError, match="不支持"):
        _append(store, operation="PUBLISH")
    assert _files(tmp_path) == {}


def test_initial_draft_requires_nonempty_frozen_snapshot_and_reason(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    with pytest.raises(NextYearStoreError, match="完整预测来源"):
        _append(store, source_snapshot={})
    with pytest.raises(NextYearStoreError, match="操作原因"):
        store.append_draft(
            department="department",
            actor="owner",
            request_id="empty-reason",
            request_hash="hash",
            parent_id=None,
            source={},
            inputs={},
            calculation={},
            reason="  ",
            operation="PREPARE",
            source_snapshot={"exists": True},
        )


def test_department_isolation_and_no_shared_mutable_inputs(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    inputs = {"fiscal_year": 2028, "target_revenue_yuan": "100", "frozen": {"students": 10}}
    source = _source()
    result = _append(store, inputs=inputs, source=source)
    saved = deepcopy(result)
    inputs["frozen"]["students"] = 99
    source["id"] = "mutated"
    result["draft"]["source_snapshot"]["forecast"]["snapshot"]["private"] = "mutated"
    assert store.current("department") == saved
    assert store.current("other")["draft"] is None
    _append(store, department="other", request="other-prepare")
    assert len(store.current("department")["history"]) == len(store.current("other")["history"]) == 1


def test_concurrent_same_parent_accepts_only_one_update(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    first = _append(store)

    def append(index: int) -> str:
        try:
            return _append(
                NextYearStore(tmp_path),
                request=f"revise-{index}",
                parent=first["draft"]["id"],
                operation="REVISE",
            )["draft"]["id"]
        except NextYearStoreError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, (2, 3)))
    assert results.count("conflict") == 1
    assert len(store.current("department")["history"]) == 2


@pytest.mark.parametrize("field", ["inputs", "prepared_inputs", "source_snapshot", "calculation", "source"])
def test_even_rehashed_envelope_cannot_hide_internal_fingerprint_mismatch(tmp_path: Path, field: str) -> None:
    store = NextYearStore(tmp_path)
    _append(store)
    path = next(tmp_path.glob("event-*.json"))
    envelope = json.loads(path.read_text())
    envelope["event"]["draft"][field]["tampered"] = "change"
    envelope["sha256"] = fingerprint(envelope["event"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(NextYearStoreError, match="指纹不一致"):
        store.current("department")


@pytest.mark.parametrize(
    "field,value",
    [("status", "CONFIRMED"), ("parent_id", "other"), ("sequence", 8), ("operation", "PUBLISH")],
)
def test_rehashed_invalid_draft_metadata_is_rejected(tmp_path: Path, field: str, value: Any) -> None:
    store = NextYearStore(tmp_path)
    _append(store)
    path = next(tmp_path.glob("event-*.json"))
    envelope = json.loads(path.read_text())
    envelope["event"]["draft"][field] = value
    envelope["sha256"] = fingerprint(envelope["event"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(NextYearStoreError):
        store.current("department")


def test_ledger_detects_envelope_corruption_and_missing_parent(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    first = _append(store)
    _append(store, request="revise-2", parent=first["draft"]["id"], operation="REVISE")
    paths = list(tmp_path.glob("event-*.json"))
    first_path = next(path for path in paths if json.loads(path.read_text())["event"]["event_no"] == 1)
    first_path.unlink()
    with pytest.raises(ValueError, match="版本链不完整"):
        store.current("department")


def test_files_and_directory_have_private_permissions(tmp_path: Path) -> None:
    root = tmp_path / "new-ledger"
    _append(NextYearStore(root))
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(next(root.glob("event-*.json")).stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "field", ["inputs", "prepared_inputs", "source_snapshot", "calculation", "source", "observed_source"]
)
def test_rehashed_wrong_object_types_are_rejected_safely(tmp_path: Path, field: str) -> None:
    store = NextYearStore(tmp_path)
    _append(store)
    path = next(tmp_path.glob("event-*.json"))
    envelope = json.loads(path.read_text())
    envelope["event"]["draft"][field] = []
    envelope["sha256"] = fingerprint(envelope["event"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(NextYearStoreError, match="内容结构"):
        store.current("department")


def test_bool_sequence_is_not_an_integer_version_number(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    _append(store)
    path = next(tmp_path.glob("event-*.json"))
    envelope = json.loads(path.read_text())
    envelope["event"]["draft"]["sequence"] = True
    envelope["sha256"] = fingerprint(envelope["event"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(NextYearStoreError, match="版本信息"):
        store.current("department")


def test_invalid_direct_append_is_validated_before_any_event_write(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    with pytest.raises(NextYearStoreError, match="来源信息"):
        _append(store, source={}, source_snapshot={"forecast": "fixture"})
    with pytest.raises(NextYearStoreError, match="不能创建预测版本"):
        store.append_version(department="department", actor="owner")
    assert _files(tmp_path) == {}
