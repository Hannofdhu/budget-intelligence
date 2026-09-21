"""Add immutable discount unlock request and confirmation records.

Revision ID: 20260904_0021
Revises: 20260903_0020
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0021"
down_revision: str | None = "20260903_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discount_unlock_request",
        sa.Column("discount_unlock_request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("optimization_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("infeasibility_proof_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("requested_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_discount_unlock_request_input"),
        sa.CheckConstraint("char_length(reason) > 0", name="ck_discount_unlock_request_reason"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_discount_unlock_request_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_discount_unlock_request_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["optimization_run_id"], ["optimization_run.optimization_run_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["infeasibility_proof_id"],
            ["infeasibility_proof.infeasibility_proof_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "budget_revision_id",
            "input_fingerprint",
            name="uq_discount_unlock_request_revision_input",
        ),
    )
    op.create_table(
        "discount_unlock_confirmation",
        sa.Column("discount_unlock_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("discount_unlock_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "char_length(input_fingerprint) = 64", name="ck_discount_unlock_confirmation_input"
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_discount_unlock_confirmation_hash"),
        sa.CheckConstraint(
            "char_length(request_hash) = 64", name="ck_discount_unlock_confirmation_request_hash"
        ),
        sa.ForeignKeyConstraint(
            ["discount_unlock_request_id"],
            ["discount_unlock_request.discount_unlock_request_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("discount_unlock_request_id", name="uq_discount_unlock_confirmation_request"),
    )
    for table in ("discount_unlock_request", "discount_unlock_confirmation"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )
    op.add_column(
        "optimization_run",
        sa.Column(
            "discount_unlock_confirmation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_optimization_run_discount_unlock_confirmation",
        "optimization_run",
        "discount_unlock_confirmation",
        ["discount_unlock_confirmation_id"],
        ["discount_unlock_confirmation_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_optimization_run_discount_unlock_confirmation",
        "optimization_run",
        type_="foreignkey",
    )
    op.drop_column("optimization_run", "discount_unlock_confirmation_id")
    for table in ("discount_unlock_confirmation", "discount_unlock_request"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.drop_table(table)
