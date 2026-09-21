"""Allow the CP-SAT entry-and-rates solver adapter.

Revision ID: 20260903_0019
Revises: 20260903_0018
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0019"
down_revision: str | None = "20260903_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_optimization_run_solver_adapter", "optimization_run", type_="check")
    op.create_check_constraint(
        "ck_optimization_run_solver_adapter",
        "optimization_run",
        "solver_adapter IN ('EXACT_ENUMERATION', 'CP_SAT_ENTRY_ONLY', 'CP_SAT_ENTRY_AND_RATES')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_optimization_run_solver_adapter", "optimization_run", type_="check")
    op.create_check_constraint(
        "ck_optimization_run_solver_adapter",
        "optimization_run",
        "solver_adapter IN ('EXACT_ENUMERATION', 'CP_SAT_ENTRY_ONLY')",
    )
