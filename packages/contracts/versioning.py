import re
import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from packages.optimization.engine import OptimizationSolverAdapter

HASH_PATTERN = r"^[0-9a-f]{64}$"


def _default_evidence() -> dict[str, object]:
    return {"schema_version": "1.0"}


class SourceSnapshotStatus(StrEnum):
    DRAFT = "DRAFT"
    WAITING_FOR_SUMMARY = "WAITING_FOR_SUMMARY"
    RECONCILED = "RECONCILED"
    FROZEN = "FROZEN"


class SourceSnapshotBuildRequest(BaseModel):
    fiscal_year: int = Field(gt=0)


class SourceSnapshotBuildResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    links: dict[str, str]


class JobResponse(BaseModel):
    job_id: uuid.UUID
    job_type: str
    status: str
    result: dict[str, object] | None
    last_error_code: str | None
    last_error_safe: str | None
    attempt_no: int
    created_at: datetime
    finished_at: datetime | None


class AuditArchiveExportRequest(BaseModel):
    reason: str = Field(default="版本审计归档", min_length=1, max_length=500)
    archive_schema_version: str = Field(
        default="phase1-audit-archive-v1",
        pattern=r"^phase1-audit-archive-v1$",
    )

    @model_validator(mode="after")
    def require_non_blank_reason(self) -> "AuditArchiveExportRequest":
        if not self.reason.strip():
            raise ValueError("归档原因不得为空")
        return self


class AuditArchiveExportResponse(BaseModel):
    job_id: uuid.UUID
    budget_revision_id: uuid.UUID
    audit_cutoff_at: datetime
    archive_schema_version: str
    status: str
    links: dict[str, str]


class AuditArchiveArtifactResponse(BaseModel):
    audit_archive_id: uuid.UUID
    job_id: uuid.UUID
    annual_budget_id: uuid.UUID
    budget_revision_id: uuid.UUID
    audit_cutoff_at: datetime
    schema_version: str
    file_name: str
    media_type: str
    size_bytes: int
    manifest_hash: str
    content_hash: str
    created_by: str
    created_at: datetime
    links: dict[str, str]


class BudgetCalculationEnqueueRequest(BaseModel):
    calculation_pipeline_version: str = Field(
        default="phase1-full-calculation-v1",
        pattern=r"^phase1-full-calculation-v1$",
    )


class BudgetCalculationJobResponse(BaseModel):
    job_id: uuid.UUID
    source_revision_id: uuid.UUID
    calculation_pipeline_version: str
    status: str
    links: dict[str, str]


class FormalPlanConfirmRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    calculation_pipeline_version: str = Field(
        default="phase1-full-calculation-v1",
        pattern=r"^phase1-full-calculation-v1$",
    )

    @model_validator(mode="after")
    def require_non_blank_reason(self) -> "FormalPlanConfirmRequest":
        if not self.reason.strip():
            raise ValueError("正式方案确认原因不得为空")
        return self


class PublicationCheckResponse(BaseModel):
    code: str
    group: str
    passed: bool
    actual: dict[str, object]
    rule: str
    repair_route: str


class PlannedNewStudentConfirmationSummary(BaseModel):
    fiscal_season: int
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    planned_new_students: int
    reason: str
    locked: bool
    handling_status: str


class PublicationReadinessResponse(BaseModel):
    budget_revision_id: uuid.UUID
    input_fingerprint: str
    gate_rule_version: str
    status: str
    blocker_count: int
    checks: list[PublicationCheckResponse]
    planned_new_students: list[PlannedNewStudentConfirmationSummary]


class FormalPlanConfirmationResponse(BaseModel):
    formal_plan_confirmation_id: uuid.UUID
    source_revision_id: uuid.UUID
    confirmed_revision_id: uuid.UUID
    input_fingerprint: str
    reason: str
    summary: dict[str, object]
    confirmed_by: str
    confirmed_at: datetime
    content_hash: str
    revision: "RevisionCommandResponse"


class BudgetPublishRequest(BaseModel):
    publication_note: str | None = Field(default=None, max_length=500)


class PublishedBudgetBaselineResponse(BaseModel):
    annual_budget_id: uuid.UUID
    fiscal_year: int
    published_revision_id: uuid.UUID
    previous_published_revision_id: uuid.UUID | None
    formal_plan_confirmation_id: uuid.UUID
    publication_input_fingerprint: str
    published_by: str
    published_at: datetime
    content_hash: str
    links: dict[str, str]


class ParameterComponentType(StrEnum):
    RATE_PARAMETERS = "RATE_PARAMETERS"
    PLANNED_NEW_STUDENTS = "PLANNED_NEW_STUDENTS"
    RESOURCE_FINANCE = "RESOURCE_FINANCE"
    DISCOUNTS = "DISCOUNTS"


