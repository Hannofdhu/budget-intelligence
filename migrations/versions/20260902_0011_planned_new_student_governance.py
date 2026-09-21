"""Add immutable planned-new-student input versions.

Revision ID: 20260902_0011
Revises: 20260902_0010
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0011"
down_revision: str | None = "20260902_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planned_new_student_version",
        sa.Column("planned_new_student_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_planned_new_student_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_scope_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_detail_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(30), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("locked_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_planned_new_student_version_year"),
        sa.CheckConstraint("record_count > 0", name="ck_planned_new_student_version_count"),
        sa.CheckConstraint(
            "locked_count BETWEEN 0 AND record_count",
            name="ck_planned_new_student_version_locked_count",
        ),
        sa.CheckConstraint(
            "operation_type IN ('CHANGE_AND_LOCK', 'UNLOCK')",
            name="ck_planned_new_student_version_operation",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_planned_new_student_version_hash"),
        sa.CheckConstraint(
            "char_length(request_hash) = 64", name="ck_planned_new_student_version_request_hash"
        ),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_planned_new_student_version_id"],
            ["planned_new_student_version.planned_new_student_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_scope_version_id"],
            ["entry_scope_version.entry_scope_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_detail_set_version_id"],
            ["entry_detail_set_version.entry_detail_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_planned_new_student_version_revision"),
    )
    op.create_table(
        "planned_new_student_count",
        sa.Column("planned_new_student_count_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("planned_new_student_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_detail_allocation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("planned_new_student_stable_key", sa.String(64), nullable=False),
        sa.Column("combination_stable_key", sa.String(64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(30), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("entry_attribute", sa.String(20), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("planned_new_students", sa.BigInteger(), nullable=False),
        sa.Column("manual_reason", sa.String(500), nullable=False),
        sa.Column("calculated_new_student_share_ppm", sa.Integer(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("locked_value", sa.BigInteger(), nullable=True),
        sa.Column("handling_status", sa.String(30), nullable=False),
        sa.Column("lock_origin", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_planned_new_student_year"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_planned_new_student_season"),
        sa.CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_planned_new_student_entry"),
        sa.CheckConstraint("planned_new_students >= 0", name="ck_planned_new_student_value"),
        sa.CheckConstraint(
            "(locked = TRUE AND locked_value = planned_new_students) OR "
            "(locked = FALSE AND locked_value IS NULL)",
            name="ck_planned_new_student_lock",
        ),
        sa.CheckConstraint(
            "(planned_new_students > 0 AND calculated_new_student_share_ppm = 1000000) OR "
            "(planned_new_students = 0 AND calculated_new_student_share_ppm IS NULL)",
            name="ck_planned_new_student_share",
        ),
        sa.CheckConstraint(
            "(locked = FALSE AND handling_status = 'FILLED_PENDING_LOCK') OR "
            "(locked = TRUE AND planned_new_students = 0 AND handling_status = 'LOCKED_ZERO') OR "
            "(locked = TRUE AND planned_new_students > 0 AND handling_status = 'LOCKED_POSITIVE')",
            name="ck_planned_new_student_status",
        ),
        sa.CheckConstraint(
            "char_length(planned_new_student_stable_key) = 64",
            name="ck_planned_new_student_stable_key",
        ),
        sa.CheckConstraint(
            "char_length(combination_stable_key) = 64",
            name="ck_planned_new_student_combination_key",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_planned_new_student_hash"),
        sa.ForeignKeyConstraint(
            ["planned_new_student_version_id"],
            ["planned_new_student_version.planned_new_student_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["combination_id"], ["budget_combination.combination_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["entry_detail_allocation_id"],
            ["entry_detail_allocation.entry_detail_allocation_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "planned_new_student_version_id",
            "planned_new_student_stable_key",
            name="uq_planned_new_student_key",
        ),
    )
    for table_name in ("planned_new_student_version", "planned_new_student_count"):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in ("planned_new_student_count", "planned_new_student_version"):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
