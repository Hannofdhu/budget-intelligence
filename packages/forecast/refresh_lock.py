"""A dedicated, fail-fast BI refresh lock shared by HTTP and background workers.

This is deliberately a different advisory-lock namespace from the short ledger
append transaction. Holding it while saving a version cannot deadlock that append.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from packages.persistence.database import get_engine
from packages.source_snapshot.adapter import SourceAdapterError


def refresh_lock_id(department: str, fiscal_year: int = 2027) -> int:
    return int.from_bytes(
        hashlib.sha256(f"forecast-source-refresh:{department}:{fiscal_year}".encode()).digest()[:8],
        "big",
        signed=True,
    )


@asynccontextmanager
async def refresh_slot(
    department: str, *, fiscal_year: int = 2027, engine: AsyncEngine | None = None
) -> AsyncIterator[None]:
    lock_id = refresh_lock_id(department, fiscal_year)
    async with (engine or get_engine()).connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id})
        if not acquired:
            raise SourceAdapterError(
                "FORECAST_REFRESH_IN_PROGRESS",
                "本部门正在更新预测，请等待本次更新完成后再试；不会重复取数",
                retryable=True,
            )
        try:
            yield
        finally:
            # Never return a pooled connection with a session-level lock held.
            # Invalidation closes the physical connection if unlock is interrupted.
            try:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
            except BaseException:
                await connection.invalidate()
                raise
