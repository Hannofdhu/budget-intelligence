"""Preserve imported historical budgets independently of source files."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0027"
down_revision: str | None = "20260907_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "historical_budget_archive",
        sa.Column("archive_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("department_id", sa.String(100), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("baseline_hash", sa.String(64), nullable=False),
        sa.Column("source_file_hash", sa.String(64), nullable=False),
        sa.Column("business_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("review", postgresql.JSONB(), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("original_workbook", sa.LargeBinary(), nullable=False),
        sa.Column("original_extraction", sa.LargeBinary(), nullable=False),
        sa.Column("accepted_extraction", sa.LargeBinary(), nullable=False),
        sa.Column("acceptance_record", sa.LargeBinary(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("import_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_historical_budget_archive_fiscal_year"),
        *[
            sa.CheckConstraint(f"char_length({column}) = 64", name=f"ck_historical_budget_archive_{column}")
            for column in (
                "baseline_hash",
                "source_file_hash",
                "business_hash",
                "content_hash",
                "source_manifest_hash",
            )
        ],
        sa.UniqueConstraint(
            "department_id", "fiscal_year", "source_file_hash", name="uq_historical_budget_archive_source"
        ),
    )
    op.create_index(
        "ix_historical_budget_archive_scope_created",
        "historical_budget_archive",
        ["department_id", "fiscal_year", "created_at"],
    )
    op.execute(
        "CREATE TRIGGER historical_budget_archive_immutable BEFORE UPDATE OR DELETE ON "
        "historical_budget_archive FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.drop_table("historical_budget_archive")
