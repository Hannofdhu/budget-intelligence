"""Guard rate reference version count breakdowns.

Revision ID: 20260901_0005
Revises: 20260901_0004
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260901_0005"
down_revision: str | None = "20260901_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_rate_reference_count_breakdown",
        "rate_reference_version",
        "reference_count = exact_count + fallback_count + manual_required_count",
    )


def downgrade() -> None:
    op.drop_constraint("ck_rate_reference_count_breakdown", "rate_reference_version", type_="check")
