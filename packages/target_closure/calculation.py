from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

TARGET_CLOSURE_RULE_VERSION = "phase1-target-closure-v1"
TARGET_TOLERANCE_YUAN = Decimal("10000.000000")
MONEY_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class TargetRevenueInput:
    target_id: str
    scope_level: str
    track: str | None
    target_revenue_yuan: Decimal
    calculated_revenue_yuan: Decimal


@dataclass(frozen=True, slots=True)
class TargetClosureItem:
    source: TargetRevenueInput
    target_revenue_yuan: Decimal
    calculated_revenue_yuan: Decimal
    variance_yuan: Decimal
    tolerance_yuan: Decimal
    check_status: str


@dataclass(frozen=True, slots=True)
class TargetClosureCalculation:
    closure_status: str
    items: tuple[TargetClosureItem, ...]

    @property
    def targets_within_tolerance(self) -> bool:
        return self.closure_status == "WITHIN_TOLERANCE"


class TargetClosureInvariantError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def calculate_target_closure(
    inputs: list[TargetRevenueInput],
) -> TargetClosureCalculation:
    _validate_inputs(inputs)
    items: list[TargetClosureItem] = []
    with localcontext() as context:
        context.prec = 38
        for source in sorted(inputs, key=_sort_key):
            target = source.target_revenue_yuan.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            calculated = source.calculated_revenue_yuan.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            variance = (calculated - target).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            if variance < -TARGET_TOLERANCE_YUAN:
                status = "GAP"
            elif variance > TARGET_TOLERANCE_YUAN:
                status = "OVER_TARGET"
            else:
                status = "WITHIN_TOLERANCE"
            items.append(
                TargetClosureItem(
                    source=source,
                    target_revenue_yuan=target,
                    calculated_revenue_yuan=calculated,
                    variance_yuan=variance,
                    tolerance_yuan=TARGET_TOLERANCE_YUAN,
                    check_status=status,
                )
            )
    statuses = {item.check_status for item in items}
    if statuses == {"WITHIN_TOLERANCE"}:
        closure_status = "WITHIN_TOLERANCE"
    elif "GAP" in statuses and "OVER_TARGET" in statuses:
        closure_status = "MIXED_PENDING_REBALANCE"
    elif "GAP" in statuses:
        closure_status = "GAP_PENDING_REBALANCE"
    else:
        closure_status = "OVER_TARGET_PENDING_REBALANCE"
    return TargetClosureCalculation(closure_status=closure_status, items=tuple(items))


def _validate_inputs(inputs: list[TargetRevenueInput]) -> None:
    if not inputs:
        raise TargetClosureInvariantError("TARGET_SET_EMPTY", "学校版目标不得为空")
    keys = [(item.scope_level, item.track) for item in inputs]
    if len(keys) != len(set(keys)):
        raise TargetClosureInvariantError("TARGET_SCOPE_DUPLICATED", "部门或赛道目标不得重复")
    department_rows = [item for item in inputs if item.scope_level == "DEPARTMENT" and item.track is None]
    track_rows = [item for item in inputs if item.scope_level == "TRACK" and item.track]
    if len(department_rows) != 1 or len(track_rows) != len(inputs) - 1 or not track_rows:
        raise TargetClosureInvariantError(
            "TARGET_SCOPE_INVALID", "目标必须包含一个部门合计以及至少一个具体赛道"
        )
    if any(item.target_revenue_yuan < 0 or item.calculated_revenue_yuan < 0 for item in inputs):
        raise TargetClosureInvariantError("TARGET_REVENUE_NEGATIVE", "目标和计算收入不得为负数")


def _sort_key(item: TargetRevenueInput) -> tuple[int, str]:
    return (0 if item.scope_level == "DEPARTMENT" else 1, item.track or "")
