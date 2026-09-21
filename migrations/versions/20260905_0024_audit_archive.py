"""Add immutable audit archive artifacts.

Revision ID: 20260905_0024
Revises: 20260904_0023
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0024"
down_revision: str | None = "20260904_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_archive_artifact",
        sa.Column("audit_archive_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("annual_budget_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("artifact_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="ck_audit_archive_size"),
        sa.CheckConstraint("char_length(manifest_hash) = 64", name="ck_audit_archive_manifest_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_audit_archive_content_hash"),
        sa.ForeignKeyConstraint(["job_id"], ["async_job.job_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["annual_budget_id"], ["annual_budget.annual_budget_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("job_id", name="uq_audit_archive_job"),
    )
    op.create_index(
        "ix_audit_archive_revision_created",
        "audit_archive_artifact",
        ["budget_revision_id", "created_at", "audit_archive_id"],
    )
    op.execute(
        "CREATE TRIGGER audit_archive_artifact_immutable BEFORE UPDATE OR DELETE ON "
        "audit_archive_artifact FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_archive_artifact_immutable ON audit_archive_artifact")
    op.drop_index("ix_audit_archive_revision_created", table_name="audit_archive_artifact")
    op.drop_table("audit_archive_artifact")
