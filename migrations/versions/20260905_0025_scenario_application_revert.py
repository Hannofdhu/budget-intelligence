"""Add immutable one-click scenario application reverts.

Revision ID: 20260905_0025
Revises: 20260905_0024
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0025"
down_revision: str | None = "20260905_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario_application_revert",
        sa.Column("scenario_application_revert_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scenario_application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reverted_from_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("restored_source_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reverted_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("restored_component_keys", postgresql.JSONB(), nullable=False),
        sa.Column("invalidated_components", postgresql.JSONB(), nullable=False),
        sa.Column("calculation_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reverted_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("char_length(reason) > 0", name="ck_scenario_application_revert_reason"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_scenario_application_revert_hash"),
        sa.CheckConstraint(
            "char_length(request_hash) = 64", name="ck_scenario_application_revert_request_hash"
        ),
        sa.ForeignKeyConstraint(
            ["scenario_application_id"],
            ["scenario_application.scenario_application_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverted_from_revision_id"],
            ["budget_revision.budget_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["restored_source_revision_id"],
            ["budget_revision.budget_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverted_revision_id"],
            ["budget_revision.budget_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["calculation_job_id"], ["async_job.job_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("scenario_application_id", name="uq_scenario_application_revert_application"),
        sa.UniqueConstraint("reverted_revision_id", name="uq_scenario_application_revert_revision"),
    )
    op.create_index(
        "ix_scenario_application_revert_time",
        "scenario_application_revert",
        ["reverted_at", "scenario_application_revert_id"],
    )
    op.execute(
        "CREATE TRIGGER scenario_application_revert_immutable BEFORE UPDATE OR DELETE ON "
        "scenario_application_revert FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS scenario_application_revert_immutable ON scenario_application_revert")
    op.drop_index("ix_scenario_application_revert_time", table_name="scenario_application_revert")
    op.drop_table("scenario_application_revert")
