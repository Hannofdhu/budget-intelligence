import uuid
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.opening import OpeningCreateRequest, OpeningVersionResponse
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.headcount.calculation import HeadcountInvariantError, fraction_string
from packages.headcount.opening import OpeningKey, SpringSource, prepare_bi_opening
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
    OpeningStudentVersion,
    RateParameter,
    RateParameterVersion,
    SourceMetricAggregate,
    SourceSnapshot,
    SourceSnapshotComponent,
)
from packages.persistence.planned_new_students import DOWNSTREAM_REMOVALS
from packages.persistence.versioning import _add_audit, _idempotency_replay, _now, _store_receipt
from packages.source_snapshot.aggregation import OPENING_ENROLLMENT


def _reject(code: str, message: str) -> None:
    raise DomainError(code=code, message=message, status_code=409)


async def get_opening_workspace(
    session: AsyncSession, *, actor: Actor, revision_id: uuid.UUID
) -> dict[str, object]:
    """Read only frozen inputs; never infer that an uncovered target is zero."""
    revision, budget = await _assert_version_access(session, actor=actor, revision_id=revision_id)
    if budget.fiscal_year < 2028:
        _reject("FY27_FINALIZED", "FY27 已定版，不从 BI 重建期初老生")
    combo_id = _manifest_uuid(revision, "combination_set_version_id", "请先建立预算明细")
    rate_id = _manifest_uuid(revision, "rate_parameter_version_id", "请先建立比例参数")
    snapshot = await session.get(SourceSnapshot, revision.source_snapshot_id)
    if snapshot is None or snapshot.status != "FROZEN":
        _reject("OPENING_SNAPSHOT_INVALID", "请先冻结来源快照")
    season = (budget.fiscal_year - 1) * 10 + 4
    aggregates = list(
        await session.scalars(
            select(SourceMetricAggregate).where(
                SourceMetricAggregate.source_snapshot_id == revision.source_snapshot_id,
                SourceMetricAggregate.metric_code == OPENING_ENROLLMENT,
                SourceMetricAggregate.fiscal_season == season,
            )
        )
    )
    components = list(
        await session.scalars(
            select(SourceSnapshotComponent)
            .where(
                SourceSnapshotComponent.source_snapshot_id == revision.source_snapshot_id,
            )
            .order_by(SourceSnapshotComponent.dataset_id, SourceSnapshotComponent.partition_value)
        )
    )
    rates = list(
        await session.scalars(
            select(RateParameter).where(
                RateParameter.rate_parameter_version_id == rate_id,
                RateParameter.rate_type == "RENEWAL_RATE",
                RateParameter.target_fiscal_season == budget.fiscal_year * 10 + 1,
            )
        )
    )
    combinations = list(
        await session.scalars(
            select(BudgetCombination)
            .where(
                BudgetCombination.combination_set_version_id == combo_id,
                BudgetCombination.enabled.is_(True),
            )
            .order_by(BudgetCombination.combination_stable_key)
        )
    )
    dimensions = ("track", "product", "grade", "subject", "campus")
    sources = []
    for component in components:
        rows = []
        for item in aggregates:
            if item.source_dataset_id != component.dataset_id:
                continue
            matching = [
                r
                for r in rates
                if all(getattr(r, k) == getattr(item, k) for k in ("track", "product", "grade", "campus"))
            ]
            valid = (
                item.numerator >= 0
                and item.numerator == item.numerator.to_integral_value()
                and item.denominator == 0
            )
            rows.append(
                {
                    **{k: getattr(item, k) for k in dimensions},
                    "enrollment": int(item.numerator) if valid else None,
                    "rate_parameter_id": str(matching[0].rate_parameter_id) if len(matching) == 1 else None,
                    "renewal_rate_ppm": matching[0].current_value_ppm if len(matching) == 1 else None,
                }
            )
        if rows:
            sources.append(
                {
                    "source_component_id": str(component.source_snapshot_component_id),
                    "dataset_name": component.dataset_name,
                    "snapshot_date": component.extracted_at.isoformat(),
                    "source_batch": component.task_batch_id,
                    "partition": component.partition_value,
                    "content_hash": component.content_hash,
                    "rows": rows,
                }
            )
    opening_status = "尚未建立期初版本"
    try:
        await load_opening_headcounts(
            session,
            revision=revision,
            fiscal_year=budget.fiscal_year,
            combinations=combinations,
            combination_version_id=combo_id,
            rate_version_id=rate_id,
        )
        opening_status = "当前期初版本来源已核验，依赖仍有效"
    except DomainError as error:
        opening_status = error.message
    return {
        "fiscal_year": budget.fiscal_year,
        "opening_status": opening_status,
        "opening_student_version_id": revision.component_manifest.get("opening_student_version_id"),
        "source_fiscal_season": season,
        "is_current_draft": budget.current_draft_revision_id == revision_id,
        "sources": sources,
        "combinations": [
            {"combination_id": str(c.combination_id), **{k: getattr(c, k) for k in dimensions}}
            for c in combinations
        ],
    }


