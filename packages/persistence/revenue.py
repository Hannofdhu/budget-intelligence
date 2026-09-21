import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    CompositeDiscountTargetInput,
    CompositeDiscountTargetResponse,
    DetailDiscountParameterInput,
    DetailDiscountParameterResponse,
    DiscountParameterConfirmationResponse,
    DiscountParameterConfirmRequest,
    DiscountParameterReplaceDraftRequest,
    DiscountParameterVersionResponse,
    RevenueAggregateResultResponse,
    RevenueCalculateRequest,
    RevenueResultVersionResponse,
    RevenueValidationResponse,
    RevisionCommandResponse,
    SeasonRevenueResultResponse,
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
    BudgetCombinationSetVersion,
    BudgetRevision,
    BudgetTarget,
    ClassSizeResult,
    ClassSizeResultVersion,
    CombinationSetConfirmation,
    CompositeDiscountTarget,
    DetailDiscountParameter,
    DiscountParameterConfirmation,
    DiscountParameterVersion,
    HeadcountResultVersion,
    OpeningCarryoverParameter,
    ResourceFinanceConfirmation,
    ResourceFinanceParameterVersion,
    RevenueAggregateResult,
    RevenueParameter,
    RevenueResultVersion,
    RevenueValidationResult,
    SeasonRevenueResult,
    TargetSetVersion,
)
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)
from packages.planned_new_students.governance import quarter_name
from packages.revenue.calculation import (
    REVENUE_RULE_VERSION,
    RevenueAggregate,
    RevenueInvariantError,
    RevenueLineResult,
    RevenueValidation,
)
from packages.revenue.calculation import (
    CompositeDiscountTargetInput as EngineTarget,
)
from packages.revenue.calculation import RevenueLineInput as EngineLine
from packages.revenue.calculation import calculate_revenue as calculate_revenue_engine

MONEY_QUANTUM = Decimal("0.000001")
FACTOR_QUANTUM = Decimal("0.000000001")
PPM_QUANTUM = Decimal("0.000000001")
DISCOUNT_DOWNSTREAM_REMOVALS = (
    "revenue_result_version_id",
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)
REVENUE_DOWNSTREAM_REMOVALS = (
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)
RevenueLinks = tuple[
    ClassSizeResult,
    RevenueParameter,
    DetailDiscountParameter,
    OpeningCarryoverParameter | None,
]


def discount_parameter_batch_content_hash(payload: DiscountParameterReplaceDraftRequest) -> str:
    return content_hash(_canonical_discount_draft(payload))


def _canonical_discount_draft(payload: DiscountParameterReplaceDraftRequest) -> dict[str, object]:
    details = [
        {
            "fiscal_season": item.fiscal_season,
            "track": item.track.strip(),
            "product": item.product.strip(),
            "grade": item.grade.strip(),
            "discount_rate_ppm": item.discount_rate_ppm,
            "source_evidence": item.source_evidence,
            "change_reason": item.change_reason.strip(),
        }
        for item in sorted(
            payload.detail_items,
            key=lambda row: (row.fiscal_season, row.track, row.product, row.grade),
        )
    ]
    targets = [
        {
            "scope_level": item.scope_level,
            "track": item.track.strip() if item.track else None,
            "period_type": item.period_type,
            "fiscal_season": item.fiscal_season,
            "target_rate_ppm": item.target_rate_ppm,
            "source_evidence": item.source_evidence,
            "change_reason": item.change_reason.strip(),
        }
        for item in sorted(payload.composite_targets, key=_target_input_sort_key)
    ]
    return {"schema_version": "1.0", "detail_items": details, "composite_targets": targets}


