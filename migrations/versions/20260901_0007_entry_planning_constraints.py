"""Align entry-planning constraint names and delete policies.

Revision ID: 20260901_0007
Revises: 20260901_0006
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260901_0007"
down_revision: str | None = "20260901_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UNIQUE_CONSTRAINTS = (
    (
        "budget_combination_set_version",
        "budget_combination_set_version_budget_revision_id_key",
        "uq_combination_set_revision",
    ),
    (
        "entry_total_version",
        "entry_total_version_budget_revision_id_key",
        "uq_entry_total_revision",
    ),
    (
        "campus_entry_suggestion_version",
        "campus_entry_suggestion_version_budget_revision_id_key",
        "uq_campus_suggestion_revision",
    ),
)

FOREIGN_KEYS = (
    (
        "budget_combination_set_version_budget_revision_id_fkey",
        "budget_combination_set_version",
        "budget_revision",
        "budget_revision_id",
        "budget_revision_id",
    ),
    (
        "budget_combination_set_version_source_snapshot_id_fkey",
        "budget_combination_set_version",
        "source_snapshot",
        "source_snapshot_id",
        "source_snapshot_id",
    ),
    (
        "budget_combination_set_version_rate_reference_version_id_fkey",
        "budget_combination_set_version",
        "rate_reference_version",
        "rate_reference_version_id",
        "rate_reference_version_id",
    ),
    (
        "budget_combination_combination_set_version_id_fkey",
        "budget_combination",
        "budget_combination_set_version",
        "combination_set_version_id",
        "combination_set_version_id",
    ),
    (
        "combination_set_confirmation_combination_set_version_id_fkey",
        "combination_set_confirmation",
        "budget_combination_set_version",
        "combination_set_version_id",
        "combination_set_version_id",
    ),
    (
        "combination_set_confirmation_confirmed_revision_id_fkey",
        "combination_set_confirmation",
        "budget_revision",
        "confirmed_revision_id",
        "budget_revision_id",
    ),
    (
        "entry_total_version_budget_revision_id_fkey",
        "entry_total_version",
        "budget_revision",
        "budget_revision_id",
        "budget_revision_id",
    ),
    (
        "campus_entry_suggestion_version_budget_revision_id_fkey",
        "campus_entry_suggestion_version",
        "budget_revision",
        "budget_revision_id",
        "budget_revision_id",
    ),
    (
        "campus_entry_suggestion_version_combination_set_version_id_fkey",
        "campus_entry_suggestion_version",
        "budget_combination_set_version",
        "combination_set_version_id",
        "combination_set_version_id",
    ),
    (
        "campus_entry_suggestion_version_entry_total_version_id_fkey",
        "campus_entry_suggestion_version",
        "entry_total_version",
        "entry_total_version_id",
        "entry_total_version_id",
    ),
    (
        "campus_entry_suggestion_version_source_snapshot_id_fkey",
        "campus_entry_suggestion_version",
        "source_snapshot",
        "source_snapshot_id",
        "source_snapshot_id",
    ),
    (
        "campus_entry_suggestion_campus_entry_suggestion_version_id_fkey",
        "campus_entry_suggestion",
        "campus_entry_suggestion_version",
        "campus_entry_suggestion_version_id",
        "campus_entry_suggestion_version_id",
    ),
)


def _recreate_foreign_keys(*, ondelete: str | None) -> None:
    for name, source_table, referent_table, local_column, remote_column in FOREIGN_KEYS:
        op.drop_constraint(name, source_table, type_="foreignkey")
        op.create_foreign_key(
            name,
            source_table,
            referent_table,
            [local_column],
            [remote_column],
            ondelete=ondelete,
        )


def upgrade() -> None:
    for table_name, generated_name, model_name in UNIQUE_CONSTRAINTS:
        op.drop_constraint(generated_name, table_name, type_="unique")
        op.create_unique_constraint(model_name, table_name, ["budget_revision_id"])
    _recreate_foreign_keys(ondelete="RESTRICT")


def downgrade() -> None:
    _recreate_foreign_keys(ondelete=None)
    for table_name, generated_name, model_name in UNIQUE_CONSTRAINTS:
        op.drop_constraint(model_name, table_name, type_="unique")
        op.create_unique_constraint(generated_name, table_name, ["budget_revision_id"])
