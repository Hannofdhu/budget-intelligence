from dataclasses import dataclass
from decimal import Decimal

from packages.domain.versioning.hashing import content_hash


class EntryAllocationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CampusAllocationInput:
    campus: str
    campus_stable_key: str
    historical_business_new_students: int
    classroom_resource: dict[str, object]
    sales_resource: dict[str, object]
    manual_reserved_suggestion: int | None
    manual_suggestion_reason: str | None


@dataclass(frozen=True)
class CampusAllocationResult:
    campus: str
    campus_stable_key: str
    historical_business_new_students: int
    history_availability: str
    classroom_resource: dict[str, object]
    sales_resource: dict[str, object]
    manual_reserved_suggestion: int
    manual_suggestion_reason: str | None
    suggested_entry_theoretical: Decimal
    suggested_entry_floor: int
    suggested_entry_fraction: Decimal
    suggestion_tail_assigned: bool
    suggested_entry_value: int
    suggestion_source_type: str
    content_hash: str

    def content(self) -> dict[str, object]:
        return {
            "campus": self.campus,
            "campus_stable_key": self.campus_stable_key,
            "historical_business_new_students": self.historical_business_new_students,
            "history_availability": self.history_availability,
            "classroom_resource": self.classroom_resource,
            "sales_resource": self.sales_resource,
            "manual_reserved_suggestion": self.manual_reserved_suggestion,
            "manual_suggestion_reason": self.manual_suggestion_reason,
            "suggested_entry_theoretical": self.suggested_entry_theoretical,
            "suggested_entry_floor": self.suggested_entry_floor,
            "suggested_entry_fraction": self.suggested_entry_fraction,
            "suggestion_tail_assigned": self.suggestion_tail_assigned,
            "suggested_entry_value": self.suggested_entry_value,
            "suggestion_source_type": self.suggestion_source_type,
        }


