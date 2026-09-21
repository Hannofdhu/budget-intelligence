"""Add governed rate-parameter versions, confirmations, and copy exceptions.

Revision ID: 20260902_0010
Revises: 20260901_0009
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0010"
down_revision: str | None = "20260901_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_parameter_version",
        sa.Column("rate_parameter_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_rate_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rate_reference_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(30), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("locked_count", sa.BigInteger(), nullable=False),
        sa.Column("copy_exception_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_rate_parameter_version_fiscal_year"),
        sa.CheckConstraint("record_count > 0", name="ck_rate_parameter_version_count"),
        sa.CheckConstraint(
            "locked_count BETWEEN 0 AND record_count", name="ck_rate_parameter_version_locked_count"
        ),
        sa.CheckConstraint("copy_exception_count >= 0", name="ck_rate_parameter_version_copy_count"),
        sa.CheckConstraint(
            "operation_type IN ('DRAFT_REPLACE', 'LOCK', 'CHANGE_AND_LOCK', 'UNLOCK', 'CROSS_YEAR_COPY')",
            name="ck_rate_parameter_version_operation",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_rate_parameter_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_rate_parameter_version_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_rate_parameter_version_id"],
            ["rate_parameter_version.rate_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rate_reference_version_id"],
            ["rate_reference_version.rate_reference_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_rate_parameter_version_revision"),
    )
    op.create_table(
        "rate_parameter",
        sa.Column("rate_parameter_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rate_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_parameter_stable_key", sa.String(64), nullable=False),
        sa.Column("cross_year_match_key", sa.String(64), nullable=False),
        sa.Column("rate_type", sa.String(40), nullable=False),
        sa.Column("rate_usage_mode", sa.String(30), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("source_fiscal_season", sa.Integer(), nullable=False),
        sa.Column("target_fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(30), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("renewal_relation_code", sa.String(40), nullable=True),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("system_value_ppm", sa.Integer(), nullable=True),
        sa.Column("current_value_ppm", sa.Integer(), nullable=False),
        sa.Column("lower_bound_ppm", sa.Integer(), nullable=False),
        sa.Column("upper_bound_ppm", sa.Integer(), nullable=False),
        sa.Column("boundary_reason", sa.String(500), nullable=False),
        sa.Column("manual_reason", sa.String(500), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("locked_value_ppm", sa.Integer(), nullable=True),
        sa.Column("lock_origin", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "rate_type IN ('RENEWAL_RATE', 'NEW_STUDENT_SHARE')", name="ck_rate_parameter_type"
        ),
        sa.CheckConstraint(
            "rate_usage_mode IN ('RATE_INPUT', 'REVERSE_INPUT')", name="ck_rate_parameter_usage"
        ),
        sa.CheckConstraint("fiscal_year > 0", name="ck_rate_parameter_fiscal_year"),
        sa.CheckConstraint("target_fiscal_season > 0", name="ck_rate_parameter_season"),
        sa.CheckConstraint(
            "current_value_ppm % 100 = 0 AND lower_bound_ppm % 100 = 0 AND upper_bound_ppm % 100 = 0",
            name="ck_rate_parameter_precision",
        ),
        sa.CheckConstraint(
            "0 <= lower_bound_ppm AND lower_bound_ppm <= current_value_ppm "
            "AND current_value_ppm <= upper_bound_ppm AND upper_bound_ppm <= 1000000",
            name="ck_rate_parameter_boundary",
        ),
        sa.CheckConstraint(
            "rate_type <> 'NEW_STUDENT_SHARE' OR rate_usage_mode <> 'REVERSE_INPUT' "
            "OR upper_bound_ppm <= 999900",
            name="ck_rate_parameter_reverse_share_max",
        ),
        sa.CheckConstraint(
            "(locked = TRUE AND locked_value_ppm = current_value_ppm) OR "
            "(locked = FALSE AND locked_value_ppm IS NULL)",
            name="ck_rate_parameter_lock",
        ),
        sa.CheckConstraint(
            "char_length(rate_parameter_stable_key) = 64", name="ck_rate_parameter_stable_key"
        ),
        sa.CheckConstraint("char_length(cross_year_match_key) = 64", name="ck_rate_parameter_cross_year_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_rate_parameter_hash"),
        sa.ForeignKeyConstraint(
            ["rate_parameter_version_id"],
            ["rate_parameter_version.rate_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rate_reference_id"], ["rate_reference.rate_reference_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "rate_parameter_version_id", "rate_parameter_stable_key", name="uq_rate_parameter_key"
        ),
    )
    op.create_table(
        "rate_boundary_confirmation",
        sa.Column("rate_boundary_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rate_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_parameter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_parameter_stable_key", sa.String(64), nullable=False),
        sa.Column("boundary_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inherited_from_confirmation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(rate_parameter_stable_key) = 64", name="ck_rate_boundary_key"),
        sa.CheckConstraint("char_length(boundary_fingerprint) = 64", name="ck_rate_boundary_fingerprint"),
        sa.ForeignKeyConstraint(
            ["rate_parameter_version_id"],
            ["rate_parameter_version.rate_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rate_parameter_id"], ["rate_parameter.rate_parameter_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["inherited_from_confirmation_id"],
            ["rate_boundary_confirmation.rate_boundary_confirmation_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "rate_parameter_version_id",
            "rate_parameter_stable_key",
            "boundary_fingerprint",
            name="uq_rate_boundary_confirmation",
        ),
    )
    op.create_table(
        "rate_parameter_copy_exception",
        sa.Column("rate_parameter_copy_exception_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rate_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_stable_key", sa.String(64), nullable=False),
        sa.Column("cross_year_match_key", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("char_length(source_stable_key) = 64", name="ck_rate_copy_source_key"),
        sa.CheckConstraint("char_length(cross_year_match_key) = 64", name="ck_rate_copy_match_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_rate_copy_hash"),
        sa.ForeignKeyConstraint(
            ["rate_parameter_version_id"],
            ["rate_parameter_version.rate_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("rate_parameter_version_id", "source_stable_key", name="uq_rate_copy_exception"),
    )
    for table_name in (
        "rate_parameter_version",
        "rate_parameter",
        "rate_boundary_confirmation",
        "rate_parameter_copy_exception",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in (
        "rate_parameter_copy_exception",
        "rate_boundary_confirmation",
        "rate_parameter",
        "rate_parameter_version",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
