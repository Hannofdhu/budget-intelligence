"""Immutable opening student preparation versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260907_0026"
down_revision: str | None = "20260905_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opening_student_version",
        sa.Column("opening_student_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_parameter_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_opening_hash"),
        sa.UniqueConstraint("budget_revision_id", name="uq_opening_revision"),
        *[
            sa.ForeignKeyConstraint([column], [target], ondelete="RESTRICT")
            for column, target in (
                ("budget_revision_id", "budget_revision.budget_revision_id"),
                ("source_component_id", "source_snapshot_component.source_snapshot_component_id"),
                ("combination_set_version_id", "budget_combination_set_version.combination_set_version_id"),
                ("rate_parameter_version_id", "rate_parameter_version.rate_parameter_version_id"),
            )
        ],
    )
    op.execute(
        "CREATE TRIGGER opening_student_version_immutable BEFORE UPDATE OR DELETE ON "
        "opening_student_version FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.drop_table("opening_student_version")