class SourceComponentInput(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_name: str = Field(min_length=1, max_length=200)
    partition_value: str = Field(min_length=1, max_length=200)
    task_batch_id: str = Field(min_length=1, max_length=200)
    extracted_at: datetime
    row_count: int = Field(ge=0)
    field_mapping_version: str = Field(min_length=1, max_length=100)
    content_hash: str = Field(pattern=HASH_PATTERN)


class SourceSnapshotCreate(BaseModel):
    cutoff_at: datetime
    components: list[SourceComponentInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_partitions(self) -> "SourceSnapshotCreate":
        keys = [(item.dataset_id, item.partition_value) for item in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("同一数据集分区在来源快照中只能出现一次")
        return self


class ReconciliationInput(BaseModel):
    metric_code: str = Field(min_length=1, max_length=100)
    left_source: str = Field(min_length=1, max_length=200)
    right_source: str = Field(min_length=1, max_length=200)
    left_numerator: Decimal
    left_denominator: Decimal
    right_numerator: Decimal
    right_denominator: Decimal
    evidence: dict[str, object] = Field(default_factory=_default_evidence)


class SourceSnapshotReconcile(BaseModel):
    checks: list[ReconciliationInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_metrics(self) -> "SourceSnapshotReconcile":
        keys = [item.metric_code for item in self.checks]
        if len(keys) != len(set(keys)):
            raise ValueError("同一指标在一次对账中只能出现一次")
        return self


class SourceComponentResponse(SourceComponentInput):
    source_snapshot_component_id: uuid.UUID


class ReconciliationResponse(ReconciliationInput):
    source_reconciliation_id: uuid.UUID
    passed: bool
    checked_at: datetime


class SourceSnapshotResponse(BaseModel):
    source_snapshot_id: uuid.UUID
    department_id: str
    cutoff_at: datetime
    status: SourceSnapshotStatus
    content_hash: str
    created_by: str
    created_at: datetime
    reconciled_at: datetime | None
    frozen_at: datetime | None
    components: list[SourceComponentResponse]
    reconciliations: list[ReconciliationResponse]


class RateReferenceSourceType(StrEnum):
    EXACT_HISTORY = "EXACT_HISTORY"
    CAMPUS_REMOVED_FALLBACK = "CAMPUS_REMOVED_FALLBACK"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class RateType(StrEnum):
    RENEWAL_RATE = "RENEWAL_RATE"
    NEW_STUDENT_SHARE = "NEW_STUDENT_SHARE"


class RateReferenceGenerateRequest(BaseModel):
    target_fiscal_year: int = Field(gt=0)
    minimum_sample_size: int = Field(default=30, gt=0, le=1_000_000)
    mature_source_fiscal_seasons: list[int] = Field(min_length=4, max_length=4)
    maturity_evidence_reference: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_four_unique_prior_year_seasons(self) -> "RateReferenceGenerateRequest":
        expected = {self.target_fiscal_year * 10 - 10 + quarter for quarter in range(1, 5)}
        actual = set(self.mature_source_fiscal_seasons)
        if actual != expected or len(actual) != len(self.mature_source_fiscal_seasons):
            raise ValueError(f"成熟季节必须完整且唯一地覆盖上一财年四季：{sorted(expected)}")
        return self


class RateReferenceVersionResponse(BaseModel):
    rate_reference_version_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    target_fiscal_year: int
    minimum_sample_size: int
    maturity_evidence: dict[str, object]
    reference_count: int
    exact_count: int
    fallback_count: int
    manual_required_count: int
    content_hash: str
    created_by: str
    created_at: datetime


class RateReferenceResponse(BaseModel):
    rate_reference_id: uuid.UUID
    rate_reference_version_id: uuid.UUID
    rate_type: RateType
    source_fiscal_season: int
    target_fiscal_season: int
    quarter_name: str
    track: str
    product: str
    grade: str
    campus: str
    renewal_relation_code: str | None
    exact_numerator: Decimal
    exact_denominator: Decimal
    reference_numerator: Decimal
    reference_denominator: Decimal
    exact_rate_ppm: int | None
    reference_rate_ppm: int | None
    minimum_sample_size: int
    source_type: RateReferenceSourceType
    fallback_reason: str | None
    system_value_ppm: int | None
    content_hash: str


class RateReferencePageResponse(BaseModel):
    items: list[RateReferenceResponse]
    next_cursor: uuid.UUID | None


class ParameterComponentInput(BaseModel):
    component_type: ParameterComponentType
    component_version_id: uuid.UUID
    content_hash: str = Field(pattern=HASH_PATTERN)
    schema_version: str = Field(min_length=1, max_length=50)
    record_count: int = Field(ge=0)


class ParameterVersionCreate(BaseModel):
    source_snapshot_id: uuid.UUID
    parent_version_id: uuid.UUID | None = None
    components: list[ParameterComponentInput] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def require_complete_component_set(self) -> "ParameterVersionCreate":
        actual = {item.component_type for item in self.components}
        expected = set(ParameterComponentType)
        if actual != expected:
            missing = sorted(item.value for item in expected - actual)
            duplicated = len(actual) != len(self.components)
            raise ValueError(f"参数版本必须包含四类组件；缺失={missing}，存在重复={duplicated}")
        return self


class ParameterComponentResponse(ParameterComponentInput):
    parameter_version_component_id: uuid.UUID


class ParameterVersionResponse(BaseModel):
    parameter_version_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    parent_version_id: uuid.UUID | None
    content_hash: str
    created_by: str
    created_at: datetime
    components: list[ParameterComponentResponse]


class AnnualBudgetCreate(BaseModel):
    fiscal_year: int = Field(gt=0)
    parameter_version_id: uuid.UUID


class RevisionDerive(BaseModel):
    parameter_version_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=500)


class AnnualBudgetResponse(BaseModel):
    annual_budget_id: uuid.UUID
    department_id: str
    fiscal_year: int
    current_draft_revision_id: uuid.UUID | None
    current_published_revision_id: uuid.UUID | None
    created_by: str
    created_at: datetime


class RevisionResponse(BaseModel):
    budget_revision_id: uuid.UUID
    annual_budget_id: uuid.UUID
    revision_no: int
    parent_revision_id: uuid.UUID | None
    command_type: str
    source_snapshot_id: uuid.UUID
    parameter_version_id: uuid.UUID
    revision_content_hash: str
    component_manifest: dict[str, object]
    created_by: str
    created_at: datetime


class RevisionLifecycleEventResponse(BaseModel):
    revision_lifecycle_event_id: uuid.UUID
    budget_revision_id: uuid.UUID
    event_type: str
    actor_id: str
    detail: dict[str, object]
    occurred_at: datetime


class RevisionStateResponse(BaseModel):
    revision: RevisionResponse
    is_current_draft: bool
    is_current_published: bool
    is_ever_published: bool
    is_formally_confirmed: bool
    lifecycle_events: list[RevisionLifecycleEventResponse]


class RevisionComponentDiffResponse(BaseModel):
    component_key: str
    change_type: str
    against_value: object | None
    revision_value: object | None


class RevisionDiffResponse(BaseModel):
    revision: RevisionStateResponse
    against: RevisionStateResponse
    relationship: str
    changed_count: int
    added_count: int
    removed_count: int
    unchanged_count: int
    components: list[RevisionComponentDiffResponse]
    content_hash: str


class RevisionDetailStateResponse(BaseModel):
    present: bool
    system_value: object | None = None
    manual_value: object | None = None
    final_value: object | None = None
    confirmed: bool = False
    status: str
    reason: str | None = None
    source_evidence: dict[str, object] = Field(default_factory=dict)
    provenance: dict[str, object] = Field(default_factory=dict)
    content_hash: str | None = None


class RevisionDetailDiffItemResponse(BaseModel):
    stable_key: str
    detail_type: str
    change_type: str
    business_key: dict[str, object]
    against: RevisionDetailStateResponse
    revision: RevisionDetailStateResponse


class RevisionDetailDiffResponse(BaseModel):
    revision_id: uuid.UUID
    against_revision_id: uuid.UUID
    component_key: str
    component_label: str
    total_count: int
    changed_count: int
    added_count: int
    removed_count: int
    unchanged_count: int
    items: list[RevisionDetailDiffItemResponse]
    next_cursor: str | None
    content_hash: str


class AuditEventResponse(BaseModel):
    event_id: uuid.UUID
    event_type: str
    annual_budget_id: uuid.UUID | None
    budget_revision_id: uuid.UUID | None
    actor_id: str
    acting_role: str
    role_snapshot: dict[str, object]
    object_type: str
    object_key: dict[str, object]
    detail: dict[str, object]
    reason: str | None
    request_id: str
    occurred_at: datetime


class AuditEventPageResponse(BaseModel):
    items: list[AuditEventResponse]
    next_cursor: uuid.UUID | None


class RevisionCommandResponse(BaseModel):
    annual_budget_id: uuid.UUID
    previous_revision_id: uuid.UUID | None
    new_revision_id: uuid.UUID
    links: dict[str, str]


class BudgetTargetInput(BaseModel):
    scope_level: str = Field(pattern=r"^(DEPARTMENT|TRACK)$")
    track: str | None = Field(default=None, max_length=100)
    target_revenue_yuan: Decimal = Field(ge=0, max_digits=28, decimal_places=6)
    source_evidence: dict[str, object]
    change_reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_concrete_scope(self) -> "BudgetTargetInput":
        if (self.scope_level == "DEPARTMENT") != (self.track is None):
            raise ValueError("部门目标不带赛道，赛道目标必须指定具体赛道")
        return self


class TargetSetReplaceAndLockRequest(BaseModel):
    items: list[BudgetTargetInput] = Field(min_length=2, max_length=1000)
    batch_content_hash: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def require_complete_unique_target_set(self) -> "TargetSetReplaceAndLockRequest":
        keys = [(item.scope_level, item.track.strip() if item.track else None) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("部门或赛道目标不得重复")
        department_count = sum(key == ("DEPARTMENT", None) for key in keys)
        track_count = sum(scope == "TRACK" for scope, _ in keys)
        if department_count != 1 or track_count < 1 or department_count + track_count != len(keys):
            raise ValueError("整套目标必须包含一个部门合计以及至少一个具体赛道")
        if any(not item.source_evidence for item in self.items):
            raise ValueError("学校版目标的来源证据不得为空")
        return self


class BudgetTargetResponse(BaseModel):
    budget_target_id: uuid.UUID
    target_stable_key: str
    scope_level: str
    track: str | None
    target_revenue_yuan: Decimal
    tolerance_yuan: Decimal
    source_evidence: dict[str, object]
    change_reason: str
    content_hash: str


class TargetSetVersionResponse(BaseModel):
    target_set_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    parent_target_set_version_id: uuid.UUID | None
    fiscal_year: int
    lock_status: str
    record_count: int
    batch_content_hash: str
    content_hash: str
    locked_by: str
    role_snapshot: dict[str, object]
    locked_at: datetime
    items: list[BudgetTargetResponse]
    revision: RevisionCommandResponse


class TargetClosureValidateRequest(BaseModel):
    calculation_rule_version: str = Field(
        default="phase1-target-closure-v1", pattern=r"^phase1-target-closure-v1$"
    )


class TargetClosureResultResponse(BaseModel):
    target_closure_result_id: uuid.UUID
    budget_target_id: uuid.UUID
    target_stable_key: str
    scope_level: str
    track: str | None
    target_revenue_yuan: Decimal
    calculated_revenue_yuan: Decimal
    variance_yuan: Decimal
    tolerance_yuan: Decimal
    check_status: str
    content_hash: str


class TargetClosureResultVersionResponse(BaseModel):
    target_closure_result_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    target_set_version_id: uuid.UUID
    revenue_result_version_id: uuid.UUID
    fiscal_year: int
    calculation_rule_version: str
    status: str
    closure_status: str
    upstream_revenue_publishable: bool
    publishable: bool
    record_count: int
    input_fingerprint: str
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[TargetClosureResultResponse]
    revision: RevisionCommandResponse


class OptimizationRunCreateRequest(BaseModel):
    solver_adapter: OptimizationSolverAdapter = OptimizationSolverAdapter.EXACT_ENUMERATION
    maximum_enumerated_states: int = Field(default=100_000, ge=1, le=1_000_000)
    maximum_solver_seconds: int = Field(default=300, ge=1, le=900)
    maximum_candidate_validations: int = Field(default=10_000, ge=1, le=1_000_000)
    maximum_group_table_rows: int = Field(default=100_000, ge=1, le=5_000_000)
    optimization_model_version: str = Field(
        default="phase1-deterministic-rebalancing-v3",
        pattern=r"^phase1-deterministic-rebalancing-v3$",
    )


class ScenarioChangeResponse(BaseModel):
    scenario_change_id: uuid.UUID
    object_type: str
    object_stable_key: str
    original_value: int
    suggested_value: int
    adjustment: int
    content_hash: str


class RebalancingScenarioResponse(BaseModel):
    rebalancing_scenario_id: uuid.UUID
    result_hash: str
    scenario_types: list[str]
    target_variances: list[dict[str, str]]
    retention_contribution_yuan: Decimal
    new_share_contribution_yuan: Decimal
    headcount_signature: list[dict[str, object]]
    objective_values: dict[str, object]
    content_hash: str
    changes: list[ScenarioChangeResponse]


class InfeasibilityProofResponse(BaseModel):
    infeasibility_proof_id: uuid.UUID
    stage: str
    profile: str
    proof_code: str
    model_hash: str
    hard_constraint_groups: list[str]
    proof_summary: dict[str, object]
    content_hash: str


class OptimizationRunResponse(BaseModel):
    optimization_run_id: uuid.UUID
    budget_revision_id: uuid.UUID
    target_closure_result_version_id: uuid.UUID
    optimization_model_version: str
    terminal_stage: str
    status: str
    discount_unlock_required: bool
    discount_unlock_confirmation_id: uuid.UUID | None
    reason_code: str | None
    solver_adapter: str
    maximum_enumerated_states: int
    maximum_solver_seconds: int
    maximum_candidate_validations: int
    maximum_group_table_rows: int
    evaluated_state_count: int
    attempts: list[dict[str, object]]
    input_fingerprint: str
    content_hash: str
    created_by: str
    created_at: datetime
    scenarios: list[RebalancingScenarioResponse]
    infeasibility_proofs: list[InfeasibilityProofResponse]


class DiscountUnlockCreateRequest(BaseModel):
    optimization_run_id: uuid.UUID
    infeasibility_proof_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_non_blank_reason(self) -> "DiscountUnlockCreateRequest":
        if not self.reason.strip():
            raise ValueError("折扣解锁原因不得为空")
        return self


class DiscountUnlockConfirmRequest(BaseModel):
    discount_unlock_request_id: uuid.UUID


class DiscountUnlockResponse(BaseModel):
    discount_unlock_request_id: uuid.UUID
    budget_revision_id: uuid.UUID
    optimization_run_id: uuid.UUID
    infeasibility_proof_id: uuid.UUID
    input_fingerprint: str
    reason: str
    status: str
    requested_by: str
    requested_at: datetime
    request_content_hash: str
    discount_unlock_confirmation_id: uuid.UUID | None
    confirmed_by: str | None
    confirmed_at: datetime | None
    confirmation_content_hash: str | None


class ScenarioApplyRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_non_blank_reason(self) -> "ScenarioApplyRequest":
        if not self.reason.strip():
            raise ValueError("候选方案应用原因不得为空")
        return self


class ScenarioApplicationResponse(BaseModel):
    scenario_application_id: uuid.UUID
    rebalancing_scenario_id: uuid.UUID
    optimization_run_id: uuid.UUID
    source_revision_id: uuid.UUID
    applied_revision_id: uuid.UUID
    input_fingerprint: str
    reason: str
    change_count: int
    entry_change_count: int
    rate_change_count: int
    discount_change_count: int
    campus_entry_set_version_id: uuid.UUID | None
    entry_detail_set_version_id: uuid.UUID | None
    planned_new_student_version_id: uuid.UUID | None
    rate_parameter_version_id: uuid.UUID | None
    discount_parameter_version_id: uuid.UUID | None
    inherited_discount_confirmation_id: uuid.UUID | None
    campus_confirmation_required: bool
    invalidated_components: list[str]
    calculation_job_id: uuid.UUID
    calculation_job_status: str
    links: dict[str, str]
    applied_by: str
    applied_at: datetime
    content_hash: str
    revision: RevisionCommandResponse


class ScenarioApplicationRevertRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_non_blank_reason(self) -> "ScenarioApplicationRevertRequest":
        if not self.reason.strip():
            raise ValueError("撤销候选应用原因不得为空")
        return self


class ScenarioApplicationRevertResponse(BaseModel):
    scenario_application_revert_id: uuid.UUID
    scenario_application_id: uuid.UUID
    reverted_from_revision_id: uuid.UUID
    restored_source_revision_id: uuid.UUID
    reverted_revision_id: uuid.UUID
    reason: str
    restored_component_keys: list[str]
    invalidated_components: list[str]
    calculation_job_id: uuid.UUID
    calculation_job_status: str
    links: dict[str, str]
    reverted_by: str
    reverted_at: datetime
    content_hash: str
    revision: RevisionCommandResponse


class ScenarioApplicationStatusResponse(BaseModel):
    scenario_application_id: uuid.UUID
    applied_revision_id: uuid.UUID
    current_revision_id: uuid.UUID
    can_revert: bool
    blocking_code: str | None
    application: ScenarioApplicationResponse


class CombinationGenerateRequest(BaseModel):
    rate_reference_version_id: uuid.UUID


class BudgetCombinationResponse(BaseModel):
    combination_id: uuid.UUID
    track: str
    product: str
    grade: str
    subject: str
    campus: str
    campus_stable_key: str
    combination_stable_key: str
    entry_attribute: str
    origin_type: str
    enabled: bool
    historical_enrollment: Decimal
    change_reason: str | None
    class_size_parameter_reference: str | None
    content_hash: str


class CombinationSetVersionResponse(BaseModel):
    combination_set_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    rate_reference_version_id: uuid.UUID
    target_fiscal_year: int
    entry_scope_rule_version: str
    record_count: int
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[BudgetCombinationResponse]
    revision: RevisionCommandResponse


class BudgetCombinationDraftInput(BaseModel):
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=100)
    campus: str = Field(min_length=1, max_length=200)
    entry_attribute: str = Field(pattern=r"^(ENTRY|NON_ENTRY)$")
    enabled: bool
    change_reason: str | None = Field(default=None, max_length=500)
    class_size_parameter_reference: str | None = Field(default=None, max_length=200)


class CombinationReplaceDraftRequest(BaseModel):
    items: list[BudgetCombinationDraftInput] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def reject_duplicate_public_keys(self) -> "CombinationReplaceDraftRequest":
        keys = [
            (
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            )
            for item in self.items
        ]
        if any(not value for key in keys for value in key):
            raise ValueError("预算组合所有维度均须填写具体值")
        if len(keys) != len(set(keys)):
            raise ValueError("预算组合公共五维不得重复")
        return self


class CombinationConfirmRequest(BaseModel):
    combination_set_version_id: uuid.UUID


class CombinationConfirmationResponse(BaseModel):
    combination_set_confirmation_id: uuid.UUID
    combination_set_version_id: uuid.UUID
    confirmed_revision_id: uuid.UUID
    input_fingerprint: str
    confirmed_by: str
    confirmed_at: datetime


class EntryTotalLockRequest(BaseModel):
    fiscal_year_entry_total: int = Field(ge=0)
    change_reason: str = Field(min_length=1, max_length=500)


class EntryTotalVersionResponse(BaseModel):
    entry_total_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    fiscal_year: int
    fiscal_year_entry_total: int
    locked_value: int
    change_reason: str
    locked_by: str
    locked_at: datetime
    content_hash: str
    revision: RevisionCommandResponse


class CampusPlanningInput(BaseModel):
    campus: str = Field(min_length=1, max_length=200)
    classroom_resource: dict[str, object]
    sales_resource: dict[str, object]
    manual_reserved_suggestion: int | None = Field(default=None, ge=0)
    manual_suggestion_reason: str | None = Field(default=None, max_length=500)


class CampusSuggestionGenerateRequest(BaseModel):
    campuses: list[CampusPlanningInput] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def reject_duplicate_campuses(self) -> "CampusSuggestionGenerateRequest":
        keys = [item.campus.strip() for item in self.campuses]
        if len(keys) != len(set(keys)):
            raise ValueError("校区输入不得重复")
        return self


class CampusEntrySuggestionResponse(BaseModel):
    campus_entry_suggestion_id: uuid.UUID
    campus: str
    campus_stable_key: str
    historical_business_new_students: int
    history_availability: str
    classroom_resource: dict[str, object]
    sales_resource: dict[str, object]
    manual_reserved_suggestion: int
    manual_suggestion_reason: str | None
    suggested_entry_theoretical: Decimal
    suggested_entry_floor: int
    suggested_entry_fraction: Decimal
    suggestion_tail_assigned: bool
    suggested_entry_value: int
    suggestion_source_type: str
    content_hash: str


class CampusSuggestionVersionResponse(BaseModel):
    campus_entry_suggestion_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    combination_set_version_id: uuid.UUID
    entry_total_version_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    fiscal_year_entry_total: int
    manual_reserved_total: int
    historical_entry_total: int
    suggestion_total: int
    record_count: int
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[CampusEntrySuggestionResponse]
    revision: RevisionCommandResponse


class CampusEntryAdjustmentReason(StrEnum):
    PERFORMANCE_JUDGMENT = "PERFORMANCE_JUDGMENT"
    CLASSROOM_RESOURCE = "CLASSROOM_RESOURCE"
    SALES_RESOURCE = "SALES_RESOURCE"
    NEW_CAMPUS_OR_PRODUCT = "NEW_CAMPUS_OR_PRODUCT"
    STRATEGIC_ADJUSTMENT = "STRATEGIC_ADJUSTMENT"
    OTHER = "OTHER"


class CampusEntryDraftInput(BaseModel):
    campus: str = Field(min_length=1, max_length=200)
    determined_entry_value: int | None = Field(default=None, ge=0)
    adjustment_reason_category: CampusEntryAdjustmentReason | None = None
    adjustment_reason_detail: str | None = Field(default=None, max_length=500)
    performance_evidence: dict[str, object]
    classroom_resource: dict[str, object]
    sales_resource: dict[str, object]


class CampusEntryReplaceDraftRequest(BaseModel):
    items: list[CampusEntryDraftInput] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def reject_duplicate_campuses(self) -> "CampusEntryReplaceDraftRequest":
        keys = [item.campus.strip() for item in self.items]
        if any(not key for key in keys):
            raise ValueError("校区必须填写具体值")
        if len(keys) != len(set(keys)):
            raise ValueError("校区入口确定值不得重复")
        return self


class CampusEntryAllocationResponse(BaseModel):
    campus_entry_allocation_id: uuid.UUID
    campus: str
    campus_stable_key: str
    suggested_entry_value: int
    determined_entry_value: int | None
    adjustment_difference: int | None
    adjustment_reason_category: str | None
    adjustment_reason_detail: str | None
    performance_evidence: dict[str, object]
    classroom_resource: dict[str, object]
    sales_resource: dict[str, object]
    content_hash: str


class CampusEntrySetVersionResponse(BaseModel):
    campus_entry_set_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    basis_combination_set_version_id: uuid.UUID
    entry_total_version_id: uuid.UUID
    suggestion_version_id: uuid.UUID
    fiscal_year_entry_total: int
    determined_total: int
    total_difference: int
    incomplete_count: int
    record_count: int
    is_confirmable: bool
    blocking_reasons: list[str]
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[CampusEntryAllocationResponse]
    revision: RevisionCommandResponse


class CampusEntryConfirmRequest(BaseModel):
    campus_entry_set_version_id: uuid.UUID


class CampusEntryConfirmationResponse(BaseModel):
    campus_entry_confirmation_id: uuid.UUID
    campus_entry_set_version_id: uuid.UUID
    confirmed_revision_id: uuid.UUID
    input_fingerprint: str
    confirmed_by: str
    confirmed_at: datetime


class EntryScopeExceptionType(StrEnum):
    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"


class EntryScopeExceptionInput(BaseModel):
    fiscal_year: int = Field(gt=0)
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=100)
    campus: str = Field(min_length=1, max_length=200)
    exception_type: EntryScopeExceptionType
    reason: str = Field(min_length=1, max_length=500)


class EntryScopeReplaceDraftRequest(BaseModel):
    items: list[EntryScopeExceptionInput] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def require_concrete_unique_dimensions(self) -> "EntryScopeReplaceDraftRequest":
        forbidden = {"全部", "*", "ALL"}
        keys: list[tuple[object, ...]] = []
        for item in self.items:
            dimensions = (
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            )
            if any(not value or value.upper() in forbidden for value in dimensions):
                raise ValueError("入口范围例外所有维度必须填写具体值且不得使用通配")
            if not item.reason.strip():
                raise ValueError("入口范围例外必须填写原因")
            keys.append((item.fiscal_year, *dimensions, item.exception_type.value))
        if len(keys) != len(set(keys)):
            raise ValueError("入口范围例外不得重复")
        return self


class EntryScopeExceptionResponse(BaseModel):
    entry_scope_exception_id: uuid.UUID
    fiscal_year: int
    track: str
    product: str
    grade: str
    subject: str
    campus: str
    exception_type: str
    reason: str
    content_hash: str


class EntryScopeEvaluationResponse(BaseModel):
    entry_scope_evaluation_id: uuid.UUID
    combination_id: uuid.UUID
    combination_stable_key: str
    default_match: bool
    manual_include_match: bool
    manual_exclude_match: bool
    effective_entry_attribute: str
    effective_source: str
    content_hash: str


class EntryScopeVersionResponse(BaseModel):
    entry_scope_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    combination_set_version_id: uuid.UUID
    fiscal_year: int
    default_rule_version: str
    record_count: int
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[EntryScopeExceptionResponse]
    evaluations: list[EntryScopeEvaluationResponse]
    revision: RevisionCommandResponse


class EntryScopeConfirmRequest(BaseModel):
    entry_scope_version_id: uuid.UUID


class EntryScopeConfirmationResponse(BaseModel):
    entry_scope_confirmation_id: uuid.UUID
    entry_scope_version_id: uuid.UUID
    confirmed_revision_id: uuid.UUID
    input_fingerprint: str
    confirmed_by: str
    confirmed_at: datetime


class ManualEntryReservationInput(BaseModel):
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=100)
    campus: str = Field(min_length=1, max_length=200)
    reservation_ratio_ppm: int = Field(ge=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=500)


class EntryDetailCalculateRequest(BaseModel):
    manual_reservations: list[ManualEntryReservationInput] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def reject_duplicate_manual_reservations(self) -> "EntryDetailCalculateRequest":
        keys = [
            (
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            )
            for item in self.manual_reservations
        ]
        if any(not value for key in keys for value in key):
            raise ValueError("人工入口预留必须填写全部具体维度")
        if len(keys) != len(set(keys)):
            raise ValueError("人工入口预留不得重复")
        return self


class EntryDetailAllocationResponse(BaseModel):
    entry_detail_allocation_id: uuid.UUID
    combination_id: uuid.UUID
    combination_stable_key: str
    track: str
    product: str
    grade: str
    subject: str
    campus: str
    effective_entry_attribute: str
    scope_evidence: dict[str, object]
    origin_type: str
    historical_business_new_students: int
    manual_reservation_ratio_ppm: int | None
    manual_reservation_reason: str | None
    allocation_source_type: str
    theoretical_value: Decimal
    floor_value: int
    fraction_value: Decimal
    tail_assigned: bool
    entry_initial_value: int
    soft_lower_bound: int | None
    soft_upper_bound: int | None
    candidate_eligible: bool
    candidate_blocking_reason: str | None
    content_hash: str


class EntryDetailSetVersionResponse(BaseModel):
    entry_detail_set_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    combination_set_version_id: uuid.UUID
    entry_scope_version_id: uuid.UUID
    campus_entry_set_version_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    fiscal_year: int
    source_fiscal_season: int
    campus_entry_total: int
    allocated_entry_total: int
    record_count: int
    zero_initial_count: int
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[EntryDetailAllocationResponse]
    revision: RevisionCommandResponse


class RateParameterDraftItem(BaseModel):
    rate_reference_id: uuid.UUID
    current_value_ppm: int = Field(ge=0, le=1_000_000)
    lower_bound_ppm: int = Field(ge=0, le=1_000_000)
    upper_bound_ppm: int = Field(ge=0, le=1_000_000)
    boundary_reason: str = Field(min_length=1, max_length=500)
    manual_reason: str | None = Field(default=None, max_length=500)


class RateParameterReplaceDraftRequest(BaseModel):
    rate_reference_version_id: uuid.UUID
    items: list[RateParameterDraftItem] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def reject_duplicate_references(self) -> "RateParameterReplaceDraftRequest":
        keys = [item.rate_reference_id for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("比例参数草稿不得重复引用同一历史参考行")
        return self


class RateParameterResponse(BaseModel):
    rate_parameter_id: uuid.UUID
    rate_reference_id: uuid.UUID
    rate_parameter_stable_key: str
    cross_year_match_key: str
    rate_type: str
    rate_usage_mode: str
    fiscal_year: int
    source_fiscal_season: int
    target_fiscal_season: int
    quarter_name: str
    track: str
    product: str
    grade: str
    campus: str
    renewal_relation_code: str | None
    source_type: str
    system_value_ppm: int | None
    current_value_ppm: int
    lower_bound_ppm: int
    upper_bound_ppm: int
    boundary_reason: str
    manual_reason: str | None
    boundary_confirmed: bool
    locked: bool
    locked_value_ppm: int | None
    lock_origin: dict[str, object]
    auto_balance_eligible: bool
    content_hash: str


class RateParameterCopyExceptionResponse(BaseModel):
    rate_parameter_copy_exception_id: uuid.UUID
    source_stable_key: str
    cross_year_match_key: str
    error_code: str
    detail: dict[str, object]
    content_hash: str


class RateParameterVersionResponse(BaseModel):
    rate_parameter_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    parent_rate_parameter_version_id: uuid.UUID | None
    rate_reference_version_id: uuid.UUID
    fiscal_year: int
    operation_type: str
    record_count: int
    locked_count: int
    confirmed_boundary_count: int
    copy_exception_count: int
    is_publishable: bool
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[RateParameterResponse]
    copy_exceptions: list[RateParameterCopyExceptionResponse]
    revision: RevisionCommandResponse


class RateBoundaryConfirmRequest(BaseModel):
    rate_parameter_version_id: uuid.UUID
    rate_parameter_stable_keys: list[str] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def require_unique_stable_keys(self) -> "RateBoundaryConfirmRequest":
        if any(not re.fullmatch(HASH_PATTERN, key) for key in self.rate_parameter_stable_keys):
            raise ValueError("比例参数稳定键必须为 64 位小写哈希")
        if len(self.rate_parameter_stable_keys) != len(set(self.rate_parameter_stable_keys)):
            raise ValueError("确认边界的比例参数键不得重复")
        return self


class RateBoundaryConfirmResponse(BaseModel):
    rate_parameter_version_id: uuid.UUID
    confirmed_revision_id: uuid.UUID
    confirmed_count: int
    confirmed_stable_keys: list[str]


class RateParameterLockItem(BaseModel):
    rate_parameter_stable_key: str = Field(pattern=HASH_PATTERN)
    reason: str = Field(min_length=1, max_length=500)


class RateParameterLockRequest(BaseModel):
    items: list[RateParameterLockItem] = Field(min_length=1, max_length=20000)


class RateParameterChangeAndLockItem(RateParameterLockItem):
    new_value_ppm: int = Field(ge=0, le=1_000_000)


class RateParameterChangeAndLockRequest(BaseModel):
    items: list[RateParameterChangeAndLockItem] = Field(min_length=1, max_length=20000)


class RateParameterUnlockRequest(BaseModel):
    items: list[RateParameterLockItem] = Field(min_length=1, max_length=20000)


class RateParameterCrossYearCopyRequest(BaseModel):
    source_rate_parameter_version_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=500)


class PlannedNewStudentKeyInput(BaseModel):
    fiscal_season: int = Field(gt=0)
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    entry_attribute: str = Field(pattern=r"^(ENTRY|NON_ENTRY)$")
    grade: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=100)
    campus: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_concrete_dimensions(self) -> "PlannedNewStudentKeyInput":
        dimensions = (self.track, self.product, self.grade, self.subject, self.campus)
        if any(not value.strip() for value in dimensions):
            raise ValueError("计划新生人次必须填写全部具体预算维度")
        return self


