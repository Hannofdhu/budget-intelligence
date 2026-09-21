import json
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    DiscountParameterVersionResponse,
    PlannedNewStudentVersionResponse,
    RateParameterVersionResponse,
    ResourceFinanceParameterVersionResponse,
    RevisionDetailDiffItemResponse,
    RevisionDetailDiffResponse,
    RevisionDetailStateResponse,
)
from packages.domain.identity.models import Actor
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.models import AnnualBudget, BudgetRevision
from packages.persistence.planned_new_students import get_planned_new_student_version
from packages.persistence.rate_parameters import get_rate_parameter_version
from packages.persistence.resource_finance import get_resource_finance_version
from packages.persistence.revenue import get_discount_parameter_version

SUPPORTED_COMPONENTS = {
    "rate_parameter_version_id": "续班率与新生占比",
    "planned_new_student_version_id": "计划新生人次",
    "resource_finance_parameter_version_id": "班均与财务参数",
    "discount_parameter_version_id": "折扣参数",
}


@dataclass(frozen=True)
class _DetailRecord:
    stable_key: str
    detail_type: str
    business_key: dict[str, object]
    state: RevisionDetailStateResponse


async def get_revision_detail_diff(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    against_revision_id: uuid.UUID,
    component_key: str,
    changed_only: bool,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> RevisionDetailDiffResponse:
    if component_key not in SUPPORTED_COMPONENTS:
        raise DomainError(
            code="REVISION_DETAIL_COMPONENT_UNSUPPORTED",
            message="该组件暂不支持业务明细比较",
            status_code=422,
            details={"supported_components": sorted(SUPPORTED_COMPONENTS)},
        )
    revision = await session.get(BudgetRevision, revision_id)
    against = await session.get(BudgetRevision, against_revision_id)
    if revision is None or against is None:
        raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
    if revision.annual_budget_id != against.annual_budget_id:
        raise DomainError(
            code="REVISION_DIFF_SCOPE_MISMATCH",
            message="只能比较同一年度预算链中的修订",
            status_code=422,
        )
    budget = await session.get(AnnualBudget, revision.annual_budget_id)
    if budget is None or budget.department_id != actor.department_id:
        raise DomainError(code="ANNUAL_BUDGET_NOT_FOUND", message="年度预算链不存在", status_code=404)

    revision_rows = await _load_component_rows(
        session,
        actor=actor,
        revision=revision,
        component_key=component_key,
    )
    against_rows = await _load_component_rows(
        session,
        actor=actor,
        revision=against,
        component_key=component_key,
    )
    items = _compare_rows(revision_rows, against_rows)
    counts = {
        change_type: sum(item.change_type == change_type for item in items)
        for change_type in ("ADDED", "REMOVED", "CHANGED", "UNCHANGED")
    }
    canonical = {
        "schema_version": "1.0",
        "revision_id": str(revision_id),
        "against_revision_id": str(against_revision_id),
        "component_key": component_key,
        "items": [item.model_dump(mode="json") for item in items],
    }

    filtered = items
    if changed_only:
        filtered = [item for item in filtered if item.change_type != "UNCHANGED"]
    normalized_query = query.strip().casefold() if query else ""
    if normalized_query:
        filtered = [
            item
            for item in filtered
            if normalized_query
            in json.dumps(
                {
                    "business_key": item.business_key,
                    "detail_type": item.detail_type,
                    "against_reason": item.against.reason,
                    "revision_reason": item.revision.reason,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).casefold()
        ]
    if cursor is not None:
        if not any(item.stable_key == cursor for item in filtered):
            raise DomainError(
                code="REVISION_DETAIL_CURSOR_NOT_FOUND",
                message="明细差异游标不存在或已不符合当前筛选条件",
                status_code=404,
            )
        filtered = [item for item in filtered if item.stable_key > cursor]

    page = filtered[: limit + 1]
    has_more = len(page) > limit
    page_items = page[:limit]
    return RevisionDetailDiffResponse(
        revision_id=revision_id,
        against_revision_id=against_revision_id,
        component_key=component_key,
        component_label=SUPPORTED_COMPONENTS[component_key],
        total_count=len(items),
        changed_count=counts["ADDED"] + counts["REMOVED"] + counts["CHANGED"],
        added_count=counts["ADDED"],
        removed_count=counts["REMOVED"],
        unchanged_count=counts["UNCHANGED"],
        items=page_items,
        next_cursor=page_items[-1].stable_key if has_more else None,
        content_hash=content_hash(canonical),
    )


async def _load_component_rows(
    session: AsyncSession,
    *,
    actor: Actor,
    revision: BudgetRevision,
    component_key: str,
) -> dict[str, _DetailRecord]:
    raw_version_id = revision.component_manifest.get(component_key)
    if raw_version_id is None:
        return {}
    try:
        version_id = uuid.UUID(str(raw_version_id))
    except ValueError as exc:
        raise DomainError(
            code="REVISION_COMPONENT_ID_INVALID",
            message="预算修订中的组件版本 ID 无效",
            status_code=409,
        ) from exc

    if component_key == "rate_parameter_version_id":
        rate_response = await get_rate_parameter_version(
            session,
            actor=actor,
            rate_parameter_version_id=version_id,
        )
        return _rate_records(rate_response)
    if component_key == "planned_new_student_version_id":
        planned_response = await get_planned_new_student_version(
            session,
            actor=actor,
            planned_new_student_version_id=version_id,
        )
        return _planned_records(planned_response)
    if component_key == "resource_finance_parameter_version_id":
        resource_response = await get_resource_finance_version(
            session,
            actor=actor,
            resource_finance_parameter_version_id=version_id,
        )
        return _resource_records(resource_response)
    discount_response = await get_discount_parameter_version(
        session,
        actor=actor,
        discount_parameter_version_id=version_id,
    )
    return _discount_records(discount_response)


def _compare_rows(
    revision_rows: dict[str, _DetailRecord],
    against_rows: dict[str, _DetailRecord],
) -> list[RevisionDetailDiffItemResponse]:
    missing = RevisionDetailStateResponse(present=False, status="NOT_PRESENT")
    items = []
    for stable_key in sorted(set(revision_rows) | set(against_rows)):
        revision = revision_rows.get(stable_key)
        against = against_rows.get(stable_key)
        revision_state = revision.state if revision else missing
        against_state = against.state if against else missing
        if against is None:
            change_type = "ADDED"
        elif revision is None:
            change_type = "REMOVED"
        elif _state_hash(revision_state) != _state_hash(against_state):
            change_type = "CHANGED"
        else:
            change_type = "UNCHANGED"
        source = revision or against
        assert source is not None
        items.append(
            RevisionDetailDiffItemResponse(
                stable_key=stable_key,
                detail_type=source.detail_type,
                change_type=change_type,
                business_key=source.business_key,
                against=against_state,
                revision=revision_state,
            )
        )
    return items


def _state_hash(state: RevisionDetailStateResponse) -> str:
    return content_hash(
        {
            "present": state.present,
            "system_value": state.system_value,
            "manual_value": state.manual_value,
            "final_value": state.final_value,
            "confirmed": state.confirmed,
            "status": state.status,
        }
    )


def _rate_records(response: RateParameterVersionResponse) -> dict[str, _DetailRecord]:
    records = {}
    for row in response.items:
        manual_value = (
            row.current_value_ppm
            if row.manual_reason is not None or row.current_value_ppm != row.system_value_ppm
            else None
        )
        records[row.rate_parameter_stable_key] = _DetailRecord(
            stable_key=row.rate_parameter_stable_key,
            detail_type=row.rate_type,
            business_key={
                "财季": row.target_fiscal_season,
                "季度": row.quarter_name,
                "赛道": row.track,
                "产品": row.product,
                "年级": row.grade,
                "校区": row.campus,
            },
            state=RevisionDetailStateResponse(
                present=True,
                system_value=row.system_value_ppm,
                manual_value=manual_value,
                final_value=row.current_value_ppm,
                confirmed=row.boundary_confirmed,
                status="LOCKED" if row.locked else "EDITABLE",
                reason=row.manual_reason or row.boundary_reason,
                source_evidence={
                    "source_type": row.source_type,
                    "rate_reference_id": str(row.rate_reference_id),
                },
                provenance={
                    "lower_bound_ppm": row.lower_bound_ppm,
                    "upper_bound_ppm": row.upper_bound_ppm,
                    "rate_usage_mode": row.rate_usage_mode,
                    "lock_origin": row.lock_origin,
                },
                content_hash=row.content_hash,
            ),
        )
    return records


def _planned_records(response: PlannedNewStudentVersionResponse) -> dict[str, _DetailRecord]:
    return {
        row.planned_new_student_stable_key: _DetailRecord(
            stable_key=row.planned_new_student_stable_key,
            detail_type="PLANNED_NEW_STUDENTS",
            business_key={
                "财季": row.fiscal_season,
                "季度": row.quarter_name,
                "赛道": row.track,
                "产品": row.product,
                "入口属性": row.entry_attribute,
                "年级": row.grade,
                "科目": row.subject,
                "校区": row.campus,
            },
            state=RevisionDetailStateResponse(
                present=True,
                manual_value=row.planned_new_students,
                final_value=row.planned_new_students,
                confirmed=row.locked,
                status=row.handling_status,
                reason=row.manual_reason,
                provenance={
                    "lock_origin": row.lock_origin,
                    "calculated_new_student_share_ppm": row.calculated_new_student_share_ppm,
                },
                content_hash=row.content_hash,
            ),
        )
        for row in response.items
    }


def _resource_records(response: ResourceFinanceParameterVersionResponse) -> dict[str, _DetailRecord]:
    status = "CONFIRMED" if response.confirmed else "PENDING_CONFIRMATION"
    records = {
        "RESOURCE_FINANCE:TAX_FACTOR": _DetailRecord(
            stable_key="RESOURCE_FINANCE:TAX_FACTOR",
            detail_type="BUDGET_TAX_FACTOR",
            business_key={"参数": "预算含税转换因子"},
            state=RevisionDetailStateResponse(
                present=True,
                final_value=str(response.budget_tax_conversion_factor),
                confirmed=response.confirmed,
                status=status,
                reason=response.tax_factor_change_reason,
                source_evidence=response.tax_factor_source_evidence,
                content_hash=response.content_hash,
            ),
        )
    }
    for class_row in response.class_size_items:
        stable_key = f"CLASS_SIZE:{class_row.class_size_parameter_stable_key}"
        records[stable_key] = _DetailRecord(
            stable_key=stable_key,
            detail_type="CLASS_SIZE",
            business_key={"赛道": class_row.track, "产品": class_row.product},
            state=RevisionDetailStateResponse(
                present=True,
                system_value=class_row.system_value,
                manual_value=(
                    class_row.final_value if class_row.final_value != class_row.system_value else None
                ),
                final_value=class_row.final_value,
                confirmed=response.confirmed,
                status=status,
                reason=class_row.change_reason,
                source_evidence=class_row.source_evidence,
                content_hash=class_row.content_hash,
            ),
        )
    for revenue_row in response.revenue_items:
        stable_key = f"REVENUE:{revenue_row.revenue_parameter_stable_key}"
        records[stable_key] = _DetailRecord(
            stable_key=stable_key,
            detail_type="REVENUE_PARAMETER",
            business_key={
                "财季": revenue_row.fiscal_season,
                "季度": revenue_row.quarter_name,
                "赛道": revenue_row.track,
                "产品": revenue_row.product,
            },
            state=RevisionDetailStateResponse(
                present=True,
                final_value={
                    "list_price_yuan": str(revenue_row.list_price_yuan),
                    "current_season_recognition_rate_ppm": (revenue_row.current_season_recognition_rate_ppm),
                    "cross_season_recognition_rate_ppm": (revenue_row.cross_season_recognition_rate_ppm),
                },
                confirmed=response.confirmed,
                status=status,
                reason=revenue_row.change_reason,
                source_evidence=revenue_row.source_evidence,
                provenance={"recognition_parameter_key": revenue_row.recognition_parameter_key},
                content_hash=revenue_row.content_hash,
            ),
        )
    for opening_row in response.opening_carryover_items:
        stable_key = f"OPENING_CARRYOVER:{opening_row.opening_carryover_stable_key}"
        records[stable_key] = _DetailRecord(
            stable_key=stable_key,
            detail_type="OPENING_CARRYOVER",
            business_key={
                "财季": opening_row.fiscal_season,
                "季度": opening_row.quarter_name,
                "赛道": opening_row.track,
                "产品": opening_row.product,
                "年级": opening_row.grade,
                "科目": opening_row.subject,
                "校区": opening_row.campus,
            },
            state=RevisionDetailStateResponse(
                present=True,
                final_value=str(opening_row.amount_yuan),
                confirmed=response.confirmed,
                status=status,
                reason=opening_row.change_reason,
                source_evidence=opening_row.source_evidence,
                content_hash=opening_row.content_hash,
            ),
        )
    return records


def _discount_records(response: DiscountParameterVersionResponse) -> dict[str, _DetailRecord]:
    status = "CONFIRMED" if response.confirmed else "PENDING_CONFIRMATION"
    records = {}
    for detail_row in response.detail_items:
        stable_key = f"DETAIL_DISCOUNT:{detail_row.detail_discount_stable_key}"
        records[stable_key] = _DetailRecord(
            stable_key=stable_key,
            detail_type="DETAIL_DISCOUNT",
            business_key={
                "财季": detail_row.fiscal_season,
                "季度": detail_row.quarter_name,
                "赛道": detail_row.track,
                "产品": detail_row.product,
                "年级": detail_row.grade,
            },
            state=RevisionDetailStateResponse(
                present=True,
                final_value=detail_row.discount_rate_ppm,
                confirmed=response.confirmed,
                status=status,
                reason=detail_row.change_reason,
                source_evidence=detail_row.source_evidence,
                content_hash=detail_row.content_hash,
            ),
        )
    for target_row in response.composite_targets:
        stable_key = f"COMPOSITE_DISCOUNT:{target_row.composite_target_stable_key}"
        records[stable_key] = _DetailRecord(
            stable_key=stable_key,
            detail_type="COMPOSITE_DISCOUNT_TARGET",
            business_key={
                "层级": target_row.scope_level,
                "赛道": target_row.track or "部门合计",
                "期间": target_row.period_type,
                "财季": target_row.fiscal_season or "全年",
            },
            state=RevisionDetailStateResponse(
                present=True,
                final_value=target_row.target_rate_ppm,
                confirmed=response.confirmed,
                status=status,
                reason=target_row.change_reason,
                source_evidence=target_row.source_evidence,
                content_hash=target_row.content_hash,
            ),
        )
    return records
