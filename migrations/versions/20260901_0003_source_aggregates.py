"""Create immutable source aggregates and mapping verification.

Revision ID: 20260901_0003
Revises: 20260831_0002
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0003"
down_revision: str | None = "20260831_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_snapshot",
        sa.Column("build_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_unique_constraint("uq_source_snapshot_build_job", "source_snapshot", ["build_job_id"])
    op.execute(
        """
        CREATE FUNCTION prevent_source_snapshot_build_job_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.build_job_id IS DISTINCT FROM OLD.build_job_id THEN
                RAISE EXCEPTION 'source_snapshot build_job_id is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER source_snapshot_build_job_immutable
        BEFORE UPDATE ON source_snapshot
        FOR EACH ROW EXECUTE FUNCTION prevent_source_snapshot_build_job_mutation();
        """
    )
    op.create_table(
        "source_metric_aggregate",
        sa.Column("source_metric_aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_code", sa.String(length=100), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(length=30), nullable=False),
        sa.Column("track", sa.String(length=100), nullable=False),
        sa.Column("product", sa.String(length=100), nullable=False),
        sa.Column("grade", sa.String(length=100), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("campus", sa.String(length=200), nullable=False),
        sa.Column("numerator", sa.Numeric(20, 6), nullable=False),
        sa.Column("denominator", sa.Numeric(20, 6), nullable=False),
        sa.Column("source_dataset_id", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("numerator >= 0", name="ck_source_metric_aggregate_numerator"),
        sa.CheckConstraint("denominator >= 0", name="ck_source_metric_aggregate_denominator"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_source_metric_aggregate_hash"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("source_metric_aggregate_id"),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "metric_code",
            "fiscal_season",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_source_metric_aggregate_key",
        ),
    )
    op.create_index(
        "ix_source_metric_aggregate_lookup",
        "source_metric_aggregate",
        ["source_snapshot_id", "metric_code", "fiscal_season"],
    )
    op.create_table(
        "entry_history_aggregate",
        sa.Column("entry_history_aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_season", sa.Integer(), nullable=False),
        sa.Column("quarter_name", sa.String(length=30), nullable=False),
        sa.Column("track", sa.String(length=100), nullable=False),
        sa.Column("product", sa.String(length=100), nullable=False),
        sa.Column("grade", sa.String(length=100), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("campus", sa.String(length=200), nullable=False),
        sa.Column("business_new_student_count", sa.BigInteger(), nullable=False),
        sa.Column("source_dataset_id", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("business_new_student_count >= 0", name="ck_entry_history_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_entry_history_hash"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("entry_history_aggregate_id"),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "fiscal_season",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_entry_history_aggregate_key",
        ),
    )
    op.create_table(
        "source_mapping_verification",
        sa.Column("source_mapping_verification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_metric_code", sa.String(length=100), nullable=False),
        sa.Column("dataset_id", sa.String(length=200), nullable=False),
        sa.Column("physical_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("business_definition", sa.String(length=1000), nullable=False),
        sa.Column("etl_resource_id", sa.String(length=200), nullable=False),
        sa.Column("field_mapping_version", sa.String(length=100), nullable=False),
        sa.Column("verified_by", sa.String(length=100), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_reference", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("source_mapping_verification_id"),
        sa.UniqueConstraint(
            "source_snapshot_id", "business_metric_code", name="uq_source_mapping_verification_metric"
        ),
    )
    for table_name in (
        "source_metric_aggregate",
        "entry_history_aggregate",
        "source_mapping_verification",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();
            """
        )


def downgrade() -> None:
    for table_name in (
        "source_mapping_verification",
        "entry_history_aggregate",
        "source_metric_aggregate",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
    op.drop_table("source_mapping_verification")
    op.drop_table("entry_history_aggregate")
    op.drop_index("ix_source_metric_aggregate_lookup", table_name="source_metric_aggregate")
    op.drop_table("source_metric_aggregate")
    op.execute("DROP TRIGGER IF EXISTS source_snapshot_build_job_immutable ON source_snapshot")
    op.execute("DROP FUNCTION IF EXISTS prevent_source_snapshot_build_job_mutation")
    op.drop_constraint("uq_source_snapshot_build_job", "source_snapshot", type_="unique")
    op.drop_column("source_snapshot", "build_job_id")
