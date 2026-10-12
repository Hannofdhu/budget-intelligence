"""Store every forecast input, result and confirmation as immutable evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0028"
down_revision: str | None = "20261009_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_ledger_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("department_id", sa.String(100), nullable=False),
        sa.Column("event_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("version_id", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("previous_event_hash", sa.String(64), nullable=True),
        sa.Column("canonical_event", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("persisted_by", sa.String(100), nullable=False),
        sa.Column("persistence_reason", sa.Text(), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_no > 0", name="ck_forecast_ledger_event_number"),
        sa.CheckConstraint("kind IN ('VERSION', 'CONFIRMATION')", name="ck_forecast_ledger_event_kind"),
        sa.CheckConstraint("origin IN ('RUNTIME', 'FILE_IMPORT')", name="ck_forecast_ledger_event_origin"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_forecast_ledger_event_hash"),
        sa.CheckConstraint(
            "previous_event_hash IS NULL OR char_length(previous_event_hash) = 64",
            name="ck_forecast_ledger_event_previous_hash",
        ),
        sa.UniqueConstraint("department_id", "event_no", name="uq_forecast_ledger_event_number"),
        sa.UniqueConstraint("department_id", "request_id", name="uq_forecast_ledger_event_request"),
    )
    op.create_index(
        "ix_forecast_ledger_event_version",
        "forecast_ledger_event",
        ["department_id", "version_id", "event_no"],
    )
    op.execute(
        "CREATE TRIGGER forecast_ledger_event_immutable BEFORE UPDATE OR DELETE ON "
        "forecast_ledger_event FOR EACH ROW EXECUTE FUNCTION prevent_immutable_version_mutation();"
    )


def downgrade() -> None:
    op.drop_table("forecast_ledger_event")
