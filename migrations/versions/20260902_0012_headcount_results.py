"""Add immutable four-season headcount results.

Revision ID: 20260902_0012
Revises: 20260902_0011
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0012"
down_revision: str | None = "20260902_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "headcount_result_version",
        sa.Column("headcount_result_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_scope_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_detail_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("planned_new_student_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("calculation_rule_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("publishable", sa.Boolean(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("validation_count", sa.BigInteger(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_headcount_result_version_year"),
        sa.CheckConstraint("record_count > 0", name="ck_headcount_result_version_count"),
        sa.CheckConstraint("validation_count >= 0", name="ck_headcount_result_validation_count"),
        sa.CheckConstraint("status = 'SUCCEEDED'", name="ck_headcount_result_version_status"),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_headcount_result_input_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_headcount_result_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_headcount_result_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_scope_version_id"], ["entry_scope_version.entry_scope_version_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["entry_detail_set_version_id"],
            ["entry_detail_set_version.entry_detail_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rate_parameter_version_id"],
            ["rate_parameter_version.rate_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["planned_new_student_version_id"],
            ["planned_new_student_version.planned_new_student_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_headcount_result_version_revision"),
    )
    op.create_table(
        "season_headcount_result",
        sa.Column("season_headcount_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("headcount_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_detail_allocation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("renewal_rate_parameter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("new_student_share_parameter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("planned_new_student_count_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("combination_stable_key", sa.String(64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("season_index", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(30), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("entry_attribute", sa.String(20), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("calculation_status", sa.String(20), nullable=False),
        sa.Column("new_student_source", sa.String(30), nullable=True),
        sa.Column("planned_eligibility_status", sa.String(30), nullable=False),
        sa.Column("theoretical_old_students", sa.Numeric(20, 9), nullable=True),
        sa.Column("theoretical_new_students", sa.Numeric(20, 9), nullable=True),
        sa.Column("theoretical_old_fraction", sa.String(200), nullable=True),
        sa.Column("theoretical_new_fraction", sa.String(200), nullable=True),
        sa.Column("integer_old_students", sa.BigInteger(), nullable=True),
        sa.Column("integer_new_students", sa.BigInteger(), nullable=True),
        sa.Column("integer_total_students", sa.BigInteger(), nullable=True),
        sa.Column("renewal_rate_ppm", sa.Integer(), nullable=True),
        sa.Column("new_student_share_input_ppm", sa.Integer(), nullable=True),
        sa.Column("realized_retention_rate_ppm", sa.Integer(), nullable=True),
        sa.Column("realized_new_student_share_ppm", sa.Integer(), nullable=True),
        sa.Column("old_rounding_variance", sa.Numeric(20, 9), nullable=True),
        sa.Column("new_rounding_variance", sa.Numeric(20, 9), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_season_headcount_year"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_season_headcount_season"),
        sa.CheckConstraint("season_index BETWEEN 1 AND 4", name="ck_season_headcount_index"),
        sa.CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_season_headcount_entry"),
        sa.CheckConstraint(
            "calculation_status IN ('CALCULATED', 'BLOCKED')", name="ck_season_headcount_status"
        ),
        sa.CheckConstraint(
            "new_student_source IS NULL OR new_student_source IN "
            "('ENTRY_ALLOCATION', 'LOCKED_MANUAL_PLAN', 'RATE_INFERRED')",
            name="ck_season_headcount_new_source",
        ),
        sa.CheckConstraint(
            "planned_eligibility_status IN "
            "('ENTRY_ABSOLUTE', 'APPLICABLE_LOCKED', 'NOT_APPLICABLE', 'REQUIRED_MISSING')",
            name="ck_season_headcount_plan_status",
        ),
        sa.CheckConstraint(
            "theoretical_old_students IS NULL OR theoretical_old_students >= 0",
            name="ck_season_headcount_old_theory",
        ),
        sa.CheckConstraint(
            "theoretical_new_students IS NULL OR theoretical_new_students >= 0",
            name="ck_season_headcount_new_theory",
        ),
        sa.CheckConstraint(
            "integer_old_students IS NULL OR integer_old_students >= 0",
            name="ck_season_headcount_old_integer",
        ),
        sa.CheckConstraint(
            "integer_new_students IS NULL OR integer_new_students >= 0",
            name="ck_season_headcount_new_integer",
        ),
        sa.CheckConstraint(
            "(calculation_status = 'CALCULATED' AND integer_old_students IS NOT NULL "
            "AND integer_new_students IS NOT NULL AND integer_total_students = "
            "integer_old_students + integer_new_students) OR "
            "(calculation_status = 'BLOCKED' AND integer_total_students IS NULL)",
            name="ck_season_headcount_total",
        ),
        sa.CheckConstraint(
            "realized_retention_rate_ppm IS NULL OR realized_retention_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_season_headcount_realized_renewal",
        ),
        sa.CheckConstraint(
            "realized_new_student_share_ppm IS NULL OR realized_new_student_share_ppm BETWEEN 0 AND 1000000",
            name="ck_season_headcount_realized_share",
        ),
        sa.CheckConstraint("char_length(combination_stable_key) = 64", name="ck_season_headcount_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_season_headcount_hash"),
        sa.ForeignKeyConstraint(
            ["headcount_result_version_id"],
            ["headcount_result_version.headcount_result_version_id"],
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
        sa.ForeignKeyConstraint(
            ["renewal_rate_parameter_id"], ["rate_parameter.rate_parameter_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["new_student_share_parameter_id"], ["rate_parameter.rate_parameter_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["planned_new_student_count_id"],
            ["planned_new_student_count.planned_new_student_count_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "headcount_result_version_id",
            "combination_id",
            "fiscal_season",
            name="uq_season_headcount_result",
        ),
    )
    op.create_table(
        "headcount_rounding_trace",
        sa.Column("headcount_rounding_trace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("season_headcount_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_type", sa.String(10), nullable=False),
        sa.Column("exact_theory_fraction", sa.String(200), nullable=False),
        sa.Column("floor_value", sa.BigInteger(), nullable=False),
        sa.Column("exact_remainder_fraction", sa.String(200), nullable=False),
        sa.Column("fixed_input", sa.Boolean(), nullable=False),
        sa.Column("remainder_rank", sa.Integer(), nullable=True),
        sa.Column("tail_assigned", sa.Boolean(), nullable=False),
        sa.Column("group_target", sa.BigInteger(), nullable=False),
        sa.Column("fixed_total", sa.BigInteger(), nullable=False),
        sa.Column("adjustable_target", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("student_type IN ('OLD', 'NEW')", name="ck_headcount_trace_type"),
        sa.CheckConstraint("floor_value >= 0", name="ck_headcount_trace_floor"),
        sa.CheckConstraint("group_target >= 0", name="ck_headcount_trace_group_target"),
        sa.CheckConstraint("fixed_total >= 0", name="ck_headcount_trace_fixed_total"),
        sa.CheckConstraint("adjustable_target >= 0", name="ck_headcount_trace_adjustable_target"),
        sa.CheckConstraint(
            "(fixed_input = TRUE AND remainder_rank IS NULL AND tail_assigned = FALSE) OR "
            "(fixed_input = FALSE AND remainder_rank > 0)",
            name="ck_headcount_trace_mode",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_headcount_trace_hash"),
        sa.ForeignKeyConstraint(
            ["season_headcount_result_id"],
            ["season_headcount_result.season_headcount_result_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("season_headcount_result_id", "student_type", name="uq_headcount_rounding_trace"),
    )
    op.create_table(
        "headcount_validation_result",
        sa.Column("headcount_validation_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("headcount_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("combination_stable_key", sa.String(64), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("severity = 'BLOCKING'", name="ck_headcount_validation_severity"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_headcount_validation_season"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_headcount_validation_hash"),
        sa.ForeignKeyConstraint(
            ["headcount_result_version_id"],
            ["headcount_result_version.headcount_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "headcount_result_version_id",
            "fiscal_season",
            "combination_stable_key",
            "code",
            name="uq_headcount_validation_result",
        ),
    )
    for table_name in (
        "headcount_result_version",
        "season_headcount_result",
        "headcount_rounding_trace",
        "headcount_validation_result",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in (
        "headcount_validation_result",
        "headcount_rounding_trace",
        "season_headcount_result",
        "headcount_result_version",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