class PlannedNewStudentChangeAndLockItem(PlannedNewStudentKeyInput):
    planned_new_students: int = Field(strict=True, ge=0)
    manual_renewal_rate_ppm: int | None = Field(default=None, strict=True, ge=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=500)


class PlannedNewStudentUnlockItem(PlannedNewStudentKeyInput):
    reason: str = Field(min_length=1, max_length=500)


def _planned_new_student_request_key(item: PlannedNewStudentKeyInput) -> tuple[object, ...]:
    return (
        item.fiscal_season,
        item.track.strip(),
        item.product.strip(),
        item.entry_attribute,
        item.grade.strip(),
        item.subject.strip(),
        item.campus.strip(),
    )


class PlannedNewStudentChangeAndLockRequest(BaseModel):
    items: list[PlannedNewStudentChangeAndLockItem] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def reject_duplicate_keys(self) -> "PlannedNewStudentChangeAndLockRequest":
        keys = [_planned_new_student_request_key(item) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("计划新生人次完整预算明细不得重复")
        return self


class PlannedNewStudentUnlockRequest(BaseModel):
    items: list[PlannedNewStudentUnlockItem] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def reject_duplicate_keys(self) -> "PlannedNewStudentUnlockRequest":
        keys = [_planned_new_student_request_key(item) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("计划新生人次完整预算明细不得重复")
        return self


class PlannedNewStudentCountResponse(BaseModel):
    planned_new_student_count_id: uuid.UUID
    planned_new_student_stable_key: str
    combination_id: uuid.UUID
    entry_detail_allocation_id: uuid.UUID | None
    combination_stable_key: str
    fiscal_year: int
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    planned_new_students: int
    manual_reason: str
    calculated_new_student_share_ppm: int | None
    locked: bool
    locked_value: int | None
    handling_status: str
    headcount_eligibility_status: str
    lock_origin: dict[str, object]
    auto_balance_eligible: bool
    enters_largest_remainder: bool
    content_hash: str


class PlannedNewStudentVersionResponse(BaseModel):
    planned_new_student_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    parent_planned_new_student_version_id: uuid.UUID | None
    combination_set_version_id: uuid.UUID
    entry_scope_version_id: uuid.UUID
    entry_detail_set_version_id: uuid.UUID
    fiscal_year: int
    operation_type: str
    record_count: int
    locked_count: int
    pending_lock_count: int
    all_items_locked: bool
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[PlannedNewStudentCountResponse]
    revision: RevisionCommandResponse


class HeadcountCalculateRequest(BaseModel):
    calculation_rule_version: str = Field(
        default="phase1-headcount-v1", pattern=r"^phase1-headcount-(v1|opening-v2)$"
    )


class HeadcountRoundingTraceResponse(BaseModel):
    student_type: str
    exact_theory_fraction: str
    floor_value: int
    exact_remainder_fraction: str
    fixed_input: bool
    remainder_rank: int | None
    tail_assigned: bool
    group_target: int
    fixed_total: int
    adjustable_target: int


class SeasonHeadcountResultResponse(BaseModel):
    season_headcount_result_id: uuid.UUID
    combination_id: uuid.UUID
    entry_detail_allocation_id: uuid.UUID | None
    combination_stable_key: str
    fiscal_year: int
    fiscal_season: int
    season_index: int
    quarter_name: str
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    calculation_status: str
    new_student_source: str | None
    planned_eligibility_status: str
    theoretical_old_students: Decimal | None
    theoretical_new_students: Decimal | None
    theoretical_old_fraction: str | None
    theoretical_new_fraction: str | None
    integer_old_students: int | None
    integer_new_students: int | None
    integer_total_students: int | None
    renewal_rate_ppm: int | None
    new_student_share_input_ppm: int | None
    realized_retention_rate_ppm: int | None
    realized_new_student_share_ppm: int | None
    old_rounding_variance: Decimal | None
    new_rounding_variance: Decimal | None
    old_rounding_trace: HeadcountRoundingTraceResponse | None
    new_rounding_trace: HeadcountRoundingTraceResponse | None
    content_hash: str


class HeadcountValidationResponse(BaseModel):
    headcount_validation_result_id: uuid.UUID
    fiscal_season: int
    combination_stable_key: str
    code: str
    severity: str
    message: str
    detail: dict[str, object]
    content_hash: str


class HeadcountResultVersionResponse(BaseModel):
    headcount_result_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    combination_set_version_id: uuid.UUID
    entry_scope_version_id: uuid.UUID
    entry_detail_set_version_id: uuid.UUID
    rate_parameter_version_id: uuid.UUID
    planned_new_student_version_id: uuid.UUID | None
    fiscal_year: int
    calculation_rule_version: str
    status: str
    publishable: bool
    record_count: int
    validation_count: int
    input_fingerprint: str
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[SeasonHeadcountResultResponse]
    validations: list[HeadcountValidationResponse]
    revision: RevisionCommandResponse


class ClassSizeParameterInput(BaseModel):
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    system_value: int = Field(strict=True, gt=0)
    final_value: int = Field(strict=True, gt=0)
    source_evidence: dict[str, object]
    change_reason: str = Field(min_length=1, max_length=500)


class RevenueParameterInput(BaseModel):
    fiscal_season: int = Field(gt=0)
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    list_price_yuan: Decimal = Field(ge=0, max_digits=20, decimal_places=6)
    current_season_recognition_rate_ppm: int = Field(ge=0, le=1_000_000)
    opening_carryover_revenue_yuan: Decimal = Field(default=Decimal(0), ge=0, max_digits=20, decimal_places=6)
    source_evidence: dict[str, object]
    change_reason: str = Field(min_length=1, max_length=500)


class OpeningCarryoverParameterInput(BaseModel):
    fiscal_season: int = Field(gt=0)
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=100)
    campus: str = Field(min_length=1, max_length=200)
    amount_yuan: Decimal = Field(ge=0, max_digits=28, decimal_places=6)
    source_evidence: dict[str, object]
    change_reason: str = Field(min_length=1, max_length=500)


class ResourceFinanceReplaceDraftRequest(BaseModel):
    budget_tax_conversion_factor: Decimal = Field(gt=0, max_digits=20, decimal_places=9)
    tax_factor_source_evidence: dict[str, object]
    tax_factor_change_reason: str = Field(min_length=1, max_length=500)
    class_size_items: list[ClassSizeParameterInput] = Field(min_length=1, max_length=5000)
    revenue_items: list[RevenueParameterInput] = Field(min_length=1, max_length=20000)
    opening_carryover_items: list[OpeningCarryoverParameterInput] = Field(min_length=1, max_length=20000)
    batch_content_hash: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def require_complete_unique_keys_and_evidence(self) -> "ResourceFinanceReplaceDraftRequest":
        class_keys = [(item.track.strip(), item.product.strip()) for item in self.class_size_items]
        revenue_keys = [
            (item.fiscal_season, item.track.strip(), item.product.strip()) for item in self.revenue_items
        ]
        opening_keys = [
            (
                item.fiscal_season,
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            )
            for item in self.opening_carryover_items
        ]
        if len(class_keys) != len(set(class_keys)):
            raise ValueError("班均参数键不得重复")
        if len(revenue_keys) != len(set(revenue_keys)):
            raise ValueError("学费与结转参数键不得重复")
        if len(opening_keys) != len(set(opening_keys)):
            raise ValueError("期初结转参数键不得重复")
        if any(item.opening_carryover_revenue_yuan != 0 for item in self.revenue_items):
            raise ValueError("旧的产品级期初结转已停用，必须改用完整财务适用键")
        evidence = [self.tax_factor_source_evidence]
        evidence.extend(item.source_evidence for item in self.class_size_items)
        evidence.extend(item.source_evidence for item in self.revenue_items)
        evidence.extend(item.source_evidence for item in self.opening_carryover_items)
        if any(not item for item in evidence):
            raise ValueError("资源与财务参数的来源证据不得为空")
        return self


class ClassSizeParameterResponse(BaseModel):
    class_size_parameter_id: uuid.UUID
    class_size_parameter_stable_key: str
    track: str
    product: str
    system_value: int
    final_value: int
    source_evidence: dict[str, object]
    change_reason: str
    content_hash: str


class RevenueParameterResponse(BaseModel):
    revenue_parameter_id: uuid.UUID
    revenue_parameter_stable_key: str
    recognition_parameter_key: str
    fiscal_year: int
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    list_price_yuan: Decimal
    current_season_recognition_rate_ppm: int
    cross_season_recognition_rate_ppm: int
    opening_carryover_revenue_yuan: Decimal
    source_evidence: dict[str, object]
    change_reason: str
    content_hash: str


class OpeningCarryoverParameterResponse(BaseModel):
    opening_carryover_parameter_id: uuid.UUID
    opening_carryover_stable_key: str
    fiscal_year: int
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    grade: str
    subject: str
    campus: str
    amount_yuan: Decimal
    source_evidence: dict[str, object]
    change_reason: str
    content_hash: str


class ResourceFinanceConfirmationResponse(BaseModel):
    resource_finance_confirmation_id: uuid.UUID
    resource_finance_parameter_version_id: uuid.UUID
    confirmed_revision_id: uuid.UUID
    input_fingerprint: str
    confirmed_by: str
    confirmed_at: datetime


class ResourceFinanceParameterVersionResponse(BaseModel):
    resource_finance_parameter_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    parent_resource_finance_parameter_version_id: uuid.UUID | None
    combination_set_version_id: uuid.UUID
    fiscal_year: int
    operation_type: str
    budget_tax_conversion_factor: Decimal
    tax_factor_source_evidence: dict[str, object]
    tax_factor_change_reason: str
    class_size_record_count: int
    revenue_record_count: int
    opening_carryover_record_count: int
    approval_status: str
    confirmed: bool
    confirmation: ResourceFinanceConfirmationResponse | None
    content_hash: str
    created_by: str
    created_at: datetime
    class_size_items: list[ClassSizeParameterResponse]
    revenue_items: list[RevenueParameterResponse]
    opening_carryover_items: list[OpeningCarryoverParameterResponse]
    revision: RevisionCommandResponse


class ResourceFinanceConfirmRequest(BaseModel):
    resource_finance_parameter_version_id: uuid.UUID


class ClassSizeValidateRequest(BaseModel):
    validation_rule_version: str = Field(default="phase1-class-size-v1", pattern=r"^phase1-class-size-v1$")


class ClassSizeResultResponse(BaseModel):
    class_size_result_id: uuid.UUID
    season_headcount_result_id: uuid.UUID
    class_size_parameter_id: uuid.UUID | None
    combination_stable_key: str
    fiscal_year: int
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    integer_total_students: int | None
    minimum_class_size: int | None
    class_size_gap: int | None
    class_size_status: str
    content_hash: str


class ClassSizeValidationResponse(BaseModel):
    class_size_validation_result_id: uuid.UUID
    fiscal_season: int
    combination_stable_key: str
    code: str
    severity: str
    message: str
    detail: dict[str, object]
    content_hash: str


class ClassSizeResultVersionResponse(BaseModel):
    class_size_result_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    headcount_result_version_id: uuid.UUID
    resource_finance_parameter_version_id: uuid.UUID
    resource_finance_confirmation_id: uuid.UUID
    fiscal_year: int
    validation_rule_version: str
    status: str
    publishable: bool
    record_count: int
    validation_count: int
    input_fingerprint: str
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[ClassSizeResultResponse]
    validations: list[ClassSizeValidationResponse]
    revision: RevisionCommandResponse


class DetailDiscountParameterInput(BaseModel):
    fiscal_season: int = Field(gt=0)
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=100)
    discount_rate_ppm: int = Field(ge=0, le=1_000_000, multiple_of=1_000)
    source_evidence: dict[str, object]
    change_reason: str = Field(min_length=1, max_length=500)


