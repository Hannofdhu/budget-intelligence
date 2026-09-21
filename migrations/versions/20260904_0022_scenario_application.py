"""Add immutable scenario application records and scenario-derived operations.

Revision ID: 20260904_0022
Revises: 20260904_0021
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0022"
down_revision: str | None = "20260904_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_rate_parameter_version_operation", "rate_parameter_version", type_="check")
    op.create_check_constraint(
        "ck_rate_parameter_version_operation",
        "rate_parameter_version",
        "operation_type IN ('DRAFT_REPLACE', 'LOCK', 'CHANGE_AND_LOCK', 'UNLOCK', "
        "'CROSS_YEAR_COPY', 'SCENARIO_APPLY')",
    )
    op.drop_constraint(
        "ck_planned_new_student_version_operation", "planned_new_student_version", type_="check"
    )
    op.create_check_constraint(
        "ck_planned_new_student_version_operation",
        "planned_new_student_version",
        "operation_type IN ('CHANGE_AND_LOCK', 'UNLOCK', 'SCENARIO_REBASE')",
    )
    op.drop_constraint("ck_discount_version_operation", "discount_parameter_version", type_="check")
    op.create_check_constraint(
        "ck_discount_version_operation",
        "discount_parameter_version",
        "operation_type IN ('DRAFT_REPLACE', 'SCENARIO_APPLY')",
    )

    op.create_table(
        "scenario_application",
        sa.Column("scenario_application_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rebalancing_scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("optimization_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("applied_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("change_count", sa.BigInteger(), nullable=False),
        sa.Column("entry_change_count", sa.BigInteger(), nullable=False),
        sa.Column("rate_change_count", sa.BigInteger(), nullable=False),
        sa.Column("discount_change_count", sa.BigInteger(), nullable=False),
        sa.Column("campus_entry_set_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("entry_detail_set_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("planned_new_student_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("rate_parameter_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("discount_parameter_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("inherited_discount_confirmation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("campus_confirmation_required", sa.Boolean(), nullable=False),
        sa.Column("invalidated_components", postgresql.JSONB(), nullable=False),
        sa.Column("applied_by", sa.String(100), nullable=False),
        sa.Column("role_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_scenario_application_input"),
        sa.CheckConstraint("char_length(reason) > 0", name="ck_scenario_application_reason"),
        sa.CheckConstraint("change_count > 0", name="ck_scenario_application_change_count"),
        sa.CheckConstraint(
            "entry_change_count >= 0 AND rate_change_count >= 0 AND discount_change_count >= 0",
            name="ck_scenario_application_type_counts",
        ),
        sa.CheckConstraint(
            "change_count = entry_change_count + rate_change_count + discount_change_count",
            name="ck_scenario_application_count_total",
        ),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_scenario_application_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_scenario_application_request_hash"),
        sa.ForeignKeyConstraint(
            ["rebalancing_scenario_id"],
            ["rebalancing_scenario.rebalancing_scenario_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["optimization_run_id"], ["optimization_run.optimization_run_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["applied_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["campus_entry_set_version_id"],
            ["campus_entry_set_version.campus_entry_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_detail_set_version_id"],
            ["entry_detail_set_version.entry_detail_set_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["planned_new_student_version_id"],
            ["planned_new_student_version.planned_new_student_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rate_parameter_version_id"],
            ["rate_parameter_version.rate_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["discount_parameter_version_id"],
            ["discount_parameter_version.discount_parameter_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inherited_discount_confirmation_id"],
            ["discount_parameter_confirmation.discount_parameter_confirmation_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("rebalancing_scenario_id", name="uq_scenario_application_scenario"),
        sa.UniqueConstraint("applied_revision_id", name="uq_scenario_application_revision"),
    )
    op.create_index(
        "ix_scenario_application_source", "scenario_application", ["source_revision_id", "applied_at"]
    )
    op.execute(
        "CREATE TRIGGER scenario_application_immutable BEFORE UPDATE OR DELETE ON "
        "scenario_application FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS scenario_application_immutable ON scenario_application")
    op.drop_table("scenario_application")

    op.drop_constraint("ck_discount_version_operation", "discount_parameter_version", type_="check")
    op.create_check_constraint(
        "ck_discount_version_operation",
        "discount_parameter_version",
        "operation_type = 'DRAFT_REPLACE'",
    )
    op.drop_constraint(
        "ck_planned_new_student_version_operation", "planned_new_student_version", type_="check"
    )
    op.create_check_constraint(
        "ck_planned_new_student_version_operation",
        "planned_new_student_version",
        "operation_type IN ('CHANGE_AND_LOCK', 'UNLOCK')",
    )
    op.drop_constraint("ck_rate_parameter_version_operation", "rate_parameter_version", type_="check")
    op.create_check_constraint(
        "ck_rate_parameter_version_operation",
        "rate_parameter_version",
        "operation_type IN ('DRAFT_REPLACE', 'LOCK', 'CHANGE_AND_LOCK', 'UNLOCK', 'CROSS_YEAR_COPY')",
    )
