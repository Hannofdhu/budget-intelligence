"""Real PostgreSQL snapshot migration, immutable evidence and concurrent writers.

All writes target a fresh test-only schema, dropped in finally. No real forecast
department, existing version or shared application table is changed.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Table, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.config.settings import get_settings
from packages.forecast import database_store as database
from packages.forecast.store import ForecastStore, ForecastStoreError, fingerprint
from packages.persistence.models import ForecastLedgerEvent

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1", reason="requires isolated PostgreSQL"
)


@asynccontextmanager
async def isolated_store() -> AsyncIterator[
    tuple[database.PostgreSQLForecastStore, async_sessionmaker[AsyncSession]]
]:
    schema = f"forecast_test_{uuid.uuid4().hex}"
    administration = create_async_engine(get_settings().database_url)
    engine = create_async_engine(
        get_settings().database_url,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    try:
        async with administration.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            table = ForecastLedgerEvent.__table__
            assert isinstance(table, Table)
            await connection.run_sync(table.create)
            await connection.execute(
                text(
                    "CREATE TRIGGER forecast_ledger_event_immutable BEFORE UPDATE OR DELETE ON "
                    "forecast_ledger_event FOR EACH ROW EXECUTE FUNCTION "
                    "public.prevent_immutable_version_mutation()"
                )
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield database.PostgreSQLForecastStore(factory), factory
    finally:
        await engine.dispose()
        # The target is a freshly generated, exact test-only schema, never public.
        async with administration.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await administration.dispose()


def inputs(department: str, request_id: str = "create", parent_id: str | None = None) -> dict[str, Any]:
    return {
        "department": department,
        "actor": "synthetic-test",
        "request_id": request_id,
        "request_hash": fingerprint(request_id),
        "parent_id": parent_id,
        "baseline_hash": "b" * 64,
        "snapshot": {
            "as_of": "2026-10-05",
            "rows": [{"income": Decimal("123.0000000000001"), "count": -1}],
        },
        "assumptions": {"states": {"暑假": "FINALIZED"}},
        "calculation": {"ready": True, "annual": {"income": "123.0000000000001"}},
        "reason": "synthetic integration test",
    }


async def test_postgresql_import_exact_versions_confirmation_no_files_and_immutable_triggers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    department = f"forecast-test-{uuid.uuid4()}"
    legacy = ForecastStore(tmp_path)
    first = legacy.append_version(**inputs(department))["version"]
    legacy.confirm(
        department=department,
        actor="synthetic-test",
        version_id=first["id"],
        request_id="confirm",
        request_hash=fingerprint("confirm"),
        reason="approved",
    )
    legacy.append_version(**inputs(department, "next", first["id"]))
    prepared = database.prepare_ledger(department, tmp_path)
    expected = legacy.current(department)
    async with isolated_store() as (store, factory):
        async with factory() as session, session.begin():
            receipt = await database.import_ledger(session, prepared, actor="test-importer", reason="test")
            assert receipt["created_events"] == 3 and receipt["verified"]
        async with factory() as session, session.begin():
            assert (await database.import_ledger(session, prepared, actor="test-importer", reason="replay"))[
                "created_events"
            ] == 0
            assert await session.scalar(select(func.count()).select_from(ForecastLedgerEvent)) == 3
            event_id = await session.scalar(select(ForecastLedgerEvent.event_id).limit(1))
            for statement in (
                update(ForecastLedgerEvent)
                .where(ForecastLedgerEvent.event_id == event_id)
                .values(persistence_reason="overwrite"),
                delete(ForecastLedgerEvent).where(ForecastLedgerEvent.event_id == event_id),
            ):
                with pytest.raises(DBAPIError, match="immutable"):
                    async with session.begin_nested():
                        await session.execute(statement)

        def forbidden_read(_path: Path, *args: Any, **kwargs: Any) -> bytes:
            raise AssertionError("runtime forecast snapshots must not depend on source files")

        monkeypatch.setattr(Path, "read_bytes", forbidden_read)
        monkeypatch.setattr(Path, "read_text", forbidden_read)
        assert await store.current(department) == expected
        selected = await store.version(department, first["id"])
        assert selected is not None and selected["version"]["status"] == "CONFIRMED"
        assert selected["version"]["snapshot"]["rows"][0]["income"] == "123.0000000000001"
        assert await store.version(f"other-{department}", first["id"]) is None
        assert (await store.current(f"other-{department}"))["version"] is None


async def test_postgresql_concurrent_idempotency_and_stale_parent_have_no_lost_versions() -> None:
    department = f"forecast-test-{uuid.uuid4()}"
    async with isolated_store() as (store, _factory):
        # Same command on two connections must create one exact version.
        first, repeated = await asyncio.gather(
            store.append_version(**inputs(department, "x" * 128)),
            store.append_version(**inputs(department, "x" * 128)),
        )
        assert first == repeated and len(first["history"]) == 1
        parent = first["version"]["id"]
        contenders = await asyncio.gather(
            store.append_version(**inputs(department, "writer-one", parent)),
            store.append_version(**inputs(department, "writer-two", parent)),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ForecastStoreError) for result in contenders) == 1
        assert len((await store.current(department))["history"]) == 2
        replay = await store.request_result(department, "x" * 128, fingerprint("x" * 128))
        assert replay == first
        with pytest.raises(ForecastStoreError, match="不同操作"):
            await store.request_result(department, "x" * 128, "different-payload")


async def test_postgresql_failed_transaction_leaves_no_partial_snapshot() -> None:
    department = f"forecast-test-{uuid.uuid4()}"
    async with isolated_store() as (store, factory):
        async with factory() as session:
            await session.begin()
            try:
                # Deliberately fail after a valid insertion/readback but before commit.
                legacy = ForecastStore()
                event = {
                    "kind": "VERSION",
                    "version": {
                        "id": str(uuid.uuid4()),
                        "sequence": 1,
                        "parent_id": None,
                        "baseline_hash": "b" * 64,
                        "baseline_kind": "APPROVED_FY27_HISTORICAL_ARCHIVE",
                        "as_of": "2026-10-05",
                        "created_at": "2026-10-09T01:00:00+00:00",
                        "created_by": "test",
                        "reason": "test",
                        "states": {"暑假": "FINALIZED"},
                        "assumptions": {"states": {"暑假": "FINALIZED"}},
                        "snapshot": {"as_of": "2026-10-05", "rows": []},
                        "snapshot_hash": fingerprint({"as_of": "2026-10-05", "rows": []}),
                        "calculation": {"ready": True},
                    },
                    "event_no": 1,
                    "department_id": department,
                    "actor": "test",
                    "request_id": "rolled-back",
                    "request_hash": "test",
                    "created_at": "2026-10-09T01:00:00+00:00",
                    "previous_event_hash": None,
                }
                prepared = database.PreparedForecastLedger(department, [event])
                await database.import_ledger(session, prepared, actor="test", reason="rollback test")
                assert legacy._view(await database.read_events(session, department))["version"] is not None
            finally:
                await session.rollback()
        assert (await store.current(department))["version"] is None