class CompositeDiscountTargetInput(BaseModel):
    scope_level: str = Field(pattern=r"^(DEPARTMENT|TRACK)$")
    track: str | None = Field(default=None, max_length=100)
    period_type: str = Field(pattern=r"^(ANNUAL|SEASON)$")
    fiscal_season: int | None = Field(default=None, gt=0)
    target_rate_ppm: int = Field(ge=0, le=1_000_000)
    source_evidence: dict[str, object]
    change_reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_scope_and_period_dimensions(self) -> "CompositeDiscountTargetInput":
        if (self.scope_level == "DEPARTMENT") != (self.track is None):
            raise ValueError("部门综合折扣目标不带赛道，赛道目标必须指定赛道")
        if (self.period_type == "ANNUAL") != (self.fiscal_season is None):
            raise ValueError("年度综合折扣目标不带财季，季度目标必须指定财季")
        return self


class DiscountParameterReplaceDraftRequest(BaseModel):
    detail_items: list[DetailDiscountParameterInput] = Field(min_length=1, max_length=20000)
    composite_targets: list[CompositeDiscountTargetInput] = Field(min_length=1, max_length=1000)
    batch_content_hash: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def require_unique_keys_and_evidence(self) -> "DiscountParameterReplaceDraftRequest":
        detail_keys = [
            (item.fiscal_season, item.track.strip(), item.product.strip(), item.grade.strip())
            for item in self.detail_items
        ]
        target_keys = [
            (
                item.scope_level,
                item.track.strip() if item.track else None,
                item.period_type,
                item.fiscal_season,
            )
            for item in self.composite_targets
        ]
        if len(detail_keys) != len(set(detail_keys)):
            raise ValueError("明细折扣参数键不得重复")
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("综合折扣目标键不得重复")
        evidence = [item.source_evidence for item in self.detail_items]
        evidence.extend(item.source_evidence for item in self.composite_targets)
        if any(not item for item in evidence):
            raise ValueError("折扣参数的来源证据不得为空")
        return self