async def rebuild_opening_for_application(
    session: AsyncSession,
    *,
    parent: BudgetRevision,
    revision: BudgetRevision,
    new_version_id: uuid.UUID,
    rate_version_id: uuid.UUID,
    actor: Actor,
    occurred_at: datetime,
) -> None:
    """Rebind verified source counts to newly persisted candidate rates in the same transaction."""
    old = await session.get(
        OpeningStudentVersion, uuid.UUID(str(parent.component_manifest["opening_student_version_id"]))
    )
    new_rates = await session.get(RateParameterVersion, rate_version_id)
    if (
        old is None
        or new_rates is None
        or old.content.get("source_verification") != "FROZEN_AGGREGATES_MATCHED"
    ):
        _reject("OPENING_NOT_VERIFIED", "候选应用必须基于已核验期初版本")
    assert old is not None and new_rates is not None
    prior_rows = list(
        await session.scalars(
            select(RateParameter).where(
                RateParameter.rate_parameter_version_id == old.rate_parameter_version_id
            )
        )
    )
    current_rows = list(
        await session.scalars(
            select(RateParameter).where(RateParameter.rate_parameter_version_id == rate_version_id)
        )
    )
    old_by_id = {str(row.rate_parameter_id): row.rate_parameter_stable_key for row in prior_rows}
    new_by_key = {row.rate_parameter_stable_key: row for row in current_rows}
    content = deepcopy(old.content)
    selected = {}
    for item in content["request"]["rows"]:
        new_rate = new_by_key[old_by_id[item["rate_parameter_id"]]]
        item["rate_parameter_id"] = str(new_rate.rate_parameter_id)
        selected[(item["track"], item["product"], item["grade"], item["subject"], item["campus"])] = new_rate
    for row in content["rows"]:
        source = row["source_key"]
        new_rate = selected[tuple(source[key] for key in ("track", "product", "grade", "subject", "campus"))]
        row["renewal_rate_ppm"] = new_rate.current_value_ppm
        row["rate_version_id"] = str(rate_version_id)
        row["theoretical_old_students"] = fraction_string(
            Fraction(row["enrollment"] * new_rate.current_value_ppm, 1000000)
            if row["target_key"] is not None
            else Fraction(0)
        )
    content["rate_hash"] = new_rates.content_hash
    content["parent_opening_student_version_id"] = str(old.opening_student_version_id)
    content["derivation"] = "SCENARIO_APPLICATION"
    content["status"] = "SOURCE_VERIFIED"
    session.add(
        OpeningStudentVersion(
            opening_student_version_id=new_version_id,
            budget_revision_id=revision.budget_revision_id,
            source_component_id=old.source_component_id,
            combination_set_version_id=old.combination_set_version_id,
            rate_parameter_version_id=rate_version_id,
            content=content,
            content_hash=content_hash(content),
            created_by=actor.user_id,
            created_at=occurred_at,
        )
    )
    await session.flush()


