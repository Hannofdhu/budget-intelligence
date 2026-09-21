import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    BudgetTargetInput,
    BudgetTargetResponse,
    RevisionCommandResponse,
    TargetClosureResultResponse,
    TargetClosureResultVersionResponse,
    TargetClosureValidateRequest,
    TargetSetReplaceAndLockRequest,
    TargetSetVersionResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.entry_planning import (
    _assert_version_access,
    _create_child_revision,
    _lock_current_revision,
    _manifest_uuid,
    _revision_link_response,
)
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    BudgetCombination,
    BudgetRevision,
    BudgetTarget,
    RevenueAggregateResult,
    RevenueResultVersion,
    TargetClosureResult,
    TargetClosureResultVersion,
    TargetSetVersion,
)
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)
from packages.target_closure.calculation import (
    MONEY_QUANTUM,
    TARGET_CLOSURE_RULE_VERSION,
    TARGET_TOLERANCE_YUAN,
    TargetClosureInvariantError,
    TargetClosureItem,
    TargetRevenueInput,
    calculate_target_closure,
)

TARGET_DOWNSTREAM_REMOVALS = (
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)
TARGET_CLOSURE_DOWNSTREAM_REMOVALS = (
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)


def target_set_batch_content_hash(payload: TargetSetReplaceAndLockRequest) -> str:
    return content_hash(_canonical_target_set(payload))


def _canonical_target_set(payload: TargetSetReplaceAndLockRequest) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "items": [
            {
                "scope_level": item.scope_level,
                "track": item.track.strip() if item.track else None,
                "target_revenue_yuan": format(item.target_revenue_yuan.quantize(MONEY_QUANTUM), "f"),
                "tolerance_yuan": format(TARGET_TOLERANCE_YUAN, "f"),
                "source_evidence": item.source_evidence,
                "change_reason": item.change_reason.strip(),
            }
            for item in sorted(payload.items, key=_target_input_sort_key)
        ],
    }


