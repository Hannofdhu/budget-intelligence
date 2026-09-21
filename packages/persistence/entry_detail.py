import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    EntryDetailAllocationResponse,
    EntryDetailCalculateRequest,
    EntryDetailSetVersionResponse,
    EntryScopeConfirmationResponse,
    EntryScopeConfirmRequest,
    EntryScopeEvaluationResponse,
    EntryScopeExceptionResponse,
    EntryScopeReplaceDraftRequest,
    EntryScopeVersionResponse,
    RevisionCommandResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.entry_planning.detail_allocation import (
    EntryDetailAllocationError,
    EntryDetailAllocationInput,
    allocate_entry_details,
)
from packages.entry_planning.scope import evaluate_entry_scope
from packages.persistence.entry_planning import (
    ENTRY_SCOPE_RULE_VERSION,
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
    CampusEntryAllocation,
    CampusEntryConfirmation,
    CampusEntrySetVersion,
    CombinationSetConfirmation,
    EntryDetailAllocation,
    EntryDetailSetVersion,
    EntryScopeConfirmation,
    EntryScopeEvaluation,
    EntryScopeException,
    EntryScopeVersion,
    RateReferenceVersion,
    SourceMetricAggregate,
)
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)

DETAIL_DECIMAL_QUANTUM = Decimal("0.000000001")


