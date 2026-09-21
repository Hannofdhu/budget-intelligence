import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    AnnualBudgetCreate,
    AnnualBudgetResponse,
    JobResponse,
    ParameterComponentResponse,
    ParameterVersionCreate,
    ParameterVersionResponse,
    RateReferenceGenerateRequest,
    RateReferencePageResponse,
    RateReferenceResponse,
    RateReferenceVersionResponse,
    ReconciliationResponse,
    RevisionCommandResponse,
    RevisionDerive,
    RevisionResponse,
    SourceComponentResponse,
    SourceSnapshotBuildRequest,
    SourceSnapshotBuildResponse,
    SourceSnapshotCreate,
    SourceSnapshotReconcile,
    SourceSnapshotResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    AnnualBudget,
    AsyncJob,
    AuditEvent,
    BudgetRevision,
    CommandReceipt,
    ParameterVersion,
    ParameterVersionComponent,
    RateReference,
    RateReferenceVersion,
    RevisionLifecycleEvent,
    SourceMetricAggregate,
    SourceReconciliation,
    SourceSnapshot,
    SourceSnapshotComponent,
)
from packages.rate_reference.generation import (
    CAMPUS_REMOVED_FALLBACK,
    EXACT_HISTORY,
    MANUAL_REQUIRED,
    RateReferenceGenerationError,
    SourceRateKey,
    SourceRateValue,
    generate_rate_references,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _role_snapshot(actor: Actor) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "user_id": actor.user_id,
        "department_id": actor.department_id,
        "roles": sorted(role.value for role in actor.roles),
    }


async def _idempotency_replay(
    session: AsyncSession,
    *,
    actor: Actor,
    command_type: str,
    idempotency_key: uuid.UUID,
    request_hash: str,
) -> dict[str, Any] | None:
    lock_key = f"{actor.user_id}:{command_type}:{idempotency_key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"), {"lock_key": lock_key}
    )
    receipt = await session.scalar(
        select(CommandReceipt).where(
            CommandReceipt.actor_id == actor.user_id,
            CommandReceipt.command_type == command_type,
            CommandReceipt.idempotency_key == str(idempotency_key),
        )
    )
    if receipt is None:
        return None
    if receipt.request_hash != request_hash:
        raise DomainError(
            code="IDEMPOTENCY_PAYLOAD_MISMATCH",
            message="同一幂等键已用于不同请求载荷",
            status_code=409,
        )
    return receipt.response


def _store_receipt(
    session: AsyncSession,
    *,
    actor: Actor,
    command_type: str,
    idempotency_key: uuid.UUID,
    request_hash: str,
    response: dict[str, Any],
    occurred_at: datetime,
) -> None:
    session.add(
        CommandReceipt(
            command_receipt_id=uuid7(),
            actor_id=actor.user_id,
            command_type=command_type,
            idempotency_key=str(idempotency_key),
            request_hash=request_hash,
            response=response,
            created_at=occurred_at,
        )
    )


def _add_audit(
    session: AsyncSession,
    *,
    event_type: str,
    actor: Actor,
    acting_role: Role,
    object_type: str,
    object_key: dict[str, object],
    detail: dict[str, object],
    request_id: str,
    occurred_at: datetime,
    annual_budget_id: uuid.UUID | None = None,
    budget_revision_id: uuid.UUID | None = None,
) -> None:
    session.add(
        AuditEvent(
            event_id=uuid7(),
            event_type=event_type,
            annual_budget_id=annual_budget_id,
            budget_revision_id=budget_revision_id,
            actor_id=actor.user_id,
            acting_role=acting_role.value,
            role_snapshot=_role_snapshot(actor),
            object_type=object_type,
            object_key=object_key,
            detail=detail,
            request_id=request_id,
            occurred_at=occurred_at,
        )
    )


def _source_response(
    snapshot: SourceSnapshot,
    components: list[SourceSnapshotComponent],
    reconciliations: list[SourceReconciliation],
) -> SourceSnapshotResponse:
    return SourceSnapshotResponse(
        source_snapshot_id=snapshot.source_snapshot_id,
        department_id=snapshot.department_id,
        cutoff_at=snapshot.cutoff_at,
        status=snapshot.status,
        content_hash=snapshot.content_hash,
        created_by=snapshot.created_by,
        created_at=snapshot.created_at,
        reconciled_at=snapshot.reconciled_at,
        frozen_at=snapshot.frozen_at,
        components=[
            SourceComponentResponse(
                source_snapshot_component_id=item.source_snapshot_component_id,
                dataset_id=item.dataset_id,
                dataset_name=item.dataset_name,
                partition_value=item.partition_value,
                task_batch_id=item.task_batch_id,
                extracted_at=item.extracted_at,
                row_count=item.row_count,
                field_mapping_version=item.field_mapping_version,
                content_hash=item.content_hash,
            )
            for item in sorted(components, key=lambda value: (value.dataset_id, value.partition_value))
        ],
        reconciliations=[
            ReconciliationResponse(
                source_reconciliation_id=item.source_reconciliation_id,
                metric_code=item.metric_code,
                left_source=item.left_source,
                right_source=item.right_source,
                left_numerator=item.left_numerator,
                left_denominator=item.left_denominator,
                right_numerator=item.right_numerator,
                right_denominator=item.right_denominator,
                passed=item.passed,
                evidence=item.evidence,
                checked_at=item.checked_at,
            )
            for item in sorted(reconciliations, key=lambda value: value.metric_code)
        ],
    )


