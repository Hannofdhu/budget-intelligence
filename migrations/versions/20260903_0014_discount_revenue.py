"""Add exact opening carryover, discount parameters, and revenue results.

Revision ID: 20260903_0014
Revises: 20260903_0013
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0014"
down_revision: str | None = "20260903_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resource_finance_parameter_version",
        sa.Column(
            "opening_carryover_record_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_table(
        "opening_carryover_parameter",
        sa.Column("opening_carryover_parameter_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opening_carryover_stable_key", sa.String(64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(30), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("amount_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_opening_carryover_year"),
        sa.CheckConstraint("(fiscal_season % 10) = 1", name="ck_opening_carryover_first_season"),
        sa.CheckConstraint("amount_yuan >= 0", name="ck_opening_carryover_amount"),
        sa.CheckConstraint("char_length(opening_carryover_stable_key) = 64", name="ck_opening_carryover_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_opening_carryover_hash"),
        sa.ForeignKeyConstraint(
            ["resource_finance_parameter_version_id"],
            ["resource_finance_parameter_version.resource_finance_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "resource_finance_parameter_version_id",
            "fiscal_season",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_opening_carryover_parameter_key",
        ),
    )
    op.create_table(
        "discount_parameter_version",
        sa.Column("discount_parameter_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_discount_parameter_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(30), nullable=False),
        sa.Column("detail_record_count", sa.BigInteger(), nullable=False),
        sa.Column("target_record_count", sa.BigInteger(), nullable=False),
        sa.Column("batch_content_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_discount_version_year"),
        sa.CheckConstraint("operation_type = 'DRAFT_REPLACE'", name="ck_discount_version_operation"),
        sa.CheckConstraint("detail_record_count > 0", name="ck_discount_version_detail_count"),
        sa.CheckConstraint("target_record_count > 0", name="ck_discount_version_target_count"),
        sa.CheckConstraint("char_length(batch_content_hash) = 64", name="ck_discount_version_batch_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_discount_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_discount_version_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_discount_parameter_version_id"],
            ["discount_parameter_version.discount_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_discount_version_revision"),
    )
    op.create_table(
        "detail_discount_parameter",
        sa.Column("detail_discount_parameter_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("discount_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detail_discount_stable_key", sa.String(64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(30), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("discount_rate_ppm", sa.Integer(), nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_detail_discount_year"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_detail_discount_season"),
        sa.CheckConstraint(
            "discount_rate_ppm BETWEEN 0 AND 1000000 AND discount_rate_ppm % 1000 = 0",
            name="ck_detail_discount_rate",
        ),
        sa.CheckConstraint("char_length(detail_discount_stable_key) = 64", name="ck_detail_discount_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_detail_discount_hash"),
        sa.ForeignKeyConstraint(
            ["discount_parameter_version_id"],
            ["discount_parameter_version.discount_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "discount_parameter_version_id",
            "fiscal_season",
            "track",
            "product",
            "grade",
            name="uq_detail_discount_parameter_key",
        ),
    )
    op.create_table(
        "composite_discount_target",
        sa.Column("composite_discount_target_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("discount_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("composite_target_stable_key", sa.String(64), nullable=False),
        sa.Column("scope_level", sa.String(20), nullable=False),
        sa.Column("track", sa.String(100)),
        sa.Column("period_type", sa.String(20), nullable=False),
        sa.Column("fiscal_season", sa.Integer()),
        sa.Column("target_rate_ppm", sa.Integer(), nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_composite_target_scope"),
        sa.CheckConstraint("period_type IN ('ANNUAL', 'SEASON')", name="ck_composite_target_period"),
        sa.CheckConstraint(
            "(scope_level = 'DEPARTMENT' AND track IS NULL) OR (scope_level = 'TRACK' AND track IS NOT NULL)",
            name="ck_composite_target_track",
        ),
        sa.CheckConstraint(
            "(period_type = 'ANNUAL' AND fiscal_season IS NULL) OR "
            "(period_type = 'SEASON' AND fiscal_season IS NOT NULL)",
            name="ck_composite_target_season",
        ),
        sa.CheckConstraint("target_rate_ppm BETWEEN 0 AND 1000000", name="ck_composite_target_rate"),
        sa.CheckConstraint("char_length(composite_target_stable_key) = 64", name="ck_composite_target_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_composite_target_hash"),
        sa.ForeignKeyConstraint(
            ["discount_parameter_version_id"],
            ["discount_parameter_version.discount_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "discount_parameter_version_id",
            "composite_target_stable_key",
            name="uq_composite_discount_target_key",
        ),
    )
    op.create_table(
        "revenue_result_version",
        sa.Column("revenue_result_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("headcount_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("class_size_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_finance_confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("discount_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("calculation_rule_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("publishable", sa.Boolean(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("aggregate_count", sa.BigInteger(), nullable=False),
        sa.Column("validation_count", sa.BigInteger(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_revenue_result_version_year"),
        sa.CheckConstraint("status = 'SUCCEEDED'", name="ck_revenue_result_version_status"),
        sa.CheckConstraint("record_count > 0", name="ck_revenue_result_version_count"),
        sa.CheckConstraint("aggregate_count > 0", name="ck_revenue_result_aggregate_count"),
        sa.CheckConstraint("validation_count >= 0", name="ck_revenue_result_validation_count"),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_revenue_result_input_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_result_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_revenue_result_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["headcount_result_version_id"],
            ["headcount_result_version.headcount_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_size_result_version_id"],
            ["class_size_result_version.class_size_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_finance_parameter_version_id"],
            ["resource_finance_parameter_version.resource_finance_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_finance_confirmation_id"],
            ["resource_finance_confirmation.resource_finance_confirmation_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["discount_parameter_version_id"],
            ["discount_parameter_version.discount_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_revenue_result_version_revision"),
    )
    op.create_table(
        "season_revenue_result",
        sa.Column("season_revenue_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revenue_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("class_size_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("season_headcount_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revenue_parameter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detail_discount_parameter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opening_carryover_parameter_id", postgresql.UUID(as_uuid=True)),
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
        sa.Column("integer_total_students", sa.BigInteger()),
        sa.Column("list_price_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("detail_discount_rate_ppm", sa.Integer(), nullable=False),
        sa.Column("discounted_tuition_yuan", sa.Numeric(28, 6)),
        sa.Column("list_price_revenue_yuan", sa.Numeric(28, 6)),
        sa.Column("business_quarter_revenue_yuan", sa.Numeric(28, 6)),
        sa.Column("budget_tax_conversion_factor", sa.Numeric(20, 9), nullable=False),
        sa.Column("net_of_tax_base_yuan", sa.Numeric(28, 6)),
        sa.Column("current_season_recognition_rate_ppm", sa.Integer(), nullable=False),
        sa.Column("cross_season_recognition_rate_ppm", sa.Integer(), nullable=False),
        sa.Column("current_recognized_revenue_yuan", sa.Numeric(28, 6)),
        sa.Column("incoming_carryover_revenue_yuan", sa.Numeric(28, 6)),
        sa.Column("cross_season_revenue_yuan", sa.Numeric(28, 6)),
        sa.Column("quarter_recognized_revenue_yuan", sa.Numeric(28, 6)),
        sa.Column("next_fiscal_year_opening_yuan", sa.Numeric(28, 6)),
        sa.Column("calculation_status", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_season_revenue_year"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_season_revenue_season"),
        sa.CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_season_revenue_entry"),
        sa.CheckConstraint(
            "calculation_status IN ('CALCULATED', 'BLOCKED')", name="ck_season_revenue_status"
        ),
        sa.CheckConstraint(
            "detail_discount_rate_ppm BETWEEN 0 AND 1000000", name="ck_season_revenue_discount"
        ),
        sa.CheckConstraint(
            "current_season_recognition_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_season_revenue_current_rate",
        ),
        sa.CheckConstraint(
            "cross_season_recognition_rate_ppm = 1000000 - current_season_recognition_rate_ppm",
            name="ck_season_revenue_cross_rate",
        ),
        sa.CheckConstraint("char_length(combination_stable_key) = 64", name="ck_season_revenue_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_season_revenue_hash"),
        sa.ForeignKeyConstraint(
            ["revenue_result_version_id"],
            ["revenue_result_version.revenue_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_size_result_id"], ["class_size_result.class_size_result_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["season_headcount_result_id"],
            ["season_headcount_result.season_headcount_result_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revenue_parameter_id"], ["revenue_parameter.revenue_parameter_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["detail_discount_parameter_id"],
            ["detail_discount_parameter.detail_discount_parameter_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["opening_carryover_parameter_id"],
            ["opening_carryover_parameter.opening_carryover_parameter_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "revenue_result_version_id", "season_headcount_result_id", name="uq_season_revenue_result"
        ),
    )
    op.create_table(
        "revenue_aggregate_result",
        sa.Column("revenue_aggregate_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revenue_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("composite_discount_target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("aggregate_stable_key", sa.String(64), nullable=False),
        sa.Column("scope_level", sa.String(20), nullable=False),
        sa.Column("track", sa.String(100)),
        sa.Column("period_type", sa.String(20), nullable=False),
        sa.Column("fiscal_season", sa.Integer()),
        sa.Column("list_price_revenue_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("discounted_revenue_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("business_revenue_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("recognized_revenue_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("composite_discount_rate_ppm", sa.Numeric(20, 9)),
        sa.Column("target_rate_ppm", sa.Integer()),
        sa.Column("variance_ppm", sa.Numeric(20, 9)),
        sa.Column("target_status", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_revenue_aggregate_scope"),
        sa.CheckConstraint("period_type IN ('ANNUAL', 'SEASON')", name="ck_revenue_aggregate_period"),
        sa.CheckConstraint(
            "target_status IN ('PASS', 'OUT_OF_TOLERANCE', 'N/A', 'TARGET_MISSING')",
            name="ck_revenue_aggregate_target_status",
        ),
        sa.CheckConstraint("char_length(aggregate_stable_key) = 64", name="ck_revenue_aggregate_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_aggregate_hash"),
        sa.ForeignKeyConstraint(
            ["revenue_result_version_id"],
            ["revenue_result_version.revenue_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["composite_discount_target_id"],
            ["composite_discount_target.composite_discount_target_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "revenue_result_version_id", "aggregate_stable_key", name="uq_revenue_aggregate_result"
        ),
    )
    op.create_table(
        "revenue_validation_result",
        sa.Column("revenue_validation_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revenue_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("validation_stable_key", sa.String(64), nullable=False),
        sa.Column("fiscal_season", sa.Integer()),
        sa.Column("combination_stable_key", sa.String(64), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("severity = 'BLOCKING'", name="ck_revenue_validation_severity"),
        sa.CheckConstraint("char_length(combination_stable_key) = 64", name="ck_revenue_validation_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_validation_hash"),
        sa.ForeignKeyConstraint(
            ["revenue_result_version_id"],
            ["revenue_result_version.revenue_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "revenue_result_version_id", "validation_stable_key", name="uq_revenue_validation_result"
        ),
    )
    for table_name in (
        "opening_carryover_parameter",
        "discount_parameter_version",
        "detail_discount_parameter",
        "composite_discount_target",
        "revenue_result_version",
        "season_revenue_result",
        "revenue_aggregate_result",
        "revenue_validation_result",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in (
        "revenue_validation_result",
        "revenue_aggregate_result",
        "season_revenue_result",
        "revenue_result_version",
        "composite_discount_target",
        "detail_discount_parameter",
        "discount_parameter_version",
        "opening_carryover_parameter",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
    op.drop_column("resource_finance_parameter_version", "opening_carryover_record_count")
