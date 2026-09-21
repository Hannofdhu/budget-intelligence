"""Create phase 0 core versioning, jobs, roles and audit tables.

Revision ID: 20260831_0001
Revises: None
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "annual_budget",
        sa.Column("annual_budget_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("current_draft_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_published_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_annual_budget_fiscal_year"),
        sa.PrimaryKeyConstraint("annual_budget_id"),
        sa.UniqueConstraint("department_id", "fiscal_year", name="uq_annual_budget_scope"),
    )
    op.create_table(
        "budget_revision",
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("annual_budget_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_type", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("revision_content_hash", sa.String(length=64), nullable=False),
        sa.Column("component_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision_no > 0", name="ck_budget_revision_number"),
        sa.ForeignKeyConstraint(
            ["annual_budget_id"], ["annual_budget.annual_budget_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("budget_revision_id"),
        sa.UniqueConstraint("annual_budget_id", "revision_no", name="uq_budget_revision_number"),
        sa.UniqueConstraint(
            "annual_budget_id", "command_type", "idempotency_key", name="uq_budget_revision_idempotency"
        ),
    )
    op.create_foreign_key(
        "fk_annual_budget_current_draft",
        "annual_budget",
        "budget_revision",
        ["current_draft_revision_id"],
        ["budget_revision_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_annual_budget_current_published",
        "annual_budget",
        "budget_revision",
        ["current_published_revision_id"],
        ["budget_revision_id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "role_membership",
        sa.Column("role_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=60), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('DATA_MAINTAINER', 'DEPARTMENT_BUDGET_OWNER', 'PERMISSION_ADMIN')",
            name="ck_role_membership_role",
        ),
        sa.PrimaryKeyConstraint("role_membership_id"),
        sa.UniqueConstraint("user_id", "department_id", "role", name="uq_role_membership"),
    )
    op.create_table(
        "async_job",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("attempt_no", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_safe", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'INCOMPLETE', 'FAILED', 'CANCELLED')",
            name="ck_async_job_status",
        ),
        sa.CheckConstraint("attempt_no >= 0", name="ck_async_job_attempt_no"),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("dedupe_key", name="uq_async_job_dedupe_key"),
    )
    op.create_index("ix_async_job_claim", "async_job", ["status", "available_at", "job_type"])
    op.create_table(
        "audit_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("annual_budget_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("acting_role", sa.String(length=60), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_key", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["annual_budget_id"], ["annual_budget.annual_budget_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_audit_event_budget_time", "audit_event", ["annual_budget_id", "occurred_at"])
    op.execute(
        """
        CREATE FUNCTION prevent_audit_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_append_only
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_event_mutation")
    op.drop_index("ix_audit_event_budget_time", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index("ix_async_job_claim", table_name="async_job")
    op.drop_table("async_job")
    op.drop_table("role_membership")
    op.drop_constraint("fk_annual_budget_current_published", "annual_budget", type_="foreignkey")
    op.drop_constraint("fk_annual_budget_current_draft", "annual_budget", type_="foreignkey")
    op.drop_table("budget_revision")
    op.drop_table("annual_budget")
