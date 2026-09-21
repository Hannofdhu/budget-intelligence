import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    RateBoundaryConfirmRequest,
    RateBoundaryConfirmResponse,
    RateParameterChangeAndLockRequest,
    RateParameterCopyExceptionResponse,
    RateParameterCrossYearCopyRequest,
    RateParameterLockRequest,
    RateParameterReplaceDraftRequest,
    RateParameterResponse,
    RateParameterUnlockRequest,
    RateParameterVersionResponse,
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
    RateBoundaryConfirmation,
    RateParameter,
    RateParameterCopyException,
    RateParameterVersion,
    RateReference,
    RateReferenceVersion,
)
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)
from packages.rate_parameters.governance import (
    RateBoundaryValues,
    RateParameterKey,
    RateValidationIssue,
    boundary_fingerprint,
    cross_year_match_key,
    rate_parameter_stable_key,
    validate_rate_values,
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


async def replace_rate_parameter_draft(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RateParameterReplaceDraftRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateParameterVersionResponse:
    command_type = "REPLACE_RATE_PARAMETER_DRAFT"
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
            return RateParameterVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        reference_version = await session.get(RateReferenceVersion, payload.rate_reference_version_id)
        if (
            reference_version is None
            or reference_version.source_snapshot_id != parent.source_snapshot_id
            or reference_version.target_fiscal_year != budget.fiscal_year
        ):
            raise DomainError(
                code="RATE_REFERENCE_VERSION_MISMATCH",
                message="比例参考版本与当前预算修订的快照或财年不一致",
                status_code=409,
            )
        references = list(
            await session.scalars(
                select(RateReference)
                .where(RateReference.rate_reference_version_id == reference_version.rate_reference_version_id)
                .order_by(RateReference.rate_reference_id)
            )
        )
        item_by_reference = {item.rate_reference_id: item for item in payload.items}
        expected_ids = {item.rate_reference_id for item in references}
        if set(item_by_reference) != expected_ids:
            raise DomainError(
                code="RATE_PARAMETER_SET_MISMATCH",
                message="比例参数草稿必须完整且仅覆盖当前比例参考版本",
                status_code=422,
                details={
                    "missing": sorted(str(value) for value in expected_ids - set(item_by_reference)),
                    "unexpected": sorted(str(value) for value in set(item_by_reference) - expected_ids),
                },
            )
        base_version, base_rows, base_confirmations = await _current_rate_state(session, parent)
        base_by_key = {row.rate_parameter_stable_key: row for row in base_rows}
        errors: list[dict[str, object]] = []
        rows_content: list[dict[str, object]] = []
        for reference in references:
            item = item_by_reference[reference.rate_reference_id]
            key = _key_from_reference(reference, budget.fiscal_year)
            stable_key = rate_parameter_stable_key(key)
            usage_mode = _usage_mode(reference.rate_type)
            values = RateBoundaryValues(
                item.current_value_ppm,
                item.lower_bound_ppm,
                item.upper_bound_ppm,
            )
            errors.extend(
                _validation_details(
                    stable_key,
                    validate_rate_values(
                        rate_type=reference.rate_type,
                        usage_mode=usage_mode,
                        values=values,
                    ),
                )
            )
            manual_needed = (
                reference.system_value_ppm is None
                or item.current_value_ppm != reference.system_value_ppm
                or (
                    reference.system_value_ppm is not None
                    and item.lower_bound_ppm < reference.system_value_ppm
                )
            )
            if manual_needed and not (item.manual_reason and item.manual_reason.strip()):
                errors.append(
                    {
                        "rate_parameter_stable_key": stable_key,
                        "code": "RATE_MANUAL_REASON_REQUIRED",
                        "message": "人工指定当前值或将下限设为历史参考以下时必须填写原因",
                    }
                )
            prior = base_by_key.get(stable_key)
            locked = bool(prior and prior.locked and prior.fiscal_year == budget.fiscal_year)
            locked_value = prior.locked_value_ppm if locked and prior is not None else None
            if locked and locked_value != item.current_value_ppm:
                errors.append(
                    {
                        "rate_parameter_stable_key": stable_key,
                        "code": "LOCKED_RATE_VALUE_CHANGED",
                        "message": "同财年已锁定参数不得通过边界草稿改写锁定值",
                    }
                )
            if locked_value is not None and not item.lower_bound_ppm <= locked_value <= item.upper_bound_ppm:
                errors.append(
                    {
                        "rate_parameter_stable_key": stable_key,
                        "code": "LOCKED_RATE_OUTSIDE_BOUNDARY",
                        "message": "新边界不得排除同财年继承的锁定值",
                    }
                )
            rows_content.append(
                _reference_row_content(
                    reference,
                    key=key,
                    stable_key=stable_key,
                    values=values,
                    boundary_reason=item.boundary_reason.strip(),
                    manual_reason=item.manual_reason.strip() if item.manual_reason else None,
                    locked=locked,
                    locked_value_ppm=locked_value,
                    lock_origin=dict(prior.lock_origin) if locked and prior is not None else {},
                )
            )
        if errors:
            _raise_batch_errors(errors)
        response = await _persist_new_version(
            session,
            actor=actor,
            parent_revision=parent,
            budget=budget,
            base_version=base_version,
            base_confirmations=base_confirmations,
            rate_reference_version_id=reference_version.rate_reference_version_id,
            fiscal_year=budget.fiscal_year,
            operation_type="DRAFT_REPLACE",
            rows_content=rows_content,
            exception_content=[],
            command_type=command_type,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    return response


async def confirm_rate_boundaries(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RateBoundaryConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateBoundaryConfirmResponse:
    command_type = "CONFIRM_RATE_BOUNDARIES"
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
            return RateBoundaryConfirmResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if revision.component_manifest.get("rate_parameter_version_id") != str(
            payload.rate_parameter_version_id
        ):
            raise DomainError(
                code="RATE_PARAMETER_VERSION_NOT_CURRENT",
                message="待确认边界不是当前修订引用的比例参数版本",
                status_code=409,
            )
        version = await session.get(RateParameterVersion, payload.rate_parameter_version_id)
        if version is None:
            raise DomainError(
                code="RATE_PARAMETER_VERSION_NOT_FOUND", message="比例参数版本不存在", status_code=404
            )
        rows = await _load_rows(session, version.rate_parameter_version_id)
        row_by_key = {row.rate_parameter_stable_key: row for row in rows}
        requested = set(payload.rate_parameter_stable_keys)
        if not requested <= set(row_by_key):
            raise DomainError(
                code="RATE_PARAMETER_KEY_NOT_FOUND",
                message="边界确认包含当前版本不存在的参数键",
                status_code=422,
                details={"unexpected": sorted(requested - set(row_by_key))},
            )
        occurred_at = _now()
        existing = await _confirmation_map(session, version.rate_parameter_version_id)
        created = 0
        for stable_key in sorted(requested):
            row = row_by_key[stable_key]
            fingerprint = _row_boundary_fingerprint(row)
            if existing.get(stable_key) == fingerprint:
                continue
            session.add(
                RateBoundaryConfirmation(
                    rate_boundary_confirmation_id=uuid7(),
                    rate_parameter_version_id=version.rate_parameter_version_id,
                    rate_parameter_id=row.rate_parameter_id,
                    rate_parameter_stable_key=stable_key,
                    boundary_fingerprint=fingerprint,
                    confirmed_revision_id=revision.budget_revision_id,
                    inherited_from_confirmation_id=None,
                    confirmed_by=actor.user_id,
                    role_snapshot=_role_snapshot(actor),
                    confirmed_at=occurred_at,
                )
            )
            created += 1
        response = RateBoundaryConfirmResponse(
            rate_parameter_version_id=version.rate_parameter_version_id,
            confirmed_revision_id=revision.budget_revision_id,
            confirmed_count=created,
            confirmed_stable_keys=sorted(requested),
        )
        _add_audit(
            session,
            event_type="RATE_BOUNDARIES_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="RATE_PARAMETER_VERSION",
            object_key={"rate_parameter_version_id": str(version.rate_parameter_version_id)},
            detail={"confirmed_stable_keys": sorted(requested)},
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


async def lock_rate_parameters(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RateParameterLockRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateParameterVersionResponse:
    changes: list[tuple[str, int | None, str]] = [
        (item.rate_parameter_stable_key, None, item.reason) for item in payload.items
    ]
    return await _mutate_locks(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=expected_revision_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        operation_type="LOCK",
        changes=changes,
    )


async def change_and_lock_rate_parameters(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RateParameterChangeAndLockRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateParameterVersionResponse:
    changes: list[tuple[str, int | None, str]] = [
        (item.rate_parameter_stable_key, item.new_value_ppm, item.reason) for item in payload.items
    ]
    return await _mutate_locks(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=expected_revision_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        operation_type="CHANGE_AND_LOCK",
        changes=changes,
    )


async def unlock_rate_parameters(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RateParameterUnlockRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateParameterVersionResponse:
    changes: list[tuple[str, int | None, str]] = [
        (item.rate_parameter_stable_key, None, item.reason) for item in payload.items
    ]
    return await _mutate_locks(
        session,
        actor=actor,
        revision_id=revision_id,
        expected_revision_id=expected_revision_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        operation_type="UNLOCK",
        changes=changes,
    )


async def _mutate_locks(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    request_id: str,
    operation_type: str,
    changes: list[tuple[str, int | None, str]],
) -> RateParameterVersionResponse:
    command_type = f"RATE_PARAMETER_{operation_type}"
    request_payload = {
        "operation_type": operation_type,
        "changes": changes,
    }
    request_hash = _command_hash(revision_id, expected_revision_id, request_payload)
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return RateParameterVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        base_version, base_rows, base_confirmations = await _required_current_rate_state(session, parent)
        if len({key for key, _, _ in changes}) != len(changes):
            _raise_batch_errors(
                [{"code": "RATE_PARAMETER_KEY_DUPLICATED", "message": "批量操作中的参数键不得重复"}]
            )
        row_by_key = {row.rate_parameter_stable_key: row for row in base_rows}
        confirmed = {item.rate_parameter_stable_key: item.boundary_fingerprint for item in base_confirmations}
        change_by_key = {key: (value, reason.strip()) for key, value, reason in changes}
        errors: list[dict[str, object]] = []
        for key, (new_value, _) in change_by_key.items():
            row = row_by_key.get(key)
            if row is None:
                errors.append({"rate_parameter_stable_key": key, "code": "RATE_PARAMETER_KEY_NOT_FOUND"})
                continue
            if confirmed.get(key) != _row_boundary_fingerprint(row):
                errors.append(
                    {
                        "rate_parameter_stable_key": key,
                        "code": "RATE_BOUNDARY_NOT_CONFIRMED",
                        "message": "参数边界未经部门预算负责人确认",
                    }
                )
            if operation_type == "LOCK" and row.locked:
                errors.append({"rate_parameter_stable_key": key, "code": "RATE_ALREADY_LOCKED"})
            if operation_type == "UNLOCK" and not row.locked:
                errors.append({"rate_parameter_stable_key": key, "code": "RATE_NOT_LOCKED"})
            if operation_type == "CHANGE_AND_LOCK":
                assert new_value is not None
                values = RateBoundaryValues(new_value, row.lower_bound_ppm, row.upper_bound_ppm)
                errors.extend(
                    _validation_details(
                        key,
                        validate_rate_values(
                            rate_type=row.rate_type,
                            usage_mode=row.rate_usage_mode,
                            values=values,
                        ),
                    )
                )
        if errors:
            _raise_batch_errors(errors)
        occurred_at = _now()
        rows_content = []
        for row in base_rows:
            content = _row_content(row)
            change = change_by_key.get(row.rate_parameter_stable_key)
            if change is not None:
                new_value, reason = change
                if operation_type == "UNLOCK":
                    content.update(
                        locked=False,
                        locked_value_ppm=None,
                        lock_origin={
                            "schema_version": "1.0",
                            "action": "UNLOCK",
                            "reason": reason,
                            "actor_id": actor.user_id,
                            "occurred_at": occurred_at.isoformat(),
                        },
                    )
                else:
                    value = row.current_value_ppm if new_value is None else new_value
                    content.update(
                        current_value_ppm=value,
                        locked=True,
                        locked_value_ppm=value,
                        manual_reason=reason if new_value is not None else row.manual_reason,
                        lock_origin={
                            "schema_version": "1.0",
                            "action": operation_type,
                            "reason": reason,
                            "actor_id": actor.user_id,
                            "role_snapshot": _role_snapshot(actor),
                            "occurred_at": occurred_at.isoformat(),
                        },
                    )
            rows_content.append(content)
        response = await _persist_new_version(
            session,
            actor=actor,
            parent_revision=parent,
            budget=budget,
            base_version=base_version,
            base_confirmations=base_confirmations,
            rate_reference_version_id=base_version.rate_reference_version_id,
            fiscal_year=budget.fiscal_year,
            operation_type=operation_type,
            rows_content=rows_content,
            exception_content=[],
            command_type=command_type,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    return response


async def copy_cross_year_rate_locks(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: RateParameterCrossYearCopyRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateParameterVersionResponse:
    command_type = "COPY_CROSS_YEAR_RATE_LOCKS"
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
            return RateParameterVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        target_version, target_rows, target_confirmations = await _required_current_rate_state(
            session, parent
        )
        source_version = await session.get(RateParameterVersion, payload.source_rate_parameter_version_id)
        if source_version is None:
            raise DomainError(
                code="RATE_PARAMETER_VERSION_NOT_FOUND", message="来源比例参数版本不存在", status_code=404
            )
        await _assert_version_access(
            session,
            actor=actor,
            revision_id=source_version.budget_revision_id,
        )
        if source_version.fiscal_year == budget.fiscal_year:
            raise DomainError(
                code="CROSS_YEAR_COPY_REQUIRES_DIFFERENT_YEAR",
                message="同财年修订已自动继承锁定，不得调用跨财年复制",
                status_code=422,
            )
        source_rows = await _load_rows(session, source_version.rate_parameter_version_id)
        target_by_match = {row.cross_year_match_key: row for row in target_rows}
        confirmed = {
            item.rate_parameter_stable_key: item.boundary_fingerprint for item in target_confirmations
        }
        copied_by_target: dict[str, RateParameter] = {}
        exceptions: list[dict[str, object]] = []
        for source in [row for row in source_rows if row.locked]:
            target = target_by_match.get(source.cross_year_match_key)
            error_code: str | None = None
            if target is None:
                error_code = "CROSS_YEAR_KEY_NOT_MATCHED"
            elif confirmed.get(target.rate_parameter_stable_key) != _row_boundary_fingerprint(target):
                error_code = "TARGET_BOUNDARY_NOT_CONFIRMED"
            elif source.locked_value_ppm is None or not (
                target.lower_bound_ppm <= source.locked_value_ppm <= target.upper_bound_ppm
            ):
                error_code = "COPIED_LOCK_OUTSIDE_TARGET_BOUNDARY"
            elif source.locked_value_ppm % 100 != 0:
                error_code = "COPIED_LOCK_PRECISION_INVALID"
            if error_code is not None:
                detail = {
                    "schema_version": "1.0",
                    "source_fiscal_year": source.fiscal_year,
                    "target_fiscal_year": budget.fiscal_year,
                    "reason": payload.reason,
                }
                exceptions.append(
                    {
                        "source_stable_key": source.rate_parameter_stable_key,
                        "cross_year_match_key": source.cross_year_match_key,
                        "error_code": error_code,
                        "detail": detail,
                    }
                )
            else:
                assert target is not None
                copied_by_target[target.rate_parameter_stable_key] = source
        occurred_at = _now()
        rows_content = []
        for target in target_rows:
            content = _row_content(target)
            copied_source = copied_by_target.get(target.rate_parameter_stable_key)
            if copied_source is not None:
                value = copied_source.locked_value_ppm
                content.update(
                    current_value_ppm=value,
                    locked=True,
                    locked_value_ppm=value,
                    manual_reason=payload.reason,
                    lock_origin={
                        "schema_version": "1.0",
                        "action": "CROSS_YEAR_COPY",
                        "source_rate_parameter_version_id": str(source_version.rate_parameter_version_id),
                        "source_rate_parameter_stable_key": copied_source.rate_parameter_stable_key,
                        "reason": payload.reason,
                        "actor_id": actor.user_id,
                        "occurred_at": occurred_at.isoformat(),
                    },
                )
            rows_content.append(content)
        response = await _persist_new_version(
            session,
            actor=actor,
            parent_revision=parent,
            budget=budget,
            base_version=target_version,
            base_confirmations=target_confirmations,
            rate_reference_version_id=target_version.rate_reference_version_id,
            fiscal_year=budget.fiscal_year,
            operation_type="CROSS_YEAR_COPY",
            rows_content=rows_content,
            exception_content=exceptions,
            command_type=command_type,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    return response


async def get_rate_parameter_version(
    session: AsyncSession,
    *,
    actor: Actor,
    rate_parameter_version_id: uuid.UUID,
) -> RateParameterVersionResponse:
    version = await session.get(RateParameterVersion, rate_parameter_version_id)
    if version is None:
        raise DomainError(
            code="RATE_PARAMETER_VERSION_NOT_FOUND", message="比例参数版本不存在", status_code=404
        )
    revision, _ = await _assert_version_access(
        session,
        actor=actor,
        revision_id=version.budget_revision_id,
    )
    rows = await _load_rows(session, version.rate_parameter_version_id)
    confirmations = list(
        await session.scalars(
            select(RateBoundaryConfirmation).where(
                RateBoundaryConfirmation.rate_parameter_version_id == version.rate_parameter_version_id
            )
        )
    )
    exceptions = await _load_exceptions(session, version.rate_parameter_version_id)
    return _version_response(
        version,
        rows,
        confirmations,
        exceptions,
        _revision_link_response(revision),
    )


async def _persist_new_version(
    session: AsyncSession,
    *,
    actor: Actor,
    parent_revision: object,
    budget: object,
    base_version: RateParameterVersion | None,
    base_confirmations: Sequence[RateBoundaryConfirmation],
    rate_reference_version_id: uuid.UUID,
    fiscal_year: int,
    operation_type: str,
    rows_content: list[dict[str, object]],
    exception_content: list[dict[str, object]],
    command_type: str,
    request_hash: str,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> RateParameterVersionResponse:
    from packages.persistence.models import AnnualBudget, BudgetRevision

    assert isinstance(parent_revision, BudgetRevision)
    assert isinstance(budget, AnnualBudget)
    rows_content.sort(key=lambda item: str(item["rate_parameter_stable_key"]))
    exception_content.sort(key=lambda item: str(item["source_stable_key"]))
    occurred_at = _now()
    version_id = uuid7()
    version_hash = content_hash(
        {
            "schema_version": "1.0",
            "parent_rate_parameter_version_id": str(base_version.rate_parameter_version_id)
            if base_version
            else None,
            "rate_reference_version_id": str(rate_reference_version_id),
            "fiscal_year": fiscal_year,
            "operation_type": operation_type,
            "rows": rows_content,
            "copy_exceptions": exception_content,
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
        component_updates={"rate_parameter_version_id": str(version_id)},
        component_removals=DOWNSTREAM_REMOVALS,
    )
    version = RateParameterVersion(
        rate_parameter_version_id=version_id,
        budget_revision_id=revision.budget_revision_id,
        parent_rate_parameter_version_id=base_version.rate_parameter_version_id if base_version else None,
        rate_reference_version_id=rate_reference_version_id,
        fiscal_year=fiscal_year,
        operation_type=operation_type,
        record_count=len(rows_content),
        locked_count=sum(bool(item["locked"]) for item in rows_content),
        copy_exception_count=len(exception_content),
        content_hash=version_hash,
        request_hash=request_hash,
        created_by=actor.user_id,
        created_at=occurred_at,
    )
    session.add(version)
    await session.flush()
    rows = [
        RateParameter(
            rate_parameter_id=uuid7(),
            rate_parameter_version_id=version_id,
            **{
                **item,
                "rate_reference_id": uuid.UUID(str(item["rate_reference_id"])),
            },
            content_hash=content_hash(item),
        )
        for item in rows_content
    ]
    session.add_all(rows)
    await session.flush()
    row_by_key = {row.rate_parameter_stable_key: row for row in rows}
    inherited_confirmations: list[RateBoundaryConfirmation] = []
    for prior in base_confirmations:
        row = row_by_key.get(prior.rate_parameter_stable_key)
        if row is None or _row_boundary_fingerprint(row) != prior.boundary_fingerprint:
            continue
        inherited_confirmations.append(
            RateBoundaryConfirmation(
                rate_boundary_confirmation_id=uuid7(),
                rate_parameter_version_id=version_id,
                rate_parameter_id=row.rate_parameter_id,
                rate_parameter_stable_key=row.rate_parameter_stable_key,
                boundary_fingerprint=prior.boundary_fingerprint,
                confirmed_revision_id=revision.budget_revision_id,
                inherited_from_confirmation_id=prior.rate_boundary_confirmation_id,
                confirmed_by=prior.confirmed_by,
                role_snapshot=prior.role_snapshot,
                confirmed_at=prior.confirmed_at,
            )
        )
    session.add_all(inherited_confirmations)
    exceptions = [
        RateParameterCopyException(
            rate_parameter_copy_exception_id=uuid7(),
            rate_parameter_version_id=version_id,
            **item,
            content_hash=content_hash(item),
        )
        for item in exception_content
    ]
    session.add_all(exceptions)
    response = _version_response(
        version,
        rows,
        inherited_confirmations,
        exceptions,
        revision_response,
    )
    _add_audit(
        session,
        event_type=f"RATE_PARAMETER_{operation_type}",
        actor=actor,
        acting_role=_acting_rate_role(actor),
        object_type="RATE_PARAMETER_VERSION",
        object_key={"rate_parameter_version_id": str(version_id)},
        detail={
            "record_count": len(rows),
            "locked_count": version.locked_count,
            "copy_exception_count": len(exceptions),
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


async def _current_rate_state(
    session: AsyncSession, revision: object
) -> tuple[RateParameterVersion | None, list[RateParameter], list[RateBoundaryConfirmation]]:
    from packages.persistence.models import BudgetRevision

    assert isinstance(revision, BudgetRevision)
    raw = revision.component_manifest.get("rate_parameter_version_id")
    if not isinstance(raw, str):
        return None, [], []
    try:
        version_id = uuid.UUID(raw)
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="比例参数版本清单无效", status_code=409
        ) from exc
    version = await session.get(RateParameterVersion, version_id)
    if version is None:
        raise DomainError(
            code="RATE_PARAMETER_VERSION_NOT_FOUND", message="比例参数版本不存在", status_code=404
        )
    rows = await _load_rows(session, version_id)
    confirmations = list(
        await session.scalars(
            select(RateBoundaryConfirmation).where(
                RateBoundaryConfirmation.rate_parameter_version_id == version_id
            )
        )
    )
    return version, rows, confirmations


async def _required_current_rate_state(
    session: AsyncSession, revision: object
) -> tuple[RateParameterVersion, list[RateParameter], list[RateBoundaryConfirmation]]:
    version, rows, confirmations = await _current_rate_state(session, revision)
    if version is None:
        raise DomainError(
            code="RATE_PARAMETER_VERSION_REQUIRED", message="当前修订尚未建立比例参数版本", status_code=409
        )
    return version, rows, confirmations


async def _load_rows(session: AsyncSession, version_id: uuid.UUID) -> list[RateParameter]:
    return list(
        await session.scalars(
            select(RateParameter)
            .where(RateParameter.rate_parameter_version_id == version_id)
            .order_by(RateParameter.rate_parameter_stable_key)
        )
    )


async def _load_exceptions(session: AsyncSession, version_id: uuid.UUID) -> list[RateParameterCopyException]:
    return list(
        await session.scalars(
            select(RateParameterCopyException)
            .where(RateParameterCopyException.rate_parameter_version_id == version_id)
            .order_by(RateParameterCopyException.source_stable_key)
        )
    )


async def _confirmation_map(session: AsyncSession, version_id: uuid.UUID) -> dict[str, str]:
    rows = list(
        await session.scalars(
            select(RateBoundaryConfirmation).where(
                RateBoundaryConfirmation.rate_parameter_version_id == version_id
            )
        )
    )
    return {row.rate_parameter_stable_key: row.boundary_fingerprint for row in rows}


def _key_from_reference(reference: RateReference, fiscal_year: int) -> RateParameterKey:
    return RateParameterKey(
        rate_type=reference.rate_type,
        fiscal_year=fiscal_year,
        target_fiscal_season=reference.target_fiscal_season,
        track=reference.track,
        product=reference.product,
        grade=reference.grade,
        campus=reference.campus,
        renewal_relation_code=reference.renewal_relation_code,
    )


def _usage_mode(rate_type: str) -> str:
    return "REVERSE_INPUT" if rate_type == "NEW_STUDENT_SHARE" else "RATE_INPUT"


def _reference_row_content(
    reference: RateReference,
    *,
    key: RateParameterKey,
    stable_key: str,
    values: RateBoundaryValues,
    boundary_reason: str,
    manual_reason: str | None,
    locked: bool,
    locked_value_ppm: int | None,
    lock_origin: dict[str, object],
) -> dict[str, object]:
    return {
        "rate_reference_id": str(reference.rate_reference_id),
        "rate_parameter_stable_key": stable_key,
        "cross_year_match_key": cross_year_match_key(key),
        "rate_type": reference.rate_type,
        "rate_usage_mode": _usage_mode(reference.rate_type),
        "fiscal_year": key.fiscal_year,
        "source_fiscal_season": reference.source_fiscal_season,
        "target_fiscal_season": reference.target_fiscal_season,
        "quarter_name": reference.quarter_name,
        "track": reference.track,
        "product": reference.product,
        "grade": reference.grade,
        "campus": reference.campus,
        "renewal_relation_code": reference.renewal_relation_code,
        "source_type": reference.source_type,
        "system_value_ppm": reference.system_value_ppm,
        "current_value_ppm": values.current_value_ppm,
        "lower_bound_ppm": values.lower_bound_ppm,
        "upper_bound_ppm": values.upper_bound_ppm,
        "boundary_reason": boundary_reason,
        "manual_reason": manual_reason,
        "locked": locked,
        "locked_value_ppm": locked_value_ppm,
        "lock_origin": lock_origin,
    }


def _row_content(row: RateParameter) -> dict[str, object]:
    return {
        "rate_reference_id": str(row.rate_reference_id),
        "rate_parameter_stable_key": row.rate_parameter_stable_key,
        "cross_year_match_key": row.cross_year_match_key,
        "rate_type": row.rate_type,
        "rate_usage_mode": row.rate_usage_mode,
        "fiscal_year": row.fiscal_year,
        "source_fiscal_season": row.source_fiscal_season,
        "target_fiscal_season": row.target_fiscal_season,
        "quarter_name": row.quarter_name,
        "track": row.track,
        "product": row.product,
        "grade": row.grade,
        "campus": row.campus,
        "renewal_relation_code": row.renewal_relation_code,
        "source_type": row.source_type,
        "system_value_ppm": row.system_value_ppm,
        "current_value_ppm": row.current_value_ppm,
        "lower_bound_ppm": row.lower_bound_ppm,
        "upper_bound_ppm": row.upper_bound_ppm,
        "boundary_reason": row.boundary_reason,
        "manual_reason": row.manual_reason,
        "locked": row.locked,
        "locked_value_ppm": row.locked_value_ppm,
        "lock_origin": row.lock_origin,
    }


def _row_boundary_fingerprint(row: RateParameter) -> str:
    return boundary_fingerprint(
        stable_key=row.rate_parameter_stable_key,
        values=RateBoundaryValues(
            row.current_value_ppm,
            row.lower_bound_ppm,
            row.upper_bound_ppm,
        ),
    )


def _version_response(
    version: RateParameterVersion,
    rows: Sequence[RateParameter],
    confirmations: Sequence[RateBoundaryConfirmation],
    exceptions: Sequence[RateParameterCopyException],
    revision: RevisionCommandResponse,
) -> RateParameterVersionResponse:
    confirmed = {item.rate_parameter_stable_key: item.boundary_fingerprint for item in confirmations}
    ordered = sorted(rows, key=lambda item: item.rate_parameter_stable_key)
    confirmed_count = sum(
        confirmed.get(row.rate_parameter_stable_key) == _row_boundary_fingerprint(row) for row in ordered
    )
    return RateParameterVersionResponse(
        rate_parameter_version_id=version.rate_parameter_version_id,
        budget_revision_id=version.budget_revision_id,
        parent_rate_parameter_version_id=version.parent_rate_parameter_version_id,
        rate_reference_version_id=version.rate_reference_version_id,
        fiscal_year=version.fiscal_year,
        operation_type=version.operation_type,
        record_count=version.record_count,
        locked_count=version.locked_count,
        confirmed_boundary_count=confirmed_count,
        copy_exception_count=version.copy_exception_count,
        is_publishable=confirmed_count == version.record_count and not exceptions,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=[
            RateParameterResponse(
                rate_parameter_id=row.rate_parameter_id,
                rate_reference_id=row.rate_reference_id,
                rate_parameter_stable_key=row.rate_parameter_stable_key,
                cross_year_match_key=row.cross_year_match_key,
                rate_type=row.rate_type,
                rate_usage_mode=row.rate_usage_mode,
                fiscal_year=row.fiscal_year,
                source_fiscal_season=row.source_fiscal_season,
                target_fiscal_season=row.target_fiscal_season,
                quarter_name=row.quarter_name,
                track=row.track,
                product=row.product,
                grade=row.grade,
                campus=row.campus,
                renewal_relation_code=row.renewal_relation_code,
                source_type=row.source_type,
                system_value_ppm=row.system_value_ppm,
                current_value_ppm=row.current_value_ppm,
                lower_bound_ppm=row.lower_bound_ppm,
                upper_bound_ppm=row.upper_bound_ppm,
                boundary_reason=row.boundary_reason,
                manual_reason=row.manual_reason,
                boundary_confirmed=confirmed.get(row.rate_parameter_stable_key)
                == _row_boundary_fingerprint(row),
                locked=row.locked,
                locked_value_ppm=row.locked_value_ppm,
                lock_origin=row.lock_origin,
                auto_balance_eligible=(
                    not row.locked
                    and confirmed.get(row.rate_parameter_stable_key) == _row_boundary_fingerprint(row)
                ),
                content_hash=row.content_hash,
            )
            for row in ordered
        ],
        copy_exceptions=[
            RateParameterCopyExceptionResponse(
                rate_parameter_copy_exception_id=item.rate_parameter_copy_exception_id,
                source_stable_key=item.source_stable_key,
                cross_year_match_key=item.cross_year_match_key,
                error_code=item.error_code,
                detail=item.detail,
                content_hash=item.content_hash,
            )
            for item in sorted(exceptions, key=lambda value: value.source_stable_key)
        ],
        revision=revision,
    )


def _validation_details(stable_key: str, issues: Sequence[RateValidationIssue]) -> list[dict[str, object]]:
    return [
        {
            "rate_parameter_stable_key": stable_key,
            "code": issue.code,
            "message": issue.message,
        }
        for issue in issues
    ]


def _raise_batch_errors(errors: list[dict[str, object]]) -> None:
    raise DomainError(
        code="RATE_PARAMETER_BATCH_INVALID",
        message="比例参数批量操作未通过完整校验，整批未保存",
        status_code=422,
        details={"errors": errors},
    )


def _command_hash(revision_id: uuid.UUID, expected_revision_id: uuid.UUID, payload: object) -> str:
    return content_hash(
        {
            "revision_id": str(revision_id),
            "expected_revision_id": str(expected_revision_id),
            "payload": payload,
        }
    )


def _acting_rate_role(actor: Actor) -> Role:
    if actor.has_role(Role.DATA_MAINTAINER):
        return Role.DATA_MAINTAINER
    return Role.DEPARTMENT_BUDGET_OWNER
