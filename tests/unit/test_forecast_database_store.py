"""Canonical migration and database-only runtime behavior without external BI."""

import copy
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.forecast import database_store as database
from packages.forecast.store import ForecastStore, ForecastStoreError, canonical, fingerprint
from packages.persistence.models import ForecastLedgerEvent
from scripts.import_forecast_ledger import verify_imported_versions


def legacy_version(
    store: ForecastStore, request: str = "create", parent: str | None = None
) -> dict[str, Any]:
    return store.append_version(
        department="department",
        actor="owner",
        request_id=request,
        request_hash=fingerprint(request),
        parent_id=parent,
        baseline_hash="b" * 64,
        snapshot={"as_of": "2026-10-05", "rows": [{"income": Decimal("123.0000000000001"), "count": -1}]},
        assumptions={"states": {"暑假": "FINALIZED"}},
        calculation={"ready": True, "annual": {"income": "123.0000000000001"}},
        reason="test",
    )


class MemorySession:
    """Only emulate SQL I/O; all validation/serialization is real implementation."""

    def __init__(self) -> None:
        self.records: list[ForecastLedgerEvent] = []
        self.lock_count = 0

    async def execute(self, _statement: Any, _parameters: Any = None) -> None:
        self.lock_count += 1

    async def scalars(self, statement: Any) -> Any:
        department = statement.compile().params["department_id_1"]
        matches = sorted(
            (row for row in self.records if row.department_id == department), key=lambda row: row.event_no
        )

        class Result:
            def all(self) -> list[ForecastLedgerEvent]:
                return matches

        return Result()

    def add(self, record: ForecastLedgerEvent) -> None:
        self.records.append(record)

    async def flush(self) -> None:
        pass

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        before = self.records[:]
        try:
            yield
        except BaseException:
            self.records = before
            raise


@pytest.fixture
def memory_store() -> tuple[database.PostgreSQLForecastStore, MemorySession]:
    session = MemorySession()

    @asynccontextmanager
    async def sessions() -> AsyncIterator[AsyncSession]:
        yield session  # type: ignore[misc]

    return database.PostgreSQLForecastStore(sessions), session  # type: ignore[arg-type]


def test_prepare_preserves_snapshot_numbers_confirmation_and_all_version_ids(tmp_path: Path) -> None:
    legacy = ForecastStore(tmp_path)
    first = legacy_version(legacy)["version"]
    legacy.confirm(
        department="department",
        actor="owner",
        version_id=first["id"],
        request_id="confirm",
        request_hash=fingerprint("confirm"),
        reason="approved",
    )
    legacy_version(legacy, "next", first["id"])
    before = {path.name: path.read_bytes() for path in tmp_path.glob("event-*.json")}
    prepared = database.prepare_ledger("department", tmp_path)
    assert prepared.version_count == 2 and len(prepared.events) == 3
    assert prepared.chain_hash == fingerprint(prepared.events[-1])
    assert prepared.events[0]["version"]["snapshot"]["rows"][0] == {
        "income": "123.0000000000001",
        "count": -1,
    }
    assert ForecastStore._versions(prepared.events)[0]["status"] == "CONFIRMED"
    assert {path.name: path.read_bytes() for path in tmp_path.glob("event-*.json")} == before


@pytest.mark.parametrize("mutation", ["chain", "snapshot", "parent", "states", "sequence", "request"])
def test_independently_rehashed_corruptions_are_rejected(tmp_path: Path, mutation: str) -> None:
    legacy = ForecastStore(tmp_path)
    first = legacy_version(legacy)["version"]
    legacy_version(legacy, "next", first["id"])
    events = copy.deepcopy(database.prepare_ledger("department", tmp_path).events)
    if mutation == "chain":
        events[1]["previous_event_hash"] = "c" * 64
    elif mutation == "snapshot":
        events[0]["version"]["snapshot"]["rows"][0]["count"] = 0
    elif mutation == "parent":
        events[1]["version"]["parent_id"] = None
    elif mutation == "states":
        events[1]["version"]["states"] = {}
    elif mutation == "sequence":
        events[1]["version"]["sequence"] = 7
    else:
        events[1]["request_id"] = events[0]["request_id"]
    with pytest.raises(ForecastStoreError):
        database.validate_events(events, "department")


