"""Store the direct business compiler group-table limit.

Revision ID: 20260903_0020
Revises: 20260903_0019
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0020"
down_revision: str | None = "20260903_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "optimization_run",
        sa.Column(
            "maximum_group_table_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="100000",
        ),
    )
    op.create_check_constraint(
        "ck_optimization_run_max_group_rows",
        "optimization_run",
        "maximum_group_table_rows > 0",
    )
    op.alter_column("optimization_run", "maximum_group_table_rows", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_optimization_run_max_group_rows", "optimization_run", type_="check")
    op.drop_column("optimization_run", "maximum_group_table_rows")
