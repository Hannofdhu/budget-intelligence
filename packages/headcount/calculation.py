from collections import defaultdict
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal

HEADCOUNT_RULE_VERSION = "phase1-headcount-v1"
OPENING_HEADCOUNT_RULE_VERSION = "phase1-headcount-opening-v2"
PPM = 1_000_000
MAX_REVERSE_SHARE_PPM = 999_900

RateType = Literal["RENEWAL_RATE", "NEW_STUDENT_SHARE"]
NewStudentSource = Literal["ENTRY_ALLOCATION", "LOCKED_MANUAL_PLAN", "RATE_INFERRED"]
EligibilityStatus = Literal["ENTRY_ABSOLUTE", "APPLICABLE_LOCKED", "NOT_APPLICABLE", "REQUIRED_MISSING"]


class HeadcountInvariantError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, order=True)
class CombinationInput:
    stable_id: str
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    entry_initial_value: int = 0


@dataclass(frozen=True)
class RateInput:
    rate_id: str
    rate_type: RateType
    fiscal_season: int
    track: str
    product: str
    grade: str
    campus: str
    value_ppm: int


@dataclass(frozen=True)
class ManualPlanInput:
    plan_id: str
    combination_stable_id: str
    fiscal_season: int
    value: int
    locked: bool


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    fiscal_season: int
    combination_stable_id: str | None
    detail: dict[str, object]


@dataclass(frozen=True)
class RoundingTrace:
    student_type: Literal["OLD", "NEW"]
    exact_theory: Fraction
    floor_value: int
    exact_remainder: Fraction
    fixed_input: bool
    remainder_rank: int | None
    tail_assigned: bool
    group_target: int
    fixed_total: int
    adjustable_target: int


@dataclass(frozen=True)
class SeasonHeadcount:
    combination: CombinationInput
    fiscal_season: int
    season_index: int
    theoretical_old_students: Fraction | None
    theoretical_new_students: Fraction | None
    integer_old_students: int | None
    integer_new_students: int | None
    integer_total_students: int | None
    new_student_source: NewStudentSource | None
    planned_eligibility_status: EligibilityStatus
    renewal_rate_id: str | None
    renewal_rate_ppm: int | None
    new_student_share_rate_id: str | None
    new_student_share_input_ppm: int | None
    planned_input_id: str | None
    realized_retention_rate_ppm: int | None
    realized_new_student_share_ppm: int | None
    old_rounding_trace: RoundingTrace | None
    new_rounding_trace: RoundingTrace | None


@dataclass(frozen=True)
class HeadcountCalculation:
    fiscal_year: int
    rule_version: str
    publishable: bool
    rows: tuple[SeasonHeadcount, ...]
    validations: tuple[ValidationIssue, ...]


def calculate_headcounts(
    *,
    fiscal_year: int,
    combinations: list[CombinationInput],
    rates: list[RateInput],
    plans: list[ManualPlanInput],
    opening_old_students: dict[str, Fraction] | None = None,
) -> HeadcountCalculation:
    if fiscal_year <= 0:
        raise HeadcountInvariantError("FISCAL_YEAR_INVALID", "财年必须为正整数")
    ordered_combinations = sorted(combinations, key=lambda item: item.stable_id)
    _validate_combinations(ordered_combinations)
    if fiscal_year >= 2028 and opening_old_students is None:
        raise HeadcountInvariantError("OPENING_VERSION_REQUIRED", "FY28 起不得默认首季老生为零")
    if opening_old_students is not None:
        if fiscal_year < 2028:
            raise HeadcountInvariantError("FY27_FINALIZED", "FY27 不使用新的跨年期初老生规则")
        if set(opening_old_students) != {row.stable_id for row in ordered_combinations}:
            raise HeadcountInvariantError("OPENING_COVERAGE_MISMATCH", "期初老生必须完整覆盖预算组合")
        if any(not isinstance(value, Fraction) or value < 0 for value in opening_old_students.values()):
            raise HeadcountInvariantError("OPENING_VALUE_INVALID", "期初老生理论值必须是非负精确分数")
    rate_by_key = _index_rates(rates, fiscal_year=fiscal_year)
    plan_by_key = _index_plans(plans, fiscal_year=fiscal_year)
    validations: list[ValidationIssue] = []
    previous_by_id: dict[str, SeasonHeadcount] = {}
    all_rows: list[SeasonHeadcount] = []

    for season_index in range(1, 5):
        fiscal_season = fiscal_year * 10 + season_index
        season_rows = [
            _build_theory_row(
                combination=combination,
                fiscal_season=fiscal_season,
                season_index=season_index,
                previous=previous_by_id.get(combination.stable_id),
                rate_by_key=rate_by_key,
                plan=plan_by_key.get((fiscal_season, combination.stable_id)),
                validations=validations,
                opening_old=(
                    opening_old_students[combination.stable_id]
                    if opening_old_students is not None
                    else Fraction(0)
                ),
            )
            for combination in ordered_combinations
        ]
        season_rows = _round_season(season_rows)
        season_rows = [
            _add_realized_rates(row, previous_by_id.get(row.combination.stable_id)) for row in season_rows
        ]
        all_rows.extend(season_rows)
        previous_by_id = {row.combination.stable_id: row for row in season_rows}

    return HeadcountCalculation(
        fiscal_year=fiscal_year,
        rule_version=OPENING_HEADCOUNT_RULE_VERSION
        if opening_old_students is not None
        else HEADCOUNT_RULE_VERSION,
        publishable=not validations,
        rows=tuple(all_rows),
        validations=tuple(
            sorted(
                validations,
                key=lambda item: (
                    item.fiscal_season,
                    item.combination_stable_id or "",
                    item.code,
                ),
            )
        ),
    )


