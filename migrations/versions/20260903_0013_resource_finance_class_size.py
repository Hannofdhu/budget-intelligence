"""Add resource-finance governance and class-size results.

Revision ID: 20260903_0013
Revises: 20260902_0012
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0013"
down_revision: str | None = "20260902_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_finance_parameter_version",
        sa.Column("resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(30), nullable=False),
        sa.Column("budget_tax_conversion_factor", sa.Numeric(20, 9), nullable=False),
        sa.Column("tax_factor_source_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("tax_factor_change_reason", sa.String(500), nullable=False),
        sa.Column("class_size_record_count", sa.BigInteger(), nullable=False),
        sa.Column("revenue_record_count", sa.BigInteger(), nullable=False),
        sa.Column("batch_content_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_resource_finance_year"),
        sa.CheckConstraint("operation_type = 'DRAFT_REPLACE'", name="ck_resource_finance_operation"),
        sa.CheckConstraint("budget_tax_conversion_factor > 0", name="ck_resource_finance_tax_factor"),
        sa.CheckConstraint("class_size_record_count > 0", name="ck_resource_finance_class_count"),
        sa.CheckConstraint("revenue_record_count > 0", name="ck_resource_finance_revenue_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_resource_finance_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_resource_finance_request_hash"),
        sa.CheckConstraint("char_length(batch_content_hash) = 64", name="ck_resource_finance_batch_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_resource_finance_parameter_version_id"],
            ["resource_finance_parameter_version.resource_finance_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_resource_finance_revision"),
    )
    op.create_table(
        "class_size_parameter",
        sa.Column("class_size_parameter_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("class_size_parameter_stable_key", sa.String(64), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("system_value", sa.Integer(), nullable=False),
        sa.Column("final_value", sa.Integer(), nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("system_value > 0", name="ck_class_size_system_value"),
        sa.CheckConstraint("final_value > 0", name="ck_class_size_final_value"),
        sa.CheckConstraint("char_length(class_size_parameter_stable_key) = 64", name="ck_class_size_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_hash"),
        sa.ForeignKeyConstraint(
            ["resource_finance_parameter_version_id"],
            ["resource_finance_parameter_version.resource_finance_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "resource_finance_parameter_version_id",
            "track",
            "product",
            name="uq_class_size_parameter_key",
        ),
    )
    op.create_table(
        "revenue_parameter",
        sa.Column("revenue_parameter_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revenue_parameter_stable_key", sa.String(64), nullable=False),
        sa.Column("recognition_parameter_key", sa.String(64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(30), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("list_price_yuan", sa.Numeric(20, 6), nullable=False),
        sa.Column("current_season_recognition_rate_ppm", sa.Integer(), nullable=False),
        sa.Column("cross_season_recognition_rate_ppm", sa.Integer(), nullable=False),
        sa.Column("opening_carryover_revenue_yuan", sa.Numeric(20, 6), nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_revenue_parameter_year"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_revenue_parameter_season"),
        sa.CheckConstraint("list_price_yuan >= 0", name="ck_revenue_parameter_price"),
        sa.CheckConstraint(
            "current_season_recognition_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_revenue_parameter_current_rate",
        ),
        sa.CheckConstraint(
            "cross_season_recognition_rate_ppm = 1000000 - current_season_recognition_rate_ppm",
            name="ck_revenue_parameter_complement",
        ),
        sa.CheckConstraint(
            "opening_carryover_revenue_yuan >= 0 AND "
            "((fiscal_season % 10) = 1 OR opening_carryover_revenue_yuan = 0)",
            name="ck_revenue_parameter_opening_carryover",
        ),
        sa.CheckConstraint(
            "char_length(revenue_parameter_stable_key) = 64",
            name="ck_revenue_parameter_key",
        ),
        sa.CheckConstraint(
            "char_length(recognition_parameter_key) = 64", name="ck_recognition_parameter_key"
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_parameter_hash"),
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
            name="uq_revenue_parameter_key",
        ),
    )
    op.create_table(
        "resource_finance_confirmation",
        sa.Column("resource_finance_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_resource_finance_confirm_hash"),
        sa.ForeignKeyConstraint(
            ["resource_finance_parameter_version_id"],
            ["resource_finance_parameter_version.resource_finance_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "resource_finance_parameter_version_id",
            "input_fingerprint",
            name="uq_resource_finance_confirmation",
        ),
    )
    op.create_table(
        "class_size_result_version",
        sa.Column("class_size_result_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("headcount_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_finance_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_finance_confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("validation_rule_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("publishable", sa.Boolean(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("validation_count", sa.BigInteger(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_class_size_result_version_year"),
        sa.CheckConstraint("status = 'SUCCEEDED'", name="ck_class_size_result_version_status"),
        sa.CheckConstraint("record_count > 0", name="ck_class_size_result_version_count"),
        sa.CheckConstraint("validation_count >= 0", name="ck_class_size_result_validation_count"),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_class_size_result_input_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_result_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_class_size_result_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["headcount_result_version_id"],
            ["headcount_result_version.headcount_result_version_id"],
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
        sa.UniqueConstraint("budget_revision_id", name="uq_class_size_result_version_revision"),
    )
    op.create_table(
        "class_size_result",
        sa.Column("class_size_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("class_size_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("season_headcount_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("class_size_parameter_id", postgresql.UUID(as_uuid=True)),
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
        sa.Column("minimum_class_size", sa.Integer()),
        sa.Column("class_size_gap", sa.Integer()),
        sa.Column("class_size_status", sa.String(40), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_class_size_result_year"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_class_size_result_season"),
        sa.CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_class_size_result_entry"),
        sa.CheckConstraint(
            "class_size_status IN ('NO_PLANNED_CLASS', 'BELOW_MINIMUM_CLASS_SIZE', 'PASS', "
            "'HEADCOUNT_UNAVAILABLE', 'PARAMETER_MISSING')",
            name="ck_class_size_result_status",
        ),
        sa.CheckConstraint(
            "integer_total_students IS NULL OR integer_total_students >= 0",
            name="ck_class_size_result_headcount",
        ),
        sa.CheckConstraint(
            "minimum_class_size IS NULL OR minimum_class_size > 0", name="ck_class_size_result_minimum"
        ),
        sa.CheckConstraint("class_size_gap IS NULL OR class_size_gap >= 0", name="ck_class_size_result_gap"),
        sa.CheckConstraint("char_length(combination_stable_key) = 64", name="ck_class_size_result_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_result_hash"),
        sa.ForeignKeyConstraint(
            ["class_size_result_version_id"],
            ["class_size_result_version.class_size_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["season_headcount_result_id"],
            ["season_headcount_result.season_headcount_result_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_size_parameter_id"], ["class_size_parameter.class_size_parameter_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "class_size_result_version_id",
            "season_headcount_result_id",
            name="uq_class_size_result_row",
        ),
    )
    op.create_table(
        "class_size_validation_result",
        sa.Column("class_size_validation_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("class_size_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("combination_stable_key", sa.String(64), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("severity = 'BLOCKING'", name="ck_class_size_validation_severity"),
        sa.CheckConstraint("fiscal_season > 0", name="ck_class_size_validation_season"),
        sa.CheckConstraint("char_length(combination_stable_key) = 64", name="ck_class_size_validation_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_validation_hash"),
        sa.ForeignKeyConstraint(
            ["class_size_result_version_id"],
            ["class_size_result_version.class_size_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "class_size_result_version_id",
            "fiscal_season",
            "combination_stable_key",
            "code",
            name="uq_class_size_validation_result",
        ),
    )
    for table_name in (
        "resource_finance_parameter_version",
        "class_size_parameter",
        "revenue_parameter",
        "resource_finance_confirmation",
        "class_size_result_version",
        "class_size_result",
        "class_size_validation_result",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in (
        "class_size_validation_result",
        "class_size_result",
        "class_size_result_version",
        "resource_finance_confirmation",
        "revenue_parameter",
        "class_size_parameter",
        "resource_finance_parameter_version",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
