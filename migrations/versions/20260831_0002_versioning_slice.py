"""Create source snapshots, parameter manifests and revision lifecycle.

Revision ID: 20260831_0002
Revises: 20260831_0001
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0002"
down_revision: str | None = "20260831_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_snapshot",
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'WAITING_FOR_SUMMARY', 'RECONCILED', 'FROZEN')",
            name="ck_source_snapshot_status",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_source_snapshot_content_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_source_snapshot_request_hash"),
        sa.PrimaryKeyConstraint("source_snapshot_id"),
    )
    op.create_index(
        "ix_source_snapshot_department_created",
        "source_snapshot",
        ["department_id", "created_at", "source_snapshot_id"],
    )
    op.create_table(
        "source_snapshot_component",
        sa.Column("source_snapshot_component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", sa.String(length=200), nullable=False),
        sa.Column("dataset_name", sa.String(length=200), nullable=False),
        sa.Column("partition_value", sa.String(length=200), nullable=False),
        sa.Column("task_batch_id", sa.String(length=200), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("field_mapping_version", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("row_count >= 0", name="ck_snapshot_component_row_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_snapshot_component_content_hash"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("source_snapshot_component_id"),
        sa.UniqueConstraint(
            "source_snapshot_id", "dataset_id", "partition_value", name="uq_snapshot_component_partition"
        ),
    )
    op.create_table(
        "source_reconciliation",
        sa.Column("source_reconciliation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_code", sa.String(length=100), nullable=False),
        sa.Column("left_source", sa.String(length=200), nullable=False),
        sa.Column("right_source", sa.String(length=200), nullable=False),
        sa.Column("left_numerator", sa.Numeric(20, 6), nullable=False),
        sa.Column("left_denominator", sa.Numeric(20, 6), nullable=False),
        sa.Column("right_numerator", sa.Numeric(20, 6), nullable=False),
        sa.Column("right_denominator", sa.Numeric(20, 6), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("source_reconciliation_id"),
        sa.UniqueConstraint("source_snapshot_id", "metric_code", name="uq_source_reconciliation_metric"),
    )
    op.create_table(
        "parameter_version",
        sa.Column("parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_parameter_version_content_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_parameter_version_request_hash"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["source_snapshot.source_snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["parameter_version.parameter_version_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("parameter_version_id"),
    )
    op.create_index(
        "ix_parameter_version_snapshot_created",
        "parameter_version",
        ["source_snapshot_id", "created_at", "parameter_version_id"],
    )
    op.create_table(
        "parameter_version_component",
        sa.Column("parameter_version_component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_type", sa.String(length=80), nullable=False),
        sa.Column("component_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "component_type IN ('RATE_PARAMETERS', 'PLANNED_NEW_STUDENTS', 'RESOURCE_FINANCE', 'DISCOUNTS')",
            name="ck_parameter_version_component_type",
        ),
        sa.CheckConstraint("record_count >= 0", name="ck_parameter_version_component_record_count"),
        sa.CheckConstraint(
            "char_length(content_hash) = 64", name="ck_parameter_version_component_content_hash"
        ),
        sa.ForeignKeyConstraint(
            ["parameter_version_id"], ["parameter_version.parameter_version_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("parameter_version_component_id"),
        sa.UniqueConstraint(
            "parameter_version_id", "component_type", name="uq_parameter_version_component_type"
        ),
    )
    op.create_table(
        "revision_lifecycle_event",
        sa.Column("revision_lifecycle_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('DRAFT_CREATED', 'FORMAL_PLAN_CONFIRMED', 'PUBLISHED', 'CURRENT_POINTER_MOVED')",
            name="ck_revision_lifecycle_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("revision_lifecycle_event_id"),
        sa.UniqueConstraint("budget_revision_id", "event_type", name="uq_revision_lifecycle_event_type"),
    )
    op.create_table(
        "command_receipt",
        sa.Column("command_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("command_type", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_command_receipt_request_hash"),
        sa.PrimaryKeyConstraint("command_receipt_id"),
        sa.UniqueConstraint("actor_id", "command_type", "idempotency_key", name="uq_command_receipt_key"),
    )

    op.add_column("budget_revision", sa.Column("request_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "budget_revision", sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "budget_revision", sa.Column("parameter_version_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_budget_revision_request_hash",
        "budget_revision",
        "request_hash IS NULL OR char_length(request_hash) = 64",
    )
    op.create_foreign_key(
        "fk_budget_revision_source_snapshot",
        "budget_revision",
        "source_snapshot",
        ["source_snapshot_id"],
        ["source_snapshot_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_budget_revision_parameter_version",
        "budget_revision",
        "parameter_version",
        ["parameter_version_id"],
        ["parameter_version_id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        CREATE FUNCTION prevent_immutable_version_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "source_snapshot_component",
        "source_reconciliation",
        "parameter_version",
        "parameter_version_component",
        "budget_revision",
        "revision_lifecycle_event",
        "command_receipt",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();
            """
        )
    op.execute(
        """
        CREATE FUNCTION enforce_source_snapshot_transition() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'source_snapshot is immutable';
            END IF;
            IF OLD.status = 'FROZEN' THEN
                RAISE EXCEPTION 'frozen source_snapshot is immutable';
            END IF;
            IF (NEW.source_snapshot_id, NEW.department_id, NEW.cutoff_at, NEW.content_hash,
                NEW.request_hash, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.source_snapshot_id, OLD.department_id, OLD.cutoff_at, OLD.content_hash,
                OLD.request_hash, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'source_snapshot identity and content are immutable';
            END IF;
            IF NOT (
                (OLD.status = 'DRAFT' AND NEW.status IN ('WAITING_FOR_SUMMARY', 'RECONCILED')) OR
                (OLD.status = 'WAITING_FOR_SUMMARY' AND NEW.status = 'RECONCILED') OR
                (OLD.status = 'RECONCILED' AND NEW.status = 'FROZEN')
            ) THEN
                RAISE EXCEPTION 'invalid source_snapshot status transition: % -> %', OLD.status, NEW.status;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER source_snapshot_transition_guard
        BEFORE UPDATE OR DELETE ON source_snapshot
        FOR EACH ROW EXECUTE FUNCTION enforce_source_snapshot_transition();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS source_snapshot_transition_guard ON source_snapshot")
    op.execute("DROP FUNCTION IF EXISTS enforce_source_snapshot_transition")
    for table_name in (
        "command_receipt",
        "revision_lifecycle_event",
        "budget_revision",
        "parameter_version_component",
        "parameter_version",
        "source_reconciliation",
        "source_snapshot_component",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_immutable_version_mutation")

    op.drop_constraint("fk_budget_revision_parameter_version", "budget_revision", type_="foreignkey")
    op.drop_constraint("fk_budget_revision_source_snapshot", "budget_revision", type_="foreignkey")
    op.drop_constraint("ck_budget_revision_request_hash", "budget_revision", type_="check")
    op.drop_column("budget_revision", "parameter_version_id")
    op.drop_column("budget_revision", "source_snapshot_id")
    op.drop_column("budget_revision", "request_hash")

    op.drop_table("command_receipt")
    op.drop_table("revision_lifecycle_event")
    op.drop_table("parameter_version_component")
    op.drop_index("ix_parameter_version_snapshot_created", table_name="parameter_version")
    op.drop_table("parameter_version")
    op.drop_table("source_reconciliation")
    op.drop_table("source_snapshot_component")
    op.drop_index("ix_source_snapshot_department_created", table_name="source_snapshot")
    op.drop_table("source_snapshot")
