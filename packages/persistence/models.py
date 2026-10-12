import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from packages.persistence.ids import uuid7


class Base(DeclarativeBase):
    pass


class AnnualBudget(Base):
    __tablename__ = "annual_budget"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_annual_budget_fiscal_year"),
        UniqueConstraint("department_id", "fiscal_year", name="uq_annual_budget_scope"),
    )

    annual_budget_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    department_id: Mapped[str] = mapped_column(String(100), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    current_draft_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "budget_revision.budget_revision_id",
            name="fk_annual_budget_current_draft",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    current_published_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "budget_revision.budget_revision_id",
            name="fk_annual_budget_current_published",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BudgetRevision(Base):
    __tablename__ = "budget_revision"
    __table_args__ = (
        CheckConstraint("revision_no > 0", name="ck_budget_revision_number"),
        CheckConstraint(
            "request_hash IS NULL OR char_length(request_hash) = 64",
            name="ck_budget_revision_request_hash",
        ),
        UniqueConstraint("annual_budget_id", "revision_no", name="uq_budget_revision_number"),
        UniqueConstraint(
            "annual_budget_id", "command_type", "idempotency_key", name="uq_budget_revision_idempotency"
        ),
    )

    budget_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    annual_budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("annual_budget.annual_budget_id", ondelete="RESTRICT"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=True
    )
    command_type: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=True
    )
    parameter_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parameter_version.parameter_version_id", ondelete="RESTRICT"), nullable=True
    )
    revision_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    component_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TargetSetVersion(Base):
    __tablename__ = "target_set_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_target_set_year"),
        CheckConstraint("lock_status = 'LOCKED'", name="ck_target_set_lock_status"),
        CheckConstraint("record_count >= 2", name="ck_target_set_record_count"),
        CheckConstraint("char_length(batch_content_hash) = 64", name="ck_target_set_batch_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_target_set_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_target_set_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_target_set_revision"),
    )

    target_set_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    parent_target_set_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("target_set_version.target_set_version_id", ondelete="RESTRICT"), nullable=True
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    lock_status: Mapped[str] = mapped_column(String(20), nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    locked_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BudgetTarget(Base):
    __tablename__ = "budget_target"
    __table_args__ = (
        CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_budget_target_scope"),
        CheckConstraint(
            "(scope_level = 'DEPARTMENT' AND track IS NULL) OR (scope_level = 'TRACK' AND track IS NOT NULL)",
            name="ck_budget_target_track",
        ),
        CheckConstraint("target_revenue_yuan >= 0", name="ck_budget_target_revenue"),
        CheckConstraint("tolerance_yuan = 10000.000000", name="ck_budget_target_tolerance"),
        CheckConstraint("char_length(target_stable_key) = 64", name="ck_budget_target_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_budget_target_hash"),
        UniqueConstraint("target_set_version_id", "target_stable_key", name="uq_budget_target_key"),
    )

    budget_target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    target_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_set_version.target_set_version_id", ondelete="RESTRICT"), nullable=False
    )
    target_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(20), nullable=False)
    track: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    tolerance_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class OpeningStudentVersion(Base):
    __tablename__ = "opening_student_version"
    __table_args__ = (
        CheckConstraint("char_length(content_hash) = 64", name="ck_opening_hash"),
        UniqueConstraint("budget_revision_id", name="uq_opening_revision"),
    )
    opening_student_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    source_component_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot_component.source_snapshot_component_id", ondelete="RESTRICT"),
        nullable=False,
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    rate_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_parameter_version.rate_parameter_version_id", ondelete="RESTRICT"), nullable=False
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshot"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'WAITING_FOR_SUMMARY', 'RECONCILED', 'FROZEN')",
            name="ck_source_snapshot_status",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_source_snapshot_content_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_source_snapshot_request_hash"),
        Index(
            "ix_source_snapshot_department_created",
            "department_id",
            "created_at",
            "source_snapshot_id",
        ),
        UniqueConstraint("build_job_id", name="uq_source_snapshot_build_job"),
    )

    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    build_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_id: Mapped[str] = mapped_column(String(100), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceSnapshotComponent(Base):
    __tablename__ = "source_snapshot_component"
    __table_args__ = (
        CheckConstraint("row_count >= 0", name="ck_snapshot_component_row_count"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_snapshot_component_content_hash"),
        UniqueConstraint(
            "source_snapshot_id", "dataset_id", "partition_value", name="uq_snapshot_component_partition"
        ),
    )

    source_snapshot_component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    partition_value: Mapped[str] = mapped_column(String(200), nullable=False)
    task_batch_id: Mapped[str] = mapped_column(String(200), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field_mapping_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SourceReconciliation(Base):
    __tablename__ = "source_reconciliation"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "metric_code", name="uq_source_reconciliation_metric"),
    )

    source_reconciliation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    metric_code: Mapped[str] = mapped_column(String(100), nullable=False)
    left_source: Mapped[str] = mapped_column(String(200), nullable=False)
    right_source: Mapped[str] = mapped_column(String(200), nullable=False)
    left_numerator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    left_denominator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    right_numerator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    right_denominator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceMetricAggregate(Base):
    __tablename__ = "source_metric_aggregate"
    __table_args__ = (
        CheckConstraint("numerator >= 0", name="ck_source_metric_aggregate_numerator"),
        CheckConstraint("denominator >= 0", name="ck_source_metric_aggregate_denominator"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_source_metric_aggregate_hash"),
        UniqueConstraint(
            "source_snapshot_id",
            "metric_code",
            "fiscal_season",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_source_metric_aggregate_key",
        ),
        Index(
            "ix_source_metric_aggregate_lookup",
            "source_snapshot_id",
            "metric_code",
            "fiscal_season",
        ),
    )

    source_metric_aggregate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    metric_code: Mapped[str] = mapped_column(String(100), nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    numerator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    denominator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    source_dataset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class EntryHistoryAggregate(Base):
    __tablename__ = "entry_history_aggregate"
    __table_args__ = (
        CheckConstraint("business_new_student_count >= 0", name="ck_entry_history_count"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_entry_history_hash"),
        UniqueConstraint(
            "source_snapshot_id",
            "fiscal_season",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_entry_history_aggregate_key",
        ),
    )

    entry_history_aggregate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    business_new_student_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_dataset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SourceMappingVerification(Base):
    __tablename__ = "source_mapping_verification"
    __table_args__ = (
        UniqueConstraint(
            "source_snapshot_id", "business_metric_code", name="uq_source_mapping_verification_metric"
        ),
    )

    source_mapping_verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    business_metric_code: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    physical_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    business_definition: Mapped[str] = mapped_column(String(1000), nullable=False)
    etl_resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    field_mapping_version: Mapped[str] = mapped_column(String(100), nullable=False)
    verified_by: Mapped[str] = mapped_column(String(100), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(500), nullable=False)


class RateReferenceVersion(Base):
    __tablename__ = "rate_reference_version"
    __table_args__ = (
        CheckConstraint("target_fiscal_year > 0", name="ck_rate_reference_target_fiscal_year"),
        CheckConstraint("minimum_sample_size > 0", name="ck_rate_reference_minimum_sample"),
        CheckConstraint("reference_count >= 0", name="ck_rate_reference_count"),
        CheckConstraint("exact_count >= 0", name="ck_rate_reference_exact_count"),
        CheckConstraint("fallback_count >= 0", name="ck_rate_reference_fallback_count"),
        CheckConstraint("manual_required_count >= 0", name="ck_rate_reference_manual_required_count"),
        CheckConstraint(
            "reference_count = exact_count + fallback_count + manual_required_count",
            name="ck_rate_reference_count_breakdown",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_rate_reference_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_rate_reference_request_hash"),
        Index(
            "ix_rate_reference_version_snapshot_created",
            "source_snapshot_id",
            "created_at",
            "rate_reference_version_id",
        ),
    )

    rate_reference_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    target_fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    maturity_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reference_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exact_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fallback_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manual_required_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RateReference(Base):
    __tablename__ = "rate_reference"
    __table_args__ = (
        CheckConstraint(
            "rate_type IN ('RENEWAL_RATE', 'NEW_STUDENT_SHARE')",
            name="ck_rate_reference_type",
        ),
        CheckConstraint(
            "source_type IN ('EXACT_HISTORY', 'CAMPUS_REMOVED_FALLBACK', 'MANUAL_REQUIRED')",
            name="ck_rate_reference_source_type",
        ),
        CheckConstraint("exact_numerator >= 0", name="ck_rate_reference_exact_numerator"),
        CheckConstraint("exact_denominator >= 0", name="ck_rate_reference_exact_denominator"),
        CheckConstraint("reference_numerator >= 0", name="ck_rate_reference_numerator"),
        CheckConstraint("reference_denominator >= 0", name="ck_rate_reference_denominator"),
        CheckConstraint("minimum_sample_size > 0", name="ck_rate_reference_row_minimum_sample"),
        CheckConstraint(
            "exact_rate_ppm IS NULL OR exact_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_rate_reference_exact_rate_ppm",
        ),
        CheckConstraint(
            "reference_rate_ppm IS NULL OR reference_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_rate_reference_rate_ppm",
        ),
        CheckConstraint(
            "system_value_ppm IS NULL OR "
            "(rate_type = 'RENEWAL_RATE' AND system_value_ppm BETWEEN 0 AND 1000000) OR "
            "(rate_type = 'NEW_STUDENT_SHARE' AND system_value_ppm BETWEEN 0 AND 999900)",
            name="ck_rate_reference_system_value_ppm",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_rate_reference_hash"),
        CheckConstraint(
            "(rate_type = 'RENEWAL_RATE' AND renewal_relation_code = 'SAME_BUDGET_ROW') "
            "OR (rate_type = 'NEW_STUDENT_SHARE' AND renewal_relation_code IS NULL)",
            name="ck_rate_reference_relation",
        ),
        UniqueConstraint(
            "rate_reference_version_id",
            "rate_type",
            "target_fiscal_season",
            "track",
            "product",
            "grade",
            "campus",
            name="uq_rate_reference_key",
        ),
        Index(
            "ix_rate_reference_page",
            "rate_reference_version_id",
            "rate_reference_id",
        ),
    )

    rate_reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    rate_reference_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_reference_version.rate_reference_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    rate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    target_fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    renewal_relation_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exact_numerator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    exact_denominator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    reference_numerator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    reference_denominator: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    exact_rate_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_rate_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    system_value_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RateParameterVersion(Base):
    __tablename__ = "rate_parameter_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_rate_parameter_version_fiscal_year"),
        CheckConstraint("record_count > 0", name="ck_rate_parameter_version_count"),
        CheckConstraint(
            "locked_count BETWEEN 0 AND record_count", name="ck_rate_parameter_version_locked_count"
        ),
        CheckConstraint("copy_exception_count >= 0", name="ck_rate_parameter_version_copy_count"),
        CheckConstraint(
            "operation_type IN ('DRAFT_REPLACE', 'LOCK', 'CHANGE_AND_LOCK', 'UNLOCK', "
            "'CROSS_YEAR_COPY', 'SCENARIO_APPLY')",
            name="ck_rate_parameter_version_operation",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_rate_parameter_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_rate_parameter_version_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_rate_parameter_version_revision"),
    )

    rate_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    parent_rate_parameter_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rate_parameter_version.rate_parameter_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    rate_reference_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_reference_version.rate_reference_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locked_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    copy_exception_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RateParameter(Base):
    __tablename__ = "rate_parameter"
    __table_args__ = (
        CheckConstraint("rate_type IN ('RENEWAL_RATE', 'NEW_STUDENT_SHARE')", name="ck_rate_parameter_type"),
        CheckConstraint("rate_usage_mode IN ('RATE_INPUT', 'REVERSE_INPUT')", name="ck_rate_parameter_usage"),
        CheckConstraint("fiscal_year > 0", name="ck_rate_parameter_fiscal_year"),
        CheckConstraint("target_fiscal_season > 0", name="ck_rate_parameter_season"),
        CheckConstraint(
            "current_value_ppm % 100 = 0 AND lower_bound_ppm % 100 = 0 AND upper_bound_ppm % 100 = 0",
            name="ck_rate_parameter_precision",
        ),
        CheckConstraint(
            "0 <= lower_bound_ppm AND lower_bound_ppm <= current_value_ppm "
            "AND current_value_ppm <= upper_bound_ppm AND upper_bound_ppm <= 1000000",
            name="ck_rate_parameter_boundary",
        ),
        CheckConstraint(
            "rate_type <> 'NEW_STUDENT_SHARE' OR rate_usage_mode <> 'REVERSE_INPUT' "
            "OR upper_bound_ppm <= 999900",
            name="ck_rate_parameter_reverse_share_max",
        ),
        CheckConstraint(
            "(locked = TRUE AND locked_value_ppm = current_value_ppm) OR "
            "(locked = FALSE AND locked_value_ppm IS NULL)",
            name="ck_rate_parameter_lock",
        ),
        CheckConstraint("char_length(rate_parameter_stable_key) = 64", name="ck_rate_parameter_stable_key"),
        CheckConstraint("char_length(cross_year_match_key) = 64", name="ck_rate_parameter_cross_year_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_rate_parameter_hash"),
        UniqueConstraint(
            "rate_parameter_version_id", "rate_parameter_stable_key", name="uq_rate_parameter_key"
        ),
    )

    rate_parameter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    rate_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_parameter_version.rate_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    rate_reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_reference.rate_reference_id", ondelete="RESTRICT"), nullable=False
    )
    rate_parameter_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    cross_year_match_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rate_usage_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    source_fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    target_fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    renewal_relation_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    system_value_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_value_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    lower_bound_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    upper_bound_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    boundary_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    manual_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    locked_value_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lock_origin: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RateBoundaryConfirmation(Base):
    __tablename__ = "rate_boundary_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(rate_parameter_stable_key) = 64", name="ck_rate_boundary_key"),
        CheckConstraint("char_length(boundary_fingerprint) = 64", name="ck_rate_boundary_fingerprint"),
        UniqueConstraint(
            "rate_parameter_version_id",
            "rate_parameter_stable_key",
            "boundary_fingerprint",
            name="uq_rate_boundary_confirmation",
        ),
    )

    rate_boundary_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    rate_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_parameter_version.rate_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    rate_parameter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_parameter.rate_parameter_id", ondelete="RESTRICT"), nullable=False
    )
    rate_parameter_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    boundary_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    inherited_from_confirmation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rate_boundary_confirmation.rate_boundary_confirmation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RateParameterCopyException(Base):
    __tablename__ = "rate_parameter_copy_exception"
    __table_args__ = (
        CheckConstraint("char_length(source_stable_key) = 64", name="ck_rate_copy_source_key"),
        CheckConstraint("char_length(cross_year_match_key) = 64", name="ck_rate_copy_match_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_rate_copy_hash"),
        UniqueConstraint("rate_parameter_version_id", "source_stable_key", name="uq_rate_copy_exception"),
    )

    rate_parameter_copy_exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    rate_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_parameter_version.rate_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    cross_year_match_key: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class PlannedNewStudentVersion(Base):
    __tablename__ = "planned_new_student_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_planned_new_student_version_year"),
        CheckConstraint("record_count > 0", name="ck_planned_new_student_version_count"),
        CheckConstraint(
            "locked_count BETWEEN 0 AND record_count",
            name="ck_planned_new_student_version_locked_count",
        ),
        CheckConstraint(
            "operation_type IN ('CHANGE_AND_LOCK', 'UNLOCK', 'SCENARIO_REBASE')",
            name="ck_planned_new_student_version_operation",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_planned_new_student_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_planned_new_student_version_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_planned_new_student_version_revision"),
    )

    planned_new_student_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    parent_planned_new_student_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_new_student_version.planned_new_student_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_scope_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_scope_version.entry_scope_version_id", ondelete="RESTRICT"), nullable=False
    )
    entry_detail_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_detail_set_version.entry_detail_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locked_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlannedNewStudentCount(Base):
    __tablename__ = "planned_new_student_count"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_planned_new_student_year"),
        CheckConstraint("fiscal_season > 0", name="ck_planned_new_student_season"),
        CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_planned_new_student_entry"),
        CheckConstraint("planned_new_students >= 0", name="ck_planned_new_student_value"),
        CheckConstraint(
            "(locked = TRUE AND locked_value = planned_new_students) OR "
            "(locked = FALSE AND locked_value IS NULL)",
            name="ck_planned_new_student_lock",
        ),
        CheckConstraint(
            "(planned_new_students > 0 AND calculated_new_student_share_ppm = 1000000) OR "
            "(planned_new_students = 0 AND calculated_new_student_share_ppm IS NULL)",
            name="ck_planned_new_student_share",
        ),
        CheckConstraint(
            "(locked = FALSE AND handling_status = 'FILLED_PENDING_LOCK') OR "
            "(locked = TRUE AND planned_new_students = 0 AND handling_status = 'LOCKED_ZERO') OR "
            "(locked = TRUE AND planned_new_students > 0 AND handling_status = 'LOCKED_POSITIVE')",
            name="ck_planned_new_student_status",
        ),
        CheckConstraint(
            "char_length(planned_new_student_stable_key) = 64",
            name="ck_planned_new_student_stable_key",
        ),
        CheckConstraint(
            "char_length(combination_stable_key) = 64",
            name="ck_planned_new_student_combination_key",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_planned_new_student_hash"),
        UniqueConstraint(
            "planned_new_student_version_id",
            "planned_new_student_stable_key",
            name="uq_planned_new_student_key",
        ),
    )

    planned_new_student_count_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    planned_new_student_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planned_new_student_version.planned_new_student_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    combination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination.combination_id", ondelete="RESTRICT"), nullable=False
    )
    entry_detail_allocation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entry_detail_allocation.entry_detail_allocation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    planned_new_student_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_attribute: Mapped[str] = mapped_column(String(20), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    planned_new_students: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manual_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    calculated_new_student_share_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    locked_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    handling_status: Mapped[str] = mapped_column(String(30), nullable=False)
    lock_origin: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class HeadcountResultVersion(Base):
    __tablename__ = "headcount_result_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_headcount_result_version_year"),
        CheckConstraint("record_count > 0", name="ck_headcount_result_version_count"),
        CheckConstraint("validation_count >= 0", name="ck_headcount_result_validation_count"),
        CheckConstraint("status = 'SUCCEEDED'", name="ck_headcount_result_version_status"),
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_headcount_result_input_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_headcount_result_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_headcount_result_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_headcount_result_version_revision"),
    )

    headcount_result_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_scope_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_scope_version.entry_scope_version_id", ondelete="RESTRICT"), nullable=False
    )
    entry_detail_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_detail_set_version.entry_detail_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    rate_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_parameter_version.rate_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    planned_new_student_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_new_student_version.planned_new_student_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    validation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SeasonHeadcountResult(Base):
    __tablename__ = "season_headcount_result"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_season_headcount_year"),
        CheckConstraint("fiscal_season > 0", name="ck_season_headcount_season"),
        CheckConstraint("season_index BETWEEN 1 AND 4", name="ck_season_headcount_index"),
        CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_season_headcount_entry"),
        CheckConstraint("calculation_status IN ('CALCULATED', 'BLOCKED')", name="ck_season_headcount_status"),
        CheckConstraint(
            "new_student_source IS NULL OR new_student_source IN "
            "('ENTRY_ALLOCATION', 'LOCKED_MANUAL_PLAN', 'RATE_INFERRED')",
            name="ck_season_headcount_new_source",
        ),
        CheckConstraint(
            "planned_eligibility_status IN "
            "('ENTRY_ABSOLUTE', 'APPLICABLE_LOCKED', 'NOT_APPLICABLE', 'REQUIRED_MISSING')",
            name="ck_season_headcount_plan_status",
        ),
        CheckConstraint(
            "theoretical_old_students IS NULL OR theoretical_old_students >= 0",
            name="ck_season_headcount_old_theory",
        ),
        CheckConstraint(
            "theoretical_new_students IS NULL OR theoretical_new_students >= 0",
            name="ck_season_headcount_new_theory",
        ),
        CheckConstraint(
            "integer_old_students IS NULL OR integer_old_students >= 0",
            name="ck_season_headcount_old_integer",
        ),
        CheckConstraint(
            "integer_new_students IS NULL OR integer_new_students >= 0",
            name="ck_season_headcount_new_integer",
        ),
        CheckConstraint(
            "(calculation_status = 'CALCULATED' AND integer_old_students IS NOT NULL "
            "AND integer_new_students IS NOT NULL AND integer_total_students = "
            "integer_old_students + integer_new_students) OR "
            "(calculation_status = 'BLOCKED' AND integer_total_students IS NULL)",
            name="ck_season_headcount_total",
        ),
        CheckConstraint(
            "realized_retention_rate_ppm IS NULL OR realized_retention_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_season_headcount_realized_renewal",
        ),
        CheckConstraint(
            "realized_new_student_share_ppm IS NULL OR realized_new_student_share_ppm BETWEEN 0 AND 1000000",
            name="ck_season_headcount_realized_share",
        ),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_season_headcount_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_season_headcount_hash"),
        UniqueConstraint(
            "headcount_result_version_id",
            "combination_id",
            "fiscal_season",
            name="uq_season_headcount_result",
        ),
    )

    season_headcount_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    headcount_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("headcount_result_version.headcount_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    combination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination.combination_id", ondelete="RESTRICT"), nullable=False
    )
    entry_detail_allocation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entry_detail_allocation.entry_detail_allocation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    renewal_rate_parameter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rate_parameter.rate_parameter_id", ondelete="RESTRICT"), nullable=True
    )
    new_student_share_parameter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rate_parameter.rate_parameter_id", ondelete="RESTRICT"), nullable=True
    )
    planned_new_student_count_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_new_student_count.planned_new_student_count_id", ondelete="RESTRICT"),
        nullable=True,
    )
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    season_index: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_attribute: Mapped[str] = mapped_column(String(20), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    calculation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    new_student_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    planned_eligibility_status: Mapped[str] = mapped_column(String(30), nullable=False)
    theoretical_old_students: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    theoretical_new_students: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    theoretical_old_fraction: Mapped[str | None] = mapped_column(String(200), nullable=True)
    theoretical_new_fraction: Mapped[str | None] = mapped_column(String(200), nullable=True)
    integer_old_students: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    integer_new_students: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    integer_total_students: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    renewal_rate_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_student_share_input_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    realized_retention_rate_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    realized_new_student_share_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_rounding_variance: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    new_rounding_variance: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class HeadcountRoundingTrace(Base):
    __tablename__ = "headcount_rounding_trace"
    __table_args__ = (
        CheckConstraint("student_type IN ('OLD', 'NEW')", name="ck_headcount_trace_type"),
        CheckConstraint("floor_value >= 0", name="ck_headcount_trace_floor"),
        CheckConstraint("group_target >= 0", name="ck_headcount_trace_group_target"),
        CheckConstraint("fixed_total >= 0", name="ck_headcount_trace_fixed_total"),
        CheckConstraint("adjustable_target >= 0", name="ck_headcount_trace_adjustable_target"),
        CheckConstraint(
            "(fixed_input = TRUE AND remainder_rank IS NULL AND tail_assigned = FALSE) OR "
            "(fixed_input = FALSE AND remainder_rank > 0)",
            name="ck_headcount_trace_mode",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_headcount_trace_hash"),
        UniqueConstraint("season_headcount_result_id", "student_type", name="uq_headcount_rounding_trace"),
    )

    headcount_rounding_trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    season_headcount_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("season_headcount_result.season_headcount_result_id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_type: Mapped[str] = mapped_column(String(10), nullable=False)
    exact_theory_fraction: Mapped[str] = mapped_column(String(200), nullable=False)
    floor_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exact_remainder_fraction: Mapped[str] = mapped_column(String(200), nullable=False)
    fixed_input: Mapped[bool] = mapped_column(Boolean, nullable=False)
    remainder_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tail_assigned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    group_target: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fixed_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    adjustable_target: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class HeadcountValidationResult(Base):
    __tablename__ = "headcount_validation_result"
    __table_args__ = (
        CheckConstraint("severity = 'BLOCKING'", name="ck_headcount_validation_severity"),
        CheckConstraint("fiscal_season > 0", name="ck_headcount_validation_season"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_headcount_validation_hash"),
        UniqueConstraint(
            "headcount_result_version_id",
            "fiscal_season",
            "combination_stable_key",
            "code",
            name="uq_headcount_validation_result",
        ),
    )

    headcount_validation_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    headcount_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("headcount_result_version.headcount_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ResourceFinanceParameterVersion(Base):
    __tablename__ = "resource_finance_parameter_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_resource_finance_year"),
        CheckConstraint("operation_type = 'DRAFT_REPLACE'", name="ck_resource_finance_operation"),
        CheckConstraint("budget_tax_conversion_factor > 0", name="ck_resource_finance_tax_factor"),
        CheckConstraint("class_size_record_count > 0", name="ck_resource_finance_class_count"),
        CheckConstraint("revenue_record_count > 0", name="ck_resource_finance_revenue_count"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_resource_finance_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_resource_finance_request_hash"),
        CheckConstraint("char_length(batch_content_hash) = 64", name="ck_resource_finance_batch_hash"),
        UniqueConstraint("budget_revision_id", name="uq_resource_finance_revision"),
    )

    resource_finance_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    parent_resource_finance_parameter_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "resource_finance_parameter_version.resource_finance_parameter_version_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    budget_tax_conversion_factor: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    tax_factor_source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tax_factor_change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    class_size_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revenue_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opening_carryover_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassSizeParameter(Base):
    __tablename__ = "class_size_parameter"
    __table_args__ = (
        CheckConstraint("system_value > 0", name="ck_class_size_system_value"),
        CheckConstraint("final_value > 0", name="ck_class_size_final_value"),
        CheckConstraint("char_length(class_size_parameter_stable_key) = 64", name="ck_class_size_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_hash"),
        UniqueConstraint(
            "resource_finance_parameter_version_id",
            "track",
            "product",
            name="uq_class_size_parameter_key",
        ),
    )

    class_size_parameter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    resource_finance_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "resource_finance_parameter_version.resource_finance_parameter_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    class_size_parameter_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    system_value: Mapped[int] = mapped_column(Integer, nullable=False)
    final_value: Mapped[int] = mapped_column(Integer, nullable=False)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RevenueParameter(Base):
    __tablename__ = "revenue_parameter"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_revenue_parameter_year"),
        CheckConstraint("fiscal_season > 0", name="ck_revenue_parameter_season"),
        CheckConstraint("list_price_yuan >= 0", name="ck_revenue_parameter_price"),
        CheckConstraint(
            "current_season_recognition_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_revenue_parameter_current_rate",
        ),
        CheckConstraint(
            "cross_season_recognition_rate_ppm = 1000000 - current_season_recognition_rate_ppm",
            name="ck_revenue_parameter_complement",
        ),
        CheckConstraint(
            "opening_carryover_revenue_yuan >= 0 AND "
            "((fiscal_season % 10) = 1 OR opening_carryover_revenue_yuan = 0)",
            name="ck_revenue_parameter_opening_carryover",
        ),
        CheckConstraint("char_length(revenue_parameter_stable_key) = 64", name="ck_revenue_parameter_key"),
        CheckConstraint("char_length(recognition_parameter_key) = 64", name="ck_recognition_parameter_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_parameter_hash"),
        UniqueConstraint(
            "resource_finance_parameter_version_id",
            "fiscal_season",
            "track",
            "product",
            name="uq_revenue_parameter_key",
        ),
    )

    revenue_parameter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    resource_finance_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "resource_finance_parameter_version.resource_finance_parameter_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    revenue_parameter_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    recognition_parameter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    list_price_yuan: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    current_season_recognition_rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    cross_season_recognition_rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    opening_carryover_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class OpeningCarryoverParameter(Base):
    __tablename__ = "opening_carryover_parameter"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_opening_carryover_year"),
        CheckConstraint("(fiscal_season % 10) = 1", name="ck_opening_carryover_first_season"),
        CheckConstraint("amount_yuan >= 0", name="ck_opening_carryover_amount"),
        CheckConstraint("char_length(opening_carryover_stable_key) = 64", name="ck_opening_carryover_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_opening_carryover_hash"),
        UniqueConstraint(
            "resource_finance_parameter_version_id",
            "fiscal_season",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_opening_carryover_parameter_key",
        ),
    )

    opening_carryover_parameter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    resource_finance_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "resource_finance_parameter_version.resource_finance_parameter_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    opening_carryover_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ResourceFinanceConfirmation(Base):
    __tablename__ = "resource_finance_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_resource_finance_confirm_hash"),
        UniqueConstraint(
            "resource_finance_parameter_version_id",
            "input_fingerprint",
            name="uq_resource_finance_confirmation",
        ),
    )

    resource_finance_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    resource_finance_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "resource_finance_parameter_version.resource_finance_parameter_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    confirmed_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassSizeResultVersion(Base):
    __tablename__ = "class_size_result_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_class_size_result_version_year"),
        CheckConstraint("status = 'SUCCEEDED'", name="ck_class_size_result_version_status"),
        CheckConstraint("record_count > 0", name="ck_class_size_result_version_count"),
        CheckConstraint("validation_count >= 0", name="ck_class_size_result_validation_count"),
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_class_size_result_input_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_result_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_class_size_result_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_class_size_result_version_revision"),
    )

    class_size_result_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    headcount_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("headcount_result_version.headcount_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    resource_finance_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "resource_finance_parameter_version.resource_finance_parameter_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    resource_finance_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_finance_confirmation.resource_finance_confirmation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    validation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassSizeResult(Base):
    __tablename__ = "class_size_result"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_class_size_result_year"),
        CheckConstraint("fiscal_season > 0", name="ck_class_size_result_season"),
        CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_class_size_result_entry"),
        CheckConstraint(
            "class_size_status IN ('NO_PLANNED_CLASS', 'BELOW_MINIMUM_CLASS_SIZE', 'PASS', "
            "'HEADCOUNT_UNAVAILABLE', 'PARAMETER_MISSING')",
            name="ck_class_size_result_status",
        ),
        CheckConstraint(
            "integer_total_students IS NULL OR integer_total_students >= 0",
            name="ck_class_size_result_headcount",
        ),
        CheckConstraint(
            "minimum_class_size IS NULL OR minimum_class_size > 0",
            name="ck_class_size_result_minimum",
        ),
        CheckConstraint("class_size_gap IS NULL OR class_size_gap >= 0", name="ck_class_size_result_gap"),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_class_size_result_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_result_hash"),
        UniqueConstraint(
            "class_size_result_version_id",
            "season_headcount_result_id",
            name="uq_class_size_result_row",
        ),
    )

    class_size_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    class_size_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("class_size_result_version.class_size_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    season_headcount_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("season_headcount_result.season_headcount_result_id", ondelete="RESTRICT"),
        nullable=False,
    )
    class_size_parameter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("class_size_parameter.class_size_parameter_id", ondelete="RESTRICT"), nullable=True
    )
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_attribute: Mapped[str] = mapped_column(String(20), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    integer_total_students: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    minimum_class_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    class_size_gap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    class_size_status: Mapped[str] = mapped_column(String(40), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ClassSizeValidationResult(Base):
    __tablename__ = "class_size_validation_result"
    __table_args__ = (
        CheckConstraint("severity = 'BLOCKING'", name="ck_class_size_validation_severity"),
        CheckConstraint("fiscal_season > 0", name="ck_class_size_validation_season"),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_class_size_validation_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_class_size_validation_hash"),
        UniqueConstraint(
            "class_size_result_version_id",
            "fiscal_season",
            "combination_stable_key",
            "code",
            name="uq_class_size_validation_result",
        ),
    )

    class_size_validation_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    class_size_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("class_size_result_version.class_size_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DiscountParameterVersion(Base):
    __tablename__ = "discount_parameter_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_discount_version_year"),
        CheckConstraint(
            "operation_type IN ('DRAFT_REPLACE', 'SCENARIO_APPLY')",
            name="ck_discount_version_operation",
        ),
        CheckConstraint("detail_record_count > 0", name="ck_discount_version_detail_count"),
        CheckConstraint("target_record_count > 0", name="ck_discount_version_target_count"),
        CheckConstraint("char_length(batch_content_hash) = 64", name="ck_discount_version_batch_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_discount_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_discount_version_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_discount_version_revision"),
    )

    discount_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    parent_discount_parameter_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discount_parameter_version.discount_parameter_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    detail_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DetailDiscountParameter(Base):
    __tablename__ = "detail_discount_parameter"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_detail_discount_year"),
        CheckConstraint("fiscal_season > 0", name="ck_detail_discount_season"),
        CheckConstraint(
            "discount_rate_ppm BETWEEN 0 AND 1000000 AND discount_rate_ppm % 1000 = 0",
            name="ck_detail_discount_rate",
        ),
        CheckConstraint("char_length(detail_discount_stable_key) = 64", name="ck_detail_discount_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_detail_discount_hash"),
        UniqueConstraint(
            "discount_parameter_version_id",
            "fiscal_season",
            "track",
            "product",
            "grade",
            name="uq_detail_discount_parameter_key",
        ),
    )

    detail_discount_parameter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    discount_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discount_parameter_version.discount_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    detail_discount_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    discount_rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CompositeDiscountTarget(Base):
    __tablename__ = "composite_discount_target"
    __table_args__ = (
        CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_composite_target_scope"),
        CheckConstraint("period_type IN ('ANNUAL', 'SEASON')", name="ck_composite_target_period"),
        CheckConstraint(
            "(scope_level = 'DEPARTMENT' AND track IS NULL) OR (scope_level = 'TRACK' AND track IS NOT NULL)",
            name="ck_composite_target_track",
        ),
        CheckConstraint(
            "(period_type = 'ANNUAL' AND fiscal_season IS NULL) OR "
            "(period_type = 'SEASON' AND fiscal_season IS NOT NULL)",
            name="ck_composite_target_season",
        ),
        CheckConstraint("target_rate_ppm BETWEEN 0 AND 1000000", name="ck_composite_target_rate"),
        CheckConstraint("char_length(composite_target_stable_key) = 64", name="ck_composite_target_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_composite_target_hash"),
        UniqueConstraint(
            "discount_parameter_version_id",
            "composite_target_stable_key",
            name="uq_composite_discount_target_key",
        ),
    )

    composite_discount_target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    discount_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discount_parameter_version.discount_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    composite_target_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(20), nullable=False)
    track: Mapped[str | None] = mapped_column(String(100), nullable=True)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    fiscal_season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DiscountParameterConfirmation(Base):
    __tablename__ = "discount_parameter_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_discount_confirm_hash"),
        UniqueConstraint(
            "discount_parameter_version_id",
            "input_fingerprint",
            name="uq_discount_parameter_confirmation",
        ),
    )

    discount_parameter_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    discount_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discount_parameter_version.discount_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RevenueResultVersion(Base):
    __tablename__ = "revenue_result_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_revenue_result_version_year"),
        CheckConstraint("status = 'SUCCEEDED'", name="ck_revenue_result_version_status"),
        CheckConstraint("record_count > 0", name="ck_revenue_result_version_count"),
        CheckConstraint("aggregate_count > 0", name="ck_revenue_result_aggregate_count"),
        CheckConstraint("validation_count >= 0", name="ck_revenue_result_validation_count"),
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_revenue_result_input_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_result_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_revenue_result_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_revenue_result_version_revision"),
    )

    revenue_result_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    headcount_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("headcount_result_version.headcount_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    class_size_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("class_size_result_version.class_size_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    resource_finance_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "resource_finance_parameter_version.resource_finance_parameter_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    resource_finance_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_finance_confirmation.resource_finance_confirmation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    discount_parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discount_parameter_version.discount_parameter_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    discount_parameter_confirmation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "discount_parameter_confirmation.discount_parameter_confirmation_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    aggregate_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    validation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SeasonRevenueResult(Base):
    __tablename__ = "season_revenue_result"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_season_revenue_year"),
        CheckConstraint("fiscal_season > 0", name="ck_season_revenue_season"),
        CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_season_revenue_entry"),
        CheckConstraint("calculation_status IN ('CALCULATED', 'BLOCKED')", name="ck_season_revenue_status"),
        CheckConstraint("detail_discount_rate_ppm BETWEEN 0 AND 1000000", name="ck_season_revenue_discount"),
        CheckConstraint(
            "current_season_recognition_rate_ppm BETWEEN 0 AND 1000000",
            name="ck_season_revenue_current_rate",
        ),
        CheckConstraint(
            "cross_season_recognition_rate_ppm = 1000000 - current_season_recognition_rate_ppm",
            name="ck_season_revenue_cross_rate",
        ),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_season_revenue_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_season_revenue_hash"),
        UniqueConstraint(
            "revenue_result_version_id", "season_headcount_result_id", name="uq_season_revenue_result"
        ),
    )

    season_revenue_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    revenue_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revenue_result_version.revenue_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    class_size_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("class_size_result.class_size_result_id", ondelete="RESTRICT"), nullable=False
    )
    season_headcount_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("season_headcount_result.season_headcount_result_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revenue_parameter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revenue_parameter.revenue_parameter_id", ondelete="RESTRICT"), nullable=False
    )
    detail_discount_parameter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detail_discount_parameter.detail_discount_parameter_id", ondelete="RESTRICT"),
        nullable=False,
    )
    opening_carryover_parameter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opening_carryover_parameter.opening_carryover_parameter_id", ondelete="RESTRICT"),
        nullable=True,
    )
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_name: Mapped[str] = mapped_column(String(30), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_attribute: Mapped[str] = mapped_column(String(20), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    integer_total_students: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    list_price_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    detail_discount_rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    discounted_tuition_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    list_price_revenue_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    business_quarter_revenue_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    budget_tax_conversion_factor: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    net_of_tax_base_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    current_season_recognition_rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    cross_season_recognition_rate_ppm: Mapped[int] = mapped_column(Integer, nullable=False)
    current_recognized_revenue_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    incoming_carryover_revenue_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    cross_season_revenue_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    quarter_recognized_revenue_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    next_fiscal_year_opening_yuan: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    calculation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RevenueAggregateResult(Base):
    __tablename__ = "revenue_aggregate_result"
    __table_args__ = (
        CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_revenue_aggregate_scope"),
        CheckConstraint("period_type IN ('ANNUAL', 'SEASON')", name="ck_revenue_aggregate_period"),
        CheckConstraint(
            "target_status IN ('PASS', 'OUT_OF_TOLERANCE', 'N/A', 'TARGET_MISSING')",
            name="ck_revenue_aggregate_target_status",
        ),
        CheckConstraint("char_length(aggregate_stable_key) = 64", name="ck_revenue_aggregate_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_aggregate_hash"),
        UniqueConstraint(
            "revenue_result_version_id", "aggregate_stable_key", name="uq_revenue_aggregate_result"
        ),
    )

    revenue_aggregate_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    revenue_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revenue_result_version.revenue_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    composite_discount_target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("composite_discount_target.composite_discount_target_id", ondelete="RESTRICT"),
        nullable=True,
    )
    aggregate_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(20), nullable=False)
    track: Mapped[str | None] = mapped_column(String(100), nullable=True)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    fiscal_season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    list_price_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    discounted_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    business_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    recognized_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    composite_discount_rate_ppm: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    target_rate_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variance_ppm: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    target_status: Mapped[str] = mapped_column(String(30), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RevenueValidationResult(Base):
    __tablename__ = "revenue_validation_result"
    __table_args__ = (
        CheckConstraint("severity = 'BLOCKING'", name="ck_revenue_validation_severity"),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_revenue_validation_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_revenue_validation_hash"),
        UniqueConstraint(
            "revenue_result_version_id",
            "validation_stable_key",
            name="uq_revenue_validation_result",
        ),
    )

    revenue_validation_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    revenue_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revenue_result_version.revenue_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    validation_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TargetClosureResultVersion(Base):
    __tablename__ = "target_closure_result_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_target_closure_version_year"),
        CheckConstraint("status = 'SUCCEEDED'", name="ck_target_closure_version_status"),
        CheckConstraint(
            "closure_status IN ('WITHIN_TOLERANCE', 'GAP_PENDING_REBALANCE', "
            "'OVER_TARGET_PENDING_REBALANCE', 'MIXED_PENDING_REBALANCE')",
            name="ck_target_closure_status",
        ),
        CheckConstraint("record_count >= 2", name="ck_target_closure_version_count"),
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_target_closure_input_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_target_closure_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_target_closure_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_target_closure_version_revision"),
    )

    target_closure_result_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    target_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_set_version.target_set_version_id", ondelete="RESTRICT"), nullable=False
    )
    revenue_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revenue_result_version.revenue_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    closure_status: Mapped[str] = mapped_column(String(40), nullable=False)
    upstream_revenue_publishable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TargetClosureResult(Base):
    __tablename__ = "target_closure_result"
    __table_args__ = (
        CheckConstraint("scope_level IN ('DEPARTMENT', 'TRACK')", name="ck_target_closure_scope"),
        CheckConstraint(
            "(scope_level = 'DEPARTMENT' AND track IS NULL) OR (scope_level = 'TRACK' AND track IS NOT NULL)",
            name="ck_target_closure_track",
        ),
        CheckConstraint("target_revenue_yuan >= 0", name="ck_target_closure_target"),
        CheckConstraint("calculated_revenue_yuan >= 0", name="ck_target_closure_calculated"),
        CheckConstraint("tolerance_yuan = 10000.000000", name="ck_target_closure_tolerance"),
        CheckConstraint(
            "check_status IN ('GAP', 'WITHIN_TOLERANCE', 'OVER_TARGET')",
            name="ck_target_closure_check_status",
        ),
        CheckConstraint("char_length(target_stable_key) = 64", name="ck_target_closure_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_target_closure_hash"),
        UniqueConstraint(
            "target_closure_result_version_id",
            "target_stable_key",
            name="uq_target_closure_result_key",
        ),
    )

    target_closure_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    target_closure_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "target_closure_result_version.target_closure_result_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    budget_target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_target.budget_target_id", ondelete="RESTRICT"), nullable=False
    )
    target_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(20), nullable=False)
    track: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    calculated_revenue_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    variance_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    tolerance_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    check_status: Mapped[str] = mapped_column(String(30), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class OptimizationRun(Base):
    __tablename__ = "optimization_run"
    __table_args__ = (
        CheckConstraint(
            "terminal_stage IN ('ENTRY_ONLY', 'ENTRY_AND_RATES', 'ENTRY_RATES_AND_DISCOUNT')",
            name="ck_optimization_run_stage",
        ),
        CheckConstraint(
            "status IN ('OPTIMAL', 'INFEASIBLE', 'INCOMPLETE', 'FAILED')",
            name="ck_optimization_run_status",
        ),
        CheckConstraint("maximum_enumerated_states > 0", name="ck_optimization_run_max_states"),
        CheckConstraint(
            "solver_adapter IN ('EXACT_ENUMERATION', 'CP_SAT_ENTRY_ONLY', 'CP_SAT_ENTRY_AND_RATES')",
            name="ck_optimization_run_solver_adapter",
        ),
        CheckConstraint("maximum_solver_seconds > 0", name="ck_optimization_run_max_seconds"),
        CheckConstraint(
            "maximum_candidate_validations > 0",
            name="ck_optimization_run_max_validations",
        ),
        CheckConstraint(
            "maximum_group_table_rows > 0",
            name="ck_optimization_run_max_group_rows",
        ),
        CheckConstraint("evaluated_state_count >= 0", name="ck_optimization_run_evaluated"),
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_optimization_run_input_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_optimization_run_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_optimization_run_request_hash"),
        Index("ix_optimization_run_revision_created", "budget_revision_id", "created_at"),
    )

    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    target_closure_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_closure_result_version.target_closure_result_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    optimization_model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    terminal_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    discount_unlock_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    discount_unlock_confirmation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "discount_unlock_confirmation.discount_unlock_confirmation_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    solver_adapter: Mapped[str] = mapped_column(String(40), nullable=False)
    maximum_enumerated_states: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maximum_solver_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maximum_candidate_validations: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maximum_group_table_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evaluated_state_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RebalancingScenario(Base):
    __tablename__ = "rebalancing_scenario"
    __table_args__ = (
        CheckConstraint("char_length(result_hash) = 64", name="ck_rebalancing_scenario_result_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_rebalancing_scenario_hash"),
        UniqueConstraint("optimization_run_id", "result_hash", name="uq_rebalancing_scenario_result"),
    )

    rebalancing_scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("optimization_run.optimization_run_id", ondelete="RESTRICT"), nullable=False
    )
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    target_variances: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    retention_contribution_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    new_share_contribution_yuan: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    headcount_signature: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    objective_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ScenarioChange(Base):
    __tablename__ = "scenario_change"
    __table_args__ = (
        CheckConstraint("object_type IN ('ENTRY', 'RATE', 'DISCOUNT')", name="ck_scenario_change_type"),
        CheckConstraint("char_length(object_stable_key) > 0", name="ck_scenario_change_key"),
        CheckConstraint("suggested_value - original_value = adjustment", name="ck_scenario_change_delta"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_scenario_change_hash"),
        UniqueConstraint(
            "rebalancing_scenario_id", "object_type", "object_stable_key", name="uq_scenario_change"
        ),
    )

    scenario_change_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    rebalancing_scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rebalancing_scenario.rebalancing_scenario_id", ondelete="RESTRICT"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)
    object_stable_key: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    suggested_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    adjustment: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ScenarioApplication(Base):
    __tablename__ = "scenario_application"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_scenario_application_input"),
        CheckConstraint("char_length(reason) > 0", name="ck_scenario_application_reason"),
        CheckConstraint("change_count > 0", name="ck_scenario_application_change_count"),
        CheckConstraint(
            "entry_change_count >= 0 AND rate_change_count >= 0 AND discount_change_count >= 0",
            name="ck_scenario_application_type_counts",
        ),
        CheckConstraint(
            "change_count = entry_change_count + rate_change_count + discount_change_count",
            name="ck_scenario_application_count_total",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_scenario_application_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_scenario_application_request_hash"),
        UniqueConstraint("rebalancing_scenario_id", name="uq_scenario_application_scenario"),
        UniqueConstraint("applied_revision_id", name="uq_scenario_application_revision"),
        Index("ix_scenario_application_source", "source_revision_id", "applied_at"),
    )

    scenario_application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    rebalancing_scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rebalancing_scenario.rebalancing_scenario_id", ondelete="RESTRICT"), nullable=False
    )
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("optimization_run.optimization_run_id", ondelete="RESTRICT"), nullable=False
    )
    source_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    applied_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    change_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_change_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rate_change_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_change_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    campus_entry_set_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campus_entry_set_version.campus_entry_set_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    entry_detail_set_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entry_detail_set_version.entry_detail_set_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    planned_new_student_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_new_student_version.planned_new_student_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    rate_parameter_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rate_parameter_version.rate_parameter_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    discount_parameter_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discount_parameter_version.discount_parameter_version_id", ondelete="RESTRICT"),
        nullable=True,
    )
    inherited_discount_confirmation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "discount_parameter_confirmation.discount_parameter_confirmation_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    campus_confirmation_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    invalidated_components: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    applied_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ScenarioApplicationRevert(Base):
    __tablename__ = "scenario_application_revert"
    __table_args__ = (
        CheckConstraint("char_length(reason) > 0", name="ck_scenario_application_revert_reason"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_scenario_application_revert_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_scenario_application_revert_request_hash"),
        UniqueConstraint("scenario_application_id", name="uq_scenario_application_revert_application"),
        UniqueConstraint("reverted_revision_id", name="uq_scenario_application_revert_revision"),
        Index("ix_scenario_application_revert_time", "reverted_at", "scenario_application_revert_id"),
    )

    scenario_application_revert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    scenario_application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenario_application.scenario_application_id", ondelete="RESTRICT"), nullable=False
    )
    reverted_from_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    restored_source_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    reverted_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    restored_component_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    invalidated_components: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    calculation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("async_job.job_id", ondelete="RESTRICT"), nullable=False
    )
    reverted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reverted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class InfeasibilityProof(Base):
    __tablename__ = "infeasibility_proof"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('ENTRY_ONLY', 'ENTRY_AND_RATES', 'ENTRY_RATES_AND_DISCOUNT')",
            name="ck_infeasibility_proof_stage",
        ),
        CheckConstraint("char_length(model_hash) = 64", name="ck_infeasibility_proof_model_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_infeasibility_proof_hash"),
        UniqueConstraint("optimization_run_id", "stage", "profile", name="uq_infeasibility_proof"),
    )

    infeasibility_proof_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("optimization_run.optimization_run_id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    profile: Mapped[str] = mapped_column(String(40), nullable=False)
    proof_code: Mapped[str] = mapped_column(String(100), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hard_constraint_groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    proof_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DiscountUnlockRequest(Base):
    __tablename__ = "discount_unlock_request"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_discount_unlock_request_input"),
        CheckConstraint("char_length(reason) > 0", name="ck_discount_unlock_request_reason"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_discount_unlock_request_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_discount_unlock_request_request_hash"),
        UniqueConstraint(
            "budget_revision_id",
            "input_fingerprint",
            name="uq_discount_unlock_request_revision_input",
        ),
    )

    discount_unlock_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("optimization_run.optimization_run_id", ondelete="RESTRICT"), nullable=False
    )
    infeasibility_proof_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("infeasibility_proof.infeasibility_proof_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DiscountUnlockConfirmation(Base):
    __tablename__ = "discount_unlock_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_discount_unlock_confirmation_input"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_discount_unlock_confirmation_hash"),
        CheckConstraint(
            "char_length(request_hash) = 64", name="ck_discount_unlock_confirmation_request_hash"
        ),
        UniqueConstraint("discount_unlock_request_id", name="uq_discount_unlock_confirmation_request"),
    )

    discount_unlock_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    discount_unlock_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discount_unlock_request.discount_unlock_request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ParameterVersion(Base):
    __tablename__ = "parameter_version"
    __table_args__ = (
        CheckConstraint("char_length(content_hash) = 64", name="ck_parameter_version_content_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_parameter_version_request_hash"),
        Index(
            "ix_parameter_version_snapshot_created",
            "source_snapshot_id",
            "created_at",
            "parameter_version_id",
        ),
    )

    parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parameter_version.parameter_version_id", ondelete="RESTRICT"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ParameterVersionComponent(Base):
    __tablename__ = "parameter_version_component"
    __table_args__ = (
        CheckConstraint(
            "component_type IN ('RATE_PARAMETERS', 'PLANNED_NEW_STUDENTS', 'RESOURCE_FINANCE', 'DISCOUNTS')",
            name="ck_parameter_version_component_type",
        ),
        CheckConstraint("record_count >= 0", name="ck_parameter_version_component_record_count"),
        CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_parameter_version_component_content_hash",
        ),
        UniqueConstraint(
            "parameter_version_id", "component_type", name="uq_parameter_version_component_type"
        ),
    )

    parameter_version_component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    parameter_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parameter_version.parameter_version_id", ondelete="RESTRICT"), nullable=False
    )
    component_type: Mapped[str] = mapped_column(String(80), nullable=False)
    component_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class BudgetCombinationSetVersion(Base):
    __tablename__ = "budget_combination_set_version"
    __table_args__ = (
        CheckConstraint("target_fiscal_year > 0", name="ck_combination_target_fiscal_year"),
        CheckConstraint("record_count > 0", name="ck_combination_set_record_count"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_combination_set_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_combination_set_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_combination_set_revision"),
    )

    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    rate_reference_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_reference_version.rate_reference_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_scope_rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BudgetCombination(Base):
    __tablename__ = "budget_combination"
    __table_args__ = (
        CheckConstraint("entry_attribute IN ('ENTRY', 'NON_ENTRY')", name="ck_combination_entry"),
        CheckConstraint("origin_type IN ('HISTORICAL', 'MANUAL')", name="ck_combination_origin"),
        CheckConstraint(
            "(origin_type = 'HISTORICAL' AND historical_enrollment > 0) OR "
            "(origin_type = 'MANUAL' AND historical_enrollment = 0)",
            name="ck_combination_history_enrollment",
        ),
        CheckConstraint("char_length(campus_stable_key) = 64", name="ck_combination_campus_key"),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_combination_stable_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_combination_hash"),
        UniqueConstraint(
            "combination_set_version_id",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            name="uq_budget_combination_public_key",
        ),
    )

    combination_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    campus_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_attribute: Mapped[str] = mapped_column(String(20), nullable=False)
    origin_type: Mapped[str] = mapped_column(String(30), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    historical_enrollment: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    change_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    class_size_parameter_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CombinationSetConfirmation(Base):
    __tablename__ = "combination_set_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_combination_confirmation_hash"),
        UniqueConstraint(
            "combination_set_version_id", "input_fingerprint", name="uq_combination_confirmation"
        ),
    )

    combination_set_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntryTotalVersion(Base):
    __tablename__ = "entry_total_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_entry_total_fiscal_year"),
        CheckConstraint("fiscal_year_entry_total >= 0", name="ck_entry_total_value"),
        CheckConstraint("locked_value = fiscal_year_entry_total", name="ck_entry_total_locked_value"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_entry_total_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_entry_total_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_entry_total_revision"),
    )

    entry_total_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_year_entry_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locked_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    locked_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CampusEntrySuggestionVersion(Base):
    __tablename__ = "campus_entry_suggestion_version"
    __table_args__ = (
        CheckConstraint("fiscal_year_entry_total >= 0", name="ck_campus_suggestion_entry_total"),
        CheckConstraint("manual_reserved_total >= 0", name="ck_campus_suggestion_reserved"),
        CheckConstraint("historical_entry_total >= 0", name="ck_campus_suggestion_history"),
        CheckConstraint("suggestion_total = fiscal_year_entry_total", name="ck_campus_suggestion_total"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_campus_suggestion_version_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_campus_suggestion_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_campus_suggestion_revision"),
    )

    campus_entry_suggestion_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_total_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_total_version.entry_total_version_id", ondelete="RESTRICT"), nullable=False
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    fiscal_year_entry_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manual_reserved_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    historical_entry_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    suggestion_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CampusEntrySuggestion(Base):
    __tablename__ = "campus_entry_suggestion"
    __table_args__ = (
        CheckConstraint("char_length(campus_stable_key) = 64", name="ck_campus_entry_key"),
        CheckConstraint("historical_business_new_students >= 0", name="ck_campus_entry_history"),
        CheckConstraint("manual_reserved_suggestion >= 0", name="ck_campus_entry_manual"),
        CheckConstraint("suggested_entry_value >= 0", name="ck_campus_entry_suggested"),
        CheckConstraint(
            "history_availability IN ('HAS_HISTORY', 'NO_HISTORY')",
            name="ck_campus_entry_history_availability",
        ),
        CheckConstraint(
            "suggestion_source_type IN ('HISTORICAL_FORMULA', 'BUSINESS_MANUAL')",
            name="ck_campus_entry_source_type",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_campus_entry_hash"),
        UniqueConstraint(
            "campus_entry_suggestion_version_id", "campus_stable_key", name="uq_campus_entry_suggestion"
        ),
    )

    campus_entry_suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    campus_entry_suggestion_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "campus_entry_suggestion_version.campus_entry_suggestion_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    campus_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    historical_business_new_students: Mapped[int] = mapped_column(BigInteger, nullable=False)
    history_availability: Mapped[str] = mapped_column(String(30), nullable=False)
    classroom_resource: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sales_resource: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manual_reserved_suggestion: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manual_suggestion_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    suggested_entry_theoretical: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    suggested_entry_floor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    suggested_entry_fraction: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    suggestion_tail_assigned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    suggested_entry_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    suggestion_source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CampusEntrySetVersion(Base):
    __tablename__ = "campus_entry_set_version"
    __table_args__ = (
        CheckConstraint("fiscal_year_entry_total >= 0", name="ck_campus_entry_set_total"),
        CheckConstraint("determined_total >= 0", name="ck_campus_entry_set_determined"),
        CheckConstraint("incomplete_count >= 0", name="ck_campus_entry_set_incomplete"),
        CheckConstraint("record_count > 0", name="ck_campus_entry_set_record_count"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_campus_entry_set_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_campus_entry_set_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_campus_entry_set_revision"),
    )

    campus_entry_set_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    basis_combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_total_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_total_version.entry_total_version_id", ondelete="RESTRICT"), nullable=False
    )
    suggestion_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "campus_entry_suggestion_version.campus_entry_suggestion_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    fiscal_year_entry_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    determined_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_difference: Mapped[int] = mapped_column(BigInteger, nullable=False)
    incomplete_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CampusEntryAllocation(Base):
    __tablename__ = "campus_entry_allocation"
    __table_args__ = (
        CheckConstraint(
            "determined_entry_value IS NULL OR determined_entry_value >= 0",
            name="ck_campus_allocation_determined",
        ),
        CheckConstraint("char_length(campus_stable_key) = 64", name="ck_campus_allocation_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_campus_allocation_hash"),
        CheckConstraint(
            "adjustment_reason_category IS NULL OR adjustment_reason_category IN "
            "('PERFORMANCE_JUDGMENT', 'CLASSROOM_RESOURCE', 'SALES_RESOURCE', "
            "'NEW_CAMPUS_OR_PRODUCT', 'STRATEGIC_ADJUSTMENT', 'OTHER')",
            name="ck_campus_allocation_reason_category",
        ),
        UniqueConstraint(
            "campus_entry_set_version_id", "campus_stable_key", name="uq_campus_entry_allocation"
        ),
    )

    campus_entry_allocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    campus_entry_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campus_entry_set_version.campus_entry_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    campus_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    suggested_entry_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    determined_entry_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    adjustment_difference: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    adjustment_reason_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    adjustment_reason_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    performance_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    classroom_resource: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sales_resource: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CampusEntryConfirmation(Base):
    __tablename__ = "campus_entry_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_campus_entry_confirmation_hash"),
        UniqueConstraint(
            "campus_entry_set_version_id", "input_fingerprint", name="uq_campus_entry_confirmation"
        ),
    )

    campus_entry_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    campus_entry_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campus_entry_set_version.campus_entry_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntryScopeVersion(Base):
    __tablename__ = "entry_scope_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_entry_scope_fiscal_year"),
        CheckConstraint("record_count >= 0", name="ck_entry_scope_record_count"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_entry_scope_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_entry_scope_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_entry_scope_revision"),
    )

    entry_scope_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    default_rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntryScopeException(Base):
    __tablename__ = "entry_scope_exception"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_entry_scope_exception_fiscal_year"),
        CheckConstraint("exception_type IN ('INCLUDE', 'EXCLUDE')", name="ck_entry_scope_exception_type"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_entry_scope_exception_hash"),
        UniqueConstraint(
            "entry_scope_version_id",
            "fiscal_year",
            "track",
            "product",
            "grade",
            "subject",
            "campus",
            "exception_type",
            name="uq_entry_scope_exception_key",
        ),
    )

    entry_scope_exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    entry_scope_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_scope_version.entry_scope_version_id", ondelete="RESTRICT"), nullable=False
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    exception_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class EntryScopeEvaluation(Base):
    __tablename__ = "entry_scope_evaluation"
    __table_args__ = (
        CheckConstraint(
            "effective_entry_attribute IN ('ENTRY', 'NON_ENTRY')",
            name="ck_entry_scope_evaluation_attribute",
        ),
        CheckConstraint(
            "effective_source IN ('MANUAL_EXCLUDE', 'MANUAL_INCLUDE', 'DEFAULT_RULE', 'NOT_ENTRY')",
            name="ck_entry_scope_evaluation_source",
        ),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_entry_scope_evaluation_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_entry_scope_evaluation_hash"),
        UniqueConstraint(
            "entry_scope_version_id",
            "combination_stable_key",
            name="uq_entry_scope_evaluation",
        ),
    )

    entry_scope_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    entry_scope_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_scope_version.entry_scope_version_id", ondelete="RESTRICT"), nullable=False
    )
    combination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination.combination_id", ondelete="RESTRICT"), nullable=False
    )
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    default_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    manual_include_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    manual_exclude_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_entry_attribute: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_source: Mapped[str] = mapped_column(String(30), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class EntryScopeConfirmation(Base):
    __tablename__ = "entry_scope_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_entry_scope_confirmation_hash"),
        UniqueConstraint("entry_scope_version_id", "input_fingerprint", name="uq_entry_scope_confirmation"),
    )

    entry_scope_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    entry_scope_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_scope_version.entry_scope_version_id", ondelete="RESTRICT"), nullable=False
    )
    confirmed_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntryDetailSetVersion(Base):
    __tablename__ = "entry_detail_set_version"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_entry_detail_fiscal_year"),
        CheckConstraint("source_fiscal_season > 0", name="ck_entry_detail_source_season"),
        CheckConstraint("campus_entry_total >= 0", name="ck_entry_detail_campus_total"),
        CheckConstraint("allocated_entry_total = campus_entry_total", name="ck_entry_detail_allocated_total"),
        CheckConstraint("record_count > 0", name="ck_entry_detail_record_count"),
        CheckConstraint("zero_initial_count >= 0", name="ck_entry_detail_zero_count"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_entry_detail_hash"),
        CheckConstraint("char_length(request_hash) = 64", name="ck_entry_detail_request_hash"),
        UniqueConstraint("budget_revision_id", name="uq_entry_detail_revision"),
    )

    entry_detail_set_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    combination_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination_set_version.combination_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_scope_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_scope_version.entry_scope_version_id", ondelete="RESTRICT"), nullable=False
    )
    campus_entry_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campus_entry_set_version.campus_entry_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshot.source_snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    source_fiscal_season: Mapped[int] = mapped_column(Integer, nullable=False)
    campus_entry_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allocated_entry_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    zero_initial_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntryDetailAllocation(Base):
    __tablename__ = "entry_detail_allocation"
    __table_args__ = (
        CheckConstraint("effective_entry_attribute = 'ENTRY'", name="ck_entry_detail_effective_entry"),
        CheckConstraint(
            "allocation_source_type IN ('MANUAL_RESERVATION', 'HISTORICAL_SHARE')",
            name="ck_entry_detail_source_type",
        ),
        CheckConstraint("historical_business_new_students >= 0", name="ck_entry_detail_history"),
        CheckConstraint("entry_initial_value >= 0", name="ck_entry_detail_initial"),
        CheckConstraint(
            "manual_reservation_ratio_ppm IS NULL OR manual_reservation_ratio_ppm BETWEEN 0 AND 1000000",
            name="ck_entry_detail_manual_ratio",
        ),
        CheckConstraint(
            "manual_reservation_ratio_ppm IS NULL OR manual_reservation_reason IS NOT NULL",
            name="ck_entry_detail_manual_reason",
        ),
        CheckConstraint(
            "soft_lower_bound IS NULL OR soft_lower_bound >= 0", name="ck_entry_detail_soft_lower"
        ),
        CheckConstraint(
            "soft_upper_bound IS NULL OR soft_upper_bound >= soft_lower_bound",
            name="ck_entry_detail_soft_upper",
        ),
        CheckConstraint(
            "(entry_initial_value = 0 AND candidate_eligible = FALSE "
            "AND soft_lower_bound IS NULL AND soft_upper_bound IS NULL) OR "
            "(entry_initial_value > 0 AND candidate_eligible = TRUE "
            "AND soft_lower_bound IS NOT NULL AND soft_upper_bound IS NOT NULL)",
            name="ck_entry_detail_candidate_eligibility",
        ),
        CheckConstraint("char_length(combination_stable_key) = 64", name="ck_entry_detail_combination_key"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_entry_detail_row_hash"),
        UniqueConstraint(
            "entry_detail_set_version_id", "combination_stable_key", name="uq_entry_detail_allocation"
        ),
    )

    entry_detail_allocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    entry_detail_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entry_detail_set_version.entry_detail_set_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    combination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_combination.combination_id", ondelete="RESTRICT"), nullable=False
    )
    combination_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    track: Mapped[str] = mapped_column(String(100), nullable=False)
    product: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    campus: Mapped[str] = mapped_column(String(200), nullable=False)
    effective_entry_attribute: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    origin_type: Mapped[str] = mapped_column(String(30), nullable=False)
    historical_business_new_students: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manual_reservation_ratio_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_reservation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    allocation_source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    theoretical_value: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    floor_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fraction_value: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    tail_assigned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entry_initial_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    soft_lower_bound: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    soft_upper_bound: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    candidate_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    candidate_blocking_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RevisionLifecycleEvent(Base):
    __tablename__ = "revision_lifecycle_event"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('DRAFT_CREATED', 'FORMAL_PLAN_CONFIRMED', 'PUBLISHED', 'CURRENT_POINTER_MOVED')",
            name="ck_revision_lifecycle_event_type",
        ),
        UniqueConstraint("budget_revision_id", "event_type", name="uq_revision_lifecycle_event_type"),
    )

    revision_lifecycle_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FormalPlanConfirmation(Base):
    __tablename__ = "formal_plan_confirmation"
    __table_args__ = (
        CheckConstraint("char_length(input_fingerprint) = 64", name="ck_formal_plan_confirmation_input"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_formal_plan_confirmation_hash"),
        CheckConstraint("char_length(reason) > 0", name="ck_formal_plan_confirmation_reason"),
        UniqueConstraint("source_revision_id", name="uq_formal_plan_confirmation_source_revision"),
        UniqueConstraint("confirmed_revision_id", name="uq_formal_plan_confirmation_revision"),
    )

    formal_plan_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    source_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    confirmed_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CommandReceipt(Base):
    __tablename__ = "command_receipt"
    __table_args__ = (
        CheckConstraint("char_length(request_hash) = 64", name="ck_command_receipt_request_hash"),
        UniqueConstraint("actor_id", "command_type", "idempotency_key", name="uq_command_receipt_key"),
    )

    command_receipt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    command_type: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RoleMembership(Base):
    __tablename__ = "role_membership"
    __table_args__ = (
        CheckConstraint(
            "role IN ('DATA_MAINTAINER', 'DEPARTMENT_BUDGET_OWNER', 'PERMISSION_ADMIN')",
            name="ck_role_membership_role",
        ),
        UniqueConstraint("user_id", "department_id", "role", name="uq_role_membership"),
    )

    role_membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    department_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(60), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AsyncJob(Base):
    __tablename__ = "async_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'INCOMPLETE', 'FAILED', 'CANCELLED')",
            name="ck_async_job_status",
        ),
        CheckConstraint("attempt_no >= 0", name="ck_async_job_attempt_no"),
        UniqueConstraint("dedupe_key", name="uq_async_job_dedupe_key"),
        Index("ix_async_job_claim", "status", "available_at", "job_type"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_safe: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditArchiveArtifact(Base):
    __tablename__ = "audit_archive_artifact"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_audit_archive_size"),
        CheckConstraint("char_length(manifest_hash) = 64", name="ck_audit_archive_manifest_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_audit_archive_content_hash"),
        UniqueConstraint("job_id", name="uq_audit_archive_job"),
        Index(
            "ix_audit_archive_revision_created",
            "budget_revision_id",
            "created_at",
            "audit_archive_id",
        ),
    )

    audit_archive_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("async_job.job_id", ondelete="RESTRICT"), nullable=False
    )
    annual_budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("annual_budget.annual_budget_id", ondelete="RESTRICT"), nullable=False
    )
    budget_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=False
    )
    audit_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_budget_time", "annual_budget_id", "occurred_at"),)

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    annual_budget_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("annual_budget.annual_budget_id", ondelete="RESTRICT"), nullable=True
    )
    budget_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("budget_revision.budget_revision_id", ondelete="RESTRICT"), nullable=True
    )
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    acting_role: Mapped[str] = mapped_column(String(60), nullable=False)
    role_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_key: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HistoricalBudgetArchive(Base):
    """Immutable imported evidence, separate from system budget revisions."""

    __tablename__ = "historical_budget_archive"
    __table_args__ = (
        CheckConstraint("fiscal_year > 0", name="ck_historical_budget_archive_fiscal_year"),
        CheckConstraint("char_length(baseline_hash) = 64", name="ck_historical_budget_archive_baseline_hash"),
        CheckConstraint(
            "char_length(source_file_hash) = 64", name="ck_historical_budget_archive_source_file_hash"
        ),
        CheckConstraint("char_length(business_hash) = 64", name="ck_historical_budget_archive_business_hash"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_historical_budget_archive_content_hash"),
        CheckConstraint(
            "char_length(source_manifest_hash) = 64", name="ck_historical_budget_archive_source_manifest_hash"
        ),
        UniqueConstraint(
            "department_id", "fiscal_year", "source_file_hash", name="uq_historical_budget_archive_source"
        ),
        Index("ix_historical_budget_archive_scope_created", "department_id", "fiscal_year", "created_at"),
    )

    archive_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    department_id: Mapped[str] = mapped_column(String(100), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    business_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_workbook: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    original_extraction: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    accepted_extraction: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    acceptance_record: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    import_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForecastLedgerEvent(Base):
    """Append-only forecast evidence; canonical bytes preserve exact numeric strings."""

    __tablename__ = "forecast_ledger_event"
    __table_args__ = (
        CheckConstraint("event_no > 0", name="ck_forecast_ledger_event_number"),
        CheckConstraint("kind IN ('VERSION', 'CONFIRMATION')", name="ck_forecast_ledger_event_kind"),
        CheckConstraint("origin IN ('RUNTIME', 'FILE_IMPORT')", name="ck_forecast_ledger_event_origin"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_forecast_ledger_event_hash"),
        CheckConstraint(
            "previous_event_hash IS NULL OR char_length(previous_event_hash) = 64",
            name="ck_forecast_ledger_event_previous_hash",
        ),
        UniqueConstraint("department_id", "event_no", name="uq_forecast_ledger_event_number"),
        UniqueConstraint("department_id", "request_id", name="uq_forecast_ledger_event_request"),
        Index("ix_forecast_ledger_event_version", "department_id", "version_id", "event_no"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    department_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    version_id: Mapped[str] = mapped_column(String(100), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_event: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    persisted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    persistence_reason: Mapped[str] = mapped_column(Text, nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
