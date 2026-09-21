import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    BudgetCombinationResponse,
    CampusEntryAllocationResponse,
    CampusEntryConfirmationResponse,
    CampusEntryConfirmRequest,
    CampusEntryReplaceDraftRequest,
    CampusEntrySetVersionResponse,
    CampusEntrySuggestionResponse,
    CampusSuggestionGenerateRequest,
    CampusSuggestionVersionResponse,
    CombinationConfirmationResponse,
    CombinationConfirmRequest,
    CombinationGenerateRequest,
    CombinationReplaceDraftRequest,
    CombinationSetVersionResponse,
    EntryTotalLockRequest,
    EntryTotalVersionResponse,
    RevisionCommandResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.entry_planning.allocation import (
    CampusAllocationInput,
    EntryAllocationError,
    allocate_campus_entry_suggestions,
)
from packages.persistence.ids import uuid7
from packages.persistence.jobs import requeue_budget_calculation_job
from packages.persistence.models import (
    AnnualBudget,
    BudgetCombination,
    BudgetCombinationSetVersion,
    BudgetRevision,
    CampusEntryAllocation,
    CampusEntryConfirmation,
    CampusEntrySetVersion,
    CampusEntrySuggestion,
    CampusEntrySuggestionVersion,
    CombinationSetConfirmation,
    EntryHistoryAggregate,
    EntryTotalVersion,
    RateReferenceVersion,
    RevisionLifecycleEvent,
    SourceMetricAggregate,
)
from packages.persistence.versioning import (
    _add_audit,
    _assert_budget_access,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)

ENTRY_SCOPE_RULE_VERSION = "default-first-season-grade-seven-v1"
COMBINATION_DECIMAL_QUANTUM = Decimal("0.000001")
CAMPUS_ALLOCATION_DECIMAL_QUANTUM = Decimal("0.000000001")


