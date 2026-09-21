"""Add combination replacement and confirmed campus entry sets.

Revision ID: 20260901_0008
Revises: 20260901_0007
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0008"
down_revision: str | None = "20260901_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_combination_origin", "budget_combination", type_="check")
    op.drop_constraint("ck_combination_history_enrollment", "budget_combination", type_="check")
    op.add_column("budget_combination", sa.Column("change_reason", sa.String(500), nullable=True))
    op.add_column(
        "budget_combination",
        sa.Column("class_size_parameter_reference", sa.String(200), nullable=True),
    )
    op.create_check_constraint(
        "ck_combination_origin",
        "budget_combination",
        "origin_type IN ('HISTORICAL', 'MANUAL')",
    )
    op.create_check_constraint(
        "ck_combination_history_enrollment",
        "budget_combination",
        "(origin_type = 'HISTORICAL' AND historical_enrollment > 0) OR "
        "(origin_type = 'MANUAL' AND historical_enrollment = 0)",
    )

    op.create_table(
        "campus_entry_set_version",
        sa.Column("campus_entry_set_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("basis_combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_total_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year_entry_total", sa.BigInteger(), nullable=False),
        sa.Column("determined_total", sa.BigInteger(), nullable=False),
        sa.Column("total_difference", sa.BigInteger(), nullable=False),
        sa.Column("incomplete_count", sa.BigInteger(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year_entry_total >= 0", name="ck_campus_entry_set_total"),
        sa.CheckConstraint("determined_total >= 0", name="ck_campus_entry_set_determined"),
        sa.CheckConstraint("incomplete_count >= 0", name="ck_campus_entry_set_incomplete"),
        sa.CheckConstraint("record_count > 0", name="ck_campus_entry_set_record_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_campus_entry_set_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_campus_entry_set_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"],
            ["budget_revision.budget_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["basis_combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_total_version_id"],
            ["entry_total_version.entry_total_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_version_id"],
            ["campus_entry_suggestion_version.campus_entry_suggestion_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_campus_entry_set_revision"),
    )
    op.create_table(
        "campus_entry_allocation",
        sa.Column("campus_entry_allocation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campus_entry_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("campus_stable_key", sa.String(64), nullable=False),
        sa.Column("suggested_entry_value", sa.BigInteger(), nullable=False),
        sa.Column("determined_entry_value", sa.BigInteger(), nullable=True),
        sa.Column("adjustment_difference", sa.BigInteger(), nullable=True),
        sa.Column("adjustment_reason_category", sa.String(40), nullable=True),
        sa.Column("adjustment_reason_detail", sa.String(500), nullable=True),
        sa.Column("performance_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("classroom_resource", postgresql.JSONB(), nullable=False),
        sa.Column("sales_resource", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "determined_entry_value IS NULL OR determined_entry_value >= 0",
            name="ck_campus_allocation_determined",
        ),
        sa.CheckConstraint("char_length(campus_stable_key) = 64", name="ck_campus_allocation_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_campus_allocation_hash"),
        sa.CheckConstraint(
            "adjustment_reason_category IS NULL OR adjustment_reason_category IN "
            "('PERFORMANCE_JUDGMENT', 'CLASSROOM_RESOURCE', 'SALES_RESOURCE', "
            "'NEW_CAMPUS_OR_PRODUCT', 'STRATEGIC_ADJUSTMENT', 'OTHER')",
            name="ck_campus_allocation_reason_category",
        ),
        sa.ForeignKeyConstraint(
            ["campus_entry_set_version_id"],
            ["campus_entry_set_version.campus_entry_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "campus_entry_set_version_id", "campus_stable_key", name="uq_campus_entry_allocation"
        ),
    )
    op.create_table(
        "campus_entry_confirmation",
        sa.Column("campus_entry_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campus_entry_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_campus_entry_confirmation_hash"),
        sa.ForeignKeyConstraint(
            ["campus_entry_set_version_id"],
            ["campus_entry_set_version.campus_entry_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_revision_id"],
            ["budget_revision.budget_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "campus_entry_set_version_id", "input_fingerprint", name="uq_campus_entry_confirmation"
        ),
    )
    for table_name in (
        "campus_entry_set_version",
        "campus_entry_allocation",
        "campus_entry_confirmation",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in (
        "campus_entry_confirmation",
        "campus_entry_allocation",
        "campus_entry_set_version",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
    op.drop_constraint("ck_combination_history_enrollment", "budget_combination", type_="check")
    op.drop_constraint("ck_combination_origin", "budget_combination", type_="check")
    op.drop_column("budget_combination", "class_size_parameter_reference")
    op.drop_column("budget_combination", "change_reason")
    op.create_check_constraint(
        "ck_combination_history_enrollment", "budget_combination", "historical_enrollment > 0"
    )
    op.create_check_constraint("ck_combination_origin", "budget_combination", "origin_type = 'HISTORICAL'")
