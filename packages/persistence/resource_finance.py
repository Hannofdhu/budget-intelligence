import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.class_size.validation import (
    CLASS_SIZE_RULE_VERSION,
    ClassSizeInput,
    ClassSizeInvariantError,
    HeadcountInput,
    validate_class_sizes,
)
from packages.class_size.validation import (
    ClassSizeResult as ClassSizeEvaluation,
)
from packages.contracts.versioning import (
    ClassSizeParameterInput,
    ClassSizeParameterResponse,
    ClassSizeResultResponse,
    ClassSizeResultVersionResponse,
    ClassSizeValidateRequest,
    ClassSizeValidationResponse,
    OpeningCarryoverParameterInput,
    OpeningCarryoverParameterResponse,
    ResourceFinanceConfirmationResponse,
    ResourceFinanceConfirmRequest,
    ResourceFinanceParameterVersionResponse,
    ResourceFinanceReplaceDraftRequest,
    RevenueParameterInput,
    RevenueParameterResponse,
    RevisionCommandResponse,
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
    ClassSizeParameter,
    ClassSizeResult,
    ClassSizeResultVersion,
    ClassSizeValidationResult,
    CombinationSetConfirmation,
    HeadcountResultVersion,
    OpeningCarryoverParameter,
    ResourceFinanceConfirmation,
    ResourceFinanceParameterVersion,
    RevenueParameter,
    SeasonHeadcountResult,
)
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)
from packages.planned_new_students.governance import quarter_name

MONEY_QUANTUM = Decimal("0.000001")
FACTOR_QUANTUM = Decimal("0.000000001")
RESOURCE_DOWNSTREAM_REMOVALS = (
    "class_size_result_version_id",
    "revenue_result_version_id",
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)
CLASS_SIZE_DOWNSTREAM_REMOVALS = (
    "revenue_result_version_id",
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)


def resource_finance_batch_content_hash(payload: ResourceFinanceReplaceDraftRequest) -> str:
    return content_hash(_canonical_draft(payload))


def _canonical_draft(payload: ResourceFinanceReplaceDraftRequest) -> dict[str, object]:
    class_items = [
        {
            "track": item.track.strip(),
            "product": item.product.strip(),
            "system_value": item.system_value,
            "final_value": item.final_value,
            "source_evidence": item.source_evidence,
            "change_reason": item.change_reason.strip(),
        }
        for item in sorted(payload.class_size_items, key=lambda value: (value.track, value.product))
    ]
    revenue_items = [
        {
            "fiscal_season": item.fiscal_season,
            "track": item.track.strip(),
            "product": item.product.strip(),
            "list_price_yuan": item.list_price_yuan.quantize(MONEY_QUANTUM),
            "current_season_recognition_rate_ppm": item.current_season_recognition_rate_ppm,
            "opening_carryover_revenue_yuan": Decimal(0).quantize(MONEY_QUANTUM),
            "source_evidence": item.source_evidence,
            "change_reason": item.change_reason.strip(),
        }
        for item in sorted(
            payload.revenue_items,
            key=lambda value: (value.fiscal_season, value.track, value.product),
        )
    ]
    opening_carryover_items = [
        {
            "fiscal_season": item.fiscal_season,
            "track": item.track.strip(),
            "product": item.product.strip(),
            "grade": item.grade.strip(),
            "subject": item.subject.strip(),
            "campus": item.campus.strip(),
            "amount_yuan": item.amount_yuan.quantize(MONEY_QUANTUM),
            "source_evidence": item.source_evidence,
            "change_reason": item.change_reason.strip(),
        }
        for item in sorted(payload.opening_carryover_items, key=_opening_input_sort_key)
    ]
    return {
        "schema_version": "1.0",
        "budget_tax_conversion_factor": payload.budget_tax_conversion_factor.quantize(FACTOR_QUANTUM),
        "tax_factor_source_evidence": payload.tax_factor_source_evidence,
        "tax_factor_change_reason": payload.tax_factor_change_reason.strip(),
        "class_size_items": class_items,
        "revenue_items": revenue_items,
        "opening_carryover_items": opening_carryover_items,
    }