def _parameter_response(
    version: ParameterVersion, components: list[ParameterVersionComponent]
) -> ParameterVersionResponse:
    return ParameterVersionResponse(
        parameter_version_id=version.parameter_version_id,
        source_snapshot_id=version.source_snapshot_id,
        parent_version_id=version.parent_version_id,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        components=[
            ParameterComponentResponse(
                parameter_version_component_id=item.parameter_version_component_id,
                component_type=item.component_type,
                component_version_id=item.component_version_id,
                content_hash=item.content_hash,
                schema_version=item.schema_version,
                record_count=item.record_count,
            )
            for item in sorted(components, key=lambda value: value.component_type)
        ],
    )


def _rate_reference_version_response(version: RateReferenceVersion) -> RateReferenceVersionResponse:
    return RateReferenceVersionResponse(
        rate_reference_version_id=version.rate_reference_version_id,
        source_snapshot_id=version.source_snapshot_id,
        target_fiscal_year=version.target_fiscal_year,
        minimum_sample_size=version.minimum_sample_size,
        maturity_evidence=version.maturity_evidence,
        reference_count=version.reference_count,
        exact_count=version.exact_count,
        fallback_count=version.fallback_count,
        manual_required_count=version.manual_required_count,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
    )


def _rate_reference_response(reference: RateReference) -> RateReferenceResponse:
    return RateReferenceResponse(
        rate_reference_id=reference.rate_reference_id,
        rate_reference_version_id=reference.rate_reference_version_id,
        rate_type=reference.rate_type,
        source_fiscal_season=reference.source_fiscal_season,
        target_fiscal_season=reference.target_fiscal_season,
        quarter_name=reference.quarter_name,
        track=reference.track,
        product=reference.product,
        grade=reference.grade,
        campus=reference.campus,
        renewal_relation_code=reference.renewal_relation_code,
        exact_numerator=reference.exact_numerator,
        exact_denominator=reference.exact_denominator,
        reference_numerator=reference.reference_numerator,
        reference_denominator=reference.reference_denominator,
        exact_rate_ppm=reference.exact_rate_ppm,
        reference_rate_ppm=reference.reference_rate_ppm,
        minimum_sample_size=reference.minimum_sample_size,
        source_type=reference.source_type,
        fallback_reason=reference.fallback_reason,
        system_value_ppm=reference.system_value_ppm,
        content_hash=reference.content_hash,
    )


def _annual_budget_response(budget: AnnualBudget) -> AnnualBudgetResponse:
    return AnnualBudgetResponse(
        annual_budget_id=budget.annual_budget_id,
        department_id=budget.department_id,
        fiscal_year=budget.fiscal_year,
        current_draft_revision_id=budget.current_draft_revision_id,
        current_published_revision_id=budget.current_published_revision_id,
        created_by=budget.created_by,
        created_at=budget.created_at,
    )


def _revision_response(revision: BudgetRevision) -> RevisionResponse:
    if revision.source_snapshot_id is None or revision.parameter_version_id is None:
        raise DomainError(
            code="LEGACY_REVISION_INCOMPLETE",
            message="该历史修订尚未绑定来源快照和参数版本",
            status_code=409,
        )
    return RevisionResponse(
        budget_revision_id=revision.budget_revision_id,
        annual_budget_id=revision.annual_budget_id,
        revision_no=revision.revision_no,
        parent_revision_id=revision.parent_revision_id,
        command_type=revision.command_type,
        source_snapshot_id=revision.source_snapshot_id,
        parameter_version_id=revision.parameter_version_id,
        revision_content_hash=revision.revision_content_hash,
        component_manifest=revision.component_manifest,
        created_by=revision.created_by,
        created_at=revision.created_at,
    )


