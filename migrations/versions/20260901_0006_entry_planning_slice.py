"""Create combination, locked entry total and campus suggestion versions.

Revision ID: 20260901_0006
Revises: 20260901_0005
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0006"
down_revision: str | None = "20260901_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_combination_set_version",
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_reference_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_fiscal_year", sa.Integer(), nullable=False),
        sa.Column("entry_scope_rule_version", sa.String(100), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("target_fiscal_year > 0", name="ck_combination_target_fiscal_year"),
        sa.CheckConstraint("record_count > 0", name="ck_combination_set_record_count"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_combination_set_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_combination_set_request_hash"),
        sa.ForeignKeyConstraint(["budget_revision_id"], ["budget_revision.budget_revision_id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["source_snapshot.source_snapshot_id"]),
        sa.ForeignKeyConstraint(
            ["rate_reference_version_id"], ["rate_reference_version.rate_reference_version_id"]
        ),
    )
    op.create_table(
        "budget_combination",
        sa.Column("combination_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("track", sa.String(100), nullable=False),
        sa.Column("product", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("campus_stable_key", sa.String(64), nullable=False),
        sa.Column("entry_attribute", sa.String(20), nullable=False),
        sa.Column("origin_type", sa.String(30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("historical_enrollment", sa.Numeric(20, 6), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_combination_entry"),
        sa.CheckConstraint("origin_type = 'HISTORICAL'", name="ck_combination_origin"),
        sa.CheckConstraint("historical_enrollment > 0", name="ck_combination_history_enrollment"),
        sa.CheckConstraint("char_length(campus_stable_key) = 64", name="ck_combination_campus_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_combination_hash"),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
        ),
        sa.UniqueConstraint(
            "combination_set_version_id",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_budget_combination_public_key",
        ),
    )
    op.create_table(
        "combination_set_confirmation",
        sa.Column("combination_set_confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_combination_confirmation_hash"),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
        ),
        sa.ForeignKeyConstraint(["confirmed_revision_id"], ["budget_revision.budget_revision_id"]),
        sa.UniqueConstraint(
            "combination_set_version_id", "input_fingerprint", name="uq_combination_confirmation"
        ),
    )
    op.create_table(
        "entry_total_version",
        sa.Column("entry_total_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_year_entry_total", sa.BigInteger(), nullable=False),
        sa.Column("locked_value", sa.BigInteger(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("locked_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_entry_total_fiscal_year"),
        sa.CheckConstraint("fiscal_year_entry_total >= 0", name="ck_entry_total_value"),
        sa.CheckConstraint("locked_value = fiscal_year_entry_total", name="ck_entry_total_locked_value"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_entry_total_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_entry_total_request_hash"),
        sa.ForeignKeyConstraint(["budget_revision_id"], ["budget_revision.budget_revision_id"]),
    )
    op.create_table(
        "campus_entry_suggestion_version",
        sa.Column("campus_entry_suggestion_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("combination_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_total_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year_entry_total", sa.BigInteger(), nullable=False),
        sa.Column("manual_reserved_total", sa.BigInteger(), nullable=False),
        sa.Column("historical_entry_total", sa.BigInteger(), nullable=False),
        sa.Column("suggestion_total", sa.BigInteger(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year_entry_total >= 0", name="ck_campus_suggestion_entry_total"),
        sa.CheckConstraint("manual_reserved_total >= 0", name="ck_campus_suggestion_reserved"),
        sa.CheckConstraint("historical_entry_total >= 0", name="ck_campus_suggestion_history"),
        sa.CheckConstraint("suggestion_total = fiscal_year_entry_total", name="ck_campus_suggestion_total"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_campus_suggestion_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_campus_suggestion_request_hash"),
        sa.ForeignKeyConstraint(["budget_revision_id"], ["budget_revision.budget_revision_id"]),
        sa.ForeignKeyConstraint(
            ["combination_set_version_id"],
            ["budget_combination_set_version.combination_set_version_id"],
        ),
        sa.ForeignKeyConstraint(["entry_total_version_id"], ["entry_total_version.entry_total_version_id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["source_snapshot.source_snapshot_id"]),
    )
    op.create_table(
        "campus_entry_suggestion",
        sa.Column("campus_entry_suggestion_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campus_entry_suggestion_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campus", sa.String(200), nullable=False),
        sa.Column("campus_stable_key", sa.String(64), nullable=False),
        sa.Column("historical_business_new_students", sa.BigInteger(), nullable=False),
        sa.Column("history_availability", sa.String(30), nullable=False),
        sa.Column("classroom_resource", postgresql.JSONB(), nullable=False),
        sa.Column("sales_resource", postgresql.JSONB(), nullable=False),
        sa.Column("manual_reserved_suggestion", sa.BigInteger(), nullable=False),
        sa.Column("manual_suggestion_reason", sa.String(500), nullable=True),
        sa.Column("suggested_entry_theoretical", sa.Numeric(20, 9), nullable=False),
        sa.Column("suggested_entry_floor", sa.BigInteger(), nullable=False),
        sa.Column("suggested_entry_fraction", sa.Numeric(20, 9), nullable=False),
        sa.Column("suggestion_tail_assigned", sa.Boolean(), nullable=False),
        sa.Column("suggested_entry_value", sa.BigInteger(), nullable=False),
        sa.Column("suggestion_source_type", sa.String(40), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("char_length(campus_stable_key) = 64", name="ck_campus_entry_key"),
        sa.CheckConstraint("historical_business_new_students >= 0", name="ck_campus_entry_history"),
        sa.CheckConstraint("manual_reserved_suggestion >= 0", name="ck_campus_entry_manual"),
        sa.CheckConstraint("suggested_entry_value >= 0", name="ck_campus_entry_suggested"),
        sa.CheckConstraint(
            "history_availability IN ('HAS_HISTORY', 'NO_HISTORY')",
            name="ck_campus_entry_history_availability",
        ),
        sa.CheckConstraint(
            "suggestion_source_type IN ('HISTORICAL_FORMULA', 'BUSINESS_MANUAL')",
            name="ck_campus_entry_source_type",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_campus_entry_hash"),
        sa.ForeignKeyConstraint(
            ["campus_entry_suggestion_version_id"],
            ["campus_entry_suggestion_version.campus_entry_suggestion_version_id"],
        ),
        sa.UniqueConstraint(
            "campus_entry_suggestion_version_id", "campus_stable_key", name="uq_campus_entry_suggestion"
        ),
    )
    for table_name in (
        "budget_combination_set_version",
        "budget_combination",
        "combination_set_confirmation",
        "entry_total_version",
        "campus_entry_suggestion_version",
        "campus_entry_suggestion",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table_name in (
        "campus_entry_suggestion",
        "campus_entry_suggestion_version",
        "entry_total_version",
        "combination_set_confirmation",
        "budget_combination",
        "budget_combination_set_version",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
        op.drop_table(table_name)