async def replace_discount_parameter_draft(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: DiscountParameterReplaceDraftRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> DiscountParameterVersionResponse:
    command_type = "REPLACE_DISCOUNT_PARAMETER_DRAFT"
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
            return DiscountParameterVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        combination_version_id = _manifest_uuid(
            parent, "combination_set_version_id", "当前修订缺少预算组合版本"
        )
        combination_version = await session.get(BudgetCombinationSetVersion, combination_version_id)
        if combination_version is None:
            raise DomainError(code="COMBINATION_SET_NOT_FOUND", message="预算组合版本不存在", status_code=404)
        confirmation = await session.scalar(
            select(CombinationSetConfirmation).where(
                CombinationSetConfirmation.combination_set_version_id == combination_version_id,
                CombinationSetConfirmation.input_fingerprint == combination_version.content_hash,
            )
        )
        if confirmation is None:
            raise DomainError(
                code="COMBINATION_SET_CONFIRMATION_REQUIRED",
                message="预算组合整套确认后才能维护折扣参数",
                status_code=409,
            )
        combinations = list(
            (
                await session.scalars(
                    select(BudgetCombination).where(
                        BudgetCombination.combination_set_version_id == combination_version_id,
                        BudgetCombination.enabled.is_(True),
                    )
                )
            ).all()
        )
        tracks = {item.track for item in combinations}
        expected_details = {
            (budget.fiscal_year * 10 + season, item.track, item.product, item.grade)
            for item in combinations
            for season in range(1, 5)
        }
        expected_targets = {
            (scope, track, period, season)
            for scope, track in [("DEPARTMENT", None), *(("TRACK", value) for value in tracks)]
            for period, season in [
                ("ANNUAL", None),
                *(("SEASON", budget.fiscal_year * 10 + index) for index in range(1, 5)),
            ]
        }
        actual_details = {
            (item.fiscal_season, item.track.strip(), item.product.strip(), item.grade.strip())
            for item in payload.detail_items
        }
        actual_targets = {
            (
                item.scope_level,
                item.track.strip() if item.track else None,
                item.period_type,
                item.fiscal_season,
            )
            for item in payload.composite_targets
        }
        errors: list[dict[str, object]] = []
        if actual_details != expected_details:
            errors.append(
                {
                    "code": "DETAIL_DISCOUNT_PARAMETER_SET_INCOMPLETE",
                    "missing": sorted(expected_details - actual_details),
                    "unexpected": sorted(actual_details - expected_details),
                }
            )
        if actual_targets != expected_targets:
            errors.append(
                {
                    "code": "COMPOSITE_DISCOUNT_TARGET_SET_INCOMPLETE",
                    "missing": sorted(expected_targets - actual_targets, key=str),
                    "unexpected": sorted(actual_targets - expected_targets, key=str),
                }
            )
        canonical = _canonical_discount_draft(payload)
        computed_batch_hash = content_hash(canonical)
        if payload.batch_content_hash != computed_batch_hash:
            errors.append(
                {
                    "code": "BATCH_CONTENT_HASH_MISMATCH",
                    "expected_batch_content_hash": computed_batch_hash,
                }
            )
        if errors:
            raise DomainError(
                code="DISCOUNT_PARAMETER_BATCH_INVALID",
                message="完整折扣参数草稿校验失败",
                status_code=422,
                details={"errors": errors},
            )

        parent_version = await _current_discount_version(session, parent)
        detail_content = [
            _detail_content(item, budget.department_id, budget.fiscal_year)
            for item in sorted(
                payload.detail_items,
                key=lambda row: (row.fiscal_season, row.track, row.product, row.grade),
            )
        ]
        target_content = [
            _target_content(item, budget.department_id, budget.fiscal_year)
            for item in sorted(payload.composite_targets, key=_target_input_sort_key)
        ]
        version_content = {
            "schema_version": "1.0",
            "fiscal_year": budget.fiscal_year,
            "combination_set_version_id": str(combination_version_id),
            "detail_items": detail_content,
            "composite_targets": target_content,
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
            component_updates={"discount_parameter_version_id": str(version_id)},
            component_removals=DISCOUNT_DOWNSTREAM_REMOVALS,
        )
        version = DiscountParameterVersion(
            discount_parameter_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            parent_discount_parameter_version_id=(
                parent_version.discount_parameter_version_id if parent_version is not None else None
            ),
            combination_set_version_id=combination_version_id,
            fiscal_year=budget.fiscal_year,
            operation_type="DRAFT_REPLACE",
            detail_record_count=len(detail_content),
            target_record_count=len(target_content),
            batch_content_hash=payload.batch_content_hash,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        detail_rows = [
            DetailDiscountParameter(
                detail_discount_parameter_id=uuid7(),
                discount_parameter_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in detail_content
        ]
        target_rows = [
            CompositeDiscountTarget(
                composite_discount_target_id=uuid7(),
                discount_parameter_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in target_content
        ]
        session.add_all(detail_rows)
        session.add_all(target_rows)
        response = _discount_response(version, detail_rows, target_rows, None, revision_response)
        _add_audit(
            session,
            event_type="DISCOUNT_PARAMETER_DRAFT_REPLACED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="DISCOUNT_PARAMETER_VERSION",
            object_key={"discount_parameter_version_id": str(version_id)},
            detail={
                "content_hash": version.content_hash,
                "batch_content_hash": payload.batch_content_hash,
                "detail_record_count": len(detail_rows),
                "target_record_count": len(target_rows),
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


async def get_discount_parameter_version(
    session: AsyncSession,
    *,
    actor: Actor,
    discount_parameter_version_id: uuid.UUID,
) -> DiscountParameterVersionResponse:
    version = await session.get(DiscountParameterVersion, discount_parameter_version_id)
    if version is None:
        raise DomainError(
            code="DISCOUNT_PARAMETER_VERSION_NOT_FOUND", message="折扣参数版本不存在", status_code=404
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    details, targets = await _load_discount_rows(session, version)
    confirmation = await _discount_confirmation(session, version)
    return _discount_response(version, details, targets, confirmation, _revision_link_response(revision))


async def confirm_discount_parameter_version(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: DiscountParameterConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> DiscountParameterConfirmationResponse:
    command_type = "CONFIRM_DISCOUNT_PARAMETER_VERSION"
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
            return DiscountParameterConfirmationResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if revision.component_manifest.get("discount_parameter_version_id") != str(
            payload.discount_parameter_version_id
        ):
            raise DomainError(
                code="DISCOUNT_PARAMETER_VERSION_NOT_CURRENT",
                message="待确认折扣参数不是当前修订引用的版本",
                status_code=409,
            )
        version = await session.get(DiscountParameterVersion, payload.discount_parameter_version_id)
        if version is None:
            raise DomainError(
                code="DISCOUNT_PARAMETER_VERSION_NOT_FOUND",
                message="折扣参数版本不存在",
                status_code=404,
            )
        existing = await _discount_confirmation(session, version)
        occurred_at = _now()
        if existing is None:
            existing = DiscountParameterConfirmation(
                discount_parameter_confirmation_id=uuid7(),
                discount_parameter_version_id=version.discount_parameter_version_id,
                confirmed_revision_id=revision.budget_revision_id,
                input_fingerprint=version.content_hash,
                confirmed_by=actor.user_id,
                role_snapshot=_role_snapshot(actor),
                summary={
                    "schema_version": "1.0",
                    "detail_record_count": version.detail_record_count,
                    "target_record_count": version.target_record_count,
                },
                confirmed_at=occurred_at,
            )
            session.add(existing)
            await session.flush()
        response = _discount_confirmation_response(existing)
        _add_audit(
            session,
            event_type="DISCOUNT_PARAMETER_VERSION_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="DISCOUNT_PARAMETER_VERSION",
            object_key={"discount_parameter_version_id": str(version.discount_parameter_version_id)},
            detail={"input_fingerprint": version.content_hash},
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


async def calculate_revenue_result(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RevenueCalculateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
    acting_role: Role = Role.DATA_MAINTAINER,
) -> RevenueResultVersionResponse:
    command_type = "CALCULATE_REVENUE_RESULT"
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
            return RevenueResultVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if not isinstance(parent.component_manifest.get("target_set_version_id"), str):
            raise DomainError(
                code="TARGET_SET_REQUIRED",
                message="学校版部门与全部赛道目标必须整套锁定后才能计算收入",
                status_code=409,
            )
        target_version_id = _manifest_uuid(
            parent, "target_set_version_id", "当前修订缺少已锁定学校版目标版本"
        )
        headcount_version_id = _manifest_uuid(
            parent, "headcount_result_version_id", "当前修订缺少人数结果版本"
        )
        class_size_version_id = _manifest_uuid(
            parent, "class_size_result_version_id", "当前修订缺少班均结果版本"
        )
        resource_version_id = _manifest_uuid(
            parent, "resource_finance_parameter_version_id", "当前修订缺少资源与财务参数版本"
        )
        discount_version_id = _manifest_uuid(
            parent, "discount_parameter_version_id", "当前修订缺少折扣参数版本"
        )
        headcount_version = await session.get(HeadcountResultVersion, headcount_version_id)
        class_size_version = await session.get(ClassSizeResultVersion, class_size_version_id)
        resource_version = await session.get(ResourceFinanceParameterVersion, resource_version_id)
        discount_version = await session.get(DiscountParameterVersion, discount_version_id)
        target_version = await session.get(TargetSetVersion, target_version_id)
        if any(
            item is None
            for item in (
                headcount_version,
                class_size_version,
                resource_version,
                discount_version,
                target_version,
            )
        ):
            raise DomainError(
                code="REVENUE_UPSTREAM_NOT_FOUND", message="收入计算的上游版本不存在", status_code=404
            )
        assert headcount_version is not None
        assert class_size_version is not None
        assert resource_version is not None
        assert discount_version is not None
        assert target_version is not None
        if target_version.fiscal_year != budget.fiscal_year or target_version.lock_status != "LOCKED":
            raise DomainError(
                code="TARGET_SET_NOT_LOCKED",
                message="学校版部门与全部赛道目标必须整套锁定后才能计算收入",
                status_code=409,
            )
        if (
            class_size_version.headcount_result_version_id != headcount_version_id
            or class_size_version.resource_finance_parameter_version_id != resource_version_id
            or resource_version.combination_set_version_id != discount_version.combination_set_version_id
        ):
            raise DomainError(
                code="REVENUE_UPSTREAM_VERSION_MISMATCH",
                message="收入计算的人数、班均、资源财务和折扣版本不是同一条依赖链",
                status_code=409,
            )
        resource_confirmation = await session.scalar(
            select(ResourceFinanceConfirmation).where(
                ResourceFinanceConfirmation.resource_finance_parameter_version_id == resource_version_id,
                ResourceFinanceConfirmation.input_fingerprint == resource_version.content_hash,
            )
        )
        if resource_confirmation is None:
            raise DomainError(
                code="RESOURCE_FINANCE_CONFIRMATION_REQUIRED",
                message="资源与财务参数必须经部门预算负责人确认后才能计算收入",
                status_code=409,
            )
        discount_confirmation = await _discount_confirmation(session, discount_version)
        if discount_confirmation is None:
            raise DomainError(
                code="DISCOUNT_PARAMETER_CONFIRMATION_REQUIRED",
                message="折扣参数必须经部门预算负责人独立确认后才能计算收入",
                status_code=409,
            )
        class_rows = list(
            (
                await session.scalars(
                    select(ClassSizeResult).where(
                        ClassSizeResult.class_size_result_version_id == class_size_version_id
                    )
                )
            ).all()
        )
        target_rows = list(
            (
                await session.scalars(
                    select(BudgetTarget).where(BudgetTarget.target_set_version_id == target_version_id)
                )
            ).all()
        )
        expected_target_scopes = {("DEPARTMENT", None), *{("TRACK", row.track) for row in class_rows}}
        actual_target_scopes = {(row.scope_level, row.track) for row in target_rows}
        if actual_target_scopes != expected_target_scopes:
            raise DomainError(
                code="TARGET_TRACK_SET_MISMATCH",
                message="学校版目标未完整覆盖当前收入计算的全部赛道",
                status_code=409,
            )
        revenue_parameters = list(
            (
                await session.scalars(
                    select(RevenueParameter).where(
                        RevenueParameter.resource_finance_parameter_version_id == resource_version_id
                    )
                )
            ).all()
        )
        opening_parameters = list(
            (
                await session.scalars(
                    select(OpeningCarryoverParameter).where(
                        OpeningCarryoverParameter.resource_finance_parameter_version_id == resource_version_id
                    )
                )
            ).all()
        )
        discount_parameters, target_parameters = await _load_discount_rows(session, discount_version)
        revenue_by_key = {(row.fiscal_season, row.track, row.product): row for row in revenue_parameters}
        opening_by_key = {
            (row.fiscal_season, row.track, row.product, row.grade, row.subject, row.campus): row
            for row in opening_parameters
        }
        discount_by_key = {
            (row.fiscal_season, row.track, row.product, row.grade): row for row in discount_parameters
        }
        missing: list[dict[str, object]] = []
        engine_lines: list[EngineLine] = []
        link_by_result_id: dict[str, RevenueLinks] = {}
        for row in sorted(class_rows, key=_class_row_sort_key):
            revenue_parameter = revenue_by_key.get((row.fiscal_season, row.track, row.product))
            discount_parameter = discount_by_key.get((row.fiscal_season, row.track, row.product, row.grade))
            opening_parameter = (
                opening_by_key.get(
                    (
                        row.fiscal_season,
                        row.track,
                        row.product,
                        row.grade,
                        row.subject,
                        row.campus,
                    )
                )
                if row.fiscal_season % 10 == 1
                else None
            )
            if (
                revenue_parameter is None
                or discount_parameter is None
                or (row.fiscal_season % 10 == 1 and opening_parameter is None)
            ):
                missing.append(
                    {
                        "fiscal_season": row.fiscal_season,
                        "combination_stable_key": row.combination_stable_key,
                        "revenue_parameter": revenue_parameter is not None,
                        "discount_parameter": discount_parameter is not None,
                        "opening_carryover_parameter": (
                            opening_parameter is not None if row.fiscal_season % 10 == 1 else True
                        ),
                    }
                )
                continue
            assert revenue_parameter is not None
            assert discount_parameter is not None
            engine_line = EngineLine(
                result_id=str(row.season_headcount_result_id),
                combination_stable_key=row.combination_stable_key,
                fiscal_season=row.fiscal_season,
                track=row.track,
                product=row.product,
                entry_attribute=row.entry_attribute,
                grade=row.grade,
                subject=row.subject,
                campus=row.campus,
                integer_total_students=row.integer_total_students,
                list_price_yuan=revenue_parameter.list_price_yuan,
                detail_discount_rate_ppm=discount_parameter.discount_rate_ppm,
                current_season_recognition_rate_ppm=(revenue_parameter.current_season_recognition_rate_ppm),
                opening_carryover_revenue_yuan=(
                    opening_parameter.amount_yuan if opening_parameter is not None else Decimal(0)
                ),
            )
            engine_lines.append(engine_line)
            link_by_result_id[engine_line.result_id] = (
                row,
                revenue_parameter,
                discount_parameter,
                opening_parameter,
            )
        if missing or len(engine_lines) != len(class_rows):
            raise DomainError(
                code="REVENUE_PARAMETER_SET_INCOMPLETE",
                message="收入计算参数未覆盖全部完整预算明细",
                status_code=409,
                details={"missing": missing},
            )
        engine_targets = [
            EngineTarget(
                target_id=str(row.composite_discount_target_id),
                scope_level=row.scope_level,
                track=row.track,
                period_type=row.period_type,
                fiscal_season=row.fiscal_season,
                target_rate_ppm=row.target_rate_ppm,
            )
            for row in target_parameters
        ]
        try:
            calculation = calculate_revenue_engine(
                fiscal_year=budget.fiscal_year,
                budget_tax_conversion_factor=resource_version.budget_tax_conversion_factor,
                lines=engine_lines,
                composite_targets=engine_targets,
            )
        except RevenueInvariantError as exc:
            raise DomainError(code=exc.code, message=exc.message, status_code=422) from exc
        row_content = [
            _revenue_row_content(
                item,
                links=link_by_result_id[item.source.result_id],
                tax_factor=resource_version.budget_tax_conversion_factor,
            )
            for item in calculation.rows
        ]
        aggregate_content = [
            _aggregate_content(item, budget.department_id) for item in calculation.aggregates
        ]
        validation_content = [_validation_content(item) for item in calculation.validations]
        if not class_size_version.publishable:
            validation_content.append(_upstream_class_size_validation(class_size_version_id))
        input_fingerprint = content_hash(
            {
                "schema_version": "1.0",
                "calculation_rule_version": REVENUE_RULE_VERSION,
                "headcount_result_version": headcount_version.content_hash,
                "class_size_result_version": class_size_version.content_hash,
                "resource_finance_parameter_version": resource_version.content_hash,
                "resource_finance_confirmation": resource_confirmation.input_fingerprint,
                "discount_parameter_version": discount_version.content_hash,
                "discount_parameter_confirmation": discount_confirmation.input_fingerprint,
            }
        )
        publishable = calculation.publishable and class_size_version.publishable
        version_content = {
            "schema_version": "1.0",
            "input_fingerprint": input_fingerprint,
            "calculation_rule_version": REVENUE_RULE_VERSION,
            "publishable": publishable,
            "rows": row_content,
            "aggregates": aggregate_content,
            "validations": validation_content,
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
            component_updates={"revenue_result_version_id": str(version_id)},
            component_removals=REVENUE_DOWNSTREAM_REMOVALS,
        )
        version = RevenueResultVersion(
            revenue_result_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            headcount_result_version_id=headcount_version_id,
            class_size_result_version_id=class_size_version_id,
            resource_finance_parameter_version_id=resource_version_id,
            resource_finance_confirmation_id=(resource_confirmation.resource_finance_confirmation_id),
            discount_parameter_version_id=discount_version_id,
            discount_parameter_confirmation_id=(discount_confirmation.discount_parameter_confirmation_id),
            fiscal_year=budget.fiscal_year,
            calculation_rule_version=REVENUE_RULE_VERSION,
            status="SUCCEEDED",
            publishable=publishable,
            record_count=len(row_content),
            aggregate_count=len(aggregate_content),
            validation_count=len(validation_content),
            input_fingerprint=input_fingerprint,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        result_rows = [_revenue_row_model(version_id, item) for item in row_content]
        aggregate_rows = [_aggregate_model(version_id, item) for item in aggregate_content]
        validation_rows = [_validation_model(version_id, item) for item in validation_content]
        session.add_all(result_rows)
        session.add_all(aggregate_rows)
        session.add_all(validation_rows)
        response = _revenue_version_response(
            version, result_rows, aggregate_rows, validation_rows, revision_response
        )
        _add_audit(
            session,
            event_type="REVENUE_RESULT_CALCULATED",
            actor=actor,
            acting_role=acting_role,
            object_type="REVENUE_RESULT_VERSION",
            object_key={"revenue_result_version_id": str(version_id)},
            detail={
                "input_fingerprint": input_fingerprint,
                "publishable": publishable,
                "validation_count": len(validation_rows),
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


async def get_revenue_result_version(
    session: AsyncSession,
    *,
    actor: Actor,
    revenue_result_version_id: uuid.UUID,
) -> RevenueResultVersionResponse:
    version = await session.get(RevenueResultVersion, revenue_result_version_id)
    if version is None:
        raise DomainError(
            code="REVENUE_RESULT_VERSION_NOT_FOUND", message="收入结果版本不存在", status_code=404
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        (
            await session.scalars(
                select(SeasonRevenueResult).where(
                    SeasonRevenueResult.revenue_result_version_id == revenue_result_version_id
                )
            )
        ).all()
    )
    aggregates = list(
        (
            await session.scalars(
                select(RevenueAggregateResult).where(
                    RevenueAggregateResult.revenue_result_version_id == revenue_result_version_id
                )
            )
        ).all()
    )
    validations = list(
        (
            await session.scalars(
                select(RevenueValidationResult).where(
                    RevenueValidationResult.revenue_result_version_id == revenue_result_version_id
                )
            )
        ).all()
    )
    return _revenue_version_response(
        version, rows, aggregates, validations, _revision_link_response(revision)
    )


async def _current_discount_version(
    session: AsyncSession, revision: BudgetRevision
) -> DiscountParameterVersion | None:
    raw = revision.component_manifest.get("discount_parameter_version_id")
    if not isinstance(raw, str):
        return None
    try:
        return await session.get(DiscountParameterVersion, uuid.UUID(raw))
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="预算修订组件清单无效", status_code=409
        ) from exc


async def _load_discount_rows(
    session: AsyncSession, version: DiscountParameterVersion
) -> tuple[list[DetailDiscountParameter], list[CompositeDiscountTarget]]:
    details = list(
        (
            await session.scalars(
                select(DetailDiscountParameter).where(
                    DetailDiscountParameter.discount_parameter_version_id
                    == version.discount_parameter_version_id
                )
            )
        ).all()
    )
    targets = list(
        (
            await session.scalars(
                select(CompositeDiscountTarget).where(
                    CompositeDiscountTarget.discount_parameter_version_id
                    == version.discount_parameter_version_id
                )
            )
        ).all()
    )
    return details, targets


async def _discount_confirmation(
    session: AsyncSession,
    version: DiscountParameterVersion,
) -> DiscountParameterConfirmation | None:
    confirmation: DiscountParameterConfirmation | None = await session.scalar(
        select(DiscountParameterConfirmation).where(
            DiscountParameterConfirmation.discount_parameter_version_id
            == version.discount_parameter_version_id,
            DiscountParameterConfirmation.input_fingerprint == version.content_hash,
        )
    )
    return confirmation


def _discount_confirmation_response(
    confirmation: DiscountParameterConfirmation,
) -> DiscountParameterConfirmationResponse:
    return DiscountParameterConfirmationResponse(
        discount_parameter_confirmation_id=confirmation.discount_parameter_confirmation_id,
        discount_parameter_version_id=confirmation.discount_parameter_version_id,
        confirmed_revision_id=confirmation.confirmed_revision_id,
        input_fingerprint=confirmation.input_fingerprint,
        confirmed_by=confirmation.confirmed_by,
        confirmed_at=confirmation.confirmed_at,
    )


def _detail_content(
    item: DetailDiscountParameterInput, department_id: str, fiscal_year: int
) -> dict[str, object]:
    fiscal_season = int(item.fiscal_season)
    track = str(item.track).strip()
    product = str(item.product).strip()
    grade = str(item.grade).strip()
    base = {
        "schema_version": "1.0",
        "department_id": department_id,
        "fiscal_year": fiscal_year,
        "fiscal_season": fiscal_season,
        "track": track,
        "product": product,
        "grade": grade,
    }
    return {
        "detail_discount_stable_key": content_hash({**base, "dimension": "DETAIL_DISCOUNT"}),
        "fiscal_year": fiscal_year,
        "fiscal_season": fiscal_season,
        "quarter_name": quarter_name(fiscal_year, fiscal_season),
        "track": track,
        "product": product,
        "grade": grade,
        "discount_rate_ppm": int(item.discount_rate_ppm),
        "source_evidence": item.source_evidence,
        "change_reason": str(item.change_reason).strip(),
    }


def _target_content(
    item: CompositeDiscountTargetInput, department_id: str, fiscal_year: int
) -> dict[str, object]:
    track_raw = item.track
    track = str(track_raw).strip() if track_raw else None
    base = {
        "schema_version": "1.0",
        "department_id": department_id,
        "fiscal_year": fiscal_year,
        "scope_level": str(item.scope_level),
        "track": track,
        "period_type": str(item.period_type),
        "fiscal_season": item.fiscal_season,
    }
    return {
        "composite_target_stable_key": content_hash({**base, "dimension": "COMPOSITE_TARGET"}),
        "scope_level": base["scope_level"],
        "track": track,
        "period_type": base["period_type"],
        "fiscal_season": base["fiscal_season"],
        "target_rate_ppm": int(item.target_rate_ppm),
        "source_evidence": item.source_evidence,
        "change_reason": str(item.change_reason).strip(),
    }


def _target_input_sort_key(item: CompositeDiscountTargetInput) -> tuple[str, str, str, int]:
    return (
        str(item.scope_level),
        str(item.track or ""),
        str(item.period_type),
        int(item.fiscal_season or 0),
    )


def _discount_response(
    version: DiscountParameterVersion,
    details: list[DetailDiscountParameter],
    targets: list[CompositeDiscountTarget],
    confirmation: DiscountParameterConfirmation | None,
    revision: RevisionCommandResponse,
) -> DiscountParameterVersionResponse:
    return DiscountParameterVersionResponse(
        discount_parameter_version_id=version.discount_parameter_version_id,
        budget_revision_id=version.budget_revision_id,
        parent_discount_parameter_version_id=version.parent_discount_parameter_version_id,
        combination_set_version_id=version.combination_set_version_id,
        fiscal_year=version.fiscal_year,
        operation_type=version.operation_type,
        detail_record_count=version.detail_record_count,
        target_record_count=version.target_record_count,
        approval_status="CONFIRMED" if confirmation is not None else "DRAFT",
        confirmed=confirmation is not None,
        confirmation=(_discount_confirmation_response(confirmation) if confirmation is not None else None),
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        detail_items=[_detail_response(item) for item in sorted(details, key=_detail_sort_key)],
        composite_targets=[_target_response(item) for item in sorted(targets, key=_target_sort_key)],
        revision=revision,
    )


def _detail_sort_key(row: DetailDiscountParameter) -> tuple[int, str, str, str]:
    return row.fiscal_season, row.track, row.product, row.grade


def _target_sort_key(row: CompositeDiscountTarget) -> tuple[str, str, str, int]:
    return row.scope_level, row.track or "", row.period_type, row.fiscal_season or 0


def _detail_response(row: DetailDiscountParameter) -> DetailDiscountParameterResponse:
    return DetailDiscountParameterResponse(
        detail_discount_parameter_id=row.detail_discount_parameter_id,
        detail_discount_stable_key=row.detail_discount_stable_key,
        fiscal_year=row.fiscal_year,
        fiscal_season=row.fiscal_season,
        quarter_name=row.quarter_name,
        track=row.track,
        product=row.product,
        grade=row.grade,
        discount_rate_ppm=row.discount_rate_ppm,
        source_evidence=row.source_evidence,
        change_reason=row.change_reason,
        content_hash=row.content_hash,
    )


def _target_response(row: CompositeDiscountTarget) -> CompositeDiscountTargetResponse:
    return CompositeDiscountTargetResponse(
        composite_discount_target_id=row.composite_discount_target_id,
        composite_target_stable_key=row.composite_target_stable_key,
        scope_level=row.scope_level,
        track=row.track,
        period_type=row.period_type,
        fiscal_season=row.fiscal_season,
        target_rate_ppm=row.target_rate_ppm,
        source_evidence=row.source_evidence,
        change_reason=row.change_reason,
        content_hash=row.content_hash,
    )


def _class_row_sort_key(
    row: ClassSizeResult,
) -> tuple[int, str, str, str, str, str, str, str]:
    return (
        row.fiscal_season,
        row.track,
        row.product,
        row.grade,
        row.subject,
        row.campus,
        row.entry_attribute,
        row.combination_stable_key,
    )


def _revenue_row_content(
    item: RevenueLineResult,
    *,
    links: RevenueLinks,
    tax_factor: Decimal,
) -> dict[str, object]:
    class_row, revenue_parameter, discount_parameter, opening_parameter = links
    source = item.source
    return {
        "class_size_result_id": str(class_row.class_size_result_id),
        "season_headcount_result_id": str(class_row.season_headcount_result_id),
        "revenue_parameter_id": str(revenue_parameter.revenue_parameter_id),
        "detail_discount_parameter_id": str(discount_parameter.detail_discount_parameter_id),
        "opening_carryover_parameter_id": (
            str(opening_parameter.opening_carryover_parameter_id) if opening_parameter else None
        ),
        "combination_stable_key": source.combination_stable_key,
        "fiscal_year": class_row.fiscal_year,
        "fiscal_season": source.fiscal_season,
        "quarter_name": class_row.quarter_name,
        "track": source.track,
        "product": source.product,
        "entry_attribute": source.entry_attribute,
        "grade": source.grade,
        "subject": source.subject,
        "campus": source.campus,
        "integer_total_students": source.integer_total_students,
        "list_price_yuan": source.list_price_yuan.quantize(MONEY_QUANTUM),
        "detail_discount_rate_ppm": source.detail_discount_rate_ppm,
        "discounted_tuition_yuan": item.discounted_tuition_yuan,
        "list_price_revenue_yuan": item.list_price_revenue_yuan,
        "business_quarter_revenue_yuan": item.business_quarter_revenue_yuan,
        "budget_tax_conversion_factor": tax_factor.quantize(FACTOR_QUANTUM),
        "net_of_tax_base_yuan": item.net_of_tax_base_yuan,
        "current_season_recognition_rate_ppm": source.current_season_recognition_rate_ppm,
        "cross_season_recognition_rate_ppm": 1_000_000 - source.current_season_recognition_rate_ppm,
        "current_recognized_revenue_yuan": item.current_recognized_revenue_yuan,
        "incoming_carryover_revenue_yuan": item.incoming_carryover_revenue_yuan,
        "cross_season_revenue_yuan": item.cross_season_revenue_yuan,
        "quarter_recognized_revenue_yuan": item.quarter_recognized_revenue_yuan,
        "next_fiscal_year_opening_yuan": item.next_fiscal_year_opening_yuan,
        "calculation_status": item.calculation_status,
    }


def _aggregate_content(item: RevenueAggregate, department_id: str) -> dict[str, object]:
    composite_rate = (
        item.composite_discount_rate_ppm.quantize(PPM_QUANTUM)
        if item.composite_discount_rate_ppm is not None
        else None
    )
    variance = item.variance_ppm.quantize(PPM_QUANTUM) if item.variance_ppm is not None else None
    content = {
        "scope_level": item.scope_level,
        "track": item.track,
        "period_type": item.period_type,
        "fiscal_season": item.fiscal_season,
        "list_price_revenue_yuan": item.list_price_revenue_yuan,
        "discounted_revenue_yuan": item.discounted_revenue_yuan,
        "business_revenue_yuan": item.business_revenue_yuan,
        "recognized_revenue_yuan": item.recognized_revenue_yuan,
        "composite_discount_rate_ppm": composite_rate,
        "composite_discount_target_id": item.composite_discount_target_id,
        "target_rate_ppm": item.target_rate_ppm,
        "variance_ppm": variance,
        "target_status": item.target_status,
    }
    return {
        "aggregate_stable_key": content_hash(
            {
                "schema_version": "1.0",
                "department_id": department_id,
                "dimension": "REVENUE_AGGREGATE",
                "scope_level": content["scope_level"],
                "track": content["track"],
                "period_type": content["period_type"],
                "fiscal_season": content["fiscal_season"],
            }
        ),
        **content,
    }


def _validation_content(item: RevenueValidation) -> dict[str, object]:
    detail = item.detail
    combination_key = str(item.combination_stable_key)
    if len(combination_key) != 64:
        combination_key = content_hash(
            {
                "schema_version": "1.0",
                "dimension": "REVENUE_AGGREGATE_VALIDATION",
                "fiscal_season": item.fiscal_season,
                "code": item.code,
                "detail": detail,
            }
        )
    base = {
        "fiscal_season": item.fiscal_season,
        "combination_stable_key": combination_key,
        "code": item.code,
        "severity": "BLOCKING",
        "message": item.message,
        "detail": detail,
    }
    return {"validation_stable_key": content_hash(base), **base}


def _upstream_class_size_validation(class_size_version_id: uuid.UUID) -> dict[str, object]:
    base = {
        "fiscal_season": None,
        "combination_stable_key": content_hash({"schema_version": "1.0", "dimension": "UPSTREAM_CLASS_SIZE"}),
        "code": "UPSTREAM_CLASS_SIZE_NOT_PUBLISHABLE",
        "severity": "BLOCKING",
        "message": "班均结果存在阻断项，收入结果可查看但不得发布",
        "detail": {"class_size_result_version_id": str(class_size_version_id)},
    }
    return {"validation_stable_key": content_hash(base), **base}


def _revenue_row_model(version_id: uuid.UUID, item: dict[str, object]) -> SeasonRevenueResult:
    return SeasonRevenueResult(
        season_revenue_result_id=uuid7(),
        revenue_result_version_id=version_id,
        class_size_result_id=uuid.UUID(str(item["class_size_result_id"])),
        season_headcount_result_id=uuid.UUID(str(item["season_headcount_result_id"])),
        revenue_parameter_id=uuid.UUID(str(item["revenue_parameter_id"])),
        detail_discount_parameter_id=uuid.UUID(str(item["detail_discount_parameter_id"])),
        opening_carryover_parameter_id=(
            uuid.UUID(str(item["opening_carryover_parameter_id"]))
            if item["opening_carryover_parameter_id"] is not None
            else None
        ),
        **{
            key: value
            for key, value in item.items()
            if key
            not in {
                "class_size_result_id",
                "season_headcount_result_id",
                "revenue_parameter_id",
                "detail_discount_parameter_id",
                "opening_carryover_parameter_id",
            }
        },
        content_hash=content_hash(item),
    )


def _aggregate_model(version_id: uuid.UUID, item: dict[str, object]) -> RevenueAggregateResult:
    return RevenueAggregateResult(
        revenue_aggregate_result_id=uuid7(),
        revenue_result_version_id=version_id,
        composite_discount_target_id=(
            uuid.UUID(str(item["composite_discount_target_id"]))
            if item["composite_discount_target_id"] is not None
            else None
        ),
        **{key: value for key, value in item.items() if key != "composite_discount_target_id"},
        content_hash=content_hash(item),
    )


def _validation_model(version_id: uuid.UUID, item: dict[str, object]) -> RevenueValidationResult:
    return RevenueValidationResult(
        revenue_validation_result_id=uuid7(),
        revenue_result_version_id=version_id,
        **item,
        content_hash=content_hash(item),
    )


def _revenue_version_response(
    version: RevenueResultVersion,
    rows: list[SeasonRevenueResult],
    aggregates: list[RevenueAggregateResult],
    validations: list[RevenueValidationResult],
    revision: RevisionCommandResponse,
) -> RevenueResultVersionResponse:
    return RevenueResultVersionResponse(
        revenue_result_version_id=version.revenue_result_version_id,
        budget_revision_id=version.budget_revision_id,
        headcount_result_version_id=version.headcount_result_version_id,
        class_size_result_version_id=version.class_size_result_version_id,
        resource_finance_parameter_version_id=version.resource_finance_parameter_version_id,
        resource_finance_confirmation_id=version.resource_finance_confirmation_id,
        discount_parameter_version_id=version.discount_parameter_version_id,
        discount_parameter_confirmation_id=version.discount_parameter_confirmation_id,
        fiscal_year=version.fiscal_year,
        calculation_rule_version=version.calculation_rule_version,
        status=version.status,
        publishable=version.publishable,
        record_count=version.record_count,
        aggregate_count=version.aggregate_count,
        validation_count=version.validation_count,
        input_fingerprint=version.input_fingerprint,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[_revenue_row_response(item) for item in sorted(rows, key=_revenue_result_sort_key)],
        aggregates=[
            _aggregate_response(item) for item in sorted(aggregates, key=_revenue_aggregate_sort_key)
        ],
        validations=[
            _validation_response(item)
            for item in sorted(
                validations,
                key=lambda row: (row.fiscal_season or 0, row.combination_stable_key, row.code),
            )
        ],
        revision=revision,
    )


def _revenue_result_sort_key(row: SeasonRevenueResult) -> tuple[int, str, str, str, str, str, str, str]:
    return (
        row.fiscal_season,
        row.track,
        row.product,
        row.grade,
        row.subject,
        row.campus,
        row.entry_attribute,
        row.combination_stable_key,
    )


def _revenue_aggregate_sort_key(row: RevenueAggregateResult) -> tuple[str, str, str, int]:
    return row.scope_level, row.track or "", row.period_type, row.fiscal_season or 0


def _revenue_row_response(row: SeasonRevenueResult) -> SeasonRevenueResultResponse:
    return SeasonRevenueResultResponse.model_validate(
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
    )


def _aggregate_response(row: RevenueAggregateResult) -> RevenueAggregateResultResponse:
    return RevenueAggregateResultResponse.model_validate(
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
    )


def _validation_response(row: RevenueValidationResult) -> RevenueValidationResponse:
    return RevenueValidationResponse.model_validate(
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
    )