async def replace_and_lock_target_set(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: TargetSetReplaceAndLockRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> TargetSetVersionResponse:
    command_type = "REPLACE_AND_LOCK_TARGET_SET"
    request_hash = content_hash(
        {
            "revision_id": str(revision_id),
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
            return TargetSetVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        expected_hash = target_set_batch_content_hash(payload)
        if expected_hash != payload.batch_content_hash:
            raise DomainError(
                code="TARGET_SET_BATCH_HASH_MISMATCH",
                message="学校版目标整批内容哈希不一致",
                status_code=409,
            )
        await _require_target_tracks_match_combinations(session, parent, payload)
        target_content = [
            _target_content(item, budget.department_id, budget.fiscal_year)
            for item in sorted(payload.items, key=_target_input_sort_key)
        ]
        occurred_at = _now()
        version_id = uuid7()
        parent_version = await _current_target_set_version(session, parent)
        version_content = {
            "schema_version": "1.0",
            "fiscal_year": budget.fiscal_year,
            "lock_status": "LOCKED",
            "targets": target_content,
        }
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates={"target_set_version_id": str(version_id)},
            component_removals=TARGET_DOWNSTREAM_REMOVALS,
        )
        version = TargetSetVersion(
            target_set_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            parent_target_set_version_id=(
                parent_version.target_set_version_id if parent_version is not None else None
            ),
            fiscal_year=budget.fiscal_year,
            lock_status="LOCKED",
            record_count=len(target_content),
            batch_content_hash=payload.batch_content_hash,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            locked_by=actor.user_id,
            role_snapshot=_role_snapshot(actor),
            locked_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows = [
            BudgetTarget(
                budget_target_id=uuid7(),
                target_set_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in target_content
        ]
        session.add_all(rows)
        response = _target_set_response(version, rows, revision_response)
        _add_audit(
            session,
            event_type="TARGET_SET_REPLACED_AND_LOCKED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="TARGET_SET_VERSION",
            object_key={"target_set_version_id": str(version_id)},
            detail={
                "content_hash": version.content_hash,
                "batch_content_hash": version.batch_content_hash,
                "record_count": version.record_count,
            },
            request_id=request_id,
            occurred_at=occurred_at,
            annual_budget_id=budget.annual_budget_id,
            budget_revision_id=revision.budget_revision_id,
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


async def get_target_set_version(
    session: AsyncSession,
    *,
    actor: Actor,
    target_set_version_id: uuid.UUID,
) -> TargetSetVersionResponse:
    version = await session.get(TargetSetVersion, target_set_version_id)
    if version is None:
        raise DomainError(
            code="TARGET_SET_VERSION_NOT_FOUND", message="学校版目标版本不存在", status_code=404
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = await _load_target_rows(session, version)
    return _target_set_response(version, rows, _revision_link_response(revision))


async def validate_target_closure(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: TargetClosureValidateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
    acting_role: Role = Role.DATA_MAINTAINER,
) -> TargetClosureResultVersionResponse:
    command_type = "VALIDATE_TARGET_CLOSURE"
    request_hash = content_hash(
        {
            "revision_id": str(revision_id),
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
            return TargetClosureResultVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        target_version_id = _manifest_uuid(parent, "target_set_version_id", "当前修订缺少学校版目标版本")
        revenue_version_id = _manifest_uuid(parent, "revenue_result_version_id", "当前修订缺少收入结果版本")
        target_version = await session.get(TargetSetVersion, target_version_id)
        revenue_version = await session.get(RevenueResultVersion, revenue_version_id)
        if target_version is None or revenue_version is None:
            raise DomainError(
                code="TARGET_CLOSURE_UPSTREAM_NOT_FOUND",
                message="目标闭合校验的学校版目标或收入结果版本不存在",
                status_code=404,
            )
        if (
            target_version.fiscal_year != budget.fiscal_year
            or revenue_version.fiscal_year != budget.fiscal_year
        ):
            raise DomainError(
                code="TARGET_CLOSURE_FISCAL_YEAR_MISMATCH",
                message="目标闭合校验的上游版本与当前预算财年不一致",
                status_code=409,
            )
        target_rows = await _load_target_rows(session, target_version)
        aggregate_rows = list(
            (
                await session.scalars(
                    select(RevenueAggregateResult).where(
                        RevenueAggregateResult.revenue_result_version_id == revenue_version_id,
                        RevenueAggregateResult.period_type == "ANNUAL",
                    )
                )
            ).all()
        )
        aggregate_by_scope = {(row.scope_level, row.track): row for row in aggregate_rows}
        target_scopes = {(row.scope_level, row.track) for row in target_rows}
        aggregate_scopes = set(aggregate_by_scope)
        if target_scopes != aggregate_scopes:
            raise DomainError(
                code="TARGET_REVENUE_SCOPE_MISMATCH",
                message="学校版目标范围与年度收入汇总范围不一致",
                status_code=409,
                details={
                    "targets_without_revenue": sorted(
                        _scope_label(item) for item in target_scopes - aggregate_scopes
                    ),
                    "revenue_without_targets": sorted(
                        _scope_label(item) for item in aggregate_scopes - target_scopes
                    ),
                },
            )
        try:
            calculation = calculate_target_closure(
                [
                    TargetRevenueInput(
                        target_id=str(row.budget_target_id),
                        scope_level=row.scope_level,
                        track=row.track,
                        target_revenue_yuan=row.target_revenue_yuan,
                        calculated_revenue_yuan=aggregate_by_scope[
                            (row.scope_level, row.track)
                        ].recognized_revenue_yuan,
                    )
                    for row in target_rows
                ]
            )
        except TargetClosureInvariantError as exc:
            raise DomainError(code=exc.code, message=exc.message, status_code=422) from exc
        target_by_id = {str(row.budget_target_id): row for row in target_rows}
        row_content = [
            _closure_content(item, target_by_id[item.source.target_id]) for item in calculation.items
        ]
        input_fingerprint = content_hash(
            {
                "schema_version": "1.0",
                "calculation_rule_version": TARGET_CLOSURE_RULE_VERSION,
                "target_set_version": target_version.content_hash,
                "revenue_result_version": revenue_version.content_hash,
            }
        )
        publishable = calculation.targets_within_tolerance and revenue_version.publishable
        version_content = {
            "schema_version": "1.0",
            "input_fingerprint": input_fingerprint,
            "calculation_rule_version": TARGET_CLOSURE_RULE_VERSION,
            "closure_status": calculation.closure_status,
            "upstream_revenue_publishable": revenue_version.publishable,
            "publishable": publishable,
            "rows": row_content,
        }
        occurred_at = _now()
        version_id = uuid7()
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates={"target_closure_result_version_id": str(version_id)},
            component_removals=TARGET_CLOSURE_DOWNSTREAM_REMOVALS,
        )
        version = TargetClosureResultVersion(
            target_closure_result_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            target_set_version_id=target_version_id,
            revenue_result_version_id=revenue_version_id,
            fiscal_year=budget.fiscal_year,
            calculation_rule_version=TARGET_CLOSURE_RULE_VERSION,
            status="SUCCEEDED",
            closure_status=calculation.closure_status,
            upstream_revenue_publishable=revenue_version.publishable,
            publishable=publishable,
            record_count=len(row_content),
            input_fingerprint=input_fingerprint,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows = [
            TargetClosureResult(
                target_closure_result_id=uuid7(),
                target_closure_result_version_id=version_id,
                budget_target_id=uuid.UUID(str(item["budget_target_id"])),
                **{key: value for key, value in item.items() if key != "budget_target_id"},
                content_hash=content_hash(item),
            )
            for item in row_content
        ]
        session.add_all(rows)
        response = _closure_version_response(version, rows, revision_response)
        _add_audit(
            session,
            event_type="TARGET_CLOSURE_VALIDATED",
            actor=actor,
            acting_role=acting_role,
            object_type="TARGET_CLOSURE_RESULT_VERSION",
            object_key={"target_closure_result_version_id": str(version_id)},
            detail={
                "input_fingerprint": input_fingerprint,
                "closure_status": calculation.closure_status,
                "publishable": publishable,
            },
            request_id=request_id,
            occurred_at=occurred_at,
            annual_budget_id=budget.annual_budget_id,
            budget_revision_id=revision.budget_revision_id,
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


async def get_target_closure_result_version(
    session: AsyncSession,
    *,
    actor: Actor,
    target_closure_result_version_id: uuid.UUID,
) -> TargetClosureResultVersionResponse:
    version = await session.get(TargetClosureResultVersion, target_closure_result_version_id)
    if version is None:
        raise DomainError(
            code="TARGET_CLOSURE_RESULT_VERSION_NOT_FOUND",
            message="目标闭合校验结果版本不存在",
            status_code=404,
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        (
            await session.scalars(
                select(TargetClosureResult).where(
                    TargetClosureResult.target_closure_result_version_id == target_closure_result_version_id
                )
            )
        ).all()
    )
    return _closure_version_response(version, rows, _revision_link_response(revision))


async def _current_target_set_version(
    session: AsyncSession, revision: BudgetRevision
) -> TargetSetVersion | None:
    raw = revision.component_manifest.get("target_set_version_id")
    if not isinstance(raw, str):
        return None
    try:
        result: TargetSetVersion | None = await session.get(TargetSetVersion, uuid.UUID(raw))
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="预算修订组件清单无效", status_code=409
        ) from exc
    return result


async def _require_target_tracks_match_combinations(
    session: AsyncSession,
    revision: BudgetRevision,
    payload: TargetSetReplaceAndLockRequest,
) -> None:
    raw = revision.component_manifest.get("combination_set_version_id")
    if not isinstance(raw, str):
        return
    try:
        version_id = uuid.UUID(raw)
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="预算修订组件清单无效", status_code=409
        ) from exc
    actual_tracks = set(
        await session.scalars(
            select(BudgetCombination.track).where(
                BudgetCombination.combination_set_version_id == version_id,
                BudgetCombination.enabled.is_(True),
            )
        )
    )
    target_tracks = {item.track.strip() for item in payload.items if item.track is not None}
    if actual_tracks != target_tracks:
        raise DomainError(
            code="TARGET_TRACK_SET_MISMATCH",
            message="学校版赛道目标必须完整覆盖当前已启用预算组合的赛道",
            status_code=422,
            details={
                "missing_tracks": sorted(actual_tracks - target_tracks),
                "unexpected_tracks": sorted(target_tracks - actual_tracks),
            },
        )


async def _load_target_rows(session: AsyncSession, version: TargetSetVersion) -> list[BudgetTarget]:
    return list(
        (
            await session.scalars(
                select(BudgetTarget).where(
                    BudgetTarget.target_set_version_id == version.target_set_version_id
                )
            )
        ).all()
    )


def _target_content(item: BudgetTargetInput, department_id: str, fiscal_year: int) -> dict[str, object]:
    track = item.track.strip() if item.track else None
    target_revenue = item.target_revenue_yuan.quantize(MONEY_QUANTUM)
    return {
        "target_stable_key": content_hash(
            {
                "schema_version": "1.0",
                "department_id": department_id,
                "fiscal_year": fiscal_year,
                "scope_level": item.scope_level,
                "track": track,
            }
        ),
        "scope_level": item.scope_level,
        "track": track,
        "target_revenue_yuan": target_revenue,
        "tolerance_yuan": TARGET_TOLERANCE_YUAN,
        "source_evidence": item.source_evidence,
        "change_reason": item.change_reason.strip(),
    }


def _closure_content(item: TargetClosureItem, target: BudgetTarget) -> dict[str, object]:
    return {
        "budget_target_id": str(target.budget_target_id),
        "target_stable_key": target.target_stable_key,
        "scope_level": target.scope_level,
        "track": target.track,
        "target_revenue_yuan": item.target_revenue_yuan,
        "calculated_revenue_yuan": item.calculated_revenue_yuan,
        "variance_yuan": item.variance_yuan,
        "tolerance_yuan": item.tolerance_yuan,
        "check_status": item.check_status,
    }


def _target_set_response(
    version: TargetSetVersion,
    rows: list[BudgetTarget],
    revision: RevisionCommandResponse,
) -> TargetSetVersionResponse:
    return TargetSetVersionResponse(
        target_set_version_id=version.target_set_version_id,
        budget_revision_id=version.budget_revision_id,
        parent_target_set_version_id=version.parent_target_set_version_id,
        fiscal_year=version.fiscal_year,
        lock_status=version.lock_status,
        record_count=version.record_count,
        batch_content_hash=version.batch_content_hash,
        content_hash=version.content_hash,
        locked_by=version.locked_by,
        role_snapshot=version.role_snapshot,
        locked_at=version.locked_at,
        items=[_target_response(item) for item in sorted(rows, key=_target_row_sort_key)],
        revision=revision,
    )


def _target_response(row: BudgetTarget) -> BudgetTargetResponse:
    return BudgetTargetResponse(
        budget_target_id=row.budget_target_id,
        target_stable_key=row.target_stable_key,
        scope_level=row.scope_level,
        track=row.track,
        target_revenue_yuan=row.target_revenue_yuan,
        tolerance_yuan=row.tolerance_yuan,
        source_evidence=row.source_evidence,
        change_reason=row.change_reason,
        content_hash=row.content_hash,
    )


def _closure_version_response(
    version: TargetClosureResultVersion,
    rows: list[TargetClosureResult],
    revision: RevisionCommandResponse,
) -> TargetClosureResultVersionResponse:
    return TargetClosureResultVersionResponse(
        target_closure_result_version_id=version.target_closure_result_version_id,
        budget_revision_id=version.budget_revision_id,
        target_set_version_id=version.target_set_version_id,
        revenue_result_version_id=version.revenue_result_version_id,
        fiscal_year=version.fiscal_year,
        calculation_rule_version=version.calculation_rule_version,
        status=version.status,
        closure_status=version.closure_status,
        upstream_revenue_publishable=version.upstream_revenue_publishable,
        publishable=version.publishable,
        record_count=version.record_count,
        input_fingerprint=version.input_fingerprint,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[_closure_response(item) for item in sorted(rows, key=_closure_row_sort_key)],
        revision=revision,
    )


def _closure_response(row: TargetClosureResult) -> TargetClosureResultResponse:
    return TargetClosureResultResponse.model_validate(
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
    )


def _target_input_sort_key(item: BudgetTargetInput) -> tuple[int, str]:
    return (0 if item.scope_level == "DEPARTMENT" else 1, item.track or "")


def _target_row_sort_key(item: BudgetTarget) -> tuple[int, str]:
    return (0 if item.scope_level == "DEPARTMENT" else 1, item.track or "")


def _closure_row_sort_key(item: TargetClosureResult) -> tuple[int, str]:
    return (0 if item.scope_level == "DEPARTMENT" else 1, item.track or "")


def _scope_label(scope: tuple[str, str | None]) -> str:
    return scope[0] if scope[1] is None else f"{scope[0]}:{scope[1]}"
