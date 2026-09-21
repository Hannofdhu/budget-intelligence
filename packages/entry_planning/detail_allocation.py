from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


class EntryDetailAllocationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EntryDetailAllocationInput:
    combination_stable_key: str
    origin_type: str
    historical_business_new_students: int
    manual_reservation_ratio_ppm: int | None


@dataclass(frozen=True)
class EntryDetailAllocationResult:
    combination_stable_key: str
    historical_business_new_students: int
    manual_reservation_ratio_ppm: int | None
    allocation_source_type: str
    theoretical_value: Decimal
    floor_value: int
    fraction_value: Decimal
    tail_assigned: bool
    entry_initial_value: int


def allocate_entry_details(
    *, campus_entry_value: int, items: list[EntryDetailAllocationInput]
) -> list[EntryDetailAllocationResult]:
    if campus_entry_value < 0:
        raise EntryDetailAllocationError("CAMPUS_ENTRY_VALUE_INVALID", "校区入口确定值不能为负数")
    if not items:
        raise EntryDetailAllocationError("ENTRY_COMBINATION_EMPTY", "校区没有有效入口明细组合")
    if len({item.combination_stable_key for item in items}) != len(items):
        raise EntryDetailAllocationError("ENTRY_COMBINATION_DUPLICATED", "入口明细组合稳定键重复")

    manual = [item for item in items if item.origin_type == "MANUAL"]
    historical = [item for item in items if item.origin_type == "HISTORICAL"]
    if len(manual) + len(historical) != len(items):
        raise EntryDetailAllocationError("ENTRY_ORIGIN_INVALID", "入口明细组合来源类型无效")
    for item in items:
        if item.historical_business_new_students < 0:
            raise EntryDetailAllocationError("ENTRY_HISTORY_INVALID", "入口历史业务新生人次不能为负数")
    for item in manual:
        if item.manual_reservation_ratio_ppm is None:
            raise EntryDetailAllocationError(
                "MANUAL_ENTRY_RATIO_REQUIRED", "人工新增入口组合必须填写人工预留比例"
            )
        if not 0 <= item.manual_reservation_ratio_ppm <= 1_000_000:
            raise EntryDetailAllocationError("MANUAL_ENTRY_RATIO_INVALID", "人工预留比例必须介于 0% 至 100%")
    for item in historical:
        if item.manual_reservation_ratio_ppm is not None:
            raise EntryDetailAllocationError(
                "HISTORICAL_ENTRY_RATIO_FORBIDDEN", "存量入口组合不能填写新产品人工预留比例"
            )

    manual_ratio_total = sum(item.manual_reservation_ratio_ppm or 0 for item in manual)
    if manual_ratio_total > 1_000_000:
        raise EntryDetailAllocationError(
            "MANUAL_ENTRY_RATIO_EXCEEDS_TOTAL",
            f"同校区人工入口比例合计超出 {(manual_ratio_total - 1_000_000) / 10_000:g} 个百分点",
        )
    manual_theoretical = {
        item.combination_stable_key: Decimal(campus_entry_value)
        * Decimal(item.manual_reservation_ratio_ppm or 0)
        / Decimal(1_000_000)
        for item in manual
    }
    manual_target = int(
        sum(manual_theoretical.values(), start=Decimal(0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    manual_results = _allocate_segment(
        items=manual,
        theoretical=manual_theoretical,
        target=manual_target,
        source_type="MANUAL_RESERVATION",
    )

    remaining = campus_entry_value - manual_target
    history_total = sum(item.historical_business_new_students for item in historical)
    if remaining > 0 and history_total == 0:
        raise EntryDetailAllocationError(
            "ENTRY_DETAIL_HISTORY_MISSING",
            "存量入口历史分母为 0 且仍有待分配额度，必须改为完整人工计划",
        )
    historical_theoretical = {
        item.combination_stable_key: (
            Decimal(remaining) * Decimal(item.historical_business_new_students) / Decimal(history_total)
            if history_total > 0
            else Decimal(0)
        )
        for item in historical
    }
    historical_results = _allocate_segment(
        items=historical,
        theoretical=historical_theoretical,
        target=remaining,
        source_type="HISTORICAL_SHARE",
    )
    results = sorted(
        [*manual_results, *historical_results],
        key=lambda item: item.combination_stable_key,
    )
    if sum(item.entry_initial_value for item in results) != campus_entry_value:
        raise EntryDetailAllocationError("ENTRY_DETAIL_TOTAL_MISMATCH", "入口明细未严格汇总到校区确定值")
    return results


def _allocate_segment(
    *,
    items: list[EntryDetailAllocationInput],
    theoretical: dict[str, Decimal],
    target: int,
    source_type: str,
) -> list[EntryDetailAllocationResult]:
    if not items:
        if target != 0:
            raise EntryDetailAllocationError("ENTRY_SEGMENT_EMPTY", "入口分配段没有可承接组合")
        return []
    floors = {key: int(value) for key, value in theoretical.items()}
    fractions = {key: value - Decimal(floors[key]) for key, value in theoretical.items()}
    tail_count = target - sum(floors.values())
    if tail_count < 0 or tail_count > len(items):
        raise EntryDetailAllocationError("ENTRY_SEGMENT_ROUNDING_INVALID", "入口分配段整数目标无效")
    tail_keys = {
        item.combination_stable_key
        for item in sorted(
            items,
            key=lambda item: (
                -fractions[item.combination_stable_key],
                item.combination_stable_key,
            ),
        )[:tail_count]
    }
    return [
        EntryDetailAllocationResult(
            combination_stable_key=item.combination_stable_key,
            historical_business_new_students=item.historical_business_new_students,
            manual_reservation_ratio_ppm=item.manual_reservation_ratio_ppm,
            allocation_source_type=source_type,
            theoretical_value=theoretical[item.combination_stable_key],
            floor_value=floors[item.combination_stable_key],
            fraction_value=fractions[item.combination_stable_key],
            tail_assigned=item.combination_stable_key in tail_keys,
            entry_initial_value=floors[item.combination_stable_key]
            + int(item.combination_stable_key in tail_keys),
        )
        for item in items
    ]
