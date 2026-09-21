"""Add entry-scope governance and initial detail allocation.

Revision ID: 20260901_0009
Revises: 20260901_0008
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0009"
down_revision: str | None = "20260901_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TRIGGER budget_combination_immutable ON budget_combination")
    op.add_column("budget_combination", sa.Column("combination_stable_key", sa.String(64), nullable=True))
    op.execute(
        "UPDATE budget_combination SET combination_stable_key = "
        "md5(concat_ws('|', track, product, grade, subject, campus)) || "
        "md5(concat_ws('|', campus, subject, grade, product, track))"
    )
    op.alter_column("budget_combination", "combination_stable_key", nullable=False)
    op.create_check_constraint(
        "ck_combination_stable_key",
        "budget_combination",
        "char_length(combination_stable_key) = 64",
    )
    op.execute(
        "CREATE TRIGGER budget_combination_immutable BEFORE UPDATE OR DELETE ON "
        "budget_combination FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )

    op.create_table(
        "entry_scope_version",
        sa.Column("entry_scope_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("default_rule_version", sa.String(100), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_entry_scope_fiscal_year"),
        sa.CheckConstraint("record_count >= 0", name="ck_entry_scope_record_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_entry_scope_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_entry_scope_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_entry_scope_revision"),
    )
    op.create_table(
        "entry_scope_exception",
        sa.Column("entry_scope_exception_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_scope_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("exception_type", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_entry_scope_exception_fiscal_year"),
        sa.CheckConstraint("exception_type IN ('INCLUDE', 'EXCLUDE')", name="ck_entry_scope_exception_type"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_entry_scope_exception_hash"),
        sa.ForeignKeyConstraint(
            ["entry_scope_version_id"],
            ["entry_scope_version.entry_scope_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "entry_scope_version_id",
            "fiscal_year",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            "exception_type",
            name="uq_entry_scope_exception_key",
        ),
    )
    op.create_table(
        "entry_scope_confirmation",
        sa.Column("entry_scope_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_scope_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_entry_scope_confirmation_hash"),
        sa.ForeignKeyConstraint(
            ["entry_scope_version_id"],
            ["entry_scope_version.entry_scope_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_revision_id"],
            ["budget_revision.budget_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "entry_scope_version_id", "input_fingerprint", name="uq_entry_scope_confirmation"
        ),
    )
    op.create_table(
        "entry_scope_evaluation",
        sa.Column("entry_scope_evaluation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_scope_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_stable_key", sa.String(64), nullable=False),
        sa.Column("default_match", sa.Boolean(), nullable=False),
        sa.Column("manual_include_match", sa.Boolean(), nullable=False),
        sa.Column("manual_exclude_match", sa.Boolean(), nullable=False),
        sa.Column("effective_entry_attribute", sa.String(20), nullable=False),
        sa.Column("effective_source", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "effective_entry_attribute IN ('ENTRY', 'NON_ENTRY')",
            name="ck_entry_scope_evaluation_attribute",
        ),
        sa.CheckConstraint(
            "effective_source IN ('MANUAL_EXCLUDE', 'MANUAL_INCLUDE', 'DEFAULT_RULE', 'NOT_ENTRY')",
            name="ck_entry_scope_evaluation_source",
        ),
        sa.CheckConstraint("char_length(combination_stable_key) = 64", name="ck_entry_scope_evaluation_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_entry_scope_evaluation_hash"),
        sa.ForeignKeyConstraint(
            ["entry_scope_version_id"],
            ["entry_scope_version.entry_scope_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["combination_id"], ["budget_combination.combination_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "entry_scope_version_id", "combination_stable_key", name="uq_entry_scope_evaluation"
        ),
    )
    op.create_table(
        "entry_detail_set_version",
        sa.Column("entry_detail_set_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_scope_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campus_entry_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("source_fiscal_season", sa.Integer(), nullable=False),
        sa.Column("campus_entry_total", sa.BigInteger(), nullable=False),
        sa.Column("allocated_entry_total", sa.BigInteger(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("zero_initial_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_entry_detail_fiscal_year"),
        sa.CheckConstraint("source_fiscal_season > 0", name="ck_entry_detail_source_season"),
        sa.CheckConstraint("campus_entry_total >= 0", name="ck_entry_detail_campus_total"),
        sa.CheckConstraint(
            "allocated_entry_total = campus_entry_total", name="ck_entry_detail_allocated_total"
        ),
        sa.CheckConstraint("record_count > 0", name="ck_entry_detail_record_count"),
        sa.CheckConstraint("zero_initial_count >= 0", name="ck_entry_detail_zero_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_entry_detail_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_entry_detail_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
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
            ["campus_entry_set_version_id"],
            ["campus_entry_set_version.campus_entry_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_entry_detail_revision"),
    )
    op.create_table(
        "entry_detail_allocation",
        sa.Column("entry_detail_allocation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_detail_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_stable_key", sa.String(64), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("effective_entry_attribute", sa.String(20), nullable=False),
        sa.Column("scope_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("origin_type", sa.String(30), nullable=False),
        sa.Column("historical_business_new_students", sa.BigInteger(), nullable=False),
        sa.Column("manual_reservation_ratio_ppm", sa.Integer(), nullable=True),
        sa.Column("manual_reservation_reason", sa.String(500), nullable=True),
        sa.Column("allocation_source_type", sa.String(40), nullable=False),
        sa.Column("theoretical_value", sa.Numeric(20, 9), nullable=False),
        sa.Column("floor_value", sa.BigInteger(), nullable=False),
        sa.Column("fraction_value", sa.Numeric(20, 9), nullable=False),
        sa.Column("tail_assigned", sa.Boolean(), nullable=False),
        sa.Column("entry_initial_value", sa.BigInteger(), nullable=False),
        sa.Column("soft_lower_bound", sa.BigInteger(), nullable=True),
        sa.Column("soft_upper_bound", sa.BigInteger(), nullable=True),
        sa.Column("candidate_eligible", sa.Boolean(), nullable=False),
        sa.Column("candidate_blocking_reason", sa.String(100), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("effective_entry_attribute = 'ENTRY'", name="ck_entry_detail_effective_entry"),
        sa.CheckConstraint(
            "allocation_source_type IN ('MANUAL_RESERVATION', 'HISTORICAL_SHARE')",
            name="ck_entry_detail_source_type",
        ),
        sa.CheckConstraint("historical_business_new_students >= 0", name="ck_entry_detail_history"),
        sa.CheckConstraint("entry_initial_value >= 0", name="ck_entry_detail_initial"),
        sa.CheckConstraint(
            "manual_reservation_ratio_ppm IS NULL OR manual_reservation_ratio_ppm BETWEEN 0 AND 1000000",
            name="ck_entry_detail_manual_ratio",
        ),
        sa.CheckConstraint(
            "manual_reservation_ratio_ppm IS NULL OR manual_reservation_reason IS NOT NULL",
            name="ck_entry_detail_manual_reason",
        ),
        sa.CheckConstraint(
            "soft_lower_bound IS NULL OR soft_lower_bound >= 0", name="ck_entry_detail_soft_lower"
        ),
        sa.CheckConstraint(
            "soft_upper_bound IS NULL OR soft_upper_bound >= soft_lower_bound",
            name="ck_entry_detail_soft_upper",
        ),
        sa.CheckConstraint(
            "(entry_initial_value = 0 AND candidate_eligible = FALSE "
            "AND soft_lower_bound IS NULL AND soft_upper_bound IS NULL) OR "
            "(entry_initial_value > 0 AND candidate_eligible = TRUE "
            "AND soft_lower_bound IS NOT NULL AND soft_upper_bound IS NOT NULL)",
            name="ck_entry_detail_candidate_eligibility",
        ),
        sa.CheckConstraint(
            "char_length(combination_stable_key) = 64", name="ck_entry_detail_combination_key"
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_entry_detail_row_hash"),
        sa.ForeignKeyConstraint(
            ["entry_detail_set_version_id"],
            ["entry_detail_set_version.entry_detail_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["combination_id"], ["budget_combination.combination_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "entry_detail_set_version_id", "combination_stable_key", name="uq_entry_detail_allocation"
        ),
    )
    for table_name in (
        "entry_scope_version",
        "entry_scope_exception",
        "entry_scope_evaluation",
        "entry_scope_confirmation",
        "entry_detail_set_version",
        "entry_detail_allocation",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in (
        "entry_detail_allocation",
        "entry_detail_set_version",
        "entry_scope_confirmation",
        "entry_scope_evaluation",
        "entry_scope_exception",
        "entry_scope_version",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
    op.drop_constraint("ck_combination_stable_key", "budget_combination", type_="check")
    op.drop_column("budget_combination", "combination_stable_key")
