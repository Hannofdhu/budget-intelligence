"""Create immutable historical rate reference versions.

Revision ID: 20260901_0004
Revises: 20260901_0003
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0004"
down_revision: str | None = "20260901_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_reference_version",
        sa.Column("rate_reference_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_fiscal_year", sa.Integer(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("maturity_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reference_count", sa.BigInteger(), nullable=False),
        sa.Column("exact_count", sa.BigInteger(), nullable=False),
        sa.Column("fallback_count", sa.BigInteger(), nullable=False),
        sa.Column("manual_required_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("target_fiscal_year > 0", name="ck_rate_reference_target_fiscal_year"),
        sa.CheckConstraint("minimum_sample_size > 0", name="ck_rate_reference_minimum_sample"),
        sa.CheckConstraint("reference_count >= 0", name="ck_rate_reference_count"),
        sa.CheckConstraint("exact_count >= 0", name="ck_rate_reference_exact_count"),
        sa.CheckConstraint("fallback_count >= 0", name="ck_rate_reference_fallback_count"),
        sa.CheckConstraint("manual_required_count >= 0", name="ck_rate_reference_manual_required_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_rate_reference_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_rate_reference_request_hash"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("rate_reference_version_id"),
    )
    op.create_index(
        "ix_rate_reference_version_snapshot_created",
        "rate_reference_version",
        ["source_snapshot_id", "created_at", "rate_reference_version_id"],
    )
    op.create_table(
        "rate_reference",
        sa.Column("rate_reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_reference_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_type", sa.String(length=40), nullable=False),
        sa.Column("source_fiscal_season", sa.Integer(), nullable=False),
        sa.Column("target_fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(length=30), nullable=False),
        sa.Column("track", sa.String(length=100), nullable=False),
        sa.Column("product", sa.String(length=100), nullable=False),
        sa.Column("grade", sa.String(length=100), nullable=False),
        sa.Column("campus", sa.String(length=200), nullable=False),
        sa.Column("renewal_relation_code", sa.String(length=40), nullable=True),
        sa.Column("exact_numerator", sa.Numeric(20, 6), nullable=False),
        sa.Column("exact_denominator", sa.Numeric(20, 6), nullable=False),
        sa.Column("reference_numerator", sa.Numeric(20, 6), nullable=False),
        sa.Column("reference_denominator", sa.Numeric(20, 6), nullable=False),
        sa.Column("exact_rate_ppm", sa.Integer(), nullable=True),
        sa.Column("reference_rate_ppm", sa.Integer(), nullable=True),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("fallback_reason", sa.String(length=80), nullable=True),
        sa.Column("system_value_ppm", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "rate_type IN ('RENEWAL_RATE', 'NEW_STUDENT_SHARE')", name="ck_rate_reference_type"
        ),
        sa.CheckConstraint(
            "source_type IN ('EXACT_HISTORY', 'CAMPUS_REMOVED_FALLBACK', 'MANUAL_REQUIRED')",
            name="ck_rate_reference_source_type",
        ),
        sa.CheckConstraint("exact_numerator >= 0", name="ck_rate_reference_exact_numerator"),
        sa.CheckConstraint("exact_denominator >= 0", name="ck_rate_reference_exact_denominator"),
        sa.CheckConstraint("reference_numerator >= 0", name="ck_rate_reference_numerator"),
        sa.CheckConstraint("reference_denominator >= 0", name="ck_rate_reference_denominator"),
        sa.CheckConstraint("minimum_sample_size > 0", name="ck_rate_reference_row_minimum_sample"),
        sa.CheckConstraint(
            "exact_rate_ppm IS NULL OR exact_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_rate_reference_exact_rate_ppm",
        ),
        sa.CheckConstraint(
            "reference_rate_ppm IS NULL OR reference_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_rate_reference_rate_ppm",
        ),
        sa.CheckConstraint(
            "system_value_ppm IS NULL OR "
            "(rate_type = 'RENEWAL_RATE' AND system_value_ppm BETWEEN 0 AND 1000000) OR "
            "(rate_type = 'NEW_STUDENT_SHARE' AND system_value_ppm BETWEEN 0 AND 999900)",
            name="ck_rate_reference_system_value_ppm",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_rate_reference_hash"),
        sa.CheckConstraint(
            "(rate_type = 'RENEWAL_RATE' AND renewal_relation_code = 'SAME_BUDGET_ROW') "
            "OR (rate_type = 'NEW_STUDENT_SHARE' AND renewal_relation_code IS NULL)",
            name="ck_rate_reference_relation",
        ),
        sa.ForeignKeyConstraint(
            ["rate_reference_version_id"],
            ["rate_reference_version.rate_reference_version_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("rate_reference_id"),
        sa.UniqueConstraint(
            "rate_reference_version_id",
            "rate_type",
            "target_fiscal_season",
            "track",
            "product",
            "grade",
            "campus",
            name="uq_rate_reference_key",
        ),
    )
    op.create_index(
        "ix_rate_reference_page",
        "rate_reference",
        ["rate_reference_version_id", "rate_reference_id"],
    )
    for table_name in ("rate_reference_version", "rate_reference"):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();
            """
        )


def downgrade() -> None:
    for table_name in ("rate_reference", "rate_reference_version"):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
    op.drop_index("ix_rate_reference_page", table_name="rate_reference")
    op.drop_table("rate_reference")
    op.drop_index("ix_rate_reference_version_snapshot_created", table_name="rate_reference_version")
    op.drop_table("rate_reference_version")
