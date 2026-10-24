"""Canonical migration and database-only runtime behavior without external BI."""

import copy
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.forecast import database_store as database
from packages.forecast.store import ForecastStore, ForecastStoreError, canonical, fingerprint
from packages.persistence.models import ForecastLedgerEvent
from scripts.import_forecast_ledger import verify_imported_versions


class AppendInputs(TypedDict):
    department: str
    actor: str
    request_id: str
    request_hash: str
    parent_id: str | None
    baseline_hash: str
    snapshot: dict[str, Any]
    assumptions: dict[str, Any]
    calculation: dict[str, Any]
    reason: str


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

    def as_async_session(self) -> AsyncSession:
        # This test double implements only the SQL I/O used by the real store.
        # Keep the deliberate substitution at this boundary, not in assertions.
        return cast(AsyncSession, self)

    async def execute(self, _statement: Any, _parameters: Any = None) -> Any:
        if getattr(_statement, "is_select", False):
            from types import SimpleNamespace

            department = _statement.compile().params["department_id_1"]
            rows = [record for record in self.records if record.department_id == department]
            head = max(rows, key=lambda record: record.event_no) if rows else None
            return SimpleNamespace(one_or_none=lambda: head)
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


async def test_read_cache_checks_head_and_invalidates_confirmation_without_revalidating_unchanged_chain(
    tmp_path: Path,
    memory_store: tuple[database.PostgreSQLForecastStore, MemorySession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, session = memory_store
    first = legacy_version(ForecastStore(tmp_path))["version"]
    await database.import_ledger(
        session.as_async_session(),
        database.prepare_ledger("department", tmp_path),
        actor="test",
        reason="test",
    )
    original = database.read_events
    reads = 0

    async def counted(active: AsyncSession, department: str) -> list[dict[str, Any]]:
        nonlocal reads
        reads += 1
        return await original(active, department)

    monkeypatch.setattr(database, "read_events", counted)
    viewed = await store.current("department")
    viewed["version"]["snapshot"]["rows"][0]["count"] = 999
    assert (await store.current("department"))["version"]["snapshot"]["rows"][0]["count"] == -1
    assert await store.latest_confirmed("department") is None
    assert reads == 1
    await store.confirm(
        department="department",
        actor="owner",
        version_id=first["id"],
        request_id="confirmation",
        request_hash="confirmation",
        reason="approved",
    )
    reads = 0
    assert (await store.current("department"))["version"]["status"] == "CONFIRMED"
    confirmed = await store.latest_confirmed("department")
    assert confirmed is not None and confirmed["id"] == first["id"]
    assert reads == 1
    assert (await store.current("other-department"))["version"] is None


@pytest.fixture
def memory_store() -> tuple[database.PostgreSQLForecastStore, MemorySession]:
    session = MemorySession()

    @asynccontextmanager
    async def sessions() -> AsyncIterator[AsyncSession]:
        yield session.as_async_session()

    # The store only calls the factory to enter a session context; the double
    # intentionally avoids creating an engine or accessing any real database.
    factory = cast(async_sessionmaker[AsyncSession], sessions)
    return database.PostgreSQLForecastStore(factory), session


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
    receipt = await database.import_ledger(
        session.as_async_session(), prepared, actor="migrator", reason="approved migration"
    )
    assert receipt["created_events"] == 3 and receipt["verified"]
    assert (
        await database.import_ledger(session.as_async_session(), prepared, actor="migrator", reason="replay")
    )["created_events"] == 0
    assert len(session.records) == 3
    assert all(row.origin == "FILE_IMPORT" and row.persisted_by == "migrator" for row in session.records)
    assert session.records[0].created_by == "owner"

    def forbidden_read(_path: Path, *args: Any, **kwargs: Any) -> bytes:
        raise AssertionError("runtime must never read file ledger or Excel")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    monkeypatch.setattr(Path, "read_text", forbidden_read)
    assert await store.current("department") == legacy._view(prepared.events)
    first_saved = await store.version("department", first["id"])
    assert first_saved is not None
    assert first_saved["version"]["status"] == "CONFIRMED"
    second_saved = await store.version("department", second["id"])
    assert second_saved is not None
    assert second_saved["latest_version_id"] == second["id"]
    assert await store.version("another", first["id"]) is None
    assert (await store.current("another"))["version"] is None
    assert [item["id"] for item in await store.versions("department")] == [second["id"], first["id"]]
    replayed = await store.request_result("department", "create", fingerprint("create"))
    assert replayed is not None
    assert replayed["version"]["id"] == first["id"]
    with pytest.raises(ForecastStoreError, match="不同操作"):
        await store.request_result("department", "create", "different")


async def test_runtime_append_atomic_freeze_confirm_replay_and_stale_parent(
    memory_store: tuple[database.PostgreSQLForecastStore, MemorySession],
) -> None:
    store, session = memory_store
    snapshot: dict[str, Any] = {
        "as_of": "2026-10-05",
        "rows": [{"income": Decimal("123.0000000000001"), "count": 0}],
    }
    inputs: AppendInputs = {
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
    stale_inputs = inputs.copy()
    stale_inputs["request_id"] = "stale"
    with pytest.raises(ForecastStoreError, match="已变化"):
        await store.append_version(**stale_inputs)
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
    await database.import_ledger(session.as_async_session(), prefix, actor="migrator", reason="initial")
    assert (
        await database.import_ledger(
            session.as_async_session(), all_events, actor="migrator", reason="extend"
        )
    )["created_events"] == 1
    assert (
        await database.import_ledger(
            session.as_async_session(), prefix, actor="migrator", reason="older prefix"
        )
    )["event_count"] == 2
    changed = copy.deepcopy(prefix.events)
    changed[0]["version"]["reason"] = "rewritten"
    with pytest.raises(ForecastStoreError, match="冲突"):
        await database.import_ledger(
            session.as_async_session(),
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
        session.as_async_session(),
        database.prepare_ledger("department", tmp_path),
        actor="migrator",
        reason="initial",
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
    await database.import_ledger(session.as_async_session(), prepared, actor="migrator", reason="initial")
    before = ForecastStore._versions(prepared.events)
    await store.confirm(
        department="department",
        actor="owner",
        version_id=before[0]["id"],
        request_id="runtime-confirm",
        request_hash="confirmation",
        reason="approved after import",
    )
    replay = await database.import_ledger(
        session.as_async_session(), prepared, actor="migrator", reason="repeat after live use"
    )
    assert replay["created_events"] == 0 and replay["latest_status"] == "CONFIRMED"
    after = await store.versions("department")
    verify_imported_versions(before, after)
    tampered = copy.deepcopy(after)
    tampered[0]["snapshot"]["rows"][0]["count"] = 500
    with pytest.raises(ValueError, match="原始历史"):
        verify_imported_versions(before, tampered)
