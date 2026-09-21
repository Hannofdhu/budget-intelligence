"""Add locked school target sets and exact revenue closure results.

Revision ID: 20260903_0016
Revises: 20260903_0015
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0016"
down_revision: str | None = "20260903_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "target_set_version",
        sa.Column("target_set_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_target_set_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("lock_status", sa.String(20), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("batch_content_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("locked_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_target_set_year"),
        sa.CheckConstraint("lock_status = 'LOCKED'", name="ck_target_set_lock_status"),
        sa.CheckConstraint("record_count >= 2", name="ck_target_set_record_count"),
        sa.CheckConstraint("char_length(batch_content_hash) = 64", name="ck_target_set_batch_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_target_set_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_target_set_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_target_set_version_id"],
            ["target_set_version.target_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_target_set_revision"),
    )
    op.create_table(
        "budget_target",
        sa.Column("budget_target_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_stable_key", sa.String(64), nullable=False),
        sa.Column("scope_level", sa.String(20), nullable=False),
        sa.Column("track", sa.String(100)),
        sa.Column("target_revenue_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("tolerance_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_budget_target_scope"),
        sa.CheckConstraint(
            "(scope_level = 'DEPARTMENT' AND track IS NULL) OR (scope_level = 'TRACK' AND track IS NOT NULL)",
            name="ck_budget_target_track",
        ),
        sa.CheckConstraint("target_revenue_yuan >= 0", name="ck_budget_target_revenue"),
        sa.CheckConstraint("tolerance_yuan = 10000.000000", name="ck_budget_target_tolerance"),
        sa.CheckConstraint("char_length(target_stable_key) = 64", name="ck_budget_target_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_budget_target_hash"),
        sa.ForeignKeyConstraint(
            ["target_set_version_id"],
            ["target_set_version.target_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("target_set_version_id", "target_stable_key", name="uq_budget_target_key"),
    )
    op.create_table(
        "target_closure_result_version",
        sa.Column("target_closure_result_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_set_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revenue_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("calculation_rule_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("closure_status", sa.String(40), nullable=False),
        sa.Column("upstream_revenue_publishable", sa.Boolean(), nullable=False),
        sa.Column("publishable", sa.Boolean(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fiscal_year > 0", name="ck_target_closure_version_year"),
        sa.CheckConstraint("status = 'SUCCEEDED'", name="ck_target_closure_version_status"),
        sa.CheckConstraint(
            "closure_status IN ('WITHIN_TOLERANCE', 'GAP_PENDING_REBALANCE', "
            "'OVER_TARGET_PENDING_REBALANCE', 'MIXED_PENDING_REBALANCE')",
            name="ck_target_closure_status",
        ),
        sa.CheckConstraint("record_count >= 2", name="ck_target_closure_version_count"),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_target_closure_input_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_target_closure_version_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_target_closure_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_set_version_id"],
            ["target_set_version.target_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revenue_result_version_id"],
            ["revenue_result_version.revenue_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("budget_revision_id", name="uq_target_closure_version_revision"),
    )
    op.create_table(
        "target_closure_result",
        sa.Column("target_closure_result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_closure_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("budget_target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_stable_key", sa.String(64), nullable=False),
        sa.Column("scope_level", sa.String(20), nullable=False),
        sa.Column("track", sa.String(100)),
        sa.Column("target_revenue_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("calculated_revenue_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("variance_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("tolerance_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("check_status", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_target_closure_scope"),
        sa.CheckConstraint(
            "(scope_level = 'DEPARTMENT' AND track IS NULL) OR (scope_level = 'TRACK' AND track IS NOT NULL)",
            name="ck_target_closure_track",
        ),
        sa.CheckConstraint("target_revenue_yuan >= 0", name="ck_target_closure_target"),
        sa.CheckConstraint("calculated_revenue_yuan >= 0", name="ck_target_closure_calculated"),
        sa.CheckConstraint("tolerance_yuan = 10000.000000", name="ck_target_closure_tolerance"),
        sa.CheckConstraint(
            "check_status IN ('GAP', 'WITHIN_TOLERANCE', 'OVER_TARGET')",
            name="ck_target_closure_check_status",
        ),
        sa.CheckConstraint("char_length(target_stable_key) = 64", name="ck_target_closure_key"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_target_closure_hash"),
        sa.ForeignKeyConstraint(
            ["target_closure_result_version_id"],
            ["target_closure_result_version.target_closure_result_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["budget_target_id"], ["budget_target.budget_target_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "target_closure_result_version_id",
            "target_stable_key",
            name="uq_target_closure_result_key",
        ),
    )
    for table in (
        "target_set_version",
        "budget_target",
        "target_closure_result_version",
        "target_closure_result",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table in (
        "target_closure_result",
        "target_closure_result_version",
        "budget_target",
        "target_set_version",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.drop_table(table)
