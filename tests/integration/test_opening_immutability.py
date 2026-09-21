import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.persistence.database import get_engine

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1",
    reason="requires migrated isolated PostgreSQL and existing synthetic version fixtures",
)


async def test_opening_version_rejects_update_and_delete() -> None:
    async with get_engine().connect() as connection:
        transaction = await connection.begin()
        try:
            version_id = uuid.uuid4()
            inserted = await connection.execute(
                text("""
                INSERT INTO opening_student_version (
                    opening_student_version_id, budget_revision_id, source_component_id,
                    combination_set_version_id, rate_parameter_version_id, content,
                    content_hash, created_by, created_at
                ) SELECT :id, c.budget_revision_id, s.source_snapshot_component_id,
                    c.combination_set_version_id, r.rate_parameter_version_id,
                    '{}'::jsonb, repeat('a', 64), 'synthetic-immutability-test', now()
                FROM budget_combination_set_version c
                CROSS JOIN rate_parameter_version r CROSS JOIN source_snapshot_component s
                WHERE NOT EXISTS (SELECT 1 FROM opening_student_version o
                    WHERE o.budget_revision_id = c.budget_revision_id)
                LIMIT 1 RETURNING opening_student_version_id
            """),
                {"id": version_id},
            )
            if inserted.scalar_one_or_none() is None:
                pytest.skip("synthetic upstream fixtures required")
            for sql in (
                "UPDATE opening_student_version SET content = '{}'::jsonb "
                "WHERE opening_student_version_id = :id",
                "DELETE FROM opening_student_version WHERE opening_student_version_id = :id",
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError) as error:
                    await connection.execute(text(sql), {"id": version_id})
                assert "immutable" in str(error.value).lower()
                await savepoint.rollback()
        finally:
            await transaction.rollback()