class DetailDiscountParameterResponse(BaseModel):
    detail_discount_parameter_id: uuid.UUID
    detail_discount_stable_key: str
    fiscal_year: int
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    grade: str
    discount_rate_ppm: int
    source_evidence: dict[str, object]
    change_reason: str
    content_hash: str


class CompositeDiscountTargetResponse(BaseModel):
    composite_discount_target_id: uuid.UUID
    composite_target_stable_key: str
    scope_level: str
    track: str | None
    period_type: str
    fiscal_season: int | None
    target_rate_ppm: int
    source_evidence: dict[str, object]
    change_reason: str
    content_hash: str


class DiscountParameterConfirmationResponse(BaseModel):
    discount_parameter_confirmation_id: uuid.UUID
    discount_parameter_version_id: uuid.UUID
    confirmed_revision_id: uuid.UUID
    input_fingerprint: str
    confirmed_by: str
    confirmed_at: datetime


class DiscountParameterConfirmRequest(BaseModel):
    discount_parameter_version_id: uuid.UUID


class DiscountParameterVersionResponse(BaseModel):
    discount_parameter_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    parent_discount_parameter_version_id: uuid.UUID | None
    combination_set_version_id: uuid.UUID
    fiscal_year: int
    operation_type: str
    detail_record_count: int
    target_record_count: int
    approval_status: str
    confirmed: bool
    confirmation: DiscountParameterConfirmationResponse | None
    content_hash: str
    created_by: str
    created_at: datetime
    detail_items: list[DetailDiscountParameterResponse]
    composite_targets: list[CompositeDiscountTargetResponse]
    revision: RevisionCommandResponse


