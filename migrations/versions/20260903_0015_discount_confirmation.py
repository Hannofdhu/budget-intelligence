"""Require separately confirmed composite discount targets.

Revision ID: 20260903_0015
Revises: 20260903_0014
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0015"
down_revision: str | None = "20260903_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discount_parameter_confirmation",
        sa.Column("discount_parameter_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("discount_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_discount_confirm_hash"),
        sa.ForeignKeyConstraint(
            ["discount_parameter_version_id"],
            ["discount_parameter_version.discount_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "discount_parameter_version_id",
            "input_fingerprint",
            name="uq_discount_parameter_confirmation",
        ),
    )
    op.add_column(
        "revenue_result_version",
        sa.Column("discount_parameter_confirmation_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_revenue_result_discount_confirmation",
        "revenue_result_version",
        "discount_parameter_confirmation",
        ["discount_parameter_confirmation_id"],
        ["discount_parameter_confirmation_id"],
        ondelete="RESTRICT",
    )
    op.execute(
        "CREATE TRIGGER discount_parameter_confirmation_immutable BEFORE UPDATE OR DELETE "
        "ON discount_parameter_confirmation FOR EACH ROW "
        "EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_revenue_result_discount_confirmation", "revenue_result_version", type_="foreignkey"
    )
    op.drop_column("revenue_result_version", "discount_parameter_confirmation_id")
    op.execute(
        "DROP TRIGGER IF EXISTS discount_parameter_confirmation_immutable ON discount_parameter_confirmation"
    )
    op.drop_table("discount_parameter_confirmation")
