import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth import current_actor
from packages.contracts.opening import OpeningCreateRequest, OpeningVersionResponse
from packages.contracts.versioning import (
    AnnualBudgetCreate,
    AnnualBudgetResponse,
    AuditArchiveArtifactResponse,
    AuditArchiveExportRequest,
    AuditArchiveExportResponse,
    AuditEventPageResponse,
    BudgetCalculationEnqueueRequest,
    BudgetCalculationJobResponse,
    BudgetPublishRequest,
    CampusEntryConfirmationResponse,
    CampusEntryConfirmRequest,
    CampusEntryReplaceDraftRequest,
    CampusEntrySetVersionResponse,
    CampusSuggestionGenerateRequest,
    CampusSuggestionVersionResponse,
    ClassSizeResultVersionResponse,
    ClassSizeValidateRequest,
    CombinationConfirmationResponse,
    CombinationConfirmRequest,
    CombinationGenerateRequest,
    CombinationReplaceDraftRequest,
    CombinationSetVersionResponse,
    DiscountParameterConfirmationResponse,
    DiscountParameterConfirmRequest,
    DiscountParameterReplaceDraftRequest,
    DiscountParameterVersionResponse,
    DiscountUnlockConfirmRequest,
    DiscountUnlockCreateRequest,
    DiscountUnlockResponse,
    EntryDetailCalculateRequest,
    EntryDetailSetVersionResponse,
    EntryScopeConfirmationResponse,
    EntryScopeConfirmRequest,
    EntryScopeReplaceDraftRequest,
    EntryScopeVersionResponse,
    EntryTotalLockRequest,
    EntryTotalVersionResponse,
    FormalPlanConfirmRequest,
    HeadcountCalculateRequest,
    HeadcountResultVersionResponse,
    JobResponse,
    OptimizationRunCreateRequest,
    OptimizationRunResponse,
    ParameterVersionCreate,
    ParameterVersionResponse,
    PlannedNewStudentChangeAndLockRequest,
    PlannedNewStudentUnlockRequest,
    PlannedNewStudentVersionResponse,
    PublicationReadinessResponse,
    PublishedBudgetBaselineResponse,
    RateBoundaryConfirmRequest,
    RateBoundaryConfirmResponse,
    RateParameterChangeAndLockRequest,
    RateParameterCrossYearCopyRequest,
    RateParameterLockRequest,
    RateParameterReplaceDraftRequest,
    RateParameterUnlockRequest,
    RateParameterVersionResponse,
    RateReferenceGenerateRequest,
    RateReferencePageResponse,
    RateReferenceVersionResponse,
    ResourceFinanceConfirmationResponse,
    ResourceFinanceConfirmRequest,
    ResourceFinanceParameterVersionResponse,
    ResourceFinanceReplaceDraftRequest,
    RevenueCalculateRequest,
    RevenueResultVersionResponse,
    RevisionCommandResponse,
    RevisionDerive,
    RevisionDetailDiffResponse,
    RevisionDiffResponse,
    RevisionResponse,
    ScenarioApplicationResponse,
    ScenarioApplicationRevertRequest,
    ScenarioApplicationRevertResponse,
    ScenarioApplicationStatusResponse,
    ScenarioApplyRequest,
    SourceSnapshotBuildRequest,
    SourceSnapshotBuildResponse,
    SourceSnapshotCreate,
    SourceSnapshotReconcile,
    SourceSnapshotResponse,
    TargetClosureResultVersionResponse,
    TargetClosureValidateRequest,
    TargetSetReplaceAndLockRequest,
    TargetSetVersionResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.persistence.audit_archive import (
    enqueue_audit_archive_export,
    get_audit_archive,
    get_audit_archive_bytes,
)
from packages.persistence.calculation_queue import enqueue_budget_calculation
from packages.persistence.database import session_scope
from packages.persistence.entry_detail import (
    calculate_entry_details,
    confirm_entry_scope,
    get_entry_detail_set_version,
    get_entry_scope_version,
    replace_entry_scope_draft,
)
from packages.persistence.entry_planning import (
    confirm_campus_entry_set,
    confirm_combination_set,
    generate_campus_suggestions,
    generate_combination_draft,
    get_campus_entry_set_version,
    get_campus_suggestion_version,
    get_combination_set_version,
    get_entry_total_version,
    replace_campus_entry_draft,
    replace_combination_draft,
    save_and_lock_entry_total,
)
from packages.persistence.headcount import (
    calculate_headcount_result,
    get_headcount_result_version,
)
from packages.persistence.opening import create_opening_version, get_opening_version, get_opening_workspace
from packages.persistence.optimization import (
    confirm_discount_unlock,
    create_optimization_run,
    get_current_discount_unlock,
    get_latest_optimization_run,
    get_optimization_run,
    request_discount_unlock,
)
from packages.persistence.planned_new_students import (
    change_and_lock_planned_new_students,
    get_planned_new_student_version,
    unlock_planned_new_students,
)
from packages.persistence.publication import (
    enqueue_formal_plan_confirmation,
    get_publication_readiness,
    publish_budget_revision,
)
from packages.persistence.rate_parameters import (
    change_and_lock_rate_parameters,
    confirm_rate_boundaries,
    copy_cross_year_rate_locks,
    get_rate_parameter_version,
    lock_rate_parameters,
    replace_rate_parameter_draft,
    unlock_rate_parameters,
)
from packages.persistence.resource_finance import (
    confirm_resource_finance_version,
    get_class_size_result_version,
    get_resource_finance_version,
    replace_resource_finance_draft,
    validate_class_size_result,
)
from packages.persistence.revenue import (
    calculate_revenue_result,
    confirm_discount_parameter_version,
    get_discount_parameter_version,
    get_revenue_result_version,
    replace_discount_parameter_draft,
)
from packages.persistence.revision_audit import get_revision_diff, list_audit_events
from packages.persistence.revision_detail_diff import get_revision_detail_diff
from packages.persistence.scenario_application import (
    apply_rebalancing_scenario,
    get_scenario_application_status,
    revert_scenario_application,
)
from packages.persistence.target_closure import (
    get_target_closure_result_version,
    get_target_set_version,
    replace_and_lock_target_set,
    validate_target_closure,
)
from packages.persistence.versioning import (
    create_annual_budget,
    create_parameter_version,
    create_rate_reference_version,
    create_source_snapshot,
    derive_revision,
    enqueue_source_snapshot_build,
    find_annual_budget,
    freeze_source_snapshot,
    get_annual_budget,
    get_job,
    get_parameter_version,
    get_rate_reference_version,
    get_revision,
    get_source_snapshot,
    list_rate_references,
    list_revisions,
    reconcile_source_snapshot,
)

router = APIRouter(tags=["versioning"])

Session = Annotated[AsyncSession, Depends(session_scope)]
CurrentActor = Annotated[Actor, Depends(current_actor)]
IdempotencyKey = Annotated[uuid.UUID, Header(alias="Idempotency-Key")]


def _require_data_maintainer(actor: Actor) -> None:
    if not actor.has_role(Role.DATA_MAINTAINER):
        raise DomainError(
            code="ROLE_FORBIDDEN",
            message="该命令要求数据维护人员角色",
            status_code=403,
        )


def _require_budget_owner(actor: Actor) -> None:
    if not actor.has_role(Role.DEPARTMENT_BUDGET_OWNER):
        raise DomainError(
            code="ROLE_FORBIDDEN",
            message="该命令要求部门预算负责人角色",
            status_code=403,
        )


def _require_rate_operator(actor: Actor) -> None:
    if not (actor.has_role(Role.DATA_MAINTAINER) or actor.has_role(Role.DEPARTMENT_BUDGET_OWNER)):
        raise DomainError(
            code="ROLE_FORBIDDEN",
            message="该命令要求数据维护人员或部门预算负责人角色",
            status_code=403,
        )


def _require_planned_new_student_operator(actor: Actor) -> None:
    if not (actor.has_role(Role.DATA_MAINTAINER) or actor.has_role(Role.DEPARTMENT_BUDGET_OWNER)):
        raise DomainError(
            code="ROLE_FORBIDDEN",
            message="该命令要求数据维护人员或部门预算负责人角色",
            status_code=403,
        )


def _require_archive_operator(actor: Actor) -> None:
    if not (actor.has_role(Role.DATA_MAINTAINER) or actor.has_role(Role.DEPARTMENT_BUDGET_OWNER)):
        raise DomainError(
            code="ROLE_FORBIDDEN",
            message="该命令要求数据维护人员或部门预算负责人角色",
            status_code=403,
        )


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _parse_if_match(value: str) -> uuid.UUID:
    candidate = value.strip()
    if candidate.startswith('W/"') and candidate.endswith('"'):
        candidate = candidate[3:-1]
    elif candidate.startswith('"') and candidate.endswith('"'):
        candidate = candidate[1:-1]
    try:
        return uuid.UUID(candidate)
    except ValueError as exc:
        raise DomainError(
            code="INVALID_IF_MATCH",
            message="If-Match 必须是预算修订 UUID",
            status_code=400,
        ) from exc


@router.post(
    "/source-snapshots:build",
    response_model=SourceSnapshotBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def build_source_snapshot_route(
    payload: SourceSnapshotBuildRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> SourceSnapshotBuildResponse:
    _require_data_maintainer(actor)
    return await enqueue_source_snapshot_build(
        session,
        actor=actor,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_route(job_id: uuid.UUID, session: Session, actor: CurrentActor) -> JobResponse:
    return await get_job(session, actor=actor, job_id=job_id)


@router.post(
    "/revisions/{revision_id}:export",
    response_model=AuditArchiveExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_audit_archive_export_route(
    revision_id: uuid.UUID,
    payload: AuditArchiveExportRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> AuditArchiveExportResponse:
    _require_archive_operator(actor)
    return await enqueue_audit_archive_export(
        session,
        actor=actor,
        revision_id=revision_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/audit-archives/{audit_archive_id}",
    response_model=AuditArchiveArtifactResponse,
)
async def get_audit_archive_route(
    audit_archive_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> AuditArchiveArtifactResponse:
    return await get_audit_archive(session, actor=actor, audit_archive_id=audit_archive_id)


@router.get("/audit-archives/{audit_archive_id}/download")
async def download_audit_archive_route(
    audit_archive_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> Response:
    metadata, artifact_bytes = await get_audit_archive_bytes(
        session,
        actor=actor,
        audit_archive_id=audit_archive_id,
    )
    return Response(
        content=artifact_bytes,
        media_type=metadata.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{metadata.file_name}"',
            "ETag": f'"{metadata.content_hash}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/revisions/{revision_id}:calculate",
    response_model=BudgetCalculationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_budget_calculation_route(
    revision_id: uuid.UUID,
    payload: BudgetCalculationEnqueueRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> BudgetCalculationJobResponse:
    _require_data_maintainer(actor)
    return await enqueue_budget_calculation(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/revisions/{revision_id}/publication-readiness",
    response_model=PublicationReadinessResponse,
)
async def get_publication_readiness_route(
    revision_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> PublicationReadinessResponse:
    return await get_publication_readiness(session, actor=actor, revision_id=revision_id)


@router.post(
    "/revisions/{revision_id}:confirm-formal-plan",
    response_model=BudgetCalculationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_formal_plan_route(
    revision_id: uuid.UUID,
    payload: FormalPlanConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> BudgetCalculationJobResponse:
    _require_budget_owner(actor)
    return await enqueue_formal_plan_confirmation(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}:publish",
    response_model=PublishedBudgetBaselineResponse,
    status_code=status.HTTP_200_OK,
)
async def publish_budget_revision_route(
    revision_id: uuid.UUID,
    payload: BudgetPublishRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> PublishedBudgetBaselineResponse:
    _require_budget_owner(actor)
    return await publish_budget_revision(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/source-snapshots",
    response_model=SourceSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_source_snapshot_route(
    payload: SourceSnapshotCreate,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> SourceSnapshotResponse:
    _require_data_maintainer(actor)
    return await create_source_snapshot(
        session,
        actor=actor,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post("/source-snapshots/{source_snapshot_id}:reconcile", response_model=SourceSnapshotResponse)
async def reconcile_source_snapshot_route(
    source_snapshot_id: uuid.UUID,
    payload: SourceSnapshotReconcile,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> SourceSnapshotResponse:
    _require_data_maintainer(actor)
    return await reconcile_source_snapshot(
        session,
        actor=actor,
        source_snapshot_id=source_snapshot_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post("/source-snapshots/{source_snapshot_id}:freeze", response_model=SourceSnapshotResponse)
async def freeze_source_snapshot_route(
    source_snapshot_id: uuid.UUID,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> SourceSnapshotResponse:
    _require_data_maintainer(actor)
    return await freeze_source_snapshot(
        session,
        actor=actor,
        source_snapshot_id=source_snapshot_id,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get("/source-snapshots/{source_snapshot_id}", response_model=SourceSnapshotResponse)
async def get_source_snapshot_route(
    source_snapshot_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> SourceSnapshotResponse:
    result = await get_source_snapshot(session, actor=actor, source_snapshot_id=source_snapshot_id)
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/source-snapshots/{source_snapshot_id}/rate-reference-versions",
    response_model=RateReferenceVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_rate_reference_version_route(
    source_snapshot_id: uuid.UUID,
    payload: RateReferenceGenerateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> RateReferenceVersionResponse:
    _require_data_maintainer(actor)
    return await create_rate_reference_version(
        session,
        actor=actor,
        source_snapshot_id=source_snapshot_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/rate-reference-versions/{rate_reference_version_id}",
    response_model=RateReferenceVersionResponse,
)
async def get_rate_reference_version_route(
    rate_reference_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> RateReferenceVersionResponse:
    result = await get_rate_reference_version(
        session, actor=actor, rate_reference_version_id=rate_reference_version_id
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.get(
    "/rate-reference-versions/{rate_reference_version_id}/references",
    response_model=RateReferencePageResponse,
)
async def list_rate_references_route(
    rate_reference_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: uuid.UUID | None = None,
) -> RateReferencePageResponse:
    return await list_rate_references(
        session,
        actor=actor,
        rate_reference_version_id=rate_reference_version_id,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/parameter-versions",
    response_model=ParameterVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_parameter_version_route(
    payload: ParameterVersionCreate,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> ParameterVersionResponse:
    _require_data_maintainer(actor)
    return await create_parameter_version(
        session,
        actor=actor,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get("/parameter-versions/{parameter_version_id}", response_model=ParameterVersionResponse)
async def get_parameter_version_route(
    parameter_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> ParameterVersionResponse:
    result = await get_parameter_version(session, actor=actor, parameter_version_id=parameter_version_id)
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/annual-budgets",
    response_model=RevisionCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_annual_budget_route(
    payload: AnnualBudgetCreate,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> RevisionCommandResponse:
    _require_data_maintainer(actor)
    return await create_annual_budget(
        session,
        actor=actor,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get("/annual-budgets", response_model=AnnualBudgetResponse)
async def find_annual_budget_route(
    department_id: str,
    fiscal_year: int,
    session: Session,
    actor: CurrentActor,
) -> AnnualBudgetResponse:
    return await find_annual_budget(
        session,
        actor=actor,
        department_id=department_id,
        fiscal_year=fiscal_year,
    )


@router.get("/annual-budgets/{annual_budget_id}", response_model=AnnualBudgetResponse)
async def get_annual_budget_route(
    annual_budget_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> AnnualBudgetResponse:
    return await get_annual_budget(session, actor=actor, annual_budget_id=annual_budget_id)


@router.get("/annual-budgets/{annual_budget_id}/revisions", response_model=list[RevisionResponse])
async def list_revisions_route(
    annual_budget_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> list[RevisionResponse]:
    return await list_revisions(session, actor=actor, annual_budget_id=annual_budget_id)


@router.get("/revisions/{revision_id}", response_model=RevisionResponse)
async def get_revision_route(
    revision_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> RevisionResponse:
    result = await get_revision(session, actor=actor, revision_id=revision_id)
    response.headers["ETag"] = f'"{result.revision_content_hash}"'
    return result


@router.get("/revisions/{revision_id}/diff", response_model=RevisionDiffResponse)
async def get_revision_diff_route(
    revision_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
    against: uuid.UUID,
) -> RevisionDiffResponse:
    return await get_revision_diff(
        session,
        actor=actor,
        revision_id=revision_id,
        against_revision_id=against,
    )


@router.get(
    "/revisions/{revision_id}/detail-diff",
    response_model=RevisionDetailDiffResponse,
)
async def get_revision_detail_diff_route(
    revision_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
    against: uuid.UUID,
    component_key: Annotated[str, Query(min_length=1, max_length=100)],
    changed_only: bool = True,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=160)] = None,
) -> RevisionDetailDiffResponse:
    return await get_revision_detail_diff(
        session,
        actor=actor,
        revision_id=revision_id,
        against_revision_id=against,
        component_key=component_key,
        changed_only=changed_only,
        query=q,
        limit=limit,
        cursor=cursor,
    )


@router.get("/audit-events", response_model=AuditEventPageResponse)
async def list_audit_events_route(
    annual_budget_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
    revision_id: uuid.UUID | None = None,
    event_type: Annotated[str | None, Query(max_length=80)] = None,
    object_type: Annotated[str | None, Query(max_length=80)] = None,
    actor_id: Annotated[str | None, Query(max_length=100)] = None,
    acting_role: Annotated[str | None, Query(max_length=60)] = None,
    reason_contains: Annotated[str | None, Query(max_length=200)] = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: uuid.UUID | None = None,
) -> AuditEventPageResponse:
    return await list_audit_events(
        session,
        actor=actor,
        annual_budget_id=annual_budget_id,
        revision_id=revision_id,
        event_type=event_type,
        object_type=object_type,
        actor_id=actor_id,
        acting_role=acting_role,
        reason_contains=reason_contains,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/revisions/{revision_id}:derive",
    response_model=RevisionCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def derive_revision_route(
    revision_id: uuid.UUID,
    payload: RevisionDerive,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RevisionCommandResponse:
    _require_data_maintainer(actor)
    return await derive_revision(
        session,
        actor=actor,
        parent_revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/targets:replace-and-lock",
    response_model=TargetSetVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_and_lock_target_set_route(
    revision_id: uuid.UUID,
    payload: TargetSetReplaceAndLockRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> TargetSetVersionResponse:
    _require_data_maintainer(actor)
    return await replace_and_lock_target_set(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get("/target-set-versions/{target_set_version_id}", response_model=TargetSetVersionResponse)
async def get_target_set_version_route(
    target_set_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> TargetSetVersionResponse:
    result = await get_target_set_version(
        session,
        actor=actor,
        target_set_version_id=target_set_version_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/revisions/{revision_id}/combinations:generate-draft",
    response_model=CombinationSetVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_combination_draft_route(
    revision_id: uuid.UUID,
    payload: CombinationGenerateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> CombinationSetVersionResponse:
    _require_data_maintainer(actor)
    return await generate_combination_draft(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/combination-set-versions/{combination_set_version_id}",
    response_model=CombinationSetVersionResponse,
)
async def get_combination_set_version_route(
    combination_set_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> CombinationSetVersionResponse:
    return await get_combination_set_version(
        session,
        actor=actor,
        combination_set_version_id=combination_set_version_id,
    )


@router.post(
    "/revisions/{revision_id}/combinations:replace-draft",
    response_model=CombinationSetVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_combination_draft_route(
    revision_id: uuid.UUID,
    payload: CombinationReplaceDraftRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> CombinationSetVersionResponse:
    _require_data_maintainer(actor)
    return await replace_combination_draft(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/combinations:confirm",
    response_model=CombinationConfirmationResponse,
)
async def confirm_combination_set_route(
    revision_id: uuid.UUID,
    payload: CombinationConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> CombinationConfirmationResponse:
    _require_budget_owner(actor)
    return await confirm_combination_set(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/entry-total:save-and-lock",
    response_model=EntryTotalVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def save_and_lock_entry_total_route(
    revision_id: uuid.UUID,
    payload: EntryTotalLockRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> EntryTotalVersionResponse:
    _require_data_maintainer(actor)
    return await save_and_lock_entry_total(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/entry-total-versions/{entry_total_version_id}",
    response_model=EntryTotalVersionResponse,
)
async def get_entry_total_version_route(
    entry_total_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> EntryTotalVersionResponse:
    return await get_entry_total_version(
        session,
        actor=actor,
        entry_total_version_id=entry_total_version_id,
    )


@router.post(
    "/revisions/{revision_id}/campus-entry:generate-suggestions",
    response_model=CampusSuggestionVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_campus_suggestions_route(
    revision_id: uuid.UUID,
    payload: CampusSuggestionGenerateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> CampusSuggestionVersionResponse:
    _require_data_maintainer(actor)
    return await generate_campus_suggestions(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/campus-entry-suggestion-versions/{campus_entry_suggestion_version_id}",
    response_model=CampusSuggestionVersionResponse,
)
async def get_campus_suggestion_version_route(
    campus_entry_suggestion_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> CampusSuggestionVersionResponse:
    return await get_campus_suggestion_version(
        session,
        actor=actor,
        campus_entry_suggestion_version_id=campus_entry_suggestion_version_id,
    )


@router.post(
    "/revisions/{revision_id}/campus-entry:replace-draft",
    response_model=CampusEntrySetVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_campus_entry_draft_route(
    revision_id: uuid.UUID,
    payload: CampusEntryReplaceDraftRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> CampusEntrySetVersionResponse:
    _require_data_maintainer(actor)
    return await replace_campus_entry_draft(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/campus-entry-set-versions/{campus_entry_set_version_id}",
    response_model=CampusEntrySetVersionResponse,
)
async def get_campus_entry_set_version_route(
    campus_entry_set_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> CampusEntrySetVersionResponse:
    return await get_campus_entry_set_version(
        session,
        actor=actor,
        campus_entry_set_version_id=campus_entry_set_version_id,
    )


@router.post(
    "/revisions/{revision_id}/campus-entry:confirm",
    response_model=CampusEntryConfirmationResponse,
)
async def confirm_campus_entry_set_route(
    revision_id: uuid.UUID,
    payload: CampusEntryConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> CampusEntryConfirmationResponse:
    _require_budget_owner(actor)
    return await confirm_campus_entry_set(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/entry-scope:replace-draft",
    response_model=EntryScopeVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_entry_scope_draft_route(
    revision_id: uuid.UUID,
    payload: EntryScopeReplaceDraftRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> EntryScopeVersionResponse:
    _require_data_maintainer(actor)
    return await replace_entry_scope_draft(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/entry-scope-versions/{entry_scope_version_id}",
    response_model=EntryScopeVersionResponse,
)
async def get_entry_scope_version_route(
    entry_scope_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> EntryScopeVersionResponse:
    return await get_entry_scope_version(session, actor=actor, entry_scope_version_id=entry_scope_version_id)


@router.post(
    "/revisions/{revision_id}/entry-scope:confirm",
    response_model=EntryScopeConfirmationResponse,
)
async def confirm_entry_scope_route(
    revision_id: uuid.UUID,
    payload: EntryScopeConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> EntryScopeConfirmationResponse:
    _require_budget_owner(actor)
    return await confirm_entry_scope(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/entry-detail:calculate",
    response_model=EntryDetailSetVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def calculate_entry_details_route(
    revision_id: uuid.UUID,
    payload: EntryDetailCalculateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> EntryDetailSetVersionResponse:
    _require_data_maintainer(actor)
    return await calculate_entry_details(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/entry-detail-set-versions/{entry_detail_set_version_id}",
    response_model=EntryDetailSetVersionResponse,
)
async def get_entry_detail_set_version_route(
    entry_detail_set_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> EntryDetailSetVersionResponse:
    return await get_entry_detail_set_version(
        session,
        actor=actor,
        entry_detail_set_version_id=entry_detail_set_version_id,
    )


@router.post(
    "/revisions/{revision_id}/rate-parameters:replace-draft",
    response_model=RateParameterVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_rate_parameter_draft_route(
    revision_id: uuid.UUID,
    payload: RateParameterReplaceDraftRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RateParameterVersionResponse:
    _require_data_maintainer(actor)
    return await replace_rate_parameter_draft(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/rate-parameter-versions/{rate_parameter_version_id}",
    response_model=RateParameterVersionResponse,
)
async def get_rate_parameter_version_route(
    rate_parameter_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> RateParameterVersionResponse:
    return await get_rate_parameter_version(
        session,
        actor=actor,
        rate_parameter_version_id=rate_parameter_version_id,
    )


@router.post(
    "/revisions/{revision_id}/rate-boundaries:confirm",
    response_model=RateBoundaryConfirmResponse,
)
async def confirm_rate_boundaries_route(
    revision_id: uuid.UUID,
    payload: RateBoundaryConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RateBoundaryConfirmResponse:
    _require_budget_owner(actor)
    return await confirm_rate_boundaries(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/rate-parameters:lock",
    response_model=RateParameterVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def lock_rate_parameters_route(
    revision_id: uuid.UUID,
    payload: RateParameterLockRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RateParameterVersionResponse:
    _require_rate_operator(actor)
    return await lock_rate_parameters(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/rate-parameters:change-and-lock",
    response_model=RateParameterVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def change_and_lock_rate_parameters_route(
    revision_id: uuid.UUID,
    payload: RateParameterChangeAndLockRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RateParameterVersionResponse:
    _require_rate_operator(actor)
    return await change_and_lock_rate_parameters(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/rate-parameters:unlock",
    response_model=RateParameterVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def unlock_rate_parameters_route(
    revision_id: uuid.UUID,
    payload: RateParameterUnlockRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RateParameterVersionResponse:
    _require_rate_operator(actor)
    return await unlock_rate_parameters(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/rate-parameters:copy-cross-year-locks",
    response_model=RateParameterVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def copy_cross_year_rate_locks_route(
    revision_id: uuid.UUID,
    payload: RateParameterCrossYearCopyRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RateParameterVersionResponse:
    _require_rate_operator(actor)
    return await copy_cross_year_rate_locks(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/planned-new-students:change-and-lock",
    response_model=PlannedNewStudentVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def change_and_lock_planned_new_students_route(
    revision_id: uuid.UUID,
    payload: PlannedNewStudentChangeAndLockRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> PlannedNewStudentVersionResponse:
    _require_planned_new_student_operator(actor)
    return await change_and_lock_planned_new_students(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/planned-new-students:unlock",
    response_model=PlannedNewStudentVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def unlock_planned_new_students_route(
    revision_id: uuid.UUID,
    payload: PlannedNewStudentUnlockRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> PlannedNewStudentVersionResponse:
    _require_planned_new_student_operator(actor)
    return await unlock_planned_new_students(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/planned-new-student-versions/{planned_new_student_version_id}",
    response_model=PlannedNewStudentVersionResponse,
)
async def get_planned_new_student_version_route(
    planned_new_student_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> PlannedNewStudentVersionResponse:
    return await get_planned_new_student_version(
        session,
        actor=actor,
        planned_new_student_version_id=planned_new_student_version_id,
    )


@router.post(
    "/revisions/{revision_id}/opening-students", response_model=OpeningVersionResponse, status_code=201
)
async def create_opening_route(
    revision_id: uuid.UUID,
    payload: OpeningCreateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> OpeningVersionResponse:
    _require_data_maintainer(actor)
    return await create_opening_version(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get("/revisions/{revision_id}/opening-students/workspace")
async def opening_workspace_route(
    revision_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> dict[str, object]:
    return await get_opening_workspace(session, actor=actor, revision_id=revision_id)


@router.get("/opening-student-versions/{version_id}", response_model=OpeningVersionResponse)
async def get_opening_route(
    version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> OpeningVersionResponse:
    return await get_opening_version(session, actor=actor, version_id=version_id)


@router.post(
    "/revisions/{revision_id}/headcounts:calculate",
    response_model=HeadcountResultVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def calculate_headcount_result_route(
    revision_id: uuid.UUID,
    payload: HeadcountCalculateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> HeadcountResultVersionResponse:
    _require_data_maintainer(actor)
    return await calculate_headcount_result(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/headcount-result-versions/{headcount_result_version_id}",
    response_model=HeadcountResultVersionResponse,
)
async def get_headcount_result_version_route(
    headcount_result_version_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> HeadcountResultVersionResponse:
    return await get_headcount_result_version(
        session,
        actor=actor,
        headcount_result_version_id=headcount_result_version_id,
    )


@router.post(
    "/revisions/{revision_id}/resource-finance:replace-draft",
    response_model=ResourceFinanceParameterVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_resource_finance_draft_route(
    revision_id: uuid.UUID,
    payload: ResourceFinanceReplaceDraftRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> ResourceFinanceParameterVersionResponse:
    _require_data_maintainer(actor)
    return await replace_resource_finance_draft(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/resource-finance:confirm",
    response_model=ResourceFinanceConfirmationResponse,
)
async def confirm_resource_finance_version_route(
    revision_id: uuid.UUID,
    payload: ResourceFinanceConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> ResourceFinanceConfirmationResponse:
    _require_budget_owner(actor)
    return await confirm_resource_finance_version(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/resource-finance-parameter-versions/{resource_finance_parameter_version_id}",
    response_model=ResourceFinanceParameterVersionResponse,
)
async def get_resource_finance_version_route(
    resource_finance_parameter_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> ResourceFinanceParameterVersionResponse:
    result = await get_resource_finance_version(
        session,
        actor=actor,
        resource_finance_parameter_version_id=resource_finance_parameter_version_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/revisions/{revision_id}/class-sizes:validate",
    response_model=ClassSizeResultVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def validate_class_size_result_route(
    revision_id: uuid.UUID,
    payload: ClassSizeValidateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> ClassSizeResultVersionResponse:
    _require_data_maintainer(actor)
    return await validate_class_size_result(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/class-size-result-versions/{class_size_result_version_id}",
    response_model=ClassSizeResultVersionResponse,
)
async def get_class_size_result_version_route(
    class_size_result_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> ClassSizeResultVersionResponse:
    result = await get_class_size_result_version(
        session,
        actor=actor,
        class_size_result_version_id=class_size_result_version_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/revisions/{revision_id}/discount-parameters:replace-draft",
    response_model=DiscountParameterVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_discount_parameter_draft_route(
    revision_id: uuid.UUID,
    payload: DiscountParameterReplaceDraftRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> DiscountParameterVersionResponse:
    _require_data_maintainer(actor)
    return await replace_discount_parameter_draft(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/discount-parameter-versions/{discount_parameter_version_id}",
    response_model=DiscountParameterVersionResponse,
)
async def get_discount_parameter_version_route(
    discount_parameter_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> DiscountParameterVersionResponse:
    result = await get_discount_parameter_version(
        session,
        actor=actor,
        discount_parameter_version_id=discount_parameter_version_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/revisions/{revision_id}/discount-parameters:confirm",
    response_model=DiscountParameterConfirmationResponse,
)
async def confirm_discount_parameter_version_route(
    revision_id: uuid.UUID,
    payload: DiscountParameterConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> DiscountParameterConfirmationResponse:
    _require_budget_owner(actor)
    return await confirm_discount_parameter_version(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/revenues:calculate",
    response_model=RevenueResultVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def calculate_revenue_result_route(
    revision_id: uuid.UUID,
    payload: RevenueCalculateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RevenueResultVersionResponse:
    _require_data_maintainer(actor)
    return await calculate_revenue_result(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/revenue-result-versions/{revenue_result_version_id}",
    response_model=RevenueResultVersionResponse,
)
async def get_revenue_result_version_route(
    revenue_result_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> RevenueResultVersionResponse:
    result = await get_revenue_result_version(
        session,
        actor=actor,
        revenue_result_version_id=revenue_result_version_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/revisions/{revision_id}/target-closures:validate",
    response_model=TargetClosureResultVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def validate_target_closure_route(
    revision_id: uuid.UUID,
    payload: TargetClosureValidateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> TargetClosureResultVersionResponse:
    _require_data_maintainer(actor)
    return await validate_target_closure(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/target-closure-result-versions/{target_closure_result_version_id}",
    response_model=TargetClosureResultVersionResponse,
)
async def get_target_closure_result_version_route(
    target_closure_result_version_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> TargetClosureResultVersionResponse:
    result = await get_target_closure_result_version(
        session,
        actor=actor,
        target_closure_result_version_id=target_closure_result_version_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.post(
    "/revisions/{revision_id}/optimization-runs",
    response_model=OptimizationRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_optimization_run_route(
    revision_id: uuid.UUID,
    payload: OptimizationRunCreateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> OptimizationRunResponse:
    _require_data_maintainer(actor)
    return await create_optimization_run(
        session,
        actor=actor,
        revision_id=revision_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/optimization-runs/{optimization_run_id}",
    response_model=OptimizationRunResponse,
)
async def get_optimization_run_route(
    optimization_run_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> OptimizationRunResponse:
    result = await get_optimization_run(
        session,
        actor=actor,
        optimization_run_id=optimization_run_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.get(
    "/revisions/{revision_id}/optimization-runs/latest",
    response_model=OptimizationRunResponse,
)
async def get_latest_optimization_run_route(
    revision_id: uuid.UUID,
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> OptimizationRunResponse:
    result = await get_latest_optimization_run(
        session,
        actor=actor,
        revision_id=revision_id,
    )
    response.headers["ETag"] = f'"{result.content_hash}"'
    return result


@router.get(
    "/revisions/{revision_id}/discount-unlocks/current",
    response_model=DiscountUnlockResponse,
)
async def get_current_discount_unlock_route(
    revision_id: uuid.UUID,
    input_fingerprint: Annotated[str, Query(min_length=64, max_length=64)],
    response: Response,
    session: Session,
    actor: CurrentActor,
) -> DiscountUnlockResponse:
    result = await get_current_discount_unlock(
        session,
        actor=actor,
        revision_id=revision_id,
        input_fingerprint=input_fingerprint,
    )
    response.headers["ETag"] = f'"{result.request_content_hash}"'
    return result


@router.post(
    "/revisions/{revision_id}/discount-unlocks:request",
    response_model=DiscountUnlockResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_discount_unlock_route(
    revision_id: uuid.UUID,
    payload: DiscountUnlockCreateRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> DiscountUnlockResponse:
    _require_data_maintainer(actor)
    return await request_discount_unlock(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/discount-unlocks:confirm",
    response_model=DiscountUnlockResponse,
)
async def confirm_discount_unlock_route(
    revision_id: uuid.UUID,
    payload: DiscountUnlockConfirmRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> DiscountUnlockResponse:
    _require_budget_owner(actor)
    return await confirm_discount_unlock(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/revisions/{revision_id}/scenarios/{scenario_id}:apply",
    response_model=ScenarioApplicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def apply_rebalancing_scenario_route(
    revision_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: ScenarioApplyRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> ScenarioApplicationResponse:
    _require_data_maintainer(actor)
    return await apply_rebalancing_scenario(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        scenario_id=scenario_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/revisions/{revision_id}/scenario-application/current",
    response_model=ScenarioApplicationStatusResponse,
)
async def current_scenario_application_route(
    revision_id: uuid.UUID,
    session: Session,
    actor: CurrentActor,
) -> ScenarioApplicationStatusResponse:
    return await get_scenario_application_status(session, actor=actor, revision_id=revision_id)


@router.post(
    "/revisions/{revision_id}/scenario-applications/{application_id}:revert",
    response_model=ScenarioApplicationRevertResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def revert_rebalancing_scenario_route(
    revision_id: uuid.UUID,
    application_id: uuid.UUID,
    payload: ScenarioApplicationRevertRequest,
    request: Request,
    session: Session,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> ScenarioApplicationRevertResponse:
    _require_data_maintainer(actor)
    return await revert_scenario_application(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=_parse_if_match(if_match),
        application_id=application_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