class RevenueCalculateRequest(BaseModel):
    calculation_rule_version: str = Field(default="phase1-revenue-v1", pattern=r"^phase1-revenue-v1$")


class SeasonRevenueResultResponse(BaseModel):
    season_revenue_result_id: uuid.UUID
    class_size_result_id: uuid.UUID
    season_headcount_result_id: uuid.UUID
    revenue_parameter_id: uuid.UUID
    detail_discount_parameter_id: uuid.UUID
    opening_carryover_parameter_id: uuid.UUID | None
    combination_stable_key: str
    fiscal_year: int
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    integer_total_students: int | None
    list_price_yuan: Decimal
    detail_discount_rate_ppm: int
    discounted_tuition_yuan: Decimal | None
    list_price_revenue_yuan: Decimal | None
    business_quarter_revenue_yuan: Decimal | None
    budget_tax_conversion_factor: Decimal
    net_of_tax_base_yuan: Decimal | None
    current_season_recognition_rate_ppm: int
    cross_season_recognition_rate_ppm: int
    current_recognized_revenue_yuan: Decimal | None
    incoming_carryover_revenue_yuan: Decimal | None
    cross_season_revenue_yuan: Decimal | None
    quarter_recognized_revenue_yuan: Decimal | None
    next_fiscal_year_opening_yuan: Decimal | None
    calculation_status: str
    content_hash: str


