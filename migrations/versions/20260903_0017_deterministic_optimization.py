"""Add immutable deterministic optimization runs and candidates.

Revision ID: 20260903_0017
Revises: 20260903_0016
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0017"
down_revision: str | None = "20260903_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "optimization_run",
        sa.Column("optimization_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_closure_result_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("optimization_model_version", sa.String(100), nullable=False),
        sa.Column("terminal_stage", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("discount_unlock_required", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(100)),
        sa.Column("maximum_enumerated_states", sa.BigInteger(), nullable=False),
        sa.Column("evaluated_state_count", sa.BigInteger(), nullable=False),
        sa.Column("attempts", postgresql.JSONB(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "terminal_stage IN ('ENTRY_ONLY', 'ENTRY_AND_RATES', 'ENTRY_RATES_AND_DISCOUNT')",
            name="ck_optimization_run_stage",
        ),
        sa.CheckConstraint(
            "status IN ('OPTIMAL', 'INFEASIBLE', 'INCOMPLETE', 'FAILED')", name="ck_optimization_run_status"
        ),
        sa.CheckConstraint("maximum_enumerated_states > 0", name="ck_optimization_run_max_states"),
        sa.CheckConstraint("evaluated_state_count >= 0", name="ck_optimization_run_evaluated"),
        sa.CheckConstraint("char_length(input_fingerprint) = 64", name="ck_optimization_run_input_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_optimization_run_hash"),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_optimization_run_request_hash"),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"], ["budget_revision.budget_revision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_closure_result_version_id"],
            ["target_closure_result_version.target_closure_result_version_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_optimization_run_revision_created", "optimization_run", ["budget_revision_id", "created_at"]
    )
    op.create_table(
        "rebalancing_scenario",
        sa.Column("rebalancing_scenario_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("optimization_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("scenario_types", postgresql.JSONB(), nullable=False),
        sa.Column("target_variances", postgresql.JSONB(), nullable=False),
        sa.Column("retention_contribution_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("new_share_contribution_yuan", sa.Numeric(28, 6), nullable=False),
        sa.Column("headcount_signature", postgresql.JSONB(), nullable=False),
        sa.Column("objective_values", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("char_length(result_hash) = 64", name="ck_rebalancing_scenario_result_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_rebalancing_scenario_hash"),
        sa.ForeignKeyConstraint(
            ["optimization_run_id"], ["optimization_run.optimization_run_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("optimization_run_id", "result_hash", name="uq_rebalancing_scenario_result"),
    )
    op.create_table(
        "scenario_change",
        sa.Column("scenario_change_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rebalancing_scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_type", sa.String(20), nullable=False),
        sa.Column("object_stable_key", sa.String(100), nullable=False),
        sa.Column("original_value", sa.BigInteger(), nullable=False),
        sa.Column("suggested_value", sa.BigInteger(), nullable=False),
        sa.Column("adjustment", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("object_type IN ('ENTRY', 'RATE', 'DISCOUNT')", name="ck_scenario_change_type"),
        sa.CheckConstraint("char_length(object_stable_key) > 0", name="ck_scenario_change_key"),
        sa.CheckConstraint("suggested_value - original_value = adjustment", name="ck_scenario_change_delta"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_scenario_change_hash"),
        sa.ForeignKeyConstraint(
            ["rebalancing_scenario_id"], ["rebalancing_scenario.rebalancing_scenario_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "rebalancing_scenario_id", "object_type", "object_stable_key", name="uq_scenario_change"
        ),
    )
    op.create_table(
        "infeasibility_proof",
        sa.Column("infeasibility_proof_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("optimization_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("profile", sa.String(40), nullable=False),
        sa.Column("proof_code", sa.String(100), nullable=False),
        sa.Column("model_hash", sa.String(64), nullable=False),
        sa.Column("hard_constraint_groups", postgresql.JSONB(), nullable=False),
        sa.Column("proof_summary", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "stage IN ('ENTRY_ONLY', 'ENTRY_AND_RATES', 'ENTRY_RATES_AND_DISCOUNT')",
            name="ck_infeasibility_proof_stage",
        ),
        sa.CheckConstraint("char_length(model_hash) = 64", name="ck_infeasibility_proof_model_hash"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_infeasibility_proof_hash"),
        sa.ForeignKeyConstraint(
            ["optimization_run_id"], ["optimization_run.optimization_run_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("optimization_run_id", "stage", "profile", name="uq_infeasibility_proof"),
    )
    for table in ("optimization_run", "rebalancing_scenario", "scenario_change", "infeasibility_proof"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
        )


def downgrade() -> None:
    for table in ("infeasibility_proof", "scenario_change", "rebalancing_scenario", "optimization_run"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.drop_table(table)