async def test_import_idempotency_exact_readback_and_historical_browsing_without_files(
    tmp_path: Path,
    memory_store: tuple[database.PostgreSQLForecastStore, MemorySession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, session = memory_store
    legacy = ForecastStore(tmp_path)
    first = legacy_version(legacy)["version"]
    legacy.confirm(
        department="department",
        actor="owner",
        version_id=first["id"],
        request_id="confirm",
        request_hash=fingerprint("confirm"),
        reason="approved",
    )
    second = legacy_version(legacy, "next", first["id"])["version"]
    prepared = database.prepare_ledger("department", tmp_path)
    receipt = await database.import_ledger(session, prepared, actor="migrator", reason="approved migration")
    assert receipt["created_events"] == 3 and receipt["verified"]
    assert (await database.import_ledger(session, prepared, actor="migrator", reason="replay"))[
        "created_events"
    ] == 0
    assert len(session.records) == 3
    assert all(row.origin == "FILE_IMPORT" and row.persisted_by == "migrator" for row in session.records)
    assert session.records[0].created_by == "owner"

    def forbidden_read(_path: Path, *args: Any, **kwargs: Any) -> bytes:
        raise AssertionError("runtime must never read file ledger or Excel")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    monkeypatch.setattr(Path, "read_text", forbidden_read)
    assert await store.current("department") == legacy._view(prepared.events)
    assert (await store.version("department", first["id"]))["version"]["status"] == "CONFIRMED"
    assert (await store.version("department", second["id"]))["latest_version_id"] == second["id"]
    assert await store.version("another", first["id"]) is None
    assert (await store.current("another"))["version"] is None
    assert [item["id"] for item in await store.versions("department")] == [second["id"], first["id"]]
    assert (await store.request_result("department", "create", fingerprint("create")))["version"][
        "id"
    ] == first["id"]
    with pytest.raises(ForecastStoreError, match="不同操作"):
        await store.request_result("department", "create", "different")


async def test_runtime_append_atomic_freeze_confirm_replay_and_stale_parent(
    memory_store: tuple[database.PostgreSQLForecastStore, MemorySession],
) -> None:
    store, session = memory_store
    snapshot = {"as_of": "2026-10-05", "rows": [{"income": Decimal("123.0000000000001"), "count": 0}]}
    inputs = {
        "department": "department",
        "actor": "owner",
        "request_id": "create",
        "request_hash": "hash",
        "parent_id": None,
        "baseline_hash": "b" * 64,
        "snapshot": snapshot,
        "assumptions": {"states": {"暑假": "FINALIZED"}},
        "calculation": {"ready": True, "annual": {"income": "123.0000000000001"}},
        "reason": "test",
    }
    result = await store.append_version(**inputs)
    snapshot["rows"][0]["count"] = 999
    loaded = await store.current("department")
    assert loaded["version"]["snapshot"]["rows"][0]["count"] == 0
    assert loaded["version"]["snapshot"]["rows"][0]["income"] == "123.0000000000001"
    with pytest.raises(ForecastStoreError, match="已变化"):
        await store.append_version(**{**inputs, "request_id": "stale"})
    assert len(session.records) == 1
    confirmation = {
        "department": "department",
        "actor": "owner",
        "version_id": result["version"]["id"],
        "request_id": "confirm",
        "request_hash": "confirmation",
        "reason": "approved",
    }
    confirmed = await store.confirm(**confirmation)
    assert confirmed["version"]["status"] == "CONFIRMED"
    second = await store.append_version(
        **{**inputs, "request_id": "next", "parent_id": result["version"]["id"]}
    )
    assert second["history"][1]["status"] == "CONFIRMED"
    assert (await store.confirm(**confirmation))["version"]["id"] == result["version"]["id"]
    assert (await store.append_version(**inputs))["version"]["id"] == result["version"]["id"]
    assert len(session.records) == 3 and session.lock_count == 6


async def test_partial_import_conflict_cannot_overwrite_or_drop_history(
    tmp_path: Path,
    memory_store: tuple[database.PostgreSQLForecastStore, MemorySession],
) -> None:
    store, session = memory_store
    legacy = ForecastStore(tmp_path)
    first = legacy_version(legacy)["version"]
    prefix = database.prepare_ledger("department", tmp_path)
    legacy_version(legacy, "next", first["id"])
    all_events = database.prepare_ledger("department", tmp_path)
    await database.import_ledger(session, prefix, actor="migrator", reason="initial")
    assert (await database.import_ledger(session, all_events, actor="migrator", reason="extend"))[
        "created_events"
    ] == 1
    assert (await database.import_ledger(session, prefix, actor="migrator", reason="older prefix"))[
        "event_count"
    ] == 2
    changed = copy.deepcopy(prefix.events)
    changed[0]["version"]["reason"] = "rewritten"
    with pytest.raises(ForecastStoreError, match="冲突"):
        await database.import_ledger(
            session,
            database.PreparedForecastLedger("department", changed),
            actor="migrator",
            reason="conflict",
        )
    assert len((await store.current("department"))["history"]) == 2


async def test_database_metadata_or_bytes_tampering_is_detected(
    tmp_path: Path,
    memory_store: tuple[database.PostgreSQLForecastStore, MemorySession],
) -> None:
    store, session = memory_store
    legacy_version(ForecastStore(tmp_path))
    await database.import_ledger(
        session, database.prepare_ledger("department", tmp_path), actor="migrator", reason="initial"
    )
    session.records[0].request_hash = "changed"
    with pytest.raises(ForecastStoreError, match="完整性"):
        await store.current("department")
    session.records[0].request_hash = fingerprint("create")
    parsed = json.loads(session.records[0].canonical_event)
    parsed["version"]["snapshot"]["rows"][0]["count"] = 100
    session.records[0].canonical_event = canonical(parsed)
    with pytest.raises(ForecastStoreError, match="完整性"):
        await store.current("department")


async def test_repeated_import_keeps_new_runtime_confirmation_without_readback_false_alarm(
    tmp_path: Path,
    memory_store: tuple[database.PostgreSQLForecastStore, MemorySession],
) -> None:
    store, session = memory_store
    legacy_version(ForecastStore(tmp_path))
    prepared = database.prepare_ledger("department", tmp_path)
    await database.import_ledger(session, prepared, actor="migrator", reason="initial")
    before = ForecastStore._versions(prepared.events)
    await store.confirm(
        department="department",
        actor="owner",
        version_id=before[0]["id"],
        request_id="runtime-confirm",
        request_hash="confirmation",
        reason="approved after import",
    )
    replay = await database.import_ledger(session, prepared, actor="migrator", reason="repeat after live use")
    assert replay["created_events"] == 0 and replay["latest_status"] == "CONFIRMED"
    after = await store.versions("department")
    verify_imported_versions(before, after)
    tampered = copy.deepcopy(after)
    tampered[0]["snapshot"]["rows"][0]["count"] = 500
    with pytest.raises(ValueError, match="原始历史"):
        verify_imported_versions(before, tampered)
