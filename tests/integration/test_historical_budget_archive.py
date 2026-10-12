"""Real PostgreSQL archive integrity, scoped and fully rolled back after each test."""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.database import get_session_factory
from packages.persistence.models import HistoricalBudgetArchive
from packages.source_snapshot import historical_archive as archive
from tests.unit.test_fy27_source_acceptance import (
    adopted_files as adopted_files,
)
from tests.unit.test_fy27_source_acceptance import (
    legacy_payload as legacy_payload,
)

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1", reason="requires isolated PostgreSQL"
)


@pytest.mark.asyncio
async def test_postgresql_roundtrip_idempotency_immutability_and_tenant_isolation(
    adopted_files: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = archive.prepare_archive(
        adopted_files["source"], adopted_files["baseline"], adopted_files["manifest"]
    )
    department = f"archive-test-{uuid.uuid4()}"
    async with get_session_factory()() as session:
        await session.begin()
        try:
            archive_id, created = await archive.import_archive(
                session, prepared, department_id=department, actor="test", reason="synthetic test"
            )
            assert created
            assert await archive.import_archive(
                session, prepared, department_id=department, actor="test", reason="replay"
            ) == (archive_id, False)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(HistoricalBudgetArchive)
                    .where(HistoricalBudgetArchive.department_id == department)
                )
                == 1
            )
            receipt = await archive.verify_database_archive(session, archive_id)
            assert receipt["verified"] and receipt["annual_revenue"] == "100"

            @asynccontextmanager
            async def same_transaction() -> AsyncIterator[AsyncSession]:
                yield session

            monkeypatch.setattr(archive, "get_session_factory", lambda: same_transaction)

            def unavailable_file(_path: Path) -> bytes:
                raise AssertionError("runtime must not read local Excel or extraction files")

            monkeypatch.setattr(Path, "read_bytes", unavailable_file)
            assert await archive.read_database_review(department) == prepared.review
            with pytest.raises(FileNotFoundError):
                await archive.read_database_review(f"other-{department}")
            for statement in (
                update(HistoricalBudgetArchive)
                .where(HistoricalBudgetArchive.archive_id == archive_id)
                .values(import_reason="overwrite"),
                delete(HistoricalBudgetArchive).where(HistoricalBudgetArchive.archive_id == archive_id),
            ):
                with pytest.raises(DBAPIError, match="immutable"):
                    async with session.begin_nested():
                        await session.execute(statement)
            assert await archive.verify_database_archive(session, archive_id) == receipt
        finally:
            await session.rollback()