async def enqueue_source_snapshot_build(
    session: AsyncSession,
    *,
    actor: Actor,
    payload: SourceSnapshotBuildRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> SourceSnapshotBuildResponse:
    command_type = "BUILD_SOURCE_SNAPSHOT"
    request_hash = content_hash(payload.model_dump(mode="json"))
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return SourceSnapshotBuildResponse.model_validate(replay)
        occurred_at = _now()
        job_id = uuid7()
        job = AsyncJob(
            job_id=job_id,
            job_type=command_type,
            dedupe_key=f"source-build:{actor.user_id}:{idempotency_key}",
            payload={
                "schema_version": "1.0",
                "department_id": actor.department_id,
                "fiscal_year": payload.fiscal_year,
                "actor_id": actor.user_id,
                "roles": sorted(role.value for role in actor.roles),
                "request_id": request_id,
            },
            result=None,
            status="QUEUED",
            priority=100,
            attempt_no=0,
            available_at=occurred_at,
            locked_by=None,
            heartbeat_at=None,
            timeout_at=None,
            last_error_code=None,
            last_error_safe=None,
            created_at=occurred_at,
            finished_at=None,
        )
        session.add(job)
        response = SourceSnapshotBuildResponse(
            job_id=job_id,
            status="QUEUED",
            links={"job": f"/api/v1/jobs/{job_id}"},
        )
        _add_audit(
            session,
            event_type="SOURCE_SNAPSHOT_BUILD_QUEUED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="ASYNC_JOB",
            object_key={"job_id": str(job_id)},
            detail={"fiscal_year": payload.fiscal_year, "job_type": command_type},
            request_id=request_id,
            occurred_at=occurred_at,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response.model_dump(mode="json"),
            occurred_at=occurred_at,
        )
    return response


async def get_job(session: AsyncSession, *, actor: Actor, job_id: uuid.UUID) -> JobResponse:
    job = await session.get(AsyncJob, job_id)
    if job is None or job.payload.get("department_id") != actor.department_id:
        raise DomainError(code="JOB_NOT_FOUND", message="异步作业不存在", status_code=404)
    return JobResponse(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        result=job.result,
        last_error_code=job.last_error_code,
        last_error_safe=job.last_error_safe,
        attempt_no=job.attempt_no,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


async def create_source_snapshot(
    session: AsyncSession,
    *,
    actor: Actor,
    payload: SourceSnapshotCreate,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> SourceSnapshotResponse:
    command_type = "CREATE_SOURCE_SNAPSHOT"
    request_payload = payload.model_dump(mode="json")
    request_hash = content_hash(request_payload)
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return SourceSnapshotResponse.model_validate(replay)

        occurred_at = _now()
        sorted_components = sorted(
            payload.components, key=lambda item: (item.dataset_id, item.partition_value)
        )
        snapshot_hash = content_hash(
            {
                "schema_version": "1.0",
                "department_id": actor.department_id,
                "cutoff_at": payload.cutoff_at.isoformat(),
                "components": [item.model_dump(mode="json") for item in sorted_components],
            }
        )
        snapshot = SourceSnapshot(
            source_snapshot_id=uuid7(),
            department_id=actor.department_id,
            cutoff_at=payload.cutoff_at,
            status="DRAFT",
            content_hash=snapshot_hash,
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
            reconciled_at=None,
            frozen_at=None,
        )
        components = [
            SourceSnapshotComponent(
                source_snapshot_component_id=uuid7(),
                source_snapshot_id=snapshot.source_snapshot_id,
                **item.model_dump(),
            )
            for item in sorted_components
        ]
        session.add(snapshot)
        session.add_all(components)
        await session.flush()
        response = _source_response(snapshot, components, [])
        response_json = response.model_dump(mode="json")
        _add_audit(
            session,
            event_type="SOURCE_SNAPSHOT_CREATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="SOURCE_SNAPSHOT",
            object_key={"source_snapshot_id": str(snapshot.source_snapshot_id)},
            detail={"content_hash": snapshot_hash, "component_count": len(components)},
            request_id=request_id,
            occurred_at=occurred_at,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response_json,
            occurred_at=occurred_at,
        )
    return response


async def reconcile_source_snapshot(
    session: AsyncSession,
    *,
    actor: Actor,
    source_snapshot_id: uuid.UUID,
    payload: SourceSnapshotReconcile,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> SourceSnapshotResponse:
    command_type = "RECONCILE_SOURCE_SNAPSHOT"
    request_hash = content_hash(
        {"source_snapshot_id": str(source_snapshot_id), **payload.model_dump(mode="json")}
    )
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return SourceSnapshotResponse.model_validate(replay)
        snapshot = await session.scalar(
            select(SourceSnapshot)
            .where(SourceSnapshot.source_snapshot_id == source_snapshot_id)
            .with_for_update()
        )
        _assert_snapshot_access(snapshot, actor)
        assert snapshot is not None
        if snapshot.status not in {"DRAFT", "WAITING_FOR_SUMMARY"}:
            raise DomainError(
                code="SNAPSHOT_STATE_INVALID",
                message="只有草稿或等待宽表快照的来源快照可以对账",
                status_code=409,
                details={"status": snapshot.status},
            )

        mismatches = [
            {
                "metric_code": item.metric_code,
                "left_numerator": str(item.left_numerator),
                "left_denominator": str(item.left_denominator),
                "right_numerator": str(item.right_numerator),
                "right_denominator": str(item.right_denominator),
            }
            for item in payload.checks
            if item.left_numerator != item.right_numerator or item.left_denominator != item.right_denominator
        ]
        if mismatches:
            raise DomainError(
                code="SOURCE_RECONCILIATION_FAILED",
                message="来源分子或分母不一致，不能冻结正式参数",
                status_code=422,
                details={"mismatches": mismatches},
            )

        occurred_at = _now()
        reconciliations = [
            SourceReconciliation(
                source_reconciliation_id=uuid7(),
                source_snapshot_id=source_snapshot_id,
                metric_code=item.metric_code,
                left_source=item.left_source,
                right_source=item.right_source,
                left_numerator=item.left_numerator,
                left_denominator=item.left_denominator,
                right_numerator=item.right_numerator,
                right_denominator=item.right_denominator,
                passed=True,
                evidence=item.evidence,
                checked_at=occurred_at,
            )
            for item in sorted(payload.checks, key=lambda value: value.metric_code)
        ]
        session.add_all(reconciliations)
        snapshot.status = "RECONCILED"
        snapshot.reconciled_at = occurred_at
        await session.flush()
        components = list(
            await session.scalars(
                select(SourceSnapshotComponent).where(
                    SourceSnapshotComponent.source_snapshot_id == source_snapshot_id
                )
            )
        )
        response = _source_response(snapshot, components, reconciliations)
        response_json = response.model_dump(mode="json")
        _add_audit(
            session,
            event_type="SOURCE_SNAPSHOT_RECONCILED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="SOURCE_SNAPSHOT",
            object_key={"source_snapshot_id": str(source_snapshot_id)},
            detail={"check_count": len(reconciliations)},
            request_id=request_id,
            occurred_at=occurred_at,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response_json,
            occurred_at=occurred_at,
        )
    return response


async def freeze_source_snapshot(
    session: AsyncSession,
    *,
    actor: Actor,
    source_snapshot_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> SourceSnapshotResponse:
    command_type = "FREEZE_SOURCE_SNAPSHOT"
    request_hash = content_hash({"source_snapshot_id": str(source_snapshot_id)})
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return SourceSnapshotResponse.model_validate(replay)
        snapshot = await session.scalar(
            select(SourceSnapshot)
            .where(SourceSnapshot.source_snapshot_id == source_snapshot_id)
            .with_for_update()
        )
        _assert_snapshot_access(snapshot, actor)
        assert snapshot is not None
        if snapshot.status != "RECONCILED":
            raise DomainError(
                code="SNAPSHOT_NOT_RECONCILED",
                message="来源快照尚未完成对账，不能冻结",
                status_code=409,
                details={"status": snapshot.status},
            )
        occurred_at = _now()
        snapshot.status = "FROZEN"
        snapshot.frozen_at = occurred_at
        await session.flush()
        components = list(
            await session.scalars(
                select(SourceSnapshotComponent).where(
                    SourceSnapshotComponent.source_snapshot_id == source_snapshot_id
                )
            )
        )
        reconciliations = list(
            await session.scalars(
                select(SourceReconciliation).where(
                    SourceReconciliation.source_snapshot_id == source_snapshot_id
                )
            )
        )
        response = _source_response(snapshot, components, reconciliations)
        response_json = response.model_dump(mode="json")
        _add_audit(
            session,
            event_type="SOURCE_SNAPSHOT_FROZEN",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="SOURCE_SNAPSHOT",
            object_key={"source_snapshot_id": str(source_snapshot_id)},
            detail={"content_hash": snapshot.content_hash},
            request_id=request_id,
            occurred_at=occurred_at,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response_json,
            occurred_at=occurred_at,
        )
    return response


async def get_source_snapshot(
    session: AsyncSession, *, actor: Actor, source_snapshot_id: uuid.UUID
) -> SourceSnapshotResponse:
    snapshot = await session.get(SourceSnapshot, source_snapshot_id)
    _assert_snapshot_access(snapshot, actor)
    assert snapshot is not None
    components = list(
        await session.scalars(
            select(SourceSnapshotComponent).where(
                SourceSnapshotComponent.source_snapshot_id == source_snapshot_id
            )
        )
    )
    reconciliations = list(
        await session.scalars(
            select(SourceReconciliation).where(SourceReconciliation.source_snapshot_id == source_snapshot_id)
        )
    )
    return _source_response(snapshot, components, reconciliations)


def _assert_snapshot_access(snapshot: SourceSnapshot | None, actor: Actor) -> None:
    if snapshot is None or snapshot.department_id != actor.department_id:
        raise DomainError(code="SOURCE_SNAPSHOT_NOT_FOUND", message="来源快照不存在", status_code=404)


async def create_rate_reference_version(
    session: AsyncSession,
    *,
    actor: Actor,
    source_snapshot_id: uuid.UUID,
    payload: RateReferenceGenerateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateReferenceVersionResponse:
    command_type = "CREATE_RATE_REFERENCE_VERSION"
    request_payload = {
        "source_snapshot_id": str(source_snapshot_id),
        **payload.model_dump(mode="json"),
    }
    request_hash = content_hash(request_payload)
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return RateReferenceVersionResponse.model_validate(replay)
        snapshot = await session.get(SourceSnapshot, source_snapshot_id)
        _assert_snapshot_access(snapshot, actor)
        assert snapshot is not None
        if snapshot.status != "FROZEN":
            raise DomainError(
                code="SOURCE_SNAPSHOT_NOT_FROZEN",
                message="只有已冻结来源快照可以生成比例参考版本",
                status_code=409,
                details={"status": snapshot.status},
            )
        aggregate_rows = list(
            await session.scalars(
                select(SourceMetricAggregate).where(
                    SourceMetricAggregate.source_snapshot_id == source_snapshot_id,
                    SourceMetricAggregate.metric_code.in_(
                        ("RENEWAL_RATE_REFERENCE", "NEW_STUDENT_SHARE_REFERENCE")
                    ),
                )
            )
        )
        source_values = [
            SourceRateValue(
                key=SourceRateKey(
                    metric_code=item.metric_code,
                    fiscal_season=item.fiscal_season,
                    quarter_name=item.quarter_name,
                    track=item.track,
                    product=item.product,
                    grade=item.grade,
                    subject=item.subject,
                    campus=item.campus,
                ),
                numerator=item.numerator,
                denominator=item.denominator,
            )
            for item in aggregate_rows
        ]
        source_seasons = {item.key.fiscal_season for item in source_values}
        confirmed_seasons = set(payload.mature_source_fiscal_seasons)
        if source_seasons != confirmed_seasons:
            raise DomainError(
                code="SEASON_MATURITY_EVIDENCE_MISMATCH",
                message="成熟季节声明与冻结快照中的来源季节不一致",
                status_code=409,
                details={
                    "source_seasons": sorted(source_seasons),
                    "confirmed_seasons": sorted(confirmed_seasons),
                },
            )
        try:
            generated = generate_rate_references(
                source_values,
                target_fiscal_year=payload.target_fiscal_year,
                minimum_sample_size=payload.minimum_sample_size,
            )
        except RateReferenceGenerationError as exc:
            raise DomainError(code=exc.code, message=exc.message, status_code=409) from exc

        maturity_evidence: dict[str, object] = {
            "schema_version": "1.0",
            "mature_source_fiscal_seasons": sorted(confirmed_seasons),
            "evidence_reference": payload.maturity_evidence_reference,
            "source_snapshot_id": str(source_snapshot_id),
        }
        version_content = {
            "schema_version": "1.0",
            "source_snapshot_id": str(source_snapshot_id),
            "target_fiscal_year": payload.target_fiscal_year,
            "minimum_sample_size": payload.minimum_sample_size,
            "maturity_evidence": maturity_evidence,
            "references": [item.content() for item in generated],
        }
        occurred_at = _now()
        exact_count = sum(item.source_type == EXACT_HISTORY for item in generated)
        fallback_count = sum(item.source_type == CAMPUS_REMOVED_FALLBACK for item in generated)
        manual_required_count = sum(item.source_type == MANUAL_REQUIRED for item in generated)
        version = RateReferenceVersion(
            rate_reference_version_id=uuid7(),
            source_snapshot_id=source_snapshot_id,
            target_fiscal_year=payload.target_fiscal_year,
            minimum_sample_size=payload.minimum_sample_size,
            maturity_evidence=maturity_evidence,
            reference_count=len(generated),
            exact_count=exact_count,
            fallback_count=fallback_count,
            manual_required_count=manual_required_count,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        references = [
            RateReference(
                rate_reference_id=uuid7(),
                rate_reference_version_id=version.rate_reference_version_id,
                rate_type=item.key.rate_type,
                source_fiscal_season=item.key.source_fiscal_season,
                target_fiscal_season=item.key.target_fiscal_season,
                quarter_name=item.key.quarter_name,
                track=item.key.track,
                product=item.key.product,
                grade=item.key.grade,
                campus=item.key.campus,
                renewal_relation_code=item.renewal_relation_code,
                exact_numerator=item.exact_numerator,
                exact_denominator=item.exact_denominator,
                reference_numerator=item.reference_numerator,
                reference_denominator=item.reference_denominator,
                exact_rate_ppm=item.exact_rate_ppm,
                reference_rate_ppm=item.reference_rate_ppm,
                minimum_sample_size=item.minimum_sample_size,
                source_type=item.source_type,
                fallback_reason=item.fallback_reason,
                system_value_ppm=item.system_value_ppm,
                content_hash=item.content_hash,
            )
            for item in generated
        ]
        session.add(version)
        await session.flush()
        session.add_all(references)
        await session.flush()
        response = _rate_reference_version_response(version)
        response_json = response.model_dump(mode="json")
        _add_audit(
            session,
            event_type="RATE_REFERENCE_VERSION_CREATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="RATE_REFERENCE_VERSION",
            object_key={"rate_reference_version_id": str(version.rate_reference_version_id)},
            detail={
                "source_snapshot_id": str(source_snapshot_id),
                "target_fiscal_year": payload.target_fiscal_year,
                "minimum_sample_size": payload.minimum_sample_size,
                "reference_count": len(generated),
                "exact_count": exact_count,
                "fallback_count": fallback_count,
                "manual_required_count": manual_required_count,
                "content_hash": version.content_hash,
            },
            request_id=request_id,
            occurred_at=occurred_at,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response_json,
            occurred_at=occurred_at,
        )
    return response


async def _assert_rate_reference_access(
    session: AsyncSession, version: RateReferenceVersion | None, actor: Actor
) -> None:
    if version is None:
        raise DomainError(
            code="RATE_REFERENCE_VERSION_NOT_FOUND", message="比例参考版本不存在", status_code=404
        )
    snapshot = await session.get(SourceSnapshot, version.source_snapshot_id)
    _assert_snapshot_access(snapshot, actor)


async def get_rate_reference_version(
    session: AsyncSession, *, actor: Actor, rate_reference_version_id: uuid.UUID
) -> RateReferenceVersionResponse:
    version = await session.get(RateReferenceVersion, rate_reference_version_id)
    await _assert_rate_reference_access(session, version, actor)
    assert version is not None
    return _rate_reference_version_response(version)


async def list_rate_references(
    session: AsyncSession,
    *,
    actor: Actor,
    rate_reference_version_id: uuid.UUID,
    limit: int,
    cursor: uuid.UUID | None,
) -> RateReferencePageResponse:
    version = await session.get(RateReferenceVersion, rate_reference_version_id)
    await _assert_rate_reference_access(session, version, actor)
    query = select(RateReference).where(RateReference.rate_reference_version_id == rate_reference_version_id)
    if cursor is not None:
        query = query.where(RateReference.rate_reference_id > cursor)
    rows = list(await session.scalars(query.order_by(RateReference.rate_reference_id).limit(limit + 1)))
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    return RateReferencePageResponse(
        items=[_rate_reference_response(item) for item in page_rows],
        next_cursor=page_rows[-1].rate_reference_id if has_more else None,
    )


async def create_parameter_version(
    session: AsyncSession,
    *,
    actor: Actor,
    payload: ParameterVersionCreate,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> ParameterVersionResponse:
    command_type = "CREATE_PARAMETER_VERSION"
    request_payload = payload.model_dump(mode="json")
    request_hash = content_hash(request_payload)
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return ParameterVersionResponse.model_validate(replay)
        snapshot = await session.get(SourceSnapshot, payload.source_snapshot_id)
        _assert_snapshot_access(snapshot, actor)
        assert snapshot is not None
        if snapshot.status != "FROZEN":
            raise DomainError(
                code="SOURCE_SNAPSHOT_NOT_FROZEN",
                message="只有已冻结来源快照可以生成正式参数版本",
                status_code=409,
                details={"status": snapshot.status},
            )
        if payload.parent_version_id is not None:
            parent = await session.get(ParameterVersion, payload.parent_version_id)
            await _assert_parameter_access(session, parent, actor)

        occurred_at = _now()
        sorted_components = sorted(payload.components, key=lambda item: item.component_type.value)
        version_hash = content_hash(
            {
                "schema_version": "1.0",
                "source_snapshot_id": str(payload.source_snapshot_id),
                "parent_version_id": str(payload.parent_version_id) if payload.parent_version_id else None,
                "components": [item.model_dump(mode="json") for item in sorted_components],
            }
        )
        version = ParameterVersion(
            parameter_version_id=uuid7(),
            source_snapshot_id=payload.source_snapshot_id,
            parent_version_id=payload.parent_version_id,
            content_hash=version_hash,
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        components = [
            ParameterVersionComponent(
                parameter_version_component_id=uuid7(),
                parameter_version_id=version.parameter_version_id,
                **item.model_dump(),
            )
            for item in sorted_components
        ]
        session.add(version)
        session.add_all(components)
        await session.flush()
        response = _parameter_response(version, components)
        response_json = response.model_dump(mode="json")
        _add_audit(
            session,
            event_type="PARAMETER_VERSION_CREATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="PARAMETER_VERSION",
            object_key={"parameter_version_id": str(version.parameter_version_id)},
            detail={
                "source_snapshot_id": str(version.source_snapshot_id),
                "content_hash": version.content_hash,
            },
            request_id=request_id,
            occurred_at=occurred_at,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response_json,
            occurred_at=occurred_at,
        )
    return response


async def _assert_parameter_access(
    session: AsyncSession, parameter: ParameterVersion | None, actor: Actor
) -> None:
    if parameter is None:
        raise DomainError(code="PARAMETER_VERSION_NOT_FOUND", message="参数版本不存在", status_code=404)
    snapshot = await session.get(SourceSnapshot, parameter.source_snapshot_id)
    _assert_snapshot_access(snapshot, actor)


async def get_parameter_version(
    session: AsyncSession, *, actor: Actor, parameter_version_id: uuid.UUID
) -> ParameterVersionResponse:
    version = await session.get(ParameterVersion, parameter_version_id)
    await _assert_parameter_access(session, version, actor)
    assert version is not None
    components = list(
        await session.scalars(
            select(ParameterVersionComponent).where(
                ParameterVersionComponent.parameter_version_id == parameter_version_id
            )
        )
    )
    return _parameter_response(version, components)


async def create_annual_budget(
    session: AsyncSession,
    *,
    actor: Actor,
    payload: AnnualBudgetCreate,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RevisionCommandResponse:
    command_type = "CREATE_ANNUAL_BUDGET"
    request_hash = content_hash(payload.model_dump(mode="json"))
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return RevisionCommandResponse.model_validate(replay)
        existing = await session.scalar(
            select(AnnualBudget).where(
                AnnualBudget.department_id == actor.department_id,
                AnnualBudget.fiscal_year == payload.fiscal_year,
            )
        )
        if existing is not None:
            raise DomainError(
                code="ANNUAL_BUDGET_ALREADY_EXISTS",
                message="该部门财年预算链已存在",
                status_code=409,
                details={"annual_budget_id": str(existing.annual_budget_id)},
            )
        parameter = await session.get(ParameterVersion, payload.parameter_version_id)
        await _assert_parameter_access(session, parameter, actor)
        assert parameter is not None
        snapshot = await session.get(SourceSnapshot, parameter.source_snapshot_id)
        assert snapshot is not None
        if snapshot.status != "FROZEN":
            raise DomainError(
                code="SOURCE_SNAPSHOT_NOT_FROZEN",
                message="首个预算修订必须绑定已冻结来源快照",
                status_code=409,
            )

        occurred_at = _now()
        annual_budget_id = uuid7()
        revision_id = uuid7()
        budget = AnnualBudget(
            annual_budget_id=annual_budget_id,
            department_id=actor.department_id,
            fiscal_year=payload.fiscal_year,
            current_draft_revision_id=None,
            current_published_revision_id=None,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(budget)
        await session.flush()
        manifest: dict[str, object] = {
            "schema_version": "1.0",
            "source_snapshot_id": str(snapshot.source_snapshot_id),
            "parameter_version_id": str(parameter.parameter_version_id),
        }
        revision = BudgetRevision(
            budget_revision_id=revision_id,
            annual_budget_id=annual_budget_id,
            revision_no=1,
            parent_revision_id=None,
            command_type=command_type,
            idempotency_key=str(idempotency_key),
            request_hash=request_hash,
            source_snapshot_id=snapshot.source_snapshot_id,
            parameter_version_id=parameter.parameter_version_id,
            revision_content_hash=content_hash(manifest),
            component_manifest=manifest,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(revision)
        await session.flush()
        budget.current_draft_revision_id = revision_id
        session.add(
            RevisionLifecycleEvent(
                revision_lifecycle_event_id=uuid7(),
                budget_revision_id=revision_id,
                event_type="DRAFT_CREATED",
                actor_id=actor.user_id,
                detail={"schema_version": "1.0", "command_type": command_type},
                occurred_at=occurred_at,
            )
        )
        _add_audit(
            session,
            event_type="ANNUAL_BUDGET_CREATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="ANNUAL_BUDGET",
            object_key={"annual_budget_id": str(annual_budget_id)},
            detail={"fiscal_year": payload.fiscal_year, "revision_no": 1},
            request_id=request_id,
            occurred_at=occurred_at,
            annual_budget_id=annual_budget_id,
            budget_revision_id=revision_id,
        )
        response = RevisionCommandResponse(
            annual_budget_id=annual_budget_id,
            previous_revision_id=None,
            new_revision_id=revision_id,
            links={
                "revision": f"/api/v1/revisions/{revision_id}",
                "annual_budget": f"/api/v1/annual-budgets/{annual_budget_id}",
            },
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response.model_dump(mode="json"),
            occurred_at=occurred_at,
        )
    return response


async def derive_revision(
    session: AsyncSession,
    *,
    actor: Actor,
    parent_revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RevisionDerive,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RevisionCommandResponse:
    command_type = "DERIVE_BUDGET_REVISION"
    request_hash = content_hash(
        {
            "parent_revision_id": str(parent_revision_id),
            "expected_revision_id": str(expected_revision_id),
            **payload.model_dump(mode="json"),
        }
    )
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return RevisionCommandResponse.model_validate(replay)
        if expected_revision_id != parent_revision_id:
            raise DomainError(
                code="REVISION_CONFLICT",
                message="If-Match 与路径中的预算修订不一致",
                status_code=409,
            )
        parent = await session.get(BudgetRevision, parent_revision_id)
        if parent is None:
            raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
        budget = await session.scalar(
            select(AnnualBudget)
            .where(AnnualBudget.annual_budget_id == parent.annual_budget_id)
            .with_for_update()
        )
        _assert_budget_access(budget, actor)
        assert budget is not None
        if budget.current_draft_revision_id != expected_revision_id:
            raise DomainError(
                code="REVISION_CONFLICT",
                message="预算草稿已产生新修订",
                status_code=409,
                details={"latest_revision_id": str(budget.current_draft_revision_id)},
            )

        parameter_version_id = payload.parameter_version_id or parent.parameter_version_id
        if parameter_version_id is None:
            raise DomainError(
                code="PARAMETER_VERSION_REQUIRED",
                message="新修订必须绑定参数版本",
                status_code=422,
            )
        parameter = await session.get(ParameterVersion, parameter_version_id)
        await _assert_parameter_access(session, parameter, actor)
        assert parameter is not None
        snapshot = await session.get(SourceSnapshot, parameter.source_snapshot_id)
        assert snapshot is not None
        if snapshot.status != "FROZEN":
            raise DomainError(
                code="SOURCE_SNAPSHOT_NOT_FROZEN",
                message="新修订必须绑定已冻结来源快照",
                status_code=409,
            )

        occurred_at = _now()
        revision_id = uuid7()
        manifest: dict[str, object] = {
            **parent.component_manifest,
            "schema_version": "1.0",
            "source_snapshot_id": str(snapshot.source_snapshot_id),
            "parameter_version_id": str(parameter.parameter_version_id),
        }
        latest_revision_no = await session.scalar(
            select(func.max(BudgetRevision.revision_no)).where(
                BudgetRevision.annual_budget_id == budget.annual_budget_id
            )
        )
        revision = BudgetRevision(
            budget_revision_id=revision_id,
            annual_budget_id=budget.annual_budget_id,
            revision_no=(latest_revision_no or 0) + 1,
            parent_revision_id=parent_revision_id,
            command_type=command_type,
            idempotency_key=str(idempotency_key),
            request_hash=request_hash,
            source_snapshot_id=snapshot.source_snapshot_id,
            parameter_version_id=parameter.parameter_version_id,
            revision_content_hash=content_hash(manifest),
            component_manifest=manifest,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(revision)
        await session.flush()
        budget.current_draft_revision_id = revision_id
        session.add(
            RevisionLifecycleEvent(
                revision_lifecycle_event_id=uuid7(),
                budget_revision_id=revision_id,
                event_type="DRAFT_CREATED",
                actor_id=actor.user_id,
                detail={"schema_version": "1.0", "reason": payload.reason},
                occurred_at=occurred_at,
            )
        )
        _add_audit(
            session,
            event_type="BUDGET_REVISION_DERIVED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="BUDGET_REVISION",
            object_key={"budget_revision_id": str(revision_id)},
            detail={
                "previous_revision_id": str(parent_revision_id),
                "reason": payload.reason,
                "revision_content_hash": revision.revision_content_hash,
            },
            request_id=request_id,
            occurred_at=occurred_at,
            annual_budget_id=budget.annual_budget_id,
            budget_revision_id=revision_id,
        )
        response = RevisionCommandResponse(
            annual_budget_id=budget.annual_budget_id,
            previous_revision_id=parent_revision_id,
            new_revision_id=revision_id,
            links={
                "revision": f"/api/v1/revisions/{revision_id}",
                "annual_budget": f"/api/v1/annual-budgets/{budget.annual_budget_id}",
            },
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response.model_dump(mode="json"),
            occurred_at=occurred_at,
        )
    return response


def _assert_budget_access(budget: AnnualBudget | None, actor: Actor) -> None:
    if budget is None or budget.department_id != actor.department_id:
        raise DomainError(code="ANNUAL_BUDGET_NOT_FOUND", message="年度预算链不存在", status_code=404)


async def get_annual_budget(
    session: AsyncSession, *, actor: Actor, annual_budget_id: uuid.UUID
) -> AnnualBudgetResponse:
    budget = await session.get(AnnualBudget, annual_budget_id)
    _assert_budget_access(budget, actor)
    assert budget is not None
    return _annual_budget_response(budget)


async def find_annual_budget(
    session: AsyncSession, *, actor: Actor, department_id: str, fiscal_year: int
) -> AnnualBudgetResponse:
    if department_id != actor.department_id:
        raise DomainError(code="ANNUAL_BUDGET_NOT_FOUND", message="年度预算链不存在", status_code=404)
    budget = await session.scalar(
        select(AnnualBudget).where(
            AnnualBudget.department_id == department_id,
            AnnualBudget.fiscal_year == fiscal_year,
        )
    )
    _assert_budget_access(budget, actor)
    assert budget is not None
    return _annual_budget_response(budget)


async def get_revision(session: AsyncSession, *, actor: Actor, revision_id: uuid.UUID) -> RevisionResponse:
    revision = await session.get(BudgetRevision, revision_id)
    if revision is None:
        raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
    budget = await session.get(AnnualBudget, revision.annual_budget_id)
    _assert_budget_access(budget, actor)
    return _revision_response(revision)


async def list_revisions(
    session: AsyncSession, *, actor: Actor, annual_budget_id: uuid.UUID
) -> list[RevisionResponse]:
    budget = await session.get(AnnualBudget, annual_budget_id)
    _assert_budget_access(budget, actor)
    revisions = list(
        await session.scalars(
            select(BudgetRevision)
            .where(BudgetRevision.annual_budget_id == annual_budget_id)
            .order_by(BudgetRevision.revision_no, BudgetRevision.budget_revision_id)
        )
    )
    return [_revision_response(item) for item in revisions]