async def load_opening_headcounts(
    session: AsyncSession,
    *,
    revision: BudgetRevision,
    fiscal_year: int,
    combinations: list[BudgetCombination],
    combination_version_id: uuid.UUID,
    rate_version_id: uuid.UUID,
) -> tuple[dict[str, Fraction] | None, str | None]:
    raw_id = revision.component_manifest.get("opening_student_version_id")
    if fiscal_year < 2028 and raw_id is None:
        return None, None
    if fiscal_year < 2028:
        _reject("FY27_FINALIZED", "FY27 不使用新的跨年期初老生规则")
    if raw_id is None:
        _reject("OPENING_VERSION_REQUIRED", "FY28 起必须先建立并核验期初老生版本")
    try:
        version_id = uuid.UUID(str(raw_id))
    except ValueError:
        _reject("OPENING_VERSION_INVALID", "期初老生版本标识无效")
        raise AssertionError("unreachable") from None
    version = await session.get(OpeningStudentVersion, version_id)
    if version is None:
        _reject("OPENING_NOT_FOUND", "期初老生版本不存在")
    assert version is not None
    owner = await session.get(BudgetRevision, version.budget_revision_id)
    component = await session.get(SourceSnapshotComponent, version.source_component_id)
    if (
        owner is None
        or owner.annual_budget_id != revision.annual_budget_id
        or component is None
        or component.source_snapshot_id != revision.source_snapshot_id
        or version.combination_set_version_id != combination_version_id
        or version.rate_parameter_version_id != rate_version_id
    ):
        _reject("OPENING_BASIS_STALE", "期初老生所依赖的来源、组合或比例版本已变化，必须重新生成")
    data = version.content
    if (
        data.get("source_verification") != "FROZEN_AGGREGATES_MATCHED"
        or data.get("fiscal_year") != fiscal_year
        or data.get("source_fiscal_season") != (fiscal_year - 1) * 10 + 4
        or content_hash(data) != version.content_hash
    ):
        _reject("OPENING_NOT_VERIFIED", "期初老生来源未核验或版本内容不完整")
    by_key = {
        OpeningKey(c.track, c.product, c.grade, c.subject, c.campus): c.combination_stable_key
        for c in combinations
    }
    by_id = {str(c.combination_id): c.combination_stable_key for c in combinations}
    values: dict[str, Fraction] = {}
    try:
        for row in data["rows"]:
            if row["target_key"] is None:
                continue
            key = OpeningKey(**row["target_key"])
            stable = by_key[key]
            if stable in values:
                raise ValueError("duplicate")
            values[stable] = Fraction(row["theoretical_old_students"])
        for zero_id in data["zero_combination_ids"]:
            stable = by_id[zero_id]
            if stable in values:
                raise ValueError("duplicate")
            values[stable] = Fraction(0)
        if set(values) != set(by_id.values()) or any(value < 0 for value in values.values()):
            raise ValueError("coverage")
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        _reject("OPENING_CONTENT_INVALID", "期初老生明细缺失、重复或理论值无效")
    return values, version.content_hash