async def replace_entry_scope_draft(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: EntryScopeReplaceDraftRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> EntryScopeVersionResponse:
    command_type = "REPLACE_ENTRY_SCOPE_DRAFT"
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
            return EntryScopeVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        combination_id = _manifest_uuid(parent, "combination_set_version_id", "预算组合尚未生成")
        combination_version = await session.get(BudgetCombinationSetVersion, combination_id)
        if combination_version is None:
            raise DomainError(code="COMBINATION_SET_NOT_FOUND", message="预算组合版本不存在", status_code=404)
        await _require_combination_confirmation(session, combination_version)
        combinations = list(
            await session.scalars(
                select(BudgetCombination).where(
                    BudgetCombination.combination_set_version_id == combination_id
                )
            )
        )
        valid_keys = {_public_key(row) for row in combinations}
        rows_content: list[dict[str, object]] = []
        for item in payload.items:
            key = (
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            )
            if item.fiscal_year != budget.fiscal_year:
                raise DomainError(
                    code="ENTRY_SCOPE_FISCAL_YEAR_MISMATCH",
                    message="入口范围例外财年必须与当前年度预算一致",
                    status_code=422,
                    details={"combination_key": list(key)},
                )
            if key not in valid_keys:
                raise DomainError(
                    code="ENTRY_SCOPE_COMBINATION_NOT_FOUND",
                    message="入口范围例外必须精确匹配当前预算组合",
                    status_code=422,
                    details={"combination_key": list(key)},
                )
            rows_content.append(
                {
                    "fiscal_year": item.fiscal_year,
                    "track": key[0],
                    "product": key[1],
                    "grade": key[2],
                    "subject": key[3],
                    "campus": key[4],
                    "exception_type": item.exception_type.value,
                    "reason": item.reason.strip(),
                }
            )
        rows_content.sort(
            key=lambda row: (
                row["track"],
                row["product"],
                row["grade"],
                row["subject"],
                row["campus"],
                row["exception_type"],
            )
        )
        include_keys = {
            (
                str(row["track"]),
                str(row["product"]),
                str(row["grade"]),
                str(row["subject"]),
                str(row["campus"]),
            )
            for row in rows_content
            if row["exception_type"] == "INCLUDE"
        }
        exclude_keys = {
            (
                str(row["track"]),
                str(row["product"]),
                str(row["grade"]),
                str(row["subject"]),
                str(row["campus"]),
            )
            for row in rows_content
            if row["exception_type"] == "EXCLUDE"
        }
        evaluation_content = [
            _scope_evaluation_content(
                row,
                include_match=_public_key(row) in include_keys,
                exclude_match=_public_key(row) in exclude_keys,
            )
            for row in sorted(combinations, key=lambda row: row.combination_stable_key)
            if row.enabled
        ]
        occurred_at = _now()
        version_id = uuid7()
        version_hash = content_hash(
            {
                "schema_version": "1.0",
                "combination_set_version_id": str(combination_id),
                "fiscal_year": budget.fiscal_year,
                "default_rule_version": ENTRY_SCOPE_RULE_VERSION,
                "rows": rows_content,
                "evaluations": evaluation_content,
            }
        )
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates={"entry_scope_version_id": str(version_id)},
            component_removals=(
                "entry_detail_set_version_id",
                "planned_new_student_version_id",
                "headcount_result_version_id",
                "class_size_result_version_id",
                "revenue_result_version_id",
                "target_closure_result_version_id",
                "optimization_run_id",
                "discount_unlock_id",
                "formal_confirmation_id",
            ),
        )
        version = EntryScopeVersion(
            entry_scope_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            combination_set_version_id=combination_id,
            fiscal_year=budget.fiscal_year,
            default_rule_version=ENTRY_SCOPE_RULE_VERSION,
            record_count=len(rows_content),
            content_hash=version_hash,
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows = [
            EntryScopeException(
                entry_scope_exception_id=uuid7(),
                entry_scope_version_id=version_id,
                **row,
                content_hash=content_hash(row),
            )
            for row in rows_content
        ]
        session.add_all(rows)
        evaluations = [
            EntryScopeEvaluation(
                entry_scope_evaluation_id=uuid7(),
                entry_scope_version_id=version_id,
                **{
                    **row,
                    "combination_id": uuid.UUID(str(row["combination_id"])),
                },
                content_hash=content_hash(row),
            )
            for row in evaluation_content
        ]
        session.add_all(evaluations)
        response = _scope_response(version, rows, evaluations, revision_response)
        _add_audit(
            session,
            event_type="ENTRY_SCOPE_DRAFT_REPLACED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="ENTRY_SCOPE_VERSION",
            object_key={"entry_scope_version_id": str(version_id)},
            detail={"record_count": len(rows), "content_hash": version_hash},
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


async def get_entry_scope_version(
    session: AsyncSession, *, actor: Actor, entry_scope_version_id: uuid.UUID
) -> EntryScopeVersionResponse:
    version = await session.get(EntryScopeVersion, entry_scope_version_id)
    if version is None:
        raise DomainError(code="ENTRY_SCOPE_NOT_FOUND", message="入口范围版本不存在", status_code=404)
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        await session.scalars(
            select(EntryScopeException)
            .where(EntryScopeException.entry_scope_version_id == entry_scope_version_id)
            .order_by(EntryScopeException.entry_scope_exception_id)
        )
    )
    evaluations = list(
        await session.scalars(
            select(EntryScopeEvaluation)
            .where(EntryScopeEvaluation.entry_scope_version_id == entry_scope_version_id)
            .order_by(EntryScopeEvaluation.combination_stable_key)
        )
    )
    return _scope_response(version, rows, evaluations, _revision_link_response(revision))


async def confirm_entry_scope(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: EntryScopeConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> EntryScopeConfirmationResponse:
    command_type = "CONFIRM_ENTRY_SCOPE"
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
            return EntryScopeConfirmationResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if revision.component_manifest.get("entry_scope_version_id") != str(payload.entry_scope_version_id):
            raise DomainError(
                code="ENTRY_SCOPE_NOT_CURRENT",
                message="待确认入口范围不是当前预算修订引用的版本",
                status_code=409,
            )
        version = await session.get(EntryScopeVersion, payload.entry_scope_version_id)
        if version is None:
            raise DomainError(code="ENTRY_SCOPE_NOT_FOUND", message="入口范围版本不存在", status_code=404)
        current_combination_id = _manifest_uuid(revision, "combination_set_version_id", "预算组合尚未生成")
        if current_combination_id != version.combination_set_version_id:
            raise DomainError(
                code="COMBINATION_VERSION_CHANGED",
                message="预算组合已变化，必须重新维护入口范围",
                status_code=409,
            )
        combination = await session.get(BudgetCombinationSetVersion, current_combination_id)
        if combination is None:
            raise DomainError(code="COMBINATION_SET_NOT_FOUND", message="预算组合版本不存在", status_code=404)
        await _require_combination_confirmation(session, combination)
        existing = await session.scalar(
            select(EntryScopeConfirmation).where(
                EntryScopeConfirmation.entry_scope_version_id == version.entry_scope_version_id,
                EntryScopeConfirmation.input_fingerprint == version.content_hash,
            )
        )
        occurred_at = _now()
        if existing is None:
            existing = EntryScopeConfirmation(
                entry_scope_confirmation_id=uuid7(),
                entry_scope_version_id=version.entry_scope_version_id,
                confirmed_revision_id=revision.budget_revision_id,
                input_fingerprint=version.content_hash,
                confirmed_by=actor.user_id,
                role_snapshot=_role_snapshot(actor),
                confirmed_at=occurred_at,
            )
            session.add(existing)
        response = EntryScopeConfirmationResponse(
            entry_scope_confirmation_id=existing.entry_scope_confirmation_id,
            entry_scope_version_id=existing.entry_scope_version_id,
            confirmed_revision_id=existing.confirmed_revision_id,
            input_fingerprint=existing.input_fingerprint,
            confirmed_by=existing.confirmed_by,
            confirmed_at=existing.confirmed_at,
        )
        _add_audit(
            session,
            event_type="ENTRY_SCOPE_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="ENTRY_SCOPE_VERSION",
            object_key={"entry_scope_version_id": str(version.entry_scope_version_id)},
            detail={"input_fingerprint": version.content_hash, "record_count": version.record_count},
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


async def calculate_entry_details(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: EntryDetailCalculateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> EntryDetailSetVersionResponse:
    command_type = "CALCULATE_ENTRY_DETAILS"
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
            return EntryDetailSetVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        combination_id = _manifest_uuid(parent, "combination_set_version_id", "预算组合尚未生成")
        scope_id = _manifest_uuid(parent, "entry_scope_version_id", "入口范围尚未确认")
        campus_set_id = _manifest_uuid(parent, "campus_entry_set_version_id", "校区入口确定值尚未确认")
        combination_version = await session.get(BudgetCombinationSetVersion, combination_id)
        scope_version = await session.get(EntryScopeVersion, scope_id)
        campus_set = await session.get(CampusEntrySetVersion, campus_set_id)
        if combination_version is None or scope_version is None or campus_set is None:
            raise DomainError(
                code="ENTRY_DETAIL_DEPENDENCY_MISSING", message="入口明细依赖版本不存在", status_code=409
            )
        await _require_combination_confirmation(session, combination_version)
        await _require_scope_confirmation(session, scope_version)
        await _require_campus_confirmation(session, campus_set)
        if scope_version.combination_set_version_id != combination_id:
            raise DomainError(
                code="ENTRY_SCOPE_COMBINATION_MISMATCH",
                message="入口范围与当前预算组合不一致",
                status_code=409,
            )
        combinations = list(
            await session.scalars(
                select(BudgetCombination).where(
                    BudgetCombination.combination_set_version_id == combination_id,
                    BudgetCombination.enabled.is_(True),
                )
            )
        )
        evaluations = list(
            await session.scalars(
                select(EntryScopeEvaluation).where(EntryScopeEvaluation.entry_scope_version_id == scope_id)
            )
        )
        evaluation_by_combination = {item.combination_id: item for item in evaluations}
        effective: list[tuple[BudgetCombination, dict[str, object]]] = []
        for row in combinations:
            evaluation = evaluation_by_combination.get(row.combination_id)
            if evaluation is None:
                raise DomainError(
                    code="ENTRY_SCOPE_EVALUATION_MISSING",
                    message="入口范围缺少预算组合判定证据",
                    status_code=409,
                )
            evidence = {
                "schema_version": "1.0",
                "default_match": evaluation.default_match,
                "manual_include_match": evaluation.manual_include_match,
                "manual_exclude_match": evaluation.manual_exclude_match,
                "effective_source": evaluation.effective_source,
            }
            if evaluation.effective_entry_attribute == "ENTRY":
                effective.append((row, evidence))

        manual_by_key = {
            (
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            ): item
            for item in payload.manual_reservations
        }
        expected_manual_keys = {_public_key(row) for row, _ in effective if row.origin_type == "MANUAL"}
        if set(manual_by_key) != expected_manual_keys:
            raise DomainError(
                code="MANUAL_ENTRY_RESERVATION_SET_MISMATCH",
                message="人工入口预留必须完整且仅覆盖当前有效的人工新增入口组合",
                status_code=422,
                details={
                    "missing": [list(key) for key in sorted(expected_manual_keys - set(manual_by_key))],
                    "unexpected": [list(key) for key in sorted(set(manual_by_key) - expected_manual_keys)],
                },
            )
        source_fiscal_season = await _source_entry_season(combination_version, session=session)
        source_rows = list(
            await session.scalars(
                select(SourceMetricAggregate).where(
                    SourceMetricAggregate.source_snapshot_id == combination_version.source_snapshot_id,
                    SourceMetricAggregate.metric_code == "NEW_STUDENT_SHARE_REFERENCE",
                    SourceMetricAggregate.fiscal_season == source_fiscal_season,
                )
            )
        )
        history_by_key: dict[tuple[str, str, str, str, str], int] = {}
        for source_row in source_rows:
            if source_row.numerator != source_row.numerator.to_integral_value():
                raise DomainError(
                    code="ENTRY_HISTORY_NOT_INTEGER", message="入口历史业务新生人次不是整数", status_code=409
                )
            key = (
                source_row.track,
                source_row.product,
                source_row.grade,
                source_row.subject,
                source_row.campus,
            )
            history_by_key[key] = history_by_key.get(key, 0) + int(source_row.numerator)
        campus_rows = list(
            await session.scalars(
                select(CampusEntryAllocation).where(
                    CampusEntryAllocation.campus_entry_set_version_id == campus_set_id
                )
            )
        )
        campus_values = {
            row.campus: row.determined_entry_value
            for row in campus_rows
            if row.determined_entry_value is not None
        }
        by_campus: dict[str, list[tuple[BudgetCombination, dict[str, object]]]] = {}
        for item in effective:
            by_campus.setdefault(item[0].campus, []).append(item)
        if set(by_campus) != set(campus_values):
            raise DomainError(
                code="ENTRY_DETAIL_CAMPUS_SET_MISMATCH",
                message="有效入口组合必须完整覆盖已确认校区入口确定值中的全部校区",
                status_code=422,
                details={
                    "missing_entry_combinations": sorted(set(campus_values) - set(by_campus)),
                    "unexpected_entry_campuses": sorted(set(by_campus) - set(campus_values)),
                },
            )
        rows_content: list[dict[str, object]] = []
        allocated_entry_total = 0
        zero_initial_count = 0
        for campus in sorted(campus_values):
            campus_value = campus_values[campus]
            assert campus_value is not None
            campus_combinations = by_campus[campus]
            allocation_inputs = [
                EntryDetailAllocationInput(
                    combination_stable_key=row.combination_stable_key,
                    origin_type=row.origin_type,
                    historical_business_new_students=history_by_key.get(_public_key(row), 0),
                    manual_reservation_ratio_ppm=(
                        manual_by_key[_public_key(row)].reservation_ratio_ppm
                        if row.origin_type == "MANUAL"
                        else None
                    ),
                )
                for row, _ in campus_combinations
            ]
            try:
                allocated = allocate_entry_details(campus_entry_value=campus_value, items=allocation_inputs)
            except EntryDetailAllocationError as exc:
                raise DomainError(
                    code=exc.code, message=f"校区 {campus}：{exc.message}", status_code=422
                ) from exc
            row_by_stable = {
                row.combination_stable_key: (row, evidence) for row, evidence in campus_combinations
            }
            for result in allocated:
                combination, evidence = row_by_stable[result.combination_stable_key]
                initial = result.entry_initial_value
                allocated_entry_total += initial
                zero_initial_count += int(initial == 0)
                manual_input = manual_by_key.get(_public_key(combination))
                rows_content.append(
                    {
                        "combination_id": str(combination.combination_id),
                        "combination_stable_key": combination.combination_stable_key,
                        "track": combination.track,
                        "product": combination.product,
                        "grade": combination.grade,
                        "subject": combination.subject,
                        "campus": combination.campus,
                        "effective_entry_attribute": "ENTRY",
                        "scope_evidence": evidence,
                        "origin_type": combination.origin_type,
                        "historical_business_new_students": result.historical_business_new_students,
                        "manual_reservation_ratio_ppm": result.manual_reservation_ratio_ppm,
                        "manual_reservation_reason": manual_input.reason.strip()
                        if manual_input is not None
                        else None,
                        "allocation_source_type": result.allocation_source_type,
                        "theoretical_value": result.theoretical_value,
                        "floor_value": result.floor_value,
                        "fraction_value": result.fraction_value,
                        "tail_assigned": result.tail_assigned,
                        "entry_initial_value": initial,
                        "soft_lower_bound": (4 * initial + 4) // 5 if initial > 0 else None,
                        "soft_upper_bound": (6 * initial) // 5 if initial > 0 else None,
                        "candidate_eligible": initial > 0,
                        "candidate_blocking_reason": None
                        if initial > 0
                        else "ZERO_INITIAL_REQUIRES_PLAN_AND_HARD_BOUNDS",
                    }
                )
        rows_content.sort(key=lambda row: str(row["combination_stable_key"]))
        campus_entry_total = sum(value for value in campus_values.values() if value is not None)
        occurred_at = _now()
        version_id = uuid7()
        version_content = {
            "schema_version": "1.0",
            "combination_set_version_id": str(combination_id),
            "entry_scope_version_id": str(scope_id),
            "campus_entry_set_version_id": str(campus_set_id),
            "source_snapshot_id": str(combination_version.source_snapshot_id),
            "source_fiscal_season": source_fiscal_season,
            "rows": rows_content,
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
            component_updates={"entry_detail_set_version_id": str(version_id)},
            component_removals=(
                "planned_new_student_version_id",
                "headcount_result_version_id",
                "class_size_result_version_id",
                "revenue_result_version_id",
                "target_closure_result_version_id",
                "optimization_run_id",
                "discount_unlock_id",
                "formal_confirmation_id",
            ),
        )
        version = EntryDetailSetVersion(
            entry_detail_set_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            combination_set_version_id=combination_id,
            entry_scope_version_id=scope_id,
            campus_entry_set_version_id=campus_set_id,
            source_snapshot_id=combination_version.source_snapshot_id,
            fiscal_year=budget.fiscal_year,
            source_fiscal_season=source_fiscal_season,
            campus_entry_total=campus_entry_total,
            allocated_entry_total=allocated_entry_total,
            record_count=len(rows_content),
            zero_initial_count=zero_initial_count,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows = [
            EntryDetailAllocation(
                entry_detail_allocation_id=uuid7(),
                entry_detail_set_version_id=version_id,
                **{
                    **row,
                    "combination_id": uuid.UUID(str(row["combination_id"])),
                },
                content_hash=content_hash(row),
            )
            for row in rows_content
        ]
        session.add_all(rows)
        response = _detail_response(version, rows, revision_response)
        _add_audit(
            session,
            event_type="ENTRY_DETAILS_CALCULATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="ENTRY_DETAIL_SET_VERSION",
            object_key={"entry_detail_set_version_id": str(version_id)},
            detail={
                "campus_entry_total": campus_entry_total,
                "record_count": len(rows),
                "zero_initial_count": version.zero_initial_count,
                "content_hash": version.content_hash,
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


async def get_entry_detail_set_version(
    session: AsyncSession, *, actor: Actor, entry_detail_set_version_id: uuid.UUID
) -> EntryDetailSetVersionResponse:
    version = await session.get(EntryDetailSetVersion, entry_detail_set_version_id)
    if version is None:
        raise DomainError(code="ENTRY_DETAIL_NOT_FOUND", message="入口明细版本不存在", status_code=404)
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        await session.scalars(
            select(EntryDetailAllocation)
            .where(EntryDetailAllocation.entry_detail_set_version_id == entry_detail_set_version_id)
            .order_by(EntryDetailAllocation.combination_stable_key)
        )
    )
    return _detail_response(version, rows, _revision_link_response(revision))


async def _require_combination_confirmation(
    session: AsyncSession, version: BudgetCombinationSetVersion
) -> None:
    confirmation = await session.scalar(
        select(CombinationSetConfirmation).where(
            CombinationSetConfirmation.combination_set_version_id == version.combination_set_version_id,
            CombinationSetConfirmation.input_fingerprint == version.content_hash,
        )
    )
    if confirmation is None:
        raise DomainError(
            code="COMBINATION_SET_NOT_CONFIRMED",
            message="当前预算组合必须先由部门预算负责人确认",
            status_code=409,
        )


async def _require_scope_confirmation(session: AsyncSession, version: EntryScopeVersion) -> None:
    confirmation = await session.scalar(
        select(EntryScopeConfirmation).where(
            EntryScopeConfirmation.entry_scope_version_id == version.entry_scope_version_id,
            EntryScopeConfirmation.input_fingerprint == version.content_hash,
        )
    )
    if confirmation is None:
        raise DomainError(
            code="ENTRY_SCOPE_NOT_CONFIRMED",
            message="入口范围必须先由部门预算负责人确认",
            status_code=409,
        )


async def _require_campus_confirmation(session: AsyncSession, version: CampusEntrySetVersion) -> None:
    confirmation = await session.scalar(
        select(CampusEntryConfirmation).where(
            CampusEntryConfirmation.campus_entry_set_version_id == version.campus_entry_set_version_id,
            CampusEntryConfirmation.input_fingerprint == version.content_hash,
        )
    )
    if confirmation is None:
        raise DomainError(
            code="CAMPUS_ENTRY_SET_NOT_CONFIRMED",
            message="校区入口确定值必须先由部门预算负责人整套确认",
            status_code=409,
        )


async def _source_entry_season(version: BudgetCombinationSetVersion, *, session: AsyncSession) -> int:
    rate_version = await session.get(RateReferenceVersion, version.rate_reference_version_id)
    seasons = (
        rate_version.maturity_evidence.get("mature_source_fiscal_seasons")
        if rate_version is not None
        else None
    )
    valid_seasons = (
        [item for item in seasons if isinstance(item, int) and item > 0] if isinstance(seasons, list) else []
    )
    if not isinstance(seasons, list) or not seasons or len(valid_seasons) != len(seasons):
        raise DomainError(
            code="ENTRY_SOURCE_SEASON_MISSING",
            message="比例参考版本缺少成熟来源季节，无法确定入口历史季度",
            status_code=409,
        )
    return min(valid_seasons)


def _public_key(row: BudgetCombination) -> tuple[str, str, str, str, str]:
    return row.track, row.product, row.grade, row.subject, row.campus


def _exception_key(row: EntryScopeException) -> tuple[str, str, str, str, str]:
    return row.track, row.product, row.grade, row.subject, row.campus


def _scope_evaluation_content(
    row: BudgetCombination, *, include_match: bool, exclude_match: bool
) -> dict[str, object]:
    decision = evaluate_entry_scope(
        grade=row.grade,
        manual_include_match=include_match,
        manual_exclude_match=exclude_match,
    )
    return {
        "combination_id": str(row.combination_id),
        "combination_stable_key": row.combination_stable_key,
        "default_match": decision.default_match,
        "manual_include_match": decision.manual_include_match,
        "manual_exclude_match": decision.manual_exclude_match,
        "effective_entry_attribute": decision.effective_entry_attribute,
        "effective_source": decision.effective_source,
    }


def _scope_exception_response(row: EntryScopeException) -> EntryScopeExceptionResponse:
    return EntryScopeExceptionResponse(
        entry_scope_exception_id=row.entry_scope_exception_id,
        fiscal_year=row.fiscal_year,
        track=row.track,
        product=row.product,
        grade=row.grade,
        subject=row.subject,
        campus=row.campus,
        exception_type=row.exception_type,
        reason=row.reason,
        content_hash=row.content_hash,
    )


def _scope_evaluation_response(row: EntryScopeEvaluation) -> EntryScopeEvaluationResponse:
    return EntryScopeEvaluationResponse(
        entry_scope_evaluation_id=row.entry_scope_evaluation_id,
        combination_id=row.combination_id,
        combination_stable_key=row.combination_stable_key,
        default_match=row.default_match,
        manual_include_match=row.manual_include_match,
        manual_exclude_match=row.manual_exclude_match,
        effective_entry_attribute=row.effective_entry_attribute,
        effective_source=row.effective_source,
        content_hash=row.content_hash,
    )


def _scope_response(
    version: EntryScopeVersion,
    rows: list[EntryScopeException],
    evaluations: list[EntryScopeEvaluation],
    revision: RevisionCommandResponse,
) -> EntryScopeVersionResponse:
    return EntryScopeVersionResponse(
        entry_scope_version_id=version.entry_scope_version_id,
        budget_revision_id=version.budget_revision_id,
        combination_set_version_id=version.combination_set_version_id,
        fiscal_year=version.fiscal_year,
        default_rule_version=version.default_rule_version,
        record_count=version.record_count,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[
            _scope_exception_response(row)
            for row in sorted(
                rows,
                key=lambda item: (
                    item.track,
                    item.product,
                    item.grade,
                    item.subject,
                    item.campus,
                    item.exception_type,
                ),
            )
        ],
        evaluations=[_scope_evaluation_response(row) for row in evaluations],
        revision=revision,
    )


def _detail_allocation_response(row: EntryDetailAllocation) -> EntryDetailAllocationResponse:
    return EntryDetailAllocationResponse(
        entry_detail_allocation_id=row.entry_detail_allocation_id,
        combination_id=row.combination_id,
        combination_stable_key=row.combination_stable_key,
        track=row.track,
        product=row.product,
        grade=row.grade,
        subject=row.subject,
        campus=row.campus,
        effective_entry_attribute=row.effective_entry_attribute,
        scope_evidence=row.scope_evidence,
        origin_type=row.origin_type,
        historical_business_new_students=row.historical_business_new_students,
        manual_reservation_ratio_ppm=row.manual_reservation_ratio_ppm,
        manual_reservation_reason=row.manual_reservation_reason,
        allocation_source_type=row.allocation_source_type,
        theoretical_value=row.theoretical_value.quantize(DETAIL_DECIMAL_QUANTUM),
        floor_value=row.floor_value,
        fraction_value=row.fraction_value.quantize(DETAIL_DECIMAL_QUANTUM),
        tail_assigned=row.tail_assigned,
        entry_initial_value=row.entry_initial_value,
        soft_lower_bound=row.soft_lower_bound,
        soft_upper_bound=row.soft_upper_bound,
        candidate_eligible=row.candidate_eligible,
        candidate_blocking_reason=row.candidate_blocking_reason,
        content_hash=row.content_hash,
    )


def _detail_response(
    version: EntryDetailSetVersion,
    rows: list[EntryDetailAllocation],
    revision: RevisionCommandResponse,
) -> EntryDetailSetVersionResponse:
    ordered = sorted(rows, key=lambda row: row.combination_stable_key)
    return EntryDetailSetVersionResponse(
        entry_detail_set_version_id=version.entry_detail_set_version_id,
        budget_revision_id=version.budget_revision_id,
        combination_set_version_id=version.combination_set_version_id,
        entry_scope_version_id=version.entry_scope_version_id,
        campus_entry_set_version_id=version.campus_entry_set_version_id,
        source_snapshot_id=version.source_snapshot_id,
        fiscal_year=version.fiscal_year,
        source_fiscal_season=version.source_fiscal_season,
        campus_entry_total=version.campus_entry_total,
        allocated_entry_total=version.allocated_entry_total,
        record_count=version.record_count,
        zero_initial_count=version.zero_initial_count,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[_detail_allocation_response(row) for row in ordered],
        revision=revision,
    )