class RevenueAggregateResultResponse(BaseModel):
    revenue_aggregate_result_id: uuid.UUID
    composite_discount_target_id: uuid.UUID | None
    aggregate_stable_key: str
    scope_level: str
    track: str | None
    period_type: str
    fiscal_season: int | None
    list_price_revenue_yuan: Decimal
    discounted_revenue_yuan: Decimal
    business_revenue_yuan: Decimal
    recognized_revenue_yuan: Decimal
    composite_discount_rate_ppm: Decimal | None
    target_rate_ppm: int | None
    variance_ppm: Decimal | None
    target_status: str
    content_hash: str


class RevenueValidationResponse(BaseModel):
    revenue_validation_result_id: uuid.UUID
    fiscal_season: int | None
    combination_stable_key: str
    code: str
    severity: str
    message: str
    detail: dict[str, object]
    content_hash: str


class RevenueResultVersionResponse(BaseModel):
    revenue_result_version_id: uuid.UUID
    budget_revision_id: uuid.UUID
    headcount_result_version_id: uuid.UUID
    class_size_result_version_id: uuid.UUID
    resource_finance_parameter_version_id: uuid.UUID
    resource_finance_confirmation_id: uuid.UUID
    discount_parameter_version_id: uuid.UUID
    discount_parameter_confirmation_id: uuid.UUID | None
    fiscal_year: int
    calculation_rule_version: str
    status: str
    publishable: bool
    record_count: int
    aggregate_count: int
    validation_count: int
    input_fingerprint: str
    content_hash: str
    created_by: str
    created_at: datetime
    items: list[SeasonRevenueResultResponse]
    aggregates: list[RevenueAggregateResultResponse]
    validations: list[RevenueValidationResponse]
    revision: RevisionCommandResponse
