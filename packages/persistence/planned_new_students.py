import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    PlannedNewStudentChangeAndLockRequest,
    PlannedNewStudentCountResponse,
    PlannedNewStudentKeyInput,
    PlannedNewStudentUnlockRequest,
    PlannedNewStudentVersionResponse,
    RevisionCommandResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.entry_planning import (
    _assert_version_access,
    _create_child_revision,
    _lock_current_revision,
    _revision_link_response,
)
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    BudgetCombination,
    EntryDetailAllocation,
    EntryDetailSetVersion,
    EntryScopeEvaluation,
    PlannedNewStudentCount,
    PlannedNewStudentVersion,
)
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)
from packages.planned_new_students.governance import (
    PlannedNewStudentKey,
    handling_status,
    planned_new_student_stable_key,
    quarter_name,
)

DOWNSTREAM_REMOVALS = (
    "headcount_result_version_id",
    "class_size_result_version_id",
    "revenue_result_version_id",
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)


async def change_and_lock_planned_new_students(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: PlannedNewStudentChangeAndLockRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> PlannedNewStudentVersionResponse:
    command_type = "CHANGE_AND_LOCK_PLANNED_NEW_STUDENTS"
    request_hash = _command_hash(revision_id, expected_revision_id, payload.model_dump(mode="json"))
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return PlannedNewStudentVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        basis, available = await _current_basis(session, parent, budget.fiscal_year)
        base_version, base_rows = await _current_state(session, parent)
        _assert_basis_unchanged(base_version, basis)
        rows_by_key = {row.planned_new_student_stable_key: _row_content(row) for row in base_rows}
        errors: list[dict[str, object]] = []
        occurred_at = _now()
        for item in payload.items:
            resolved = _resolve_input(item, budget.fiscal_year, available, errors)
            if resolved is None:
                continue
            key, combination, allocation = resolved
            stable_key = planned_new_student_stable_key(key)
            rows_by_key[stable_key] = {
                "combination_id": str(combination.combination_id),
                "entry_detail_allocation_id": (
                    str(allocation.entry_detail_allocation_id) if allocation is not None else None
                ),
                "planned_new_student_stable_key": stable_key,
                "combination_stable_key": combination.combination_stable_key,
                "fiscal_year": key.fiscal_year,
                "fiscal_season": key.fiscal_season,
                "quarter_name": quarter_name(key.fiscal_year, key.fiscal_season),
                "track": key.track,
                "product": key.product,
                "entry_attribute": key.entry_attribute,
                "grade": key.grade,
                "subject": key.subject,
                "campus": key.campus,
                "planned_new_students": item.planned_new_students,
                "manual_reason": item.reason.strip(),
                "calculated_new_student_share_ppm": (1_000_000 if item.planned_new_students > 0 else None),
                "locked": True,
                "locked_value": item.planned_new_students,
                "handling_status": handling_status(
                    planned_new_students=item.planned_new_students, locked=True
                ),
                "lock_origin": {
                    "schema_version": "1.0",
                    "action": "CHANGE_AND_LOCK",
                    "reason": item.reason.strip(),
                    "actor_id": actor.user_id,
                    "role_snapshot": _role_snapshot(actor),
                    "occurred_at": occurred_at.isoformat(),
                },
            }
        _raise_batch_errors(errors)
        response = await _persist_version(
            session,
            actor=actor,
            parent_revision=parent,
            budget=budget,
            base_version=base_version,
            basis=basis,
            operation_type="CHANGE_AND_LOCK",
            rows_content=list(rows_by_key.values()),
            command_type=command_type,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    return response


async def unlock_planned_new_students(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: PlannedNewStudentUnlockRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> PlannedNewStudentVersionResponse:
    command_type = "UNLOCK_PLANNED_NEW_STUDENTS"
    request_hash = _command_hash(revision_id, expected_revision_id, payload.model_dump(mode="json"))
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return PlannedNewStudentVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        basis, available = await _current_basis(session, parent, budget.fiscal_year)
        base_version, base_rows = await _current_state(session, parent)
        if base_version is None:
            raise DomainError(
                code="PLANNED_NEW_STUDENT_VERSION_REQUIRED",
                message="当前修订尚未建立计划新生人次版本",
                status_code=409,
            )
        _assert_basis_unchanged(base_version, basis)
        rows_by_key = {row.planned_new_student_stable_key: _row_content(row) for row in base_rows}
        errors: list[dict[str, object]] = []
        occurred_at = _now()
        for item in payload.items:
            resolved = _resolve_input(item, budget.fiscal_year, available, errors)
            if resolved is None:
                continue
            key, _, _ = resolved
            stable_key = planned_new_student_stable_key(key)
            existing = rows_by_key.get(stable_key)
            if existing is None:
                errors.append(
                    _error(stable_key, "PLANNED_NEW_STUDENT_NOT_FOUND", "该完整预算明细尚未填写计划新生人次")
                )
                continue
            if not bool(existing["locked"]):
                errors.append(
                    _error(stable_key, "PLANNED_NEW_STUDENT_NOT_LOCKED", "该计划新生人次当前未锁定")
                )
                continue
            existing["locked"] = False
            existing["locked_value"] = None
            planned_count = existing["planned_new_students"]
            assert isinstance(planned_count, int)
            existing["handling_status"] = handling_status(planned_new_students=planned_count, locked=False)
            existing["lock_origin"] = {
                "schema_version": "1.0",
                "action": "UNLOCK",
                "reason": item.reason.strip(),
                "actor_id": actor.user_id,
                "role_snapshot": _role_snapshot(actor),
                "occurred_at": occurred_at.isoformat(),
            }
        _raise_batch_errors(errors)
        response = await _persist_version(
            session,
            actor=actor,
            parent_revision=parent,
            budget=budget,
            base_version=base_version,
            basis=basis,
            operation_type="UNLOCK",
            rows_content=list(rows_by_key.values()),
            command_type=command_type,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    return response


async def get_planned_new_student_version(
    session: AsyncSession,
    *,
    actor: Actor,
    planned_new_student_version_id: uuid.UUID,
) -> PlannedNewStudentVersionResponse:
    version = await session.get(PlannedNewStudentVersion, planned_new_student_version_id)
    if version is None:
        raise DomainError(
            code="PLANNED_NEW_STUDENT_VERSION_NOT_FOUND",
            message="计划新生人次版本不存在",
            status_code=404,
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = await _load_rows(session, version.planned_new_student_version_id)
    return _version_response(version, rows, _revision_link_response(revision))


async def _current_basis(
    session: AsyncSession, revision: object, fiscal_year: int
) -> tuple[
    dict[str, uuid.UUID],
    dict[
        tuple[str, str, str, str, str],
        tuple[BudgetCombination, EntryScopeEvaluation, EntryDetailAllocation | None],
    ],
]:
    from packages.persistence.models import BudgetRevision

    assert isinstance(revision, BudgetRevision)
    combination_version_id = _manifest_uuid(revision, "combination_set_version_id")
    entry_scope_version_id = _manifest_uuid(revision, "entry_scope_version_id")
    entry_detail_version_id = _manifest_uuid(revision, "entry_detail_set_version_id")
    detail_version = await session.get(EntryDetailSetVersion, entry_detail_version_id)
    if (
        detail_version is None
        or detail_version.combination_set_version_id != combination_version_id
        or detail_version.entry_scope_version_id != entry_scope_version_id
        or detail_version.fiscal_year != fiscal_year
    ):
        raise DomainError(
            code="PLANNED_NEW_STUDENT_BASIS_MISMATCH",
            message="当前入口明细与预算组合、入口范围或财年不一致",
            status_code=409,
        )
    pairs = list(
        (
            await session.execute(
                select(BudgetCombination, EntryScopeEvaluation, EntryDetailAllocation)
                .join(
                    EntryScopeEvaluation,
                    EntryScopeEvaluation.combination_id == BudgetCombination.combination_id,
                )
                .outerjoin(
                    EntryDetailAllocation,
                    EntryDetailAllocation.combination_id == BudgetCombination.combination_id,
                )
                .where(
                    BudgetCombination.combination_set_version_id == combination_version_id,
                    BudgetCombination.enabled.is_(True),
                    EntryScopeEvaluation.entry_scope_version_id == entry_scope_version_id,
                    (
                        (EntryDetailAllocation.entry_detail_set_version_id == entry_detail_version_id)
                        | (EntryDetailAllocation.entry_detail_allocation_id.is_(None))
                    ),
                )
            )
        ).all()
    )
    available = {
        (row.track, row.product, row.grade, row.subject, row.campus): (
            row,
            evaluation,
            allocation,
        )
        for row, evaluation, allocation in pairs
    }
    return (
        {
            "combination_set_version_id": combination_version_id,
            "entry_scope_version_id": entry_scope_version_id,
            "entry_detail_set_version_id": entry_detail_version_id,
        },
        available,
    )


def _resolve_input(
    item: PlannedNewStudentKeyInput,
    fiscal_year: int,
    available: dict[
        tuple[str, str, str, str, str],
        tuple[BudgetCombination, EntryScopeEvaluation, EntryDetailAllocation | None],
    ],
    errors: list[dict[str, object]],
) -> tuple[PlannedNewStudentKey, BudgetCombination, EntryDetailAllocation | None] | None:
    key = PlannedNewStudentKey(
        fiscal_year=fiscal_year,
        fiscal_season=item.fiscal_season,
        track=item.track.strip(),
        product=item.product.strip(),
        entry_attribute=item.entry_attribute,
        grade=item.grade.strip(),
        subject=item.subject.strip(),
        campus=item.campus.strip(),
    )
    stable_key = planned_new_student_stable_key(key)
    if quarter_name(fiscal_year, item.fiscal_season) is None:
        errors.append(
            _error(
                stable_key, "PLANNED_NEW_STUDENT_SEASON_INVALID", "季节财年必须是当前财年的第 1 至第 4 季度"
            )
        )
        return None
    resolved = available.get((key.track, key.product, key.grade, key.subject, key.campus))
    if resolved is None:
        errors.append(
            _error(stable_key, "PLANNED_NEW_STUDENT_DETAIL_NOT_FOUND", "完整预算明细不存在或未启用")
        )
        return None
    combination, evaluation, allocation = resolved
    if evaluation.effective_entry_attribute != key.entry_attribute:
        errors.append(
            _error(
                stable_key, "PLANNED_NEW_STUDENT_ENTRY_ATTRIBUTE_MISMATCH", "入口属性与当前入口范围判定不一致"
            )
        )
        return None
    if (
        allocation is not None
        and allocation.entry_initial_value > 0
        and key.fiscal_season == fiscal_year * 10 + 1
    ):
        errors.append(
            _error(
                stable_key,
                "PLANNED_NEW_STUDENT_ABSOLUTE_INPUT_EXISTS",
                "该明细已有入口新生绝对人数，不得再填写计划新生人次",
            )
        )
        return None
    return key, combination, allocation


async def _persist_version(
    session: AsyncSession,
    *,
    actor: Actor,
    parent_revision: object,
    budget: object,
    base_version: PlannedNewStudentVersion | None,
    basis: dict[str, uuid.UUID],
    operation_type: str,
    rows_content: list[dict[str, object]],
    command_type: str,
    request_hash: str,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> PlannedNewStudentVersionResponse:
    from packages.persistence.models import AnnualBudget, BudgetRevision

    assert isinstance(parent_revision, BudgetRevision)
    assert isinstance(budget, AnnualBudget)
    rows_content.sort(key=lambda item: str(item["planned_new_student_stable_key"]))
    occurred_at = _now()
    version_id = uuid7()
    version_hash = content_hash(
        {
            "schema_version": "1.0",
            "parent_planned_new_student_version_id": str(base_version.planned_new_student_version_id)
            if base_version
            else None,
            **{key: str(value) for key, value in basis.items()},
            "fiscal_year": budget.fiscal_year,
            "operation_type": operation_type,
            "rows": rows_content,
        }
    )
    revision, revision_response = await _create_child_revision(
        session,
        parent=parent_revision,
        budget=budget,
        actor=actor,
        command_type=command_type,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        occurred_at=occurred_at,
        component_updates={"planned_new_student_version_id": str(version_id)},
        component_removals=DOWNSTREAM_REMOVALS,
    )
    version = PlannedNewStudentVersion(
        planned_new_student_version_id=version_id,
        budget_revision_id=revision.budget_revision_id,
        parent_planned_new_student_version_id=(
            base_version.planned_new_student_version_id if base_version else None
        ),
        combination_set_version_id=basis["combination_set_version_id"],
        entry_scope_version_id=basis["entry_scope_version_id"],
        entry_detail_set_version_id=basis["entry_detail_set_version_id"],
        fiscal_year=budget.fiscal_year,
        operation_type=operation_type,
        record_count=len(rows_content),
        locked_count=sum(bool(item["locked"]) for item in rows_content),
        content_hash=version_hash,
        request_hash=request_hash,
        created_by=actor.user_id,
        created_at=occurred_at,
    )
    session.add(version)
    await session.flush()
    rows = [
        PlannedNewStudentCount(
            planned_new_student_count_id=uuid7(),
            planned_new_student_version_id=version_id,
            **{
                **item,
                "combination_id": uuid.UUID(str(item["combination_id"])),
                "entry_detail_allocation_id": (
                    uuid.UUID(str(item["entry_detail_allocation_id"]))
                    if item["entry_detail_allocation_id"] is not None
                    else None
                ),
            },
            content_hash=content_hash(item),
        )
        for item in rows_content
    ]
    session.add_all(rows)
    response = _version_response(version, rows, revision_response)
    _add_audit(
        session,
        event_type=f"PLANNED_NEW_STUDENT_{operation_type}",
        actor=actor,
        acting_role=_acting_role(actor),
        object_type="PLANNED_NEW_STUDENT_VERSION",
        object_key={"planned_new_student_version_id": str(version_id)},
        detail={
            "record_count": len(rows),
            "locked_count": version.locked_count,
            "content_hash": version_hash,
            "downstream_invalidated": list(DOWNSTREAM_REMOVALS),
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


async def _current_state(
    session: AsyncSession, revision: object
) -> tuple[PlannedNewStudentVersion | None, list[PlannedNewStudentCount]]:
    from packages.persistence.models import BudgetRevision

    assert isinstance(revision, BudgetRevision)
    raw = revision.component_manifest.get("planned_new_student_version_id")
    if not isinstance(raw, str):
        return None, []
    try:
        version_id = uuid.UUID(raw)
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="计划新生人次版本清单无效", status_code=409
        ) from exc
    version = await session.get(PlannedNewStudentVersion, version_id)
    if version is None:
        raise DomainError(
            code="PLANNED_NEW_STUDENT_VERSION_NOT_FOUND",
            message="计划新生人次版本不存在",
            status_code=404,
        )
    return version, await _load_rows(session, version_id)


async def _load_rows(session: AsyncSession, version_id: uuid.UUID) -> list[PlannedNewStudentCount]:
    return list(
        await session.scalars(
            select(PlannedNewStudentCount)
            .where(PlannedNewStudentCount.planned_new_student_version_id == version_id)
            .order_by(PlannedNewStudentCount.planned_new_student_stable_key)
        )
    )


def _assert_basis_unchanged(version: PlannedNewStudentVersion | None, basis: dict[str, uuid.UUID]) -> None:
    if version is None:
        return
    if (
        version.combination_set_version_id != basis["combination_set_version_id"]
        or version.entry_scope_version_id != basis["entry_scope_version_id"]
        or version.entry_detail_set_version_id != basis["entry_detail_set_version_id"]
    ):
        raise DomainError(
            code="PLANNED_NEW_STUDENT_BASIS_CHANGED",
            message="预算组合、入口范围或入口明细已变化，旧计划新生人次不得继续修改",
            status_code=409,
        )


def _manifest_uuid(revision: object, key: str) -> uuid.UUID:
    from packages.persistence.models import BudgetRevision

    assert isinstance(revision, BudgetRevision)
    raw = revision.component_manifest.get(key)
    if not isinstance(raw, str):
        raise DomainError(
            code="PLANNED_NEW_STUDENT_BASIS_REQUIRED",
            message="必须先完成预算组合、入口范围和入口明细纵切",
            status_code=409,
            details={"missing_component": key},
        )
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="预算修订组件清单无效", status_code=409
        ) from exc


def _row_content(row: PlannedNewStudentCount) -> dict[str, object]:
    return {
        "combination_id": str(row.combination_id),
        "entry_detail_allocation_id": (
            str(row.entry_detail_allocation_id) if row.entry_detail_allocation_id is not None else None
        ),
        "planned_new_student_stable_key": row.planned_new_student_stable_key,
        "combination_stable_key": row.combination_stable_key,
        "fiscal_year": row.fiscal_year,
        "fiscal_season": row.fiscal_season,
        "quarter_name": row.quarter_name,
        "track": row.track,
        "product": row.product,
        "entry_attribute": row.entry_attribute,
        "grade": row.grade,
        "subject": row.subject,
        "campus": row.campus,
        "planned_new_students": row.planned_new_students,
        "manual_reason": row.manual_reason,
        "calculated_new_student_share_ppm": row.calculated_new_student_share_ppm,
        "locked": row.locked,
        "locked_value": row.locked_value,
        "handling_status": row.handling_status,
        "lock_origin": row.lock_origin,
    }


def _version_response(
    version: PlannedNewStudentVersion,
    rows: Sequence[PlannedNewStudentCount],
    revision: RevisionCommandResponse,
) -> PlannedNewStudentVersionResponse:
    items = [
        PlannedNewStudentCountResponse(
            planned_new_student_count_id=row.planned_new_student_count_id,
            planned_new_student_stable_key=row.planned_new_student_stable_key,
            combination_id=row.combination_id,
            entry_detail_allocation_id=row.entry_detail_allocation_id,
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
            planned_new_students=row.planned_new_students,
            manual_reason=row.manual_reason,
            calculated_new_student_share_ppm=row.calculated_new_student_share_ppm,
            locked=row.locked,
            locked_value=row.locked_value,
            handling_status=row.handling_status,
            headcount_eligibility_status="PENDING_HEADCOUNT_EVALUATION",
            lock_origin=row.lock_origin,
            auto_balance_eligible=False,
            enters_largest_remainder=False,
            content_hash=row.content_hash,
        )
        for row in rows
    ]
    return PlannedNewStudentVersionResponse(
        planned_new_student_version_id=version.planned_new_student_version_id,
        budget_revision_id=version.budget_revision_id,
        parent_planned_new_student_version_id=version.parent_planned_new_student_version_id,
        combination_set_version_id=version.combination_set_version_id,
        entry_scope_version_id=version.entry_scope_version_id,
        entry_detail_set_version_id=version.entry_detail_set_version_id,
        fiscal_year=version.fiscal_year,
        operation_type=version.operation_type,
        record_count=version.record_count,
        locked_count=version.locked_count,
        pending_lock_count=version.record_count - version.locked_count,
        all_items_locked=version.locked_count == version.record_count,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=items,
        revision=revision,
    )


def _command_hash(revision_id: uuid.UUID, expected_revision_id: uuid.UUID, payload: object) -> str:
    return content_hash(
        {
            "revision_id": str(revision_id),
            "expected_revision_id": str(expected_revision_id),
            "payload": payload,
        }
    )


def _raise_batch_errors(errors: list[dict[str, object]]) -> None:
    if errors:
        raise DomainError(
            code="PLANNED_NEW_STUDENT_BATCH_INVALID",
            message="计划新生人次整批校验失败，未创建任何版本或预算修订",
            status_code=422,
            details={"errors": errors},
        )


def _error(stable_key: str, code: str, message: str) -> dict[str, object]:
    return {
        "planned_new_student_stable_key": stable_key,
        "code": code,
        "message": message,
    }


def _acting_role(actor: Actor) -> Role:
    return Role.DATA_MAINTAINER if actor.has_role(Role.DATA_MAINTAINER) else Role.DEPARTMENT_BUDGET_OWNER