async def create_opening_version(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: OpeningCreateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> OpeningVersionResponse:
    if not actor.has_role(Role.DATA_MAINTAINER):
        raise DomainError(code="PERMISSION_DENIED", message="需要数据维护人员权限", status_code=403)
    command = "CREATE_OPENING_STUDENT_VERSION"
    request_hash = content_hash(
        {
            "revision": str(revision_id),
            "expected": str(expected_revision_id),
            "payload": payload.model_dump(mode="json"),
        }
    )
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return OpeningVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        if budget.fiscal_year < 2028:
            _reject("FY27_FINALIZED", "FY27 不从 BI 重建期初老生")
        combo_id = _manifest_uuid(parent, "combination_set_version_id", "必须先建立预算组合")
        rate_id = _manifest_uuid(parent, "rate_parameter_version_id", "必须先建立比例参数")
        combo_version = await session.get(BudgetCombinationSetVersion, combo_id)
        rate_version = await session.get(RateParameterVersion, rate_id)
        component = await session.get(SourceSnapshotComponent, payload.source_component_id)
        if combo_version is None or rate_version is None or component is None:
            _reject("OPENING_BASIS_MISSING", "期初老生来源或参数不存在")
        assert combo_version is not None and rate_version is not None and component is not None
        for owner in (combo_version, rate_version):
            _, owner_budget = await _assert_version_access(
                session,
                actor=actor,
                revision_id=owner.budget_revision_id,
            )
            if owner_budget.annual_budget_id != budget.annual_budget_id:
                _reject("OPENING_BASIS_MISMATCH", "上游版本不属于当前年度预算")
        snapshot = await session.get(SourceSnapshot, component.source_snapshot_id)
        if (
            snapshot is None
            or snapshot.department_id != budget.department_id
            or snapshot.status != "FROZEN"
            or snapshot.source_snapshot_id != parent.source_snapshot_id
        ):
            _reject("OPENING_SNAPSHOT_INVALID", "必须使用当前预算同部门的冻结来源快照")
        assert snapshot is not None
        frozen_rows = list(
            await session.scalars(
                select(SourceMetricAggregate).where(
                    SourceMetricAggregate.source_snapshot_id == snapshot.source_snapshot_id,
                    SourceMetricAggregate.source_dataset_id == component.dataset_id,
                    SourceMetricAggregate.metric_code == OPENING_ENROLLMENT,
                    SourceMetricAggregate.fiscal_season == payload.source_fiscal_season,
                )
            )
        )
        if not frozen_rows:
            _reject("OPENING_FROZEN_ENROLLMENT_MISSING", "冻结快照缺少普通口径春季招生人次，不能替用其他指标")
        frozen_counts = {}
        for frozen in frozen_rows:
            key = OpeningKey(frozen.track, frozen.product, frozen.grade, frozen.subject, frozen.campus)
            if (
                frozen.numerator < 0
                or frozen.numerator != frozen.numerator.to_integral_value()
                or frozen.denominator != 0
                or key in frozen_counts
            ):
                _reject("OPENING_FROZEN_ENROLLMENT_INVALID", "冻结招生汇总内容无效")
            frozen_counts[key] = int(frozen.numerator)
        declared = {
            OpeningKey(r.track, r.product, r.grade, r.subject, r.campus): r.enrollment for r in payload.rows
        }
        if len(declared) != len(payload.rows) or declared != frozen_counts:
            _reject("OPENING_FROZEN_ENROLLMENT_MISMATCH", "提交的春季来源与冻结招生汇总不一致或不完整")
        if (
            combo_version.target_fiscal_year != budget.fiscal_year
            or rate_version.fiscal_year != budget.fiscal_year
        ):
            _reject("OPENING_BASIS_MISMATCH", "上游版本财年不一致")
        combinations = list(
            await session.scalars(
                select(BudgetCombination).where(
                    BudgetCombination.combination_set_version_id == combo_id,
                    BudgetCombination.enabled.is_(True),
                )
            )
        )
        available = {OpeningKey(c.track, c.product, c.grade, c.subject, c.campus): c for c in combinations}
        sources: list[SpringSource] = []
        for item in payload.rows:
            rate = await session.get(RateParameter, item.rate_parameter_id)
            if (
                rate is None
                or rate.rate_parameter_version_id != rate_id
                or rate.rate_type != "RENEWAL_RATE"
                or rate.target_fiscal_season != budget.fiscal_year * 10 + 1
                or (rate.track, rate.product, rate.grade, rate.campus)
                != (item.track, item.product, item.grade, item.campus)
            ):
                _reject("OPENING_RATE_MISMATCH", "必须绑定来源年级对应的春续暑续班率")
            assert rate is not None
            sources.append(
                SpringSource(
                    OpeningKey(item.track, item.product, item.grade, item.subject, item.campus),
                    item.enrollment,
                    rate.current_value_ppm,
                    str(rate_id),
                )
            )
        try:
            preparation = prepare_bi_opening(
                fiscal_year=budget.fiscal_year,
                source_fiscal_season=payload.source_fiscal_season,
                snapshot_date=component.extracted_at.date(),
                source_batch=component.task_batch_id,
                source_snapshot_id=str(snapshot.source_snapshot_id),
                rows=sources,
            )
        except HeadcountInvariantError as error:
            _reject(error.code, error.message)
            raise AssertionError("unreachable") from error
        targets = {row.target_key for row in preparation.rows if row.target_key is not None}
        zeros = set(payload.zero_combination_ids)
        zero_keys = {key for key, combo in available.items() if combo.combination_id in zeros}
        if (
            len(zeros) != len(payload.zero_combination_ids)
            or len(zero_keys) != len(zeros)
            or targets & zero_keys
            or targets | zero_keys != set(available)
            or not available
        ):
            _reject("OPENING_COVERAGE_MISMATCH", "来源映射与显式零必须无重复地覆盖所有启用预算组合")
        rows_content = []
        for row in preparation.rows:
            data = asdict(row)
            data["theoretical_old_students"] = fraction_string(row.theoretical_old_students)
            rows_content.append(data)
        content = {
            "schema_version": "1.0",
            "rule_version": preparation.rule_version,
            "fiscal_year": budget.fiscal_year,
            "source_fiscal_season": payload.source_fiscal_season,
            "source_snapshot_id": str(snapshot.source_snapshot_id),
            "source_hash": component.content_hash,
            "source_batch": preparation.source_batch,
            "snapshot_date": preparation.snapshot_date.isoformat(),
            "source_dataset_id": component.dataset_id,
            "source_partition": component.partition_value,
            "combination_hash": combo_version.content_hash,
            "rate_hash": rate_version.content_hash,
            "rows": rows_content,
            "zero_combination_ids": sorted(str(value) for value in zeros),
            "request": payload.model_dump(mode="json"),
            "source_verification": "FROZEN_AGGREGATES_MATCHED",
            "source_metric_code": OPENING_ENROLLMENT,
            "verified_aggregate_hashes": sorted(row.content_hash for row in frozen_rows),
            "status": "SOURCE_VERIFIED",
        }
        version_id, now = uuid7(), _now()
        revision, link = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=now,
            component_updates={"opening_student_version_id": str(version_id)},
            component_removals=DOWNSTREAM_REMOVALS,
        )
        version = OpeningStudentVersion(
            opening_student_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            source_component_id=payload.source_component_id,
            combination_set_version_id=combo_id,
            rate_parameter_version_id=rate_id,
            content=content,
            content_hash=content_hash(content),
            created_by=actor.user_id,
            created_at=now,
        )
        session.add(version)
        await session.flush()
        response = OpeningVersionResponse(
            opening_student_version_id=version_id,
            content_hash=version.content_hash,
            content=content,
            revision=link,
        )
        _add_audit(
            session,
            event_type=command,
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="OPENING_STUDENT_VERSION",
            object_key={"id": str(version_id)},
            detail={"content_hash": version.content_hash, "reason": payload.reason},
            request_id=request_id,
            occurred_at=now,
            annual_budget_id=budget.annual_budget_id,
            budget_revision_id=revision.budget_revision_id,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response.model_dump(mode="json"),
            occurred_at=now,
        )
        return response


async def get_opening_version(
    session: AsyncSession,
    *,
    actor: Actor,
    version_id: uuid.UUID,
) -> OpeningVersionResponse:
    version = await session.get(OpeningStudentVersion, version_id)
    if version is None:
        raise DomainError(code="OPENING_NOT_FOUND", message="期初老生版本不存在", status_code=404)
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    return OpeningVersionResponse(
        opening_student_version_id=version_id,
        content_hash=version.content_hash,
        content=version.content,
        revision=_revision_link_response(revision),
    )