def fraction_string(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def round_half_up(value: Fraction) -> int:
    if value < 0:
        raise HeadcountInvariantError("NEGATIVE_THEORY", "理论人数不得为负数")
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def ratio_ppm(numerator: int, denominator: int) -> int | None:
    if denominator == 0:
        return None
    return round_half_up(Fraction(numerator * PPM, denominator))


def _validate_combinations(combinations: list[CombinationInput]) -> None:
    if not combinations:
        raise HeadcountInvariantError("COMBINATIONS_EMPTY", "至少需要一个已启用预算组合")
    stable_ids = [item.stable_id for item in combinations]
    if len(stable_ids) != len(set(stable_ids)):
        raise HeadcountInvariantError("COMBINATION_DUPLICATED", "预算组合稳定 ID 不得重复")
    for item in combinations:
        if item.entry_attribute not in {"ENTRY", "NON_ENTRY"}:
            raise HeadcountInvariantError("ENTRY_ATTRIBUTE_INVALID", "入口属性必须为 ENTRY 或 NON_ENTRY")
        if item.entry_initial_value < 0:
            raise HeadcountInvariantError("ENTRY_VALUE_NEGATIVE", "入口初始人数不得为负数")


def _index_rates(rates: list[RateInput], *, fiscal_year: int) -> dict[tuple[object, ...], RateInput]:
    result: dict[tuple[object, ...], RateInput] = {}
    valid_seasons = {fiscal_year * 10 + index for index in range(1, 5)}
    for item in rates:
        if item.fiscal_season not in valid_seasons:
            continue
        maximum = MAX_REVERSE_SHARE_PPM if item.rate_type == "NEW_STUDENT_SHARE" else PPM
        if not 0 <= item.value_ppm <= maximum:
            raise HeadcountInvariantError("RATE_VALUE_INVALID", "比例参数超出人数推演允许范围")
        key = (
            item.rate_type,
            item.fiscal_season,
            item.track,
            item.product,
            item.grade,
            item.campus,
        )
        if key in result:
            raise HeadcountInvariantError("RATE_DUPLICATED", "人数推演比例参数精确键不得重复")
        result[key] = item
    return result


def _index_plans(plans: list[ManualPlanInput], *, fiscal_year: int) -> dict[tuple[int, str], ManualPlanInput]:
    result: dict[tuple[int, str], ManualPlanInput] = {}
    valid_seasons = {fiscal_year * 10 + index for index in range(1, 5)}
    for item in plans:
        if item.fiscal_season not in valid_seasons:
            continue
        if item.value < 0:
            raise HeadcountInvariantError("PLANNED_VALUE_NEGATIVE", "计划新生人次不得为负数")
        key = (item.fiscal_season, item.combination_stable_id)
        if key in result:
            raise HeadcountInvariantError("PLANNED_INPUT_DUPLICATED", "计划新生人次精确键不得重复")
        result[key] = item
    return result


def _rate_key(rate_type: RateType, season: int, item: CombinationInput) -> tuple[object, ...]:
    return rate_type, season, item.track, item.product, item.grade, item.campus


def _build_theory_row(
    *,
    combination: CombinationInput,
    fiscal_season: int,
    season_index: int,
    previous: SeasonHeadcount | None,
    rate_by_key: dict[tuple[object, ...], RateInput],
    plan: ManualPlanInput | None,
    validations: list[ValidationIssue],
    opening_old: Fraction = Fraction(0),
) -> SeasonHeadcount:
    old_theory: Fraction | None
    renewal: RateInput | None = None
    if season_index == 1:
        old_theory = opening_old
    elif previous is None or previous.integer_total_students is None:
        old_theory = None
        validations.append(
            _issue(
                "UPSTREAM_HEADCOUNT_UNAVAILABLE",
                "上一季整数总人次不可用，无法推演本季老生",
                fiscal_season,
                combination,
            )
        )
    else:
        renewal = rate_by_key.get(_rate_key("RENEWAL_RATE", fiscal_season, combination))
        if renewal is None:
            old_theory = None
            validations.append(
                _issue(
                    "RENEWAL_RATE_MISSING",
                    "缺少与完整人数明细精确匹配的续班率参数",
                    fiscal_season,
                    combination,
                )
            )
        else:
            old_theory = Fraction(previous.integer_total_students * renewal.value_ppm, PPM)

    source: NewStudentSource | None = None
    eligibility: EligibilityStatus = "NOT_APPLICABLE"
    new_theory: Fraction | None = None
    share: RateInput | None = None
    planned_input_id: str | None = None
    if season_index == 1 and combination.entry_attribute == "ENTRY" and combination.entry_initial_value > 0:
        source = "ENTRY_ALLOCATION"
        eligibility = "ENTRY_ABSOLUTE"
        new_theory = Fraction(combination.entry_initial_value)
    elif old_theory is not None and old_theory > 0:
        source = "RATE_INFERRED"
        eligibility = "NOT_APPLICABLE"
        share = rate_by_key.get(_rate_key("NEW_STUDENT_SHARE", fiscal_season, combination))
        if share is None:
            new_theory = None
            validations.append(
                _issue(
                    "NEW_STUDENT_SHARE_MISSING",
                    "缺少与完整人数明细精确匹配的新生占比参数",
                    fiscal_season,
                    combination,
                )
            )
        else:
            new_theory = old_theory * share.value_ppm / (PPM - share.value_ppm)
    elif old_theory is not None:
        source = "LOCKED_MANUAL_PLAN"
        planned_input_id = plan.plan_id if plan is not None else None
        if plan is None or not plan.locked:
            eligibility = "REQUIRED_MISSING"
            validations.append(
                _issue(
                    "LOCKED_PLANNED_NEW_STUDENTS_REQUIRED",
                    "老生理论人次为 0，必须填写并锁定计划新生人次（含显式 0）",
                    fiscal_season,
                    combination,
                    handling_status="EMPTY" if plan is None else "FILLED_PENDING_LOCK",
                )
            )
        else:
            eligibility = "APPLICABLE_LOCKED"
            new_theory = Fraction(plan.value)
    else:
        eligibility = "REQUIRED_MISSING"

    return SeasonHeadcount(
        combination=combination,
        fiscal_season=fiscal_season,
        season_index=season_index,
        theoretical_old_students=old_theory,
        theoretical_new_students=new_theory,
        integer_old_students=None,
        integer_new_students=None,
        integer_total_students=None,
        new_student_source=source,
        planned_eligibility_status=eligibility,
        renewal_rate_id=renewal.rate_id if renewal else None,
        renewal_rate_ppm=renewal.value_ppm if renewal else None,
        new_student_share_rate_id=share.rate_id if share else None,
        new_student_share_input_ppm=share.value_ppm if share else None,
        planned_input_id=planned_input_id,
        realized_retention_rate_ppm=None,
        realized_new_student_share_ppm=None,
        old_rounding_trace=None,
        new_rounding_trace=None,
    )


def _issue(
    code: str,
    message: str,
    fiscal_season: int,
    combination: CombinationInput,
    **detail: object,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        fiscal_season=fiscal_season,
        combination_stable_id=combination.stable_id,
        detail={
            "track": combination.track,
            "product": combination.product,
            "entry_attribute": combination.entry_attribute,
            "grade": combination.grade,
            "subject": combination.subject,
            "campus": combination.campus,
            **detail,
        },
    )


def _round_season(rows: list[SeasonHeadcount]) -> list[SeasonHeadcount]:
    grouped: dict[tuple[str, str], list[SeasonHeadcount]] = defaultdict(list)
    for row in rows:
        grouped[(row.combination.track, row.combination.campus)].append(row)
    rounded: list[SeasonHeadcount] = []
    for key in sorted(grouped):
        group = sorted(grouped[key], key=lambda item: item.combination.stable_id)
        old_values = {row.combination.stable_id: row.theoretical_old_students for row in group}
        old_result = _largest_remainder(old_values, fixed_ids=set(), student_type="OLD")
        fixed_ids = {
            row.combination.stable_id
            for row in group
            if row.new_student_source in {"ENTRY_ALLOCATION", "LOCKED_MANUAL_PLAN"}
            and row.theoretical_new_students is not None
        }
        new_values = {row.combination.stable_id: row.theoretical_new_students for row in group}
        new_result = _largest_remainder(new_values, fixed_ids=fixed_ids, student_type="NEW")
        for row in group:
            stable_id = row.combination.stable_id
            old_integer, old_trace = old_result.get(stable_id, (None, None))
            new_integer, new_trace = new_result.get(stable_id, (None, None))
            total = old_integer + new_integer if old_integer is not None and new_integer is not None else None
            rounded.append(
                replace(
                    row,
                    integer_old_students=old_integer,
                    integer_new_students=new_integer,
                    integer_total_students=total,
                    old_rounding_trace=old_trace,
                    new_rounding_trace=new_trace,
                )
            )
    return sorted(rounded, key=lambda item: item.combination.stable_id)


def _largest_remainder(
    values: dict[str, Fraction | None],
    *,
    fixed_ids: set[str],
    student_type: Literal["OLD", "NEW"],
) -> dict[str, tuple[int | None, RoundingTrace | None]]:
    if any(value is None for value in values.values()):
        return {stable_id: (None, None) for stable_id in values}
    exact_values = {stable_id: value for stable_id, value in values.items() if value is not None}
    group_target = round_half_up(sum(exact_values.values(), start=Fraction(0)))
    fixed_total = sum(int(exact_values[stable_id]) for stable_id in fixed_ids)
    adjustable_ids = sorted(set(exact_values) - fixed_ids)
    adjustable_target = group_target - fixed_total
    if adjustable_target < 0:
        raise HeadcountInvariantError("ROUNDING_FIXED_TOTAL_EXCEEDS_TARGET", "定量人数超过分组理论整数总量")
    floors = {
        stable_id: exact_values[stable_id].numerator // exact_values[stable_id].denominator
        for stable_id in adjustable_ids
    }
    tail = adjustable_target - sum(floors.values())
    if tail < 0 or tail > len(adjustable_ids):
        raise HeadcountInvariantError("ROUNDING_TAIL_INVALID", "最大余数尾差超出可调明细数量")
    ranked = sorted(
        adjustable_ids,
        key=lambda stable_id: (-(exact_values[stable_id] - floors[stable_id]), stable_id),
    )
    rank_by_id = {stable_id: rank for rank, stable_id in enumerate(ranked, start=1)}
    assigned = set(ranked[:tail])
    result: dict[str, tuple[int | None, RoundingTrace | None]] = {}
    for stable_id in sorted(exact_values):
        value = exact_values[stable_id]
        floor_value = value.numerator // value.denominator
        fixed = stable_id in fixed_ids
        integer_value = int(value) if fixed else floors[stable_id] + int(stable_id in assigned)
        result[stable_id] = (
            integer_value,
            RoundingTrace(
                student_type=student_type,
                exact_theory=value,
                floor_value=floor_value,
                exact_remainder=value - floor_value,
                fixed_input=fixed,
                remainder_rank=None if fixed else rank_by_id[stable_id],
                tail_assigned=stable_id in assigned,
                group_target=group_target,
                fixed_total=fixed_total,
                adjustable_target=adjustable_target,
            ),
        )
    if sum(item[0] or 0 for item in result.values()) != group_target:
        raise HeadcountInvariantError("ROUNDING_SUM_MISMATCH", "最大余数结果未保持分组整数总量")
    return result


def _add_realized_rates(row: SeasonHeadcount, previous: SeasonHeadcount | None) -> SeasonHeadcount:
    realized_retention = None
    if (
        previous is not None
        and previous.integer_total_students is not None
        and row.integer_old_students is not None
    ):
        realized_retention = ratio_ppm(row.integer_old_students, previous.integer_total_students)
    realized_share = None
    if row.integer_new_students is not None and row.integer_total_students is not None:
        realized_share = ratio_ppm(row.integer_new_students, row.integer_total_students)
    return replace(
        row,
        realized_retention_rate_ppm=realized_retention,
        realized_new_student_share_ppm=realized_share,
    )