async def replace_resource_finance_draft(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: ResourceFinanceReplaceDraftRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> ResourceFinanceParameterVersionResponse:
    command_type = "REPLACE_RESOURCE_FINANCE_DRAFT"
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
            return ResourceFinanceParameterVersionResponse.model_validate(replay)
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
                message="预算组合整套确认后才能维护资源与财务参数",
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
        expected_products = {(item.track, item.product) for item in combinations}
        expected_revenue = {
            (budget.fiscal_year * 10 + season, track, product)
            for track, product in expected_products
            for season in range(1, 5)
        }
        first_season = budget.fiscal_year * 10 + 1
        expected_opening = {
            (first_season, item.track, item.product, item.grade, item.subject, item.campus)
            for item in combinations
        }
        actual_products = {(item.track.strip(), item.product.strip()) for item in payload.class_size_items}
        actual_revenue = {
            (item.fiscal_season, item.track.strip(), item.product.strip()) for item in payload.revenue_items
        }
        actual_opening = {
            (
                item.fiscal_season,
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            )
            for item in payload.opening_carryover_items
        }
        errors: list[dict[str, object]] = []
        if actual_products != expected_products:
            errors.append(
                {
                    "code": "CLASS_SIZE_PARAMETER_SET_INCOMPLETE",
                    "missing": sorted(expected_products - actual_products),
                    "unexpected": sorted(actual_products - expected_products),
                }
            )
        if actual_revenue != expected_revenue:
            errors.append(
                {
                    "code": "REVENUE_PARAMETER_SET_INCOMPLETE",
                    "missing": sorted(expected_revenue - actual_revenue),
                    "unexpected": sorted(actual_revenue - expected_revenue),
                }
            )
        if actual_opening != expected_opening:
            errors.append(
                {
                    "code": "OPENING_CARRYOVER_PARAMETER_SET_INCOMPLETE",
                    "missing": sorted(expected_opening - actual_opening),
                    "unexpected": sorted(actual_opening - expected_opening),
                }
            )
        canonical = _canonical_draft(payload)
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
                code="RESOURCE_FINANCE_BATCH_INVALID",
                message="完整资源与财务参数草稿校验失败",
                status_code=422,
                details={"errors": errors},
            )

        parent_version = await _current_resource_version(session, parent)
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
            component_updates={"resource_finance_parameter_version_id": str(version_id)},
            component_removals=RESOURCE_DOWNSTREAM_REMOVALS,
        )
        class_content = [
            _class_content(item, budget.department_id)
            for item in sorted(payload.class_size_items, key=lambda value: (value.track, value.product))
        ]
        revenue_content = [
            _revenue_content(item, budget.department_id, budget.fiscal_year)
            for item in sorted(
                payload.revenue_items,
                key=lambda value: (value.fiscal_season, value.track, value.product),
            )
        ]
        opening_content = [
            _opening_content(item, budget.department_id, budget.fiscal_year)
            for item in sorted(payload.opening_carryover_items, key=_opening_input_sort_key)
        ]
        version_content = {
            "schema_version": "1.0",
            "fiscal_year": budget.fiscal_year,
            "combination_set_version_id": str(combination_version_id),
            "budget_tax_conversion_factor": payload.budget_tax_conversion_factor.quantize(FACTOR_QUANTUM),
            "tax_factor_source_evidence": payload.tax_factor_source_evidence,
            "tax_factor_change_reason": payload.tax_factor_change_reason.strip(),
            "class_size_items": class_content,
            "revenue_items": revenue_content,
            "opening_carryover_items": opening_content,
        }
        version = ResourceFinanceParameterVersion(
            resource_finance_parameter_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            parent_resource_finance_parameter_version_id=(
                parent_version.resource_finance_parameter_version_id if parent_version is not None else None
            ),
            combination_set_version_id=combination_version_id,
            fiscal_year=budget.fiscal_year,
            operation_type="DRAFT_REPLACE",
            budget_tax_conversion_factor=payload.budget_tax_conversion_factor.quantize(FACTOR_QUANTUM),
            tax_factor_source_evidence=payload.tax_factor_source_evidence,
            tax_factor_change_reason=payload.tax_factor_change_reason.strip(),
            class_size_record_count=len(class_content),
            revenue_record_count=len(revenue_content),
            opening_carryover_record_count=len(opening_content),
            batch_content_hash=payload.batch_content_hash,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        class_rows = [
            ClassSizeParameter(
                class_size_parameter_id=uuid7(),
                resource_finance_parameter_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in class_content
        ]
        revenue_rows = [
            RevenueParameter(
                revenue_parameter_id=uuid7(),
                resource_finance_parameter_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in revenue_content
        ]
        opening_rows = [
            OpeningCarryoverParameter(
                opening_carryover_parameter_id=uuid7(),
                resource_finance_parameter_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in opening_content
        ]
        session.add_all(class_rows)
        session.add_all(revenue_rows)
        session.add_all(opening_rows)
        response = _resource_response(
            version, class_rows, revenue_rows, opening_rows, None, revision_response
        )
        _add_audit(
            session,
            event_type="RESOURCE_FINANCE_DRAFT_REPLACED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="RESOURCE_FINANCE_PARAMETER_VERSION",
            object_key={"resource_finance_parameter_version_id": str(version_id)},
            detail={
                "content_hash": version.content_hash,
                "batch_content_hash": payload.batch_content_hash,
                "class_size_record_count": len(class_rows),
                "revenue_record_count": len(revenue_rows),
                "opening_carryover_record_count": len(opening_rows),
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


async def confirm_resource_finance_version(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: ResourceFinanceConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> ResourceFinanceConfirmationResponse:
    command_type = "CONFIRM_RESOURCE_FINANCE_VERSION"
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
            return ResourceFinanceConfirmationResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if revision.component_manifest.get("resource_finance_parameter_version_id") != str(
            payload.resource_finance_parameter_version_id
        ):
            raise DomainError(
                code="RESOURCE_FINANCE_VERSION_NOT_CURRENT",
                message="待确认资源与财务参数不是当前修订引用的版本",
                status_code=409,
            )
        version = await session.get(
            ResourceFinanceParameterVersion, payload.resource_finance_parameter_version_id
        )
        if version is None:
            raise DomainError(
                code="RESOURCE_FINANCE_VERSION_NOT_FOUND",
                message="资源与财务参数版本不存在",
                status_code=404,
            )
        existing = await session.scalar(
            select(ResourceFinanceConfirmation).where(
                ResourceFinanceConfirmation.resource_finance_parameter_version_id
                == version.resource_finance_parameter_version_id,
                ResourceFinanceConfirmation.input_fingerprint == version.content_hash,
            )
        )
        occurred_at = _now()
        if existing is None:
            existing = ResourceFinanceConfirmation(
                resource_finance_confirmation_id=uuid7(),
                resource_finance_parameter_version_id=version.resource_finance_parameter_version_id,
                confirmed_revision_id=revision.budget_revision_id,
                input_fingerprint=version.content_hash,
                confirmed_by=actor.user_id,
                role_snapshot=_role_snapshot(actor),
                summary={
                    "schema_version": "1.0",
                    "class_size_record_count": version.class_size_record_count,
                    "revenue_record_count": version.revenue_record_count,
                    "opening_carryover_record_count": version.opening_carryover_record_count,
                    "budget_tax_conversion_factor": format(version.budget_tax_conversion_factor, "f"),
                },
                confirmed_at=occurred_at,
            )
            session.add(existing)
            await session.flush()
        response = _confirmation_response(existing)
        _add_audit(
            session,
            event_type="RESOURCE_FINANCE_VERSION_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="RESOURCE_FINANCE_PARAMETER_VERSION",
            object_key={
                "resource_finance_parameter_version_id": str(version.resource_finance_parameter_version_id)
            },
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


async def get_resource_finance_version(
    session: AsyncSession,
    *,
    actor: Actor,
    resource_finance_parameter_version_id: uuid.UUID,
) -> ResourceFinanceParameterVersionResponse:
    version = await session.get(ResourceFinanceParameterVersion, resource_finance_parameter_version_id)
    if version is None:
        raise DomainError(
            code="RESOURCE_FINANCE_VERSION_NOT_FOUND",
            message="资源与财务参数版本不存在",
            status_code=404,
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    class_rows, revenue_rows, opening_rows, confirmation = await _load_resource_rows(session, version)
    return _resource_response(
        version,
        class_rows,
        revenue_rows,
        opening_rows,
        confirmation,
        _revision_link_response(revision),
    )


async def validate_class_size_result(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: ClassSizeValidateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
    acting_role: Role = Role.DATA_MAINTAINER,
) -> ClassSizeResultVersionResponse:
    command_type = "VALIDATE_CLASS_SIZE_RESULT"
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
            return ClassSizeResultVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        headcount_version_id = _manifest_uuid(
            parent, "headcount_result_version_id", "当前修订缺少四季人数结果"
        )
        resource_version_id = _manifest_uuid(
            parent,
            "resource_finance_parameter_version_id",
            "当前修订缺少资源与财务参数版本",
        )
        headcount_version = await session.get(HeadcountResultVersion, headcount_version_id)
        resource_version = await session.get(ResourceFinanceParameterVersion, resource_version_id)
        if headcount_version is None or resource_version is None:
            raise DomainError(
                code="CLASS_SIZE_DEPENDENCY_NOT_FOUND",
                message="班均校验依赖版本不存在",
                status_code=404,
            )
        confirmation = await session.scalar(
            select(ResourceFinanceConfirmation).where(
                ResourceFinanceConfirmation.resource_finance_parameter_version_id == resource_version_id,
                ResourceFinanceConfirmation.input_fingerprint == resource_version.content_hash,
            )
        )
        if confirmation is None:
            raise DomainError(
                code="RESOURCE_FINANCE_CONFIRMATION_REQUIRED",
                message="完整资源与财务参数版本确认后才能执行班均校验",
                status_code=409,
            )
        headcounts = list(
            (
                await session.scalars(
                    select(SeasonHeadcountResult).where(
                        SeasonHeadcountResult.headcount_result_version_id == headcount_version_id
                    )
                )
            ).all()
        )
        class_parameters = list(
            (
                await session.scalars(
                    select(ClassSizeParameter).where(
                        ClassSizeParameter.resource_finance_parameter_version_id == resource_version_id
                    )
                )
            ).all()
        )
        try:
            calculation = validate_class_sizes(
                headcounts=[_headcount_input(item) for item in headcounts],
                parameters=[
                    ClassSizeInput(
                        str(item.class_size_parameter_id),
                        item.track,
                        item.product,
                        item.final_value,
                    )
                    for item in class_parameters
                ],
            )
        except ClassSizeInvariantError as exc:
            raise DomainError(code=exc.code, message=exc.message, status_code=409) from exc
        row_by_id = {str(item.season_headcount_result_id): item for item in headcounts}
        parameter_by_id = {str(item.class_size_parameter_id): item for item in class_parameters}
        rows_content = [
            _class_result_content(item, row_by_id=row_by_id, parameter_by_id=parameter_by_id)
            for item in calculation.rows
        ]
        validation_content = [
            {
                "fiscal_season": item.fiscal_season,
                "combination_stable_key": item.combination_stable_key,
                "code": item.code,
                "severity": "BLOCKING",
                "message": item.message,
                "detail": item.detail,
            }
            for item in calculation.validations
        ]
        input_fingerprint = content_hash(
            {
                "schema_version": "1.0",
                "validation_rule_version": CLASS_SIZE_RULE_VERSION,
                "headcount_result_version": headcount_version.content_hash,
                "resource_finance_parameter_version": resource_version.content_hash,
                "resource_finance_confirmation": confirmation.input_fingerprint,
            }
        )
        version_content = {
            "schema_version": "1.0",
            "input_fingerprint": input_fingerprint,
            "validation_rule_version": CLASS_SIZE_RULE_VERSION,
            "publishable": calculation.publishable,
            "rows": rows_content,
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
            component_updates={"class_size_result_version_id": str(version_id)},
            component_removals=CLASS_SIZE_DOWNSTREAM_REMOVALS,
        )
        version = ClassSizeResultVersion(
            class_size_result_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            headcount_result_version_id=headcount_version_id,
            resource_finance_parameter_version_id=resource_version_id,
            resource_finance_confirmation_id=confirmation.resource_finance_confirmation_id,
            fiscal_year=budget.fiscal_year,
            validation_rule_version=CLASS_SIZE_RULE_VERSION,
            status="SUCCEEDED",
            publishable=calculation.publishable,
            record_count=len(rows_content),
            validation_count=len(validation_content),
            input_fingerprint=input_fingerprint,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        result_rows = [_class_result_model(version_id, item) for item in rows_content]
        validation_rows = [
            ClassSizeValidationResult(
                class_size_validation_result_id=uuid7(),
                class_size_result_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in validation_content
        ]
        session.add_all(result_rows)
        session.add_all(validation_rows)
        response = _class_result_version_response(version, result_rows, validation_rows, revision_response)
        _add_audit(
            session,
            event_type="CLASS_SIZE_RESULT_VALIDATED",
            actor=actor,
            acting_role=acting_role,
            object_type="CLASS_SIZE_RESULT_VERSION",
            object_key={"class_size_result_version_id": str(version_id)},
            detail={
                "input_fingerprint": input_fingerprint,
                "publishable": calculation.publishable,
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


async def get_class_size_result_version(
    session: AsyncSession,
    *,
    actor: Actor,
    class_size_result_version_id: uuid.UUID,
) -> ClassSizeResultVersionResponse:
    version = await session.get(ClassSizeResultVersion, class_size_result_version_id)
    if version is None:
        raise DomainError(
            code="CLASS_SIZE_RESULT_VERSION_NOT_FOUND",
            message="班均校验结果版本不存在",
            status_code=404,
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        (
            await session.scalars(
                select(ClassSizeResult).where(
                    ClassSizeResult.class_size_result_version_id == class_size_result_version_id
                )
            )
        ).all()
    )
    validations = list(
        (
            await session.scalars(
                select(ClassSizeValidationResult).where(
                    ClassSizeValidationResult.class_size_result_version_id == class_size_result_version_id
                )
            )
        ).all()
    )
    return _class_result_version_response(version, rows, validations, _revision_link_response(revision))


async def _current_resource_version(
    session: AsyncSession, revision: BudgetRevision
) -> ResourceFinanceParameterVersion | None:
    raw = getattr(revision, "component_manifest", {}).get("resource_finance_parameter_version_id")
    if not isinstance(raw, str):
        return None
    try:
        return await session.get(ResourceFinanceParameterVersion, uuid.UUID(raw))
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="预算修订组件清单无效", status_code=409
        ) from exc


async def _load_resource_rows(
    session: AsyncSession, version: ResourceFinanceParameterVersion
) -> tuple[
    list[ClassSizeParameter],
    list[RevenueParameter],
    list[OpeningCarryoverParameter],
    ResourceFinanceConfirmation | None,
]:
    class_rows = list(
        (
            await session.scalars(
                select(ClassSizeParameter).where(
                    ClassSizeParameter.resource_finance_parameter_version_id
                    == version.resource_finance_parameter_version_id
                )
            )
        ).all()
    )
    revenue_rows = list(
        (
            await session.scalars(
                select(RevenueParameter).where(
                    RevenueParameter.resource_finance_parameter_version_id
                    == version.resource_finance_parameter_version_id
                )
            )
        ).all()
    )
    opening_rows = list(
        (
            await session.scalars(
                select(OpeningCarryoverParameter).where(
                    OpeningCarryoverParameter.resource_finance_parameter_version_id
                    == version.resource_finance_parameter_version_id
                )
            )
        ).all()
    )
    confirmation = await session.scalar(
        select(ResourceFinanceConfirmation).where(
            ResourceFinanceConfirmation.resource_finance_parameter_version_id
            == version.resource_finance_parameter_version_id,
            ResourceFinanceConfirmation.input_fingerprint == version.content_hash,
        )
    )
    return class_rows, revenue_rows, opening_rows, confirmation


def _class_content(item: ClassSizeParameterInput, department_id: str) -> dict[str, object]:
    track = item.track.strip()
    product = item.product.strip()
    return {
        "class_size_parameter_stable_key": content_hash(
            {
                "schema_version": "1.0",
                "department_id": department_id,
                "dimension": "CLASS_SIZE_PARAMETER",
                "track": track,
                "product": product,
            }
        ),
        "track": track,
        "product": product,
        "system_value": item.system_value,
        "final_value": item.final_value,
        "source_evidence": item.source_evidence,
        "change_reason": item.change_reason.strip(),
    }


def _revenue_content(item: RevenueParameterInput, department_id: str, fiscal_year: int) -> dict[str, object]:
    fiscal_season = item.fiscal_season
    track = item.track.strip()
    product = item.product.strip()
    base_key = {
        "schema_version": "1.0",
        "department_id": department_id,
        "fiscal_year": fiscal_year,
        "fiscal_season": fiscal_season,
        "track": track,
        "product": product,
    }
    current_rate = item.current_season_recognition_rate_ppm
    return {
        "revenue_parameter_stable_key": content_hash({**base_key, "dimension": "REVENUE_PARAMETER"}),
        "recognition_parameter_key": content_hash({**base_key, "dimension": "RECOGNITION_PARAMETER"}),
        "fiscal_year": fiscal_year,
        "fiscal_season": fiscal_season,
        "quarter_name": quarter_name(fiscal_year, fiscal_season),
        "track": track,
        "product": product,
        "list_price_yuan": item.list_price_yuan.quantize(MONEY_QUANTUM),
        "current_season_recognition_rate_ppm": current_rate,
        "cross_season_recognition_rate_ppm": 1_000_000 - current_rate,
        "opening_carryover_revenue_yuan": Decimal(0).quantize(MONEY_QUANTUM),
        "source_evidence": item.source_evidence,
        "change_reason": item.change_reason.strip(),
    }


def _opening_content(
    item: OpeningCarryoverParameterInput, department_id: str, fiscal_year: int
) -> dict[str, object]:
    fiscal_season = item.fiscal_season
    track = item.track.strip()
    product = item.product.strip()
    grade = item.grade.strip()
    subject = item.subject.strip()
    campus = item.campus.strip()
    base_key = {
        "schema_version": "1.0",
        "department_id": department_id,
        "fiscal_year": fiscal_year,
        "fiscal_season": fiscal_season,
        "track": track,
        "product": product,
        "grade": grade,
        "subject": subject,
        "campus": campus,
    }
    return {
        "opening_carryover_stable_key": content_hash(
            {**base_key, "dimension": "OPENING_CARRYOVER_PARAMETER"}
        ),
        "fiscal_year": fiscal_year,
        "fiscal_season": fiscal_season,
        "quarter_name": quarter_name(fiscal_year, fiscal_season),
        "track": track,
        "product": product,
        "grade": grade,
        "subject": subject,
        "campus": campus,
        "amount_yuan": item.amount_yuan.quantize(MONEY_QUANTUM),
        "source_evidence": item.source_evidence,
        "change_reason": item.change_reason.strip(),
    }


def _headcount_input(row: SeasonHeadcountResult) -> HeadcountInput:
    return HeadcountInput(
        result_id=str(row.season_headcount_result_id),
        combination_stable_key=row.combination_stable_key,
        fiscal_season=row.fiscal_season,
        track=row.track,
        product=row.product,
        entry_attribute=row.entry_attribute,
        grade=row.grade,
        subject=row.subject,
        campus=row.campus,
        calculation_status=row.calculation_status,
        integer_total_students=row.integer_total_students,
    )


def _class_result_content(
    item: ClassSizeEvaluation,
    *,
    row_by_id: dict[str, SeasonHeadcountResult],
    parameter_by_id: dict[str, ClassSizeParameter],
) -> dict[str, object]:
    headcount = item.headcount
    source = row_by_id[headcount.result_id]
    parameter_id = item.parameter_id
    parameter = parameter_by_id.get(parameter_id) if parameter_id is not None else None
    return {
        "season_headcount_result_id": str(source.season_headcount_result_id),
        "class_size_parameter_id": str(parameter.class_size_parameter_id) if parameter else None,
        "combination_stable_key": source.combination_stable_key,
        "fiscal_year": source.fiscal_year,
        "fiscal_season": source.fiscal_season,
        "quarter_name": source.quarter_name,
        "track": source.track,
        "product": source.product,
        "entry_attribute": source.entry_attribute,
        "grade": source.grade,
        "subject": source.subject,
        "campus": source.campus,
        "integer_total_students": source.integer_total_students,
        "minimum_class_size": item.minimum_class_size,
        "class_size_gap": item.class_size_gap,
        "class_size_status": item.status,
    }


def _class_result_model(version_id: uuid.UUID, content: dict[str, object]) -> ClassSizeResult:
    return ClassSizeResult(
        class_size_result_id=uuid7(),
        class_size_result_version_id=version_id,
        season_headcount_result_id=uuid.UUID(str(content["season_headcount_result_id"])),
        class_size_parameter_id=(
            uuid.UUID(str(content["class_size_parameter_id"]))
            if content["class_size_parameter_id"] is not None
            else None
        ),
        combination_stable_key=str(content["combination_stable_key"]),
        fiscal_year=int(str(content["fiscal_year"])),
        fiscal_season=int(str(content["fiscal_season"])),
        quarter_name=str(content["quarter_name"]),
        track=str(content["track"]),
        product=str(content["product"]),
        entry_attribute=str(content["entry_attribute"]),
        grade=str(content["grade"]),
        subject=str(content["subject"]),
        campus=str(content["campus"]),
        integer_total_students=(
            int(str(content["integer_total_students"]))
            if content["integer_total_students"] is not None
            else None
        ),
        minimum_class_size=(
            int(str(content["minimum_class_size"])) if content["minimum_class_size"] is not None else None
        ),
        class_size_gap=(
            int(str(content["class_size_gap"])) if content["class_size_gap"] is not None else None
        ),
        class_size_status=str(content["class_size_status"]),
        content_hash=content_hash(content),
    )


def _confirmation_response(row: ResourceFinanceConfirmation) -> ResourceFinanceConfirmationResponse:
    return ResourceFinanceConfirmationResponse(
        resource_finance_confirmation_id=row.resource_finance_confirmation_id,
        resource_finance_parameter_version_id=row.resource_finance_parameter_version_id,
        confirmed_revision_id=row.confirmed_revision_id,
        input_fingerprint=row.input_fingerprint,
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
    )


def _resource_response(
    version: ResourceFinanceParameterVersion,
    class_rows: list[ClassSizeParameter],
    revenue_rows: list[RevenueParameter],
    opening_rows: list[OpeningCarryoverParameter],
    confirmation: ResourceFinanceConfirmation | None,
    revision: RevisionCommandResponse,
) -> ResourceFinanceParameterVersionResponse:
    return ResourceFinanceParameterVersionResponse(
        resource_finance_parameter_version_id=version.resource_finance_parameter_version_id,
        budget_revision_id=version.budget_revision_id,
        parent_resource_finance_parameter_version_id=(version.parent_resource_finance_parameter_version_id),
        combination_set_version_id=version.combination_set_version_id,
        fiscal_year=version.fiscal_year,
        operation_type=version.operation_type,
        budget_tax_conversion_factor=version.budget_tax_conversion_factor.quantize(FACTOR_QUANTUM),
        tax_factor_source_evidence=version.tax_factor_source_evidence,
        tax_factor_change_reason=version.tax_factor_change_reason,
        class_size_record_count=version.class_size_record_count,
        revenue_record_count=version.revenue_record_count,
        opening_carryover_record_count=version.opening_carryover_record_count,
        approval_status="CONFIRMED" if confirmation is not None else "DRAFT",
        confirmed=confirmation is not None,
        confirmation=_confirmation_response(confirmation) if confirmation is not None else None,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        class_size_items=[_class_response(item) for item in sorted(class_rows, key=_class_sort_key)],
        revenue_items=[_revenue_response(item) for item in sorted(revenue_rows, key=_revenue_sort_key)],
        opening_carryover_items=[
            _opening_response(item) for item in sorted(opening_rows, key=_opening_sort_key)
        ],
        revision=revision,
    )


def _class_sort_key(row: ClassSizeParameter) -> tuple[str, str]:
    return row.track, row.product


def _revenue_sort_key(row: RevenueParameter) -> tuple[int, str, str]:
    return row.fiscal_season, row.track, row.product


def _opening_input_sort_key(
    row: OpeningCarryoverParameterInput,
) -> tuple[int, str, str, str, str, str]:
    return row.fiscal_season, row.track, row.product, row.grade, row.subject, row.campus


def _opening_sort_key(
    row: OpeningCarryoverParameter,
) -> tuple[int, str, str, str, str, str]:
    return row.fiscal_season, row.track, row.product, row.grade, row.subject, row.campus


def _class_response(row: ClassSizeParameter) -> ClassSizeParameterResponse:
    return ClassSizeParameterResponse(
        class_size_parameter_id=row.class_size_parameter_id,
        class_size_parameter_stable_key=row.class_size_parameter_stable_key,
        track=row.track,
        product=row.product,
        system_value=row.system_value,
        final_value=row.final_value,
        source_evidence=row.source_evidence,
        change_reason=row.change_reason,
        content_hash=row.content_hash,
    )


def _revenue_response(row: RevenueParameter) -> RevenueParameterResponse:
    return RevenueParameterResponse(
        revenue_parameter_id=row.revenue_parameter_id,
        revenue_parameter_stable_key=row.revenue_parameter_stable_key,
        recognition_parameter_key=row.recognition_parameter_key,
        fiscal_year=row.fiscal_year,
        fiscal_season=row.fiscal_season,
        quarter_name=row.quarter_name,
        track=row.track,
        product=row.product,
        list_price_yuan=row.list_price_yuan.quantize(MONEY_QUANTUM),
        current_season_recognition_rate_ppm=row.current_season_recognition_rate_ppm,
        cross_season_recognition_rate_ppm=row.cross_season_recognition_rate_ppm,
        opening_carryover_revenue_yuan=row.opening_carryover_revenue_yuan.quantize(MONEY_QUANTUM),
        source_evidence=row.source_evidence,
        change_reason=row.change_reason,
        content_hash=row.content_hash,
    )


def _opening_response(row: OpeningCarryoverParameter) -> OpeningCarryoverParameterResponse:
    return OpeningCarryoverParameterResponse(
        opening_carryover_parameter_id=row.opening_carryover_parameter_id,
        opening_carryover_stable_key=row.opening_carryover_stable_key,
        fiscal_year=row.fiscal_year,
        fiscal_season=row.fiscal_season,
        quarter_name=row.quarter_name,
        track=row.track,
        product=row.product,
        grade=row.grade,
        subject=row.subject,
        campus=row.campus,
        amount_yuan=row.amount_yuan.quantize(MONEY_QUANTUM),
        source_evidence=row.source_evidence,
        change_reason=row.change_reason,
        content_hash=row.content_hash,
    )


def _class_result_response(row: ClassSizeResult) -> ClassSizeResultResponse:
    return ClassSizeResultResponse(
        class_size_result_id=row.class_size_result_id,
        season_headcount_result_id=row.season_headcount_result_id,
        class_size_parameter_id=row.class_size_parameter_id,
        combination_stable_key=row.combination_stable_key,
        fiscal_year=row.fiscal_year,
        fiscal_season=row.fiscal_season,
        quarter_name=row.quarter_name,
        track=row.track,
        product=row.product,
        entry_attribute=row.entry_attribute,
        grade=row.grade,
        subject=row.subject,
        campus=row.campus,
        integer_total_students=row.integer_total_students,
        minimum_class_size=row.minimum_class_size,
        class_size_gap=row.class_size_gap,
        class_size_status=row.class_size_status,
        content_hash=row.content_hash,
    )


def _validation_response(row: ClassSizeValidationResult) -> ClassSizeValidationResponse:
    return ClassSizeValidationResponse(
        class_size_validation_result_id=row.class_size_validation_result_id,
        fiscal_season=row.fiscal_season,
        combination_stable_key=row.combination_stable_key,
        code=row.code,
        severity=row.severity,
        message=row.message,
        detail=row.detail,
        content_hash=row.content_hash,
    )


def _class_size_result_sort_key(
    row: ClassSizeResult,
) -> tuple[int, str, str, str, str, str, str, str]:
    return (
        row.fiscal_season,
        row.track,
        row.product,
        row.entry_attribute,
        row.grade,
        row.subject,
        row.campus,
        row.combination_stable_key,
    )


def _class_result_version_response(
    version: ClassSizeResultVersion,
    rows: list[ClassSizeResult],
    validations: list[ClassSizeValidationResult],
    revision: RevisionCommandResponse,
) -> ClassSizeResultVersionResponse:
    return ClassSizeResultVersionResponse(
        class_size_result_version_id=version.class_size_result_version_id,
        budget_revision_id=version.budget_revision_id,
        headcount_result_version_id=version.headcount_result_version_id,
        resource_finance_parameter_version_id=version.resource_finance_parameter_version_id,
        resource_finance_confirmation_id=version.resource_finance_confirmation_id,
        fiscal_year=version.fiscal_year,
        validation_rule_version=version.validation_rule_version,
        status=version.status,
        publishable=version.publishable,
        record_count=version.record_count,
        validation_count=version.validation_count,
        input_fingerprint=version.input_fingerprint,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[_class_result_response(item) for item in sorted(rows, key=_class_size_result_sort_key)],
        validations=[
            _validation_response(item)
            for item in sorted(
                validations,
                key=lambda row: (row.fiscal_season, row.combination_stable_key, row.code),
            )
        ],
        revision=revision,
    )
