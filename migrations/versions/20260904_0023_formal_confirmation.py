"""Add immutable formal plan confirmations.

Revision ID: 20260904_0023
Revises: 20260904_0022
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0023"
down_revision: str | None = "20260904_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "formal_plan_confirmation",
        sa.Column("formal_plan_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_formal_plan_confirmation_input"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_formal_plan_confirmation_hash"),
        sa.CheckConstraint("char_length(reason) > 0", name="ck_formal_plan_confirmation_reason"),
        sa.ForeignKeyConstraint(
            ["source_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("source_revision_id", name="uq_formal_plan_confirmation_source_revision"),
        sa.UniqueConstraint("confirmed_revision_id", name="uq_formal_plan_confirmation_revision"),
    )
    op.execute(
        "CREATE TRIGGER formal_plan_confirmation_immutable BEFORE UPDATE OR DELETE ON "
        "formal_plan_confirmation FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS formal_plan_confirmation_immutable ON formal_plan_confirmation")
    op.drop_table("formal_plan_confirmation")
