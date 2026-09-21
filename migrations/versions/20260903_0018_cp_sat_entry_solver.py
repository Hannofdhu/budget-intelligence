"""Add an auditable CP-SAT entry-only solver selection.

Revision ID: 20260903_0018
Revises: 20260903_0017
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0018"
down_revision: str | None = "20260903_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "optimization_run",
        sa.Column(
            "solver_adapter",
            sa.String(40),
            nullable=False,
            server_default="EXACT_ENUMERATION",
        ),
    )
    op.add_column(
        "optimization_run",
        sa.Column(
            "maximum_solver_seconds",
            sa.BigInteger(),
            nullable=False,
            server_default="300",
        ),
    )
    op.add_column(
        "optimization_run",
        sa.Column(
            "maximum_candidate_validations",
            sa.BigInteger(),
            nullable=False,
            server_default="10000",
        ),
    )
    op.create_check_constraint(
        "ck_optimization_run_solver_adapter",
        "optimization_run",
        "solver_adapter IN ('EXACT_ENUMERATION', 'CP_SAT_ENTRY_ONLY')",
    )
    op.create_check_constraint(
        "ck_optimization_run_max_seconds",
        "optimization_run",
        "maximum_solver_seconds > 0",
    )
    op.create_check_constraint(
        "ck_optimization_run_max_validations",
        "optimization_run",
        "maximum_candidate_validations > 0",
    )
    op.alter_column("optimization_run", "solver_adapter", server_default=None)
    op.alter_column("optimization_run", "maximum_solver_seconds", server_default=None)
    op.alter_column("optimization_run", "maximum_candidate_validations", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_optimization_run_max_validations", "optimization_run", type_="check")
    op.drop_constraint("ck_optimization_run_max_seconds", "optimization_run", type_="check")
    op.drop_constraint("ck_optimization_run_solver_adapter", "optimization_run", type_="check")
    op.drop_column("optimization_run", "maximum_candidate_validations")
    op.drop_column("optimization_run", "maximum_solver_seconds")
    op.drop_column("optimization_run", "solver_adapter")