async def _lock_current_revision(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
) -> tuple[BudgetRevision, AnnualBudget]:
    if revision_id != expected_revision_id:
        raise DomainError(
            code="REVISION_CONFLICT", message="If-Match 与路径中的预算修订不一致", status_code=409
        )
    parent = await session.get(BudgetRevision, revision_id)
    if parent is None:
        raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
    budget = await session.scalar(
        select(AnnualBudget).where(AnnualBudget.annual_budget_id == parent.annual_budget_id).with_for_update()
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
    return parent, budget


async def _create_child_revision(
    session: AsyncSession,
    *,
    parent: BudgetRevision,
    budget: AnnualBudget,
    actor: Actor,
    command_type: str,
    idempotency_key: uuid.UUID,
    request_hash: str,
    occurred_at: datetime,
    component_updates: dict[str, str],
    component_removals: tuple[str, ...] = (),
) -> tuple[BudgetRevision, RevisionCommandResponse]:
    if parent.source_snapshot_id is None or parent.parameter_version_id is None:
        raise DomainError(
            code="LEGACY_REVISION_INCOMPLETE", message="当前修订缺少来源快照或参数版本", status_code=409
        )
    manifest = dict(parent.component_manifest)
    for key in component_removals:
        manifest.pop(key, None)
    manifest.update(component_updates)
    manifest["schema_version"] = "1.0"
    latest_revision_no = await session.scalar(
        select(func.max(BudgetRevision.revision_no)).where(
            BudgetRevision.annual_budget_id == budget.annual_budget_id
        )
    )
    revision_id = uuid7()
    revision = BudgetRevision(
        budget_revision_id=revision_id,
        annual_budget_id=budget.annual_budget_id,
        revision_no=(latest_revision_no or 0) + 1,
        parent_revision_id=parent.budget_revision_id,
        command_type=command_type,
        idempotency_key=str(idempotency_key),
        request_hash=request_hash,
        source_snapshot_id=parent.source_snapshot_id,
        parameter_version_id=parent.parameter_version_id,
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
    return revision, RevisionCommandResponse(
        annual_budget_id=budget.annual_budget_id,
        previous_revision_id=parent.budget_revision_id,
        new_revision_id=revision_id,
        links={
            "revision": f"/api/v1/revisions/{revision_id}",
            "annual_budget": f"/api/v1/annual-budgets/{budget.annual_budget_id}",
        },
    )


async def generate_combination_draft(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: CombinationGenerateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> CombinationSetVersionResponse:
    command_type = "GENERATE_COMBINATION_DRAFT"
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
            return CombinationSetVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        rate_version = await session.get(RateReferenceVersion, payload.rate_reference_version_id)
        if (
            rate_version is None
            or rate_version.source_snapshot_id != parent.source_snapshot_id
            or rate_version.target_fiscal_year != budget.fiscal_year
        ):
            raise DomainError(
                code="RATE_REFERENCE_VERSION_MISMATCH",
                message="比例参考版本与当前预算来源快照或目标财年不一致",
                status_code=409,
            )
        metric_rows = list(
            await session.scalars(
                select(SourceMetricAggregate).where(
                    SourceMetricAggregate.source_snapshot_id == parent.source_snapshot_id,
                    SourceMetricAggregate.metric_code == "NEW_STUDENT_SHARE_REFERENCE",
                )
            )
        )
        history_by_key: dict[tuple[str, str, str, str, str], Decimal] = {}
        for item in metric_rows:
            key = (item.track, item.product, item.grade, item.subject, item.campus)
            history_by_key[key] = history_by_key.get(key, Decimal(0)) + item.denominator
        enabled = [(key, value) for key, value in history_by_key.items() if value > 0]
        if not enabled:
            raise DomainError(
                code="NO_HISTORICAL_COMBINATIONS",
                message="冻结快照中没有成熟季节有效招生人次大于 0 的历史组合",
                status_code=409,
            )
        occurred_at = _now()
        version_id = uuid7()
        rows_content = [
            {
                "track": key[0],
                "product": key[1],
                "grade": key[2],
                "subject": key[3],
                "campus": key[4],
                "campus_stable_key": _campus_key(actor.department_id, key[4]),
                "combination_stable_key": _combination_stable_key(actor.department_id, key),
                "entry_attribute": "ENTRY" if key[2] == "七年级" else "NON_ENTRY",
                "origin_type": "HISTORICAL",
                "enabled": True,
                "historical_enrollment": value,
                "change_reason": None,
                "class_size_parameter_reference": None,
            }
            for key, value in sorted(enabled)
        ]
        version_hash = content_hash(
            {
                "schema_version": "1.0",
                "source_snapshot_id": str(parent.source_snapshot_id),
                "rate_reference_version_id": str(rate_version.rate_reference_version_id),
                "target_fiscal_year": budget.fiscal_year,
                "entry_scope_rule_version": ENTRY_SCOPE_RULE_VERSION,
                "rows": rows_content,
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
            component_updates={"combination_set_version_id": str(version_id)},
            component_removals=(
                "entry_scope_version_id",
                "campus_entry_suggestion_version_id",
                "campus_entry_set_version_id",
                "entry_detail_set_version_id",
                "planned_new_student_version_id",
                "resource_finance_parameter_version_id",
                "headcount_result_version_id",
                "class_size_result_version_id",
                "revenue_result_version_id",
                "target_closure_result_version_id",
                "optimization_run_id",
                "discount_unlock_id",
                "formal_confirmation_id",
            ),
        )
        await session.flush()
        version = BudgetCombinationSetVersion(
            combination_set_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            source_snapshot_id=parent.source_snapshot_id,
            rate_reference_version_id=rate_version.rate_reference_version_id,
            target_fiscal_year=budget.fiscal_year,
            entry_scope_rule_version=ENTRY_SCOPE_RULE_VERSION,
            record_count=len(rows_content),
            content_hash=version_hash,
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        combination_rows = [
            BudgetCombination(
                combination_id=uuid7(),
                combination_set_version_id=version_id,
                **row,
                content_hash=content_hash(row),
            )
            for row in rows_content
        ]
        session.add_all(combination_rows)
        response = CombinationSetVersionResponse(
            combination_set_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            source_snapshot_id=parent.source_snapshot_id,
            rate_reference_version_id=rate_version.rate_reference_version_id,
            target_fiscal_year=budget.fiscal_year,
            entry_scope_rule_version=ENTRY_SCOPE_RULE_VERSION,
            record_count=len(rows_content),
            content_hash=version_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
            items=[_combination_response(row) for row in combination_rows],
            revision=revision_response,
        )
        _add_audit(
            session,
            event_type="COMBINATION_DRAFT_GENERATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="COMBINATION_SET_VERSION",
            object_key={"combination_set_version_id": str(version_id)},
            detail={"record_count": len(rows_content), "content_hash": version_hash},
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


async def get_combination_set_version(
    session: AsyncSession, *, actor: Actor, combination_set_version_id: uuid.UUID
) -> CombinationSetVersionResponse:
    version = await session.get(BudgetCombinationSetVersion, combination_set_version_id)
    if version is None:
        raise DomainError(code="COMBINATION_SET_NOT_FOUND", message="预算组合版本不存在", status_code=404)
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        await session.scalars(
            select(BudgetCombination)
            .where(BudgetCombination.combination_set_version_id == combination_set_version_id)
            .order_by(
                BudgetCombination.track,
                BudgetCombination.product,
                BudgetCombination.grade,
                BudgetCombination.subject,
                BudgetCombination.campus,
            )
        )
    )
    return CombinationSetVersionResponse(
        combination_set_version_id=version.combination_set_version_id,
        budget_revision_id=version.budget_revision_id,
        source_snapshot_id=version.source_snapshot_id,
        rate_reference_version_id=version.rate_reference_version_id,
        target_fiscal_year=version.target_fiscal_year,
        entry_scope_rule_version=version.entry_scope_rule_version,
        record_count=version.record_count,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[_combination_response(row) for row in rows],
        revision=_revision_link_response(revision),
    )


async def replace_combination_draft(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: CombinationReplaceDraftRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> CombinationSetVersionResponse:
    command_type = "REPLACE_COMBINATION_DRAFT"
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
            return CombinationSetVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        base_version_id = _manifest_uuid(parent, "combination_set_version_id", "预算组合尚未生成")
        base_version = await session.get(BudgetCombinationSetVersion, base_version_id)
        if base_version is None:
            raise DomainError(code="COMBINATION_SET_NOT_FOUND", message="预算组合版本不存在", status_code=404)
        base_rows = list(
            await session.scalars(
                select(BudgetCombination).where(
                    BudgetCombination.combination_set_version_id == base_version_id
                )
            )
        )
        base_by_key = {_combination_public_key(row): row for row in base_rows}
        requested_by_key = {
            (
                item.track.strip(),
                item.product.strip(),
                item.grade.strip(),
                item.subject.strip(),
                item.campus.strip(),
            ): item
            for item in payload.items
        }
        missing = sorted(set(base_by_key) - set(requested_by_key))
        if missing:
            raise DomainError(
                code="COMBINATION_SET_INCOMPLETE",
                message="组合草稿必须保留全部既有组合；关闭组合请显式设置 enabled=false",
                status_code=422,
                details={"missing": [list(key) for key in missing]},
            )

        rows_content: list[dict[str, object]] = []
        for key in sorted(requested_by_key):
            item = requested_by_key[key]
            existing = base_by_key.get(key)
            reason = item.change_reason.strip() if item.change_reason else None
            class_size_reference = (
                item.class_size_parameter_reference.strip() if item.class_size_parameter_reference else None
            )
            if existing is None:
                if not item.enabled:
                    raise DomainError(
                        code="NEW_COMBINATION_MUST_BE_ENABLED",
                        message="新增预算组合不能以关闭状态加入草稿",
                        status_code=422,
                        details={"combination_key": list(key)},
                    )
                if not reason or not class_size_reference:
                    raise DomainError(
                        code="MANUAL_COMBINATION_EVIDENCE_REQUIRED",
                        message="无历史组合人工启用时必须填写原因和班均参数引用",
                        status_code=422,
                        details={"combination_key": list(key)},
                    )
                origin_type = "MANUAL"
                historical_enrollment = Decimal(0)
                campus_key = _campus_key(actor.department_id, key[4])
                combination_key = _combination_stable_key(actor.department_id, key)
            else:
                changed = existing.enabled != item.enabled or existing.entry_attribute != item.entry_attribute
                if changed and not reason:
                    raise DomainError(
                        code="COMBINATION_CHANGE_REASON_REQUIRED",
                        message="关闭、重新启用或调整入口属性时必须填写原因",
                        status_code=422,
                        details={"combination_key": list(key)},
                    )
                origin_type = existing.origin_type
                historical_enrollment = existing.historical_enrollment
                campus_key = existing.campus_stable_key
                combination_key = existing.combination_stable_key
                if origin_type == "MANUAL":
                    reason = reason or existing.change_reason
                    class_size_reference = class_size_reference or existing.class_size_parameter_reference
                    if not reason or not class_size_reference:
                        raise DomainError(
                            code="MANUAL_COMBINATION_EVIDENCE_REQUIRED",
                            message="人工预算组合必须始终保留原因和班均参数引用",
                            status_code=422,
                            details={"combination_key": list(key)},
                        )
                else:
                    class_size_reference = None
                    if not changed:
                        reason = None
            rows_content.append(
                {
                    "track": key[0],
                    "product": key[1],
                    "grade": key[2],
                    "subject": key[3],
                    "campus": key[4],
                    "campus_stable_key": campus_key,
                    "combination_stable_key": combination_key,
                    "entry_attribute": item.entry_attribute,
                    "origin_type": origin_type,
                    "enabled": item.enabled,
                    "historical_enrollment": historical_enrollment,
                    "change_reason": reason,
                    "class_size_parameter_reference": class_size_reference,
                }
            )

        prior_campuses = {row.campus_stable_key for row in base_rows if row.enabled}
        next_campuses = {str(row["campus_stable_key"]) for row in rows_content if bool(row["enabled"])}
        campus_scope_changed = prior_campuses != next_campuses
        prior_products = {(row.track, row.product) for row in base_rows if row.enabled}
        next_products = {
            (str(row["track"]), str(row["product"])) for row in rows_content if bool(row["enabled"])
        }
        product_scope_changed = prior_products != next_products
        prior_discount_scope = {(row.track, row.product, row.grade) for row in base_rows if row.enabled}
        next_discount_scope = {
            (str(row["track"]), str(row["product"]), str(row["grade"]))
            for row in rows_content
            if bool(row["enabled"])
        }
        discount_scope_changed = prior_discount_scope != next_discount_scope
        prior_finance_scope = {
            (row.track, row.product, row.grade, row.subject, row.campus) for row in base_rows if row.enabled
        }
        next_finance_scope = {
            (
                str(row["track"]),
                str(row["product"]),
                str(row["grade"]),
                str(row["subject"]),
                str(row["campus"]),
            )
            for row in rows_content
            if bool(row["enabled"])
        }
        finance_scope_changed = prior_finance_scope != next_finance_scope
        version_id = uuid7()
        occurred_at = _now()
        version_hash = content_hash(
            {
                "schema_version": "1.0",
                "source_snapshot_id": str(base_version.source_snapshot_id),
                "rate_reference_version_id": str(base_version.rate_reference_version_id),
                "target_fiscal_year": base_version.target_fiscal_year,
                "entry_scope_rule_version": base_version.entry_scope_rule_version,
                "rows": rows_content,
            }
        )
        removals = [
            "entry_scope_version_id",
            "campus_entry_suggestion_version_id",
            "entry_detail_set_version_id",
            "planned_new_student_version_id",
            "headcount_result_version_id",
            "class_size_result_version_id",
            "revenue_result_version_id",
            "target_closure_result_version_id",
            "optimization_run_id",
            "discount_unlock_id",
            "formal_confirmation_id",
        ]
        if campus_scope_changed:
            removals.append("campus_entry_set_version_id")
        if product_scope_changed or finance_scope_changed:
            removals.append("resource_finance_parameter_version_id")
        if discount_scope_changed:
            removals.append("discount_parameter_version_id")
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates={"combination_set_version_id": str(version_id)},
            component_removals=tuple(removals),
        )
        version = BudgetCombinationSetVersion(
            combination_set_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            source_snapshot_id=base_version.source_snapshot_id,
            rate_reference_version_id=base_version.rate_reference_version_id,
            target_fiscal_year=base_version.target_fiscal_year,
            entry_scope_rule_version=base_version.entry_scope_rule_version,
            record_count=len(rows_content),
            content_hash=version_hash,
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        combination_rows = [
            BudgetCombination(
                combination_id=uuid7(),
                combination_set_version_id=version_id,
                **row,
                content_hash=content_hash(row),
            )
            for row in rows_content
        ]
        session.add_all(combination_rows)
        response = CombinationSetVersionResponse(
            combination_set_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            source_snapshot_id=version.source_snapshot_id,
            rate_reference_version_id=version.rate_reference_version_id,
            target_fiscal_year=version.target_fiscal_year,
            entry_scope_rule_version=version.entry_scope_rule_version,
            record_count=version.record_count,
            content_hash=version.content_hash,
            created_by=version.created_by,
            created_at=version.created_at,
            items=[_combination_response(row) for row in combination_rows],
            revision=revision_response,
        )
        _add_audit(
            session,
            event_type="COMBINATION_DRAFT_REPLACED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="COMBINATION_SET_VERSION",
            object_key={"combination_set_version_id": str(version_id)},
            detail={
                "record_count": len(rows_content),
                "campus_scope_changed": campus_scope_changed,
                "campus_entry_set_preserved": not campus_scope_changed
                and "campus_entry_set_version_id" in parent.component_manifest,
                "content_hash": version_hash,
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


async def confirm_combination_set(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: CombinationConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> CombinationConfirmationResponse:
    command_type = "CONFIRM_COMBINATION_SET"
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
            return CombinationConfirmationResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if revision.component_manifest.get("combination_set_version_id") != str(
            payload.combination_set_version_id
        ):
            raise DomainError(
                code="COMBINATION_SET_NOT_CURRENT",
                message="待确认组合版本不是当前预算修订引用的版本",
                status_code=409,
            )
        version = await session.get(BudgetCombinationSetVersion, payload.combination_set_version_id)
        if version is None:
            raise DomainError(code="COMBINATION_SET_NOT_FOUND", message="预算组合版本不存在", status_code=404)
        fingerprint = version.content_hash
        existing = await session.scalar(
            select(CombinationSetConfirmation).where(
                CombinationSetConfirmation.combination_set_version_id == version.combination_set_version_id,
                CombinationSetConfirmation.input_fingerprint == fingerprint,
            )
        )
        occurred_at = _now()
        if existing is None:
            existing = CombinationSetConfirmation(
                combination_set_confirmation_id=uuid7(),
                combination_set_version_id=version.combination_set_version_id,
                confirmed_revision_id=revision.budget_revision_id,
                input_fingerprint=fingerprint,
                confirmed_by=actor.user_id,
                role_snapshot=_role_snapshot(actor),
                summary={"schema_version": "1.0", "record_count": version.record_count},
                confirmed_at=occurred_at,
            )
            session.add(existing)
        response = CombinationConfirmationResponse(
            combination_set_confirmation_id=existing.combination_set_confirmation_id,
            combination_set_version_id=existing.combination_set_version_id,
            confirmed_revision_id=existing.confirmed_revision_id,
            input_fingerprint=existing.input_fingerprint,
            confirmed_by=existing.confirmed_by,
            confirmed_at=existing.confirmed_at,
        )
        _add_audit(
            session,
            event_type="COMBINATION_SET_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="COMBINATION_SET_VERSION",
            object_key={"combination_set_version_id": str(version.combination_set_version_id)},
            detail={"input_fingerprint": fingerprint, "record_count": version.record_count},
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


async def save_and_lock_entry_total(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: EntryTotalLockRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> EntryTotalVersionResponse:
    command_type = "SAVE_AND_LOCK_ENTRY_TOTAL"
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
            return EntryTotalVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        occurred_at = _now()
        version_id = uuid7()
        entry_content = {
            "schema_version": "1.0",
            "fiscal_year": budget.fiscal_year,
            "fiscal_year_entry_total": payload.fiscal_year_entry_total,
            "locked_value": payload.fiscal_year_entry_total,
            "change_reason": payload.change_reason.strip(),
            "locked_by": actor.user_id,
            "role_snapshot": _role_snapshot(actor),
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
            component_updates={"entry_total_version_id": str(version_id)},
            component_removals=(
                "campus_entry_suggestion_version_id",
                "campus_entry_set_version_id",
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
        await session.flush()
        version = EntryTotalVersion(
            entry_total_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            fiscal_year=budget.fiscal_year,
            fiscal_year_entry_total=payload.fiscal_year_entry_total,
            locked_value=payload.fiscal_year_entry_total,
            change_reason=payload.change_reason.strip(),
            locked_by=actor.user_id,
            role_snapshot=_role_snapshot(actor),
            locked_at=occurred_at,
            content_hash=content_hash(entry_content),
            request_hash=request_hash,
        )
        session.add(version)
        response = EntryTotalVersionResponse(
            entry_total_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            fiscal_year=budget.fiscal_year,
            fiscal_year_entry_total=payload.fiscal_year_entry_total,
            locked_value=payload.fiscal_year_entry_total,
            change_reason=payload.change_reason.strip(),
            locked_by=actor.user_id,
            locked_at=occurred_at,
            content_hash=version.content_hash,
            revision=revision_response,
        )
        _add_audit(
            session,
            event_type="ENTRY_TOTAL_LOCKED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="ENTRY_TOTAL_VERSION",
            object_key={"entry_total_version_id": str(version_id)},
            detail={
                "fiscal_year_entry_total": payload.fiscal_year_entry_total,
                "change_reason": payload.change_reason.strip(),
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


async def get_entry_total_version(
    session: AsyncSession, *, actor: Actor, entry_total_version_id: uuid.UUID
) -> EntryTotalVersionResponse:
    version = await session.get(EntryTotalVersion, entry_total_version_id)
    if version is None:
        raise DomainError(code="ENTRY_TOTAL_NOT_FOUND", message="财年入口总值版本不存在", status_code=404)
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    return EntryTotalVersionResponse(
        entry_total_version_id=version.entry_total_version_id,
        budget_revision_id=version.budget_revision_id,
        fiscal_year=version.fiscal_year,
        fiscal_year_entry_total=version.fiscal_year_entry_total,
        locked_value=version.locked_value,
        change_reason=version.change_reason,
        locked_by=version.locked_by,
        locked_at=version.locked_at,
        content_hash=version.content_hash,
        revision=_revision_link_response(revision),
    )


async def generate_campus_suggestions(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: CampusSuggestionGenerateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> CampusSuggestionVersionResponse:
    command_type = "GENERATE_CAMPUS_ENTRY_SUGGESTIONS"
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
            return CampusSuggestionVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        combination_id = _manifest_uuid(parent, "combination_set_version_id", "预算组合尚未生成")
        entry_total_id = _manifest_uuid(parent, "entry_total_version_id", "财年入口总值尚未锁定")
        combination_version = await session.get(BudgetCombinationSetVersion, combination_id)
        entry_total = await session.get(EntryTotalVersion, entry_total_id)
        if combination_version is None or entry_total is None:
            raise DomainError(
                code="ENTRY_DEPENDENCY_MISSING", message="入口建议依赖版本不存在", status_code=409
            )
        confirmation = await session.scalar(
            select(CombinationSetConfirmation).where(
                CombinationSetConfirmation.combination_set_version_id == combination_id,
                CombinationSetConfirmation.input_fingerprint == combination_version.content_hash,
            )
        )
        if confirmation is None:
            raise DomainError(
                code="COMBINATION_SET_NOT_CONFIRMED",
                message="预算组合必须先由部门预算负责人确认",
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
        campus_keys = {item.campus: item.campus_stable_key for item in combinations}
        requested = {item.campus.strip(): item for item in payload.campuses}
        if set(requested) != set(campus_keys):
            raise DomainError(
                code="CAMPUS_INPUT_SET_MISMATCH",
                message="校区资源输入必须完整覆盖已确认预算组合中的全部校区",
                status_code=422,
                details={
                    "missing": sorted(set(campus_keys) - set(requested)),
                    "unexpected": sorted(set(requested) - set(campus_keys)),
                },
            )
        entry_combinations = {
            (item.track, item.product, item.grade, item.subject, item.campus)
            for item in combinations
            if item.entry_attribute == "ENTRY"
        }
        history_rows = list(
            await session.scalars(
                select(EntryHistoryAggregate).where(
                    EntryHistoryAggregate.source_snapshot_id == parent.source_snapshot_id
                )
            )
        )
        history_by_campus = {campus: 0 for campus in campus_keys}
        for item in history_rows:
            key = (item.track, item.product, item.grade, item.subject, item.campus)
            if key in entry_combinations:
                history_by_campus[item.campus] += item.business_new_student_count
        allocation_inputs = [
            CampusAllocationInput(
                campus=campus,
                campus_stable_key=campus_keys[campus],
                historical_business_new_students=history_by_campus[campus],
                classroom_resource=requested[campus].classroom_resource,
                sales_resource=requested[campus].sales_resource,
                manual_reserved_suggestion=requested[campus].manual_reserved_suggestion,
                manual_suggestion_reason=requested[campus].manual_suggestion_reason,
            )
            for campus in sorted(campus_keys)
        ]
        try:
            allocated = allocate_campus_entry_suggestions(
                fiscal_year_entry_total=entry_total.locked_value,
                campuses=allocation_inputs,
            )
        except EntryAllocationError as exc:
            raise DomainError(code=exc.code, message=exc.message, status_code=422) from exc
        occurred_at = _now()
        version_id = uuid7()
        version_content = {
            "schema_version": "1.0",
            "combination_set_version_id": str(combination_id),
            "entry_total_version_id": str(entry_total_id),
            "source_snapshot_id": str(parent.source_snapshot_id),
            "items": [item.content() for item in allocated],
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
            component_updates={"campus_entry_suggestion_version_id": str(version_id)},
            component_removals=(
                "campus_entry_set_version_id",
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
        await session.flush()
        manual_reserved_total = sum(item.manual_reserved_suggestion for item in allocated)
        historical_entry_total = sum(item.historical_business_new_students for item in allocated)
        version = CampusEntrySuggestionVersion(
            campus_entry_suggestion_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            combination_set_version_id=combination_id,
            entry_total_version_id=entry_total_id,
            source_snapshot_id=parent.source_snapshot_id,
            fiscal_year_entry_total=entry_total.locked_value,
            manual_reserved_total=manual_reserved_total,
            historical_entry_total=historical_entry_total,
            suggestion_total=sum(item.suggested_entry_value for item in allocated),
            record_count=len(allocated),
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows = [
            CampusEntrySuggestion(
                campus_entry_suggestion_id=uuid7(),
                campus_entry_suggestion_version_id=version_id,
                **item.content(),
                content_hash=item.content_hash,
            )
            for item in allocated
        ]
        session.add_all(rows)
        response = CampusSuggestionVersionResponse(
            campus_entry_suggestion_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            combination_set_version_id=combination_id,
            entry_total_version_id=entry_total_id,
            source_snapshot_id=parent.source_snapshot_id,
            fiscal_year_entry_total=entry_total.locked_value,
            manual_reserved_total=manual_reserved_total,
            historical_entry_total=historical_entry_total,
            suggestion_total=version.suggestion_total,
            record_count=len(rows),
            content_hash=version.content_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
            items=[_campus_response(row) for row in rows],
            revision=revision_response,
        )
        _add_audit(
            session,
            event_type="CAMPUS_ENTRY_SUGGESTIONS_GENERATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="CAMPUS_ENTRY_SUGGESTION_VERSION",
            object_key={"campus_entry_suggestion_version_id": str(version_id)},
            detail={
                "entry_total": entry_total.locked_value,
                "record_count": len(rows),
                "manual_reserved_total": manual_reserved_total,
                "historical_entry_total": historical_entry_total,
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


async def get_campus_suggestion_version(
    session: AsyncSession,
    *,
    actor: Actor,
    campus_entry_suggestion_version_id: uuid.UUID,
) -> CampusSuggestionVersionResponse:
    version = await session.get(CampusEntrySuggestionVersion, campus_entry_suggestion_version_id)
    if version is None:
        raise DomainError(
            code="CAMPUS_SUGGESTION_NOT_FOUND", message="校区入口建议版本不存在", status_code=404
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        await session.scalars(
            select(CampusEntrySuggestion)
            .where(
                CampusEntrySuggestion.campus_entry_suggestion_version_id == campus_entry_suggestion_version_id
            )
            .order_by(CampusEntrySuggestion.campus_stable_key)
        )
    )
    return CampusSuggestionVersionResponse(
        campus_entry_suggestion_version_id=version.campus_entry_suggestion_version_id,
        budget_revision_id=version.budget_revision_id,
        combination_set_version_id=version.combination_set_version_id,
        entry_total_version_id=version.entry_total_version_id,
        source_snapshot_id=version.source_snapshot_id,
        fiscal_year_entry_total=version.fiscal_year_entry_total,
        manual_reserved_total=version.manual_reserved_total,
        historical_entry_total=version.historical_entry_total,
        suggestion_total=version.suggestion_total,
        record_count=version.record_count,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[_campus_response(row) for row in rows],
        revision=_revision_link_response(revision),
    )


async def replace_campus_entry_draft(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: CampusEntryReplaceDraftRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> CampusEntrySetVersionResponse:
    command_type = "REPLACE_CAMPUS_ENTRY_DRAFT"
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
            return CampusEntrySetVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        suggestion_id = _manifest_uuid(parent, "campus_entry_suggestion_version_id", "校区入口建议尚未生成")
        suggestion_version = await session.get(CampusEntrySuggestionVersion, suggestion_id)
        if suggestion_version is None:
            raise DomainError(
                code="CAMPUS_SUGGESTION_NOT_FOUND", message="校区入口建议版本不存在", status_code=404
            )
        suggestion_rows = list(
            await session.scalars(
                select(CampusEntrySuggestion).where(
                    CampusEntrySuggestion.campus_entry_suggestion_version_id == suggestion_id
                )
            )
        )
        suggestion_by_campus = {row.campus: row for row in suggestion_rows}
        requested = {item.campus.strip(): item for item in payload.items}
        if set(requested) != set(suggestion_by_campus):
            raise DomainError(
                code="CAMPUS_ENTRY_SET_INCOMPLETE",
                message="校区入口确定值草稿必须完整覆盖当前建议中的全部校区",
                status_code=422,
                details={
                    "missing": sorted(set(suggestion_by_campus) - set(requested)),
                    "unexpected": sorted(set(requested) - set(suggestion_by_campus)),
                },
            )
        rows_content: list[dict[str, object]] = []
        for campus in sorted(requested):
            item = requested[campus]
            suggestion = suggestion_by_campus[campus]
            reason_category = (
                item.adjustment_reason_category.value if item.adjustment_reason_category is not None else None
            )
            reason_detail = item.adjustment_reason_detail.strip() if item.adjustment_reason_detail else None
            difference = (
                item.determined_entry_value - suggestion.suggested_entry_value
                if item.determined_entry_value is not None
                else None
            )
            rows_content.append(
                {
                    "campus": campus,
                    "campus_stable_key": suggestion.campus_stable_key,
                    "suggested_entry_value": suggestion.suggested_entry_value,
                    "determined_entry_value": item.determined_entry_value,
                    "adjustment_difference": difference,
                    "adjustment_reason_category": reason_category,
                    "adjustment_reason_detail": reason_detail,
                    "performance_evidence": item.performance_evidence,
                    "classroom_resource": item.classroom_resource,
                    "sales_resource": item.sales_resource,
                }
            )
        determined_total = sum(
            item.determined_entry_value for item in payload.items if item.determined_entry_value is not None
        )
        incomplete_count = sum(item.determined_entry_value is None for item in payload.items)
        total_difference = suggestion_version.fiscal_year_entry_total - determined_total
        version_id = uuid7()
        occurred_at = _now()
        version_content = {
            "schema_version": "1.0",
            "basis_combination_set_version_id": str(suggestion_version.combination_set_version_id),
            "entry_total_version_id": str(suggestion_version.entry_total_version_id),
            "suggestion_version_id": str(suggestion_id),
            "fiscal_year_entry_total": suggestion_version.fiscal_year_entry_total,
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
            component_updates={"campus_entry_set_version_id": str(version_id)},
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
        version = CampusEntrySetVersion(
            campus_entry_set_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            basis_combination_set_version_id=suggestion_version.combination_set_version_id,
            entry_total_version_id=suggestion_version.entry_total_version_id,
            suggestion_version_id=suggestion_id,
            fiscal_year_entry_total=suggestion_version.fiscal_year_entry_total,
            determined_total=determined_total,
            total_difference=total_difference,
            incomplete_count=incomplete_count,
            record_count=len(rows_content),
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows = [
            CampusEntryAllocation(
                campus_entry_allocation_id=uuid7(),
                campus_entry_set_version_id=version_id,
                **row,
                content_hash=content_hash(row),
            )
            for row in rows_content
        ]
        session.add_all(rows)
        response = _campus_entry_set_response(version=version, rows=rows, revision_response=revision_response)
        _add_audit(
            session,
            event_type="CAMPUS_ENTRY_DRAFT_REPLACED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="CAMPUS_ENTRY_SET_VERSION",
            object_key={"campus_entry_set_version_id": str(version_id)},
            detail={
                "determined_total": determined_total,
                "total_difference": total_difference,
                "incomplete_count": incomplete_count,
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


async def get_campus_entry_set_version(
    session: AsyncSession, *, actor: Actor, campus_entry_set_version_id: uuid.UUID
) -> CampusEntrySetVersionResponse:
    version = await session.get(CampusEntrySetVersion, campus_entry_set_version_id)
    if version is None:
        raise DomainError(
            code="CAMPUS_ENTRY_SET_NOT_FOUND", message="校区入口确定值版本不存在", status_code=404
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        await session.scalars(
            select(CampusEntryAllocation)
            .where(CampusEntryAllocation.campus_entry_set_version_id == campus_entry_set_version_id)
            .order_by(CampusEntryAllocation.campus_stable_key)
        )
    )
    return _campus_entry_set_response(
        version=version, rows=rows, revision_response=_revision_link_response(revision)
    )


async def confirm_campus_entry_set(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: CampusEntryConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> CampusEntryConfirmationResponse:
    command_type = "CONFIRM_CAMPUS_ENTRY_SET"
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
            return CampusEntryConfirmationResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if revision.component_manifest.get("campus_entry_set_version_id") != str(
            payload.campus_entry_set_version_id
        ):
            raise DomainError(
                code="CAMPUS_ENTRY_SET_NOT_CURRENT",
                message="待确认校区入口确定值不是当前预算修订引用的版本",
                status_code=409,
            )
        version = await session.get(CampusEntrySetVersion, payload.campus_entry_set_version_id)
        if version is None:
            raise DomainError(
                code="CAMPUS_ENTRY_SET_NOT_FOUND", message="校区入口确定值版本不存在", status_code=404
            )
        current_combination_id = _manifest_uuid(revision, "combination_set_version_id", "预算组合尚未生成")
        current_combination = await session.get(BudgetCombinationSetVersion, current_combination_id)
        combination_confirmation = (
            await session.scalar(
                select(CombinationSetConfirmation).where(
                    CombinationSetConfirmation.combination_set_version_id == current_combination_id,
                    CombinationSetConfirmation.input_fingerprint == current_combination.content_hash,
                )
            )
            if current_combination is not None
            else None
        )
        if combination_confirmation is None:
            raise DomainError(
                code="COMBINATION_SET_NOT_CONFIRMED",
                message="当前预算组合必须先由部门预算负责人确认",
                status_code=409,
            )
        current_entry_total_id = _manifest_uuid(revision, "entry_total_version_id", "财年入口总值尚未锁定")
        if current_entry_total_id != version.entry_total_version_id:
            raise DomainError(
                code="ENTRY_TOTAL_VERSION_CHANGED",
                message="财年入口总值已变化，必须重新维护整套校区入口确定值",
                status_code=409,
            )
        rows = list(
            await session.scalars(
                select(CampusEntryAllocation).where(
                    CampusEntryAllocation.campus_entry_set_version_id == version.campus_entry_set_version_id
                )
            )
        )
        blockers = _campus_entry_blockers(version, rows)
        if blockers:
            raise DomainError(
                code="CAMPUS_ENTRY_SET_NOT_CONFIRMABLE",
                message="整套校区入口确定值尚不满足确认条件",
                status_code=422,
                details={"blocking_reasons": blockers},
            )
        existing = await session.scalar(
            select(CampusEntryConfirmation).where(
                CampusEntryConfirmation.campus_entry_set_version_id == version.campus_entry_set_version_id,
                CampusEntryConfirmation.input_fingerprint == version.content_hash,
            )
        )
        occurred_at = _now()
        if existing is None:
            existing = CampusEntryConfirmation(
                campus_entry_confirmation_id=uuid7(),
                campus_entry_set_version_id=version.campus_entry_set_version_id,
                confirmed_revision_id=revision.budget_revision_id,
                input_fingerprint=version.content_hash,
                confirmed_by=actor.user_id,
                role_snapshot=_role_snapshot(actor),
                summary={
                    "schema_version": "1.0",
                    "fiscal_year_entry_total": version.fiscal_year_entry_total,
                    "determined_total": version.determined_total,
                    "record_count": version.record_count,
                },
                confirmed_at=occurred_at,
            )
            session.add(existing)
        await requeue_budget_calculation_job(
            session,
            revision_id=revision.budget_revision_id,
        )
        response = CampusEntryConfirmationResponse(
            campus_entry_confirmation_id=existing.campus_entry_confirmation_id,
            campus_entry_set_version_id=existing.campus_entry_set_version_id,
            confirmed_revision_id=existing.confirmed_revision_id,
            input_fingerprint=existing.input_fingerprint,
            confirmed_by=existing.confirmed_by,
            confirmed_at=existing.confirmed_at,
        )
        _add_audit(
            session,
            event_type="CAMPUS_ENTRY_SET_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="CAMPUS_ENTRY_SET_VERSION",
            object_key={"campus_entry_set_version_id": str(version.campus_entry_set_version_id)},
            detail={
                "input_fingerprint": version.content_hash,
                "fiscal_year_entry_total": version.fiscal_year_entry_total,
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


def _combination_public_key(
    row: BudgetCombination,
) -> tuple[str, str, str, str, str]:
    return row.track, row.product, row.grade, row.subject, row.campus


def _campus_entry_blockers(version: CampusEntrySetVersion, rows: list[CampusEntryAllocation]) -> list[str]:
    blockers: list[str] = []
    if version.incomplete_count > 0:
        blockers.append("CAMPUS_VALUE_INCOMPLETE")
    if version.total_difference != 0:
        blockers.append("CAMPUS_TOTAL_NOT_BALANCED")
    if any(
        not row.performance_evidence or not row.classroom_resource or not row.sales_resource for row in rows
    ):
        blockers.append("CAMPUS_BASIS_INCOMPLETE")
    if any(
        row.adjustment_difference not in (None, 0)
        and (
            not row.adjustment_reason_category
            or not row.adjustment_reason_detail
            or not row.adjustment_reason_detail.strip()
        )
        for row in rows
    ):
        blockers.append("CAMPUS_ADJUSTMENT_REASON_REQUIRED")
    return blockers


def _campus_entry_allocation_response(
    row: CampusEntryAllocation,
) -> CampusEntryAllocationResponse:
    return CampusEntryAllocationResponse(
        campus_entry_allocation_id=row.campus_entry_allocation_id,
        campus=row.campus,
        campus_stable_key=row.campus_stable_key,
        suggested_entry_value=row.suggested_entry_value,
        determined_entry_value=row.determined_entry_value,
        adjustment_difference=row.adjustment_difference,
        adjustment_reason_category=row.adjustment_reason_category,
        adjustment_reason_detail=row.adjustment_reason_detail,
        performance_evidence=row.performance_evidence,
        classroom_resource=row.classroom_resource,
        sales_resource=row.sales_resource,
        content_hash=row.content_hash,
    )


def _campus_entry_set_response(
    *,
    version: CampusEntrySetVersion,
    rows: list[CampusEntryAllocation],
    revision_response: RevisionCommandResponse,
) -> CampusEntrySetVersionResponse:
    ordered = sorted(rows, key=lambda row: row.campus_stable_key)
    blockers = _campus_entry_blockers(version, ordered)
    return CampusEntrySetVersionResponse(
        campus_entry_set_version_id=version.campus_entry_set_version_id,
        budget_revision_id=version.budget_revision_id,
        basis_combination_set_version_id=version.basis_combination_set_version_id,
        entry_total_version_id=version.entry_total_version_id,
        suggestion_version_id=version.suggestion_version_id,
        fiscal_year_entry_total=version.fiscal_year_entry_total,
        determined_total=version.determined_total,
        total_difference=version.total_difference,
        incomplete_count=version.incomplete_count,
        record_count=version.record_count,
        is_confirmable=not blockers,
        blocking_reasons=blockers,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[_campus_entry_allocation_response(row) for row in ordered],
        revision=revision_response,
    )


def _campus_key(department_id: str, campus: str) -> str:
    return content_hash(
        {"schema_version": "1.0", "department_id": department_id, "dimension": "CAMPUS", "value": campus}
    )


def _combination_stable_key(department_id: str, key: tuple[str, str, str, str, str]) -> str:
    return content_hash(
        {
            "schema_version": "1.0",
            "department_id": department_id,
            "dimension": "BUDGET_COMBINATION",
            "track": key[0],
            "product": key[1],
            "grade": key[2],
            "subject": key[3],
            "campus": key[4],
        }
    )


async def _assert_version_access(
    session: AsyncSession, *, actor: Actor, revision_id: uuid.UUID
) -> tuple[BudgetRevision, AnnualBudget]:
    revision = await session.get(BudgetRevision, revision_id)
    if revision is None:
        raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
    budget = await session.get(AnnualBudget, revision.annual_budget_id)
    _assert_budget_access(budget, actor)
    assert budget is not None
    return revision, budget


def _revision_link_response(revision: BudgetRevision) -> RevisionCommandResponse:
    return RevisionCommandResponse(
        annual_budget_id=revision.annual_budget_id,
        previous_revision_id=revision.parent_revision_id,
        new_revision_id=revision.budget_revision_id,
        links={
            "revision": f"/api/v1/revisions/{revision.budget_revision_id}",
            "annual_budget": f"/api/v1/annual-budgets/{revision.annual_budget_id}",
        },
    )


def _combination_response(row: BudgetCombination) -> BudgetCombinationResponse:
    return BudgetCombinationResponse(
        combination_id=row.combination_id,
        track=row.track,
        product=row.product,
        grade=row.grade,
        subject=row.subject,
        campus=row.campus,
        campus_stable_key=row.campus_stable_key,
        combination_stable_key=row.combination_stable_key,
        entry_attribute=row.entry_attribute,
        origin_type=row.origin_type,
        enabled=row.enabled,
        historical_enrollment=row.historical_enrollment.quantize(COMBINATION_DECIMAL_QUANTUM),
        change_reason=row.change_reason,
        class_size_parameter_reference=row.class_size_parameter_reference,
        content_hash=row.content_hash,
    )


def _manifest_uuid(revision: BudgetRevision, key: str, message: str) -> uuid.UUID:
    raw = revision.component_manifest.get(key)
    if not isinstance(raw, str):
        raise DomainError(code="ENTRY_DEPENDENCY_MISSING", message=message, status_code=409)
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="预算修订组件清单无效", status_code=409
        ) from exc


def _campus_response(row: CampusEntrySuggestion) -> CampusEntrySuggestionResponse:
    return CampusEntrySuggestionResponse(
        campus_entry_suggestion_id=row.campus_entry_suggestion_id,
        campus=row.campus,
        campus_stable_key=row.campus_stable_key,
        historical_business_new_students=row.historical_business_new_students,
        history_availability=row.history_availability,
        classroom_resource=row.classroom_resource,
        sales_resource=row.sales_resource,
        manual_reserved_suggestion=row.manual_reserved_suggestion,
        manual_suggestion_reason=row.manual_suggestion_reason,
        suggested_entry_theoretical=row.suggested_entry_theoretical.quantize(
            CAMPUS_ALLOCATION_DECIMAL_QUANTUM
        ),
        suggested_entry_floor=row.suggested_entry_floor,
        suggested_entry_fraction=row.suggested_entry_fraction.quantize(CAMPUS_ALLOCATION_DECIMAL_QUANTUM),
        suggestion_tail_assigned=row.suggestion_tail_assigned,
        suggested_entry_value=row.suggested_entry_value,
        suggestion_source_type=row.suggestion_source_type,
        content_hash=row.content_hash,
    )