def allocate_campus_entry_suggestions(
    *, fiscal_year_entry_total: int, campuses: list[CampusAllocationInput]
) -> list[CampusAllocationResult]:
    if fiscal_year_entry_total < 0:
        raise EntryAllocationError("ENTRY_TOTAL_INVALID", "财年入口总值不能为负数")
    if not campuses:
        raise EntryAllocationError("CAMPUS_SET_EMPTY", "预算组合中没有可生成建议的校区")
    if len({item.campus_stable_key for item in campuses}) != len(campuses):
        raise EntryAllocationError("CAMPUS_DUPLICATED", "校区输入存在重复")

    historical = [item for item in campuses if item.historical_business_new_students > 0]
    no_history = [item for item in campuses if item.historical_business_new_students == 0]
    for item in campuses:
        if item.historical_business_new_students < 0:
            raise EntryAllocationError("CAMPUS_HISTORY_INVALID", "校区历史入口人次不能为负数")
        if not item.classroom_resource or not item.sales_resource:
            raise EntryAllocationError(
                "CAMPUS_RESOURCE_REQUIRED", f"校区 {item.campus} 必须填写教室资源和销售资源"
            )
    for item in no_history:
        if item.manual_reserved_suggestion is None or item.manual_reserved_suggestion < 0:
            raise EntryAllocationError(
                "MANUAL_RESERVATION_REQUIRED", f"无历史校区 {item.campus} 必须填写非负整数建议预留"
            )
        if not item.manual_suggestion_reason or not item.manual_suggestion_reason.strip():
            raise EntryAllocationError(
                "MANUAL_RESERVATION_REASON_REQUIRED", f"无历史校区 {item.campus} 必须填写建议原因"
            )
    for item in historical:
        if item.manual_reserved_suggestion is not None:
            raise EntryAllocationError(
                "HISTORICAL_CAMPUS_MANUAL_RESERVATION_FORBIDDEN",
                f"有历史校区 {item.campus} 不使用人工预留",
            )

    reserved_total = sum(item.manual_reserved_suggestion or 0 for item in no_history)
    if reserved_total > fiscal_year_entry_total:
        raise EntryAllocationError(
            "MANUAL_RESERVATION_EXCEEDS_TOTAL",
            f"无历史校区建议预留超出财年入口总值 {reserved_total - fiscal_year_entry_total} 人",
        )
    remaining = fiscal_year_entry_total - reserved_total
    historical_total = sum(item.historical_business_new_students for item in historical)
    if not historical:
        if reserved_total != fiscal_year_entry_total:
            raise EntryAllocationError(
                "ALL_CAMPUSES_MANUAL_TOTAL_MISMATCH",
                "全部校区均无历史时，人工建议合计必须等于财年入口总值",
            )
        return [
            _manual_result(item) for item in sorted(no_history, key=lambda value: value.campus_stable_key)
        ]

    theoretical: dict[str, Decimal] = {}
    floors: dict[str, int] = {}
    fractions: dict[str, Decimal] = {}
    for item in historical:
        value = (
            Decimal(remaining) * Decimal(item.historical_business_new_students) / Decimal(historical_total)
        )
        floor = int(value)
        theoretical[item.campus_stable_key] = value
        floors[item.campus_stable_key] = floor
        fractions[item.campus_stable_key] = value - Decimal(floor)
    tail_count = remaining - sum(floors.values())
    tail_keys = {
        item.campus_stable_key
        for item in sorted(
            historical,
            key=lambda value: (-fractions[value.campus_stable_key], value.campus_stable_key),
        )[:tail_count]
    }
    results = [_manual_result(item) for item in no_history]
    for item in historical:
        stable_key = item.campus_stable_key
        tail = stable_key in tail_keys
        result_content: dict[str, object] = {
            "campus": item.campus,
            "campus_stable_key": stable_key,
            "historical_business_new_students": item.historical_business_new_students,
            "history_availability": "HAS_HISTORY",
            "classroom_resource": item.classroom_resource,
            "sales_resource": item.sales_resource,
            "manual_reserved_suggestion": 0,
            "manual_suggestion_reason": None,
            "suggested_entry_theoretical": theoretical[stable_key],
            "suggested_entry_floor": floors[stable_key],
            "suggested_entry_fraction": fractions[stable_key],
            "suggestion_tail_assigned": tail,
            "suggested_entry_value": floors[stable_key] + int(tail),
            "suggestion_source_type": "HISTORICAL_FORMULA",
        }
        results.append(
            CampusAllocationResult(
                campus=item.campus,
                campus_stable_key=stable_key,
                historical_business_new_students=item.historical_business_new_students,
                history_availability="HAS_HISTORY",
                classroom_resource=item.classroom_resource,
                sales_resource=item.sales_resource,
                manual_reserved_suggestion=0,
                manual_suggestion_reason=None,
                suggested_entry_theoretical=theoretical[stable_key],
                suggested_entry_floor=floors[stable_key],
                suggested_entry_fraction=fractions[stable_key],
                suggestion_tail_assigned=tail,
                suggested_entry_value=floors[stable_key] + int(tail),
                suggestion_source_type="HISTORICAL_FORMULA",
                content_hash=content_hash(result_content),
            )
        )
    results.sort(key=lambda value: value.campus_stable_key)
    if sum(item.suggested_entry_value for item in results) != fiscal_year_entry_total:
        raise EntryAllocationError("SUGGESTION_TOTAL_MISMATCH", "校区入口建议合计未调平")
    return results


def _manual_result(item: CampusAllocationInput) -> CampusAllocationResult:
    value = item.manual_reserved_suggestion or 0
    content: dict[str, object] = {
        "campus": item.campus,
        "campus_stable_key": item.campus_stable_key,
        "historical_business_new_students": 0,
        "history_availability": "NO_HISTORY",
        "classroom_resource": item.classroom_resource,
        "sales_resource": item.sales_resource,
        "manual_reserved_suggestion": value,
        "manual_suggestion_reason": item.manual_suggestion_reason,
        "suggested_entry_theoretical": Decimal(value),
        "suggested_entry_floor": value,
        "suggested_entry_fraction": Decimal(0),
        "suggestion_tail_assigned": False,
        "suggested_entry_value": value,
        "suggestion_source_type": "BUSINESS_MANUAL",
    }
    return CampusAllocationResult(
        campus=item.campus,
        campus_stable_key=item.campus_stable_key,
        historical_business_new_students=0,
        history_availability="NO_HISTORY",
        classroom_resource=item.classroom_resource,
        sales_resource=item.sales_resource,
        manual_reserved_suggestion=value,
        manual_suggestion_reason=item.manual_suggestion_reason,
        suggested_entry_theoretical=Decimal(value),
        suggested_entry_floor=value,
        suggested_entry_fraction=Decimal(0),
        suggestion_tail_assigned=False,
        suggested_entry_value=value,
        suggestion_source_type="BUSINESS_MANUAL",
        content_hash=content_hash(content),
    )
