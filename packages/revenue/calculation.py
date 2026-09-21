from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

REVENUE_RULE_VERSION = "phase1-revenue-v1"
MONEY_QUANTUM = Decimal("0.000001")
PPM_BASE = Decimal(1_000_000)
COMPOSITE_DISCOUNT_TOLERANCE_PPM = Decimal(500)


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ActualNetRevenue:
    """An already net-of-tax amount; it must never pass through the budget tax factor."""

    recognized_revenue_yuan: Decimal


def actual_net_revenue(value: ActualNetRevenue) -> Decimal:
    if value.recognized_revenue_yuan < 0:
        raise RevenueInvariantError("NEGATIVE_ACTUAL_NET_REVENUE", "实际不含税收入不得为负数")
    return money(value.recognized_revenue_yuan)


@dataclass(frozen=True)
class RevenueLineInput:
    result_id: str
    combination_stable_key: str
    fiscal_season: int
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    integer_total_students: int | None
    list_price_yuan: Decimal
    detail_discount_rate_ppm: int
    current_season_recognition_rate_ppm: int
    opening_carryover_revenue_yuan: Decimal


@dataclass(frozen=True)
class CompositeDiscountTargetInput:
    target_id: str
    scope_level: str
    track: str | None
    period_type: str
    fiscal_season: int | None
    target_rate_ppm: int


@dataclass(frozen=True)
class RevenueLineResult:
    source: RevenueLineInput
    calculation_status: str
    discounted_tuition_yuan: Decimal | None
    list_price_revenue_yuan: Decimal | None
    business_quarter_revenue_yuan: Decimal | None
    net_of_tax_base_yuan: Decimal | None
    current_recognized_revenue_yuan: Decimal | None
    incoming_carryover_revenue_yuan: Decimal | None
    cross_season_revenue_yuan: Decimal | None
    quarter_recognized_revenue_yuan: Decimal | None
    next_fiscal_year_opening_yuan: Decimal | None


@dataclass(frozen=True)
class RevenueAggregate:
    scope_level: str
    track: str | None
    period_type: str
    fiscal_season: int | None
    list_price_revenue_yuan: Decimal
    discounted_revenue_yuan: Decimal
    business_revenue_yuan: Decimal
    recognized_revenue_yuan: Decimal
    composite_discount_rate_ppm: Decimal | None
    composite_discount_target_id: str | None
    target_rate_ppm: int | None
    variance_ppm: Decimal | None
    target_status: str


@dataclass(frozen=True)
class RevenueValidation:
    fiscal_season: int | None
    combination_stable_key: str
    code: str
    message: str
    detail: dict[str, object]


@dataclass(frozen=True)
class RevenueCalculation:
    rows: tuple[RevenueLineResult, ...]
    aggregates: tuple[RevenueAggregate, ...]
    validations: tuple[RevenueValidation, ...]

    @property
    def publishable(self) -> bool:
        return not self.validations


class RevenueInvariantError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def calculate_revenue(
    *,
    fiscal_year: int,
    budget_tax_conversion_factor: Decimal,
    lines: list[RevenueLineInput],
    composite_targets: list[CompositeDiscountTargetInput],
) -> RevenueCalculation:
    if budget_tax_conversion_factor <= 0:
        raise RevenueInvariantError("INVALID_BUDGET_TAX_FACTOR", "预算含税转换系数必须为正数")
    expected_seasons = {fiscal_year * 10 + index for index in range(1, 5)}
    target_by_key = _validate_targets(composite_targets, expected_seasons)
    _validate_lines(lines, expected_seasons)

    validations: list[RevenueValidation] = []
    rows: list[RevenueLineResult] = []
    prior_cross_by_key: dict[str, Decimal | None] = {}

    with localcontext() as context:
        context.prec = 38
        for line in sorted(lines, key=_line_sort_key):
            if line.integer_total_students is None:
                rows.append(_unavailable_line(line))
                prior_cross_by_key[line.combination_stable_key] = None
                validations.append(
                    RevenueValidation(
                        line.fiscal_season,
                        line.combination_stable_key,
                        "HEADCOUNT_UNAVAILABLE_FOR_REVENUE",
                        "人数结果未完成，不能计算明细收入",
                        {"headcount_result_id": line.result_id},
                    )
                )
                continue
            discounted_tuition = money(
                line.list_price_yuan * Decimal(line.detail_discount_rate_ppm) / PPM_BASE
            )
            list_price_revenue = money(Decimal(line.integer_total_students) * line.list_price_yuan)
            business_revenue = money(Decimal(line.integer_total_students) * discounted_tuition)
            net_of_tax_base = money(business_revenue / budget_tax_conversion_factor)
            current_revenue = money(
                net_of_tax_base * Decimal(line.current_season_recognition_rate_ppm) / PPM_BASE
            )
            cross_revenue = money(
                net_of_tax_base * Decimal(1_000_000 - line.current_season_recognition_rate_ppm) / PPM_BASE
            )
            incoming: Decimal | None
            if line.fiscal_season % 10 == 1:
                incoming = money(line.opening_carryover_revenue_yuan)
            else:
                incoming = prior_cross_by_key.get(line.combination_stable_key)
            if incoming is None:
                rows.append(_unavailable_line(line, discounted_tuition, list_price_revenue, business_revenue))
                prior_cross_by_key[line.combination_stable_key] = cross_revenue
                validations.append(
                    RevenueValidation(
                        line.fiscal_season,
                        line.combination_stable_key,
                        "PRIOR_SEASON_CARRYOVER_UNAVAILABLE",
                        "上一财季跨季结转不可用，当季收入不能确认",
                        {},
                    )
                )
                continue
            quarter_recognized = money(current_revenue + incoming)
            rows.append(
                RevenueLineResult(
                    source=line,
                    calculation_status="CALCULATED",
                    discounted_tuition_yuan=discounted_tuition,
                    list_price_revenue_yuan=list_price_revenue,
                    business_quarter_revenue_yuan=business_revenue,
                    net_of_tax_base_yuan=net_of_tax_base,
                    current_recognized_revenue_yuan=current_revenue,
                    incoming_carryover_revenue_yuan=incoming,
                    cross_season_revenue_yuan=cross_revenue,
                    quarter_recognized_revenue_yuan=quarter_recognized,
                    next_fiscal_year_opening_yuan=(cross_revenue if line.fiscal_season % 10 == 4 else None),
                )
            )
            prior_cross_by_key[line.combination_stable_key] = cross_revenue

    aggregates = _aggregate(rows, target_by_key, validations)
    return RevenueCalculation(tuple(rows), tuple(aggregates), tuple(validations))


def _validate_lines(lines: list[RevenueLineInput], expected_seasons: set[int]) -> None:
    if not lines:
        raise RevenueInvariantError("REVENUE_LINES_EMPTY", "收入计算明细不得为空")
    seen: set[tuple[str, int]] = set()
    seasons_by_combination: dict[str, set[int]] = {}
    for line in lines:
        key = line.combination_stable_key, line.fiscal_season
        if key in seen:
            raise RevenueInvariantError("DUPLICATE_REVENUE_LINE", "同一组合同一财季只能有一条收入明细")
        seen.add(key)
        seasons_by_combination.setdefault(line.combination_stable_key, set()).add(line.fiscal_season)
        if line.fiscal_season not in expected_seasons:
            raise RevenueInvariantError("INVALID_FISCAL_SEASON", "收入明细财季不属于当前财年")
        if line.integer_total_students is not None and line.integer_total_students < 0:
            raise RevenueInvariantError("NEGATIVE_HEADCOUNT", "收入明细人次不得为负数")
        if line.list_price_yuan < 0 or line.opening_carryover_revenue_yuan < 0:
            raise RevenueInvariantError("NEGATIVE_REVENUE_INPUT", "价格和期初结转不得为负数")
        if line.detail_discount_rate_ppm not in range(0, 1_000_001, 1_000):
            raise RevenueInvariantError("INVALID_DISCOUNT_STEP", "明细折扣率必须按 0.1 个百分点调整")
        if not 0 <= line.current_season_recognition_rate_ppm <= 1_000_000:
            raise RevenueInvariantError("INVALID_RECOGNITION_RATE", "当季结转比例必须在 0% 至 100% 之间")
    if any(seasons != expected_seasons for seasons in seasons_by_combination.values()):
        raise RevenueInvariantError("INCOMPLETE_REVENUE_SEASONS", "每个预算组合必须完整覆盖四个财季")


def _validate_targets(
    targets: list[CompositeDiscountTargetInput], expected_seasons: set[int]
) -> dict[tuple[str, str | None, str, int | None], CompositeDiscountTargetInput]:
    result: dict[tuple[str, str | None, str, int | None], CompositeDiscountTargetInput] = {}
    for target in targets:
        if target.scope_level not in {"DEPARTMENT", "TRACK"}:
            raise RevenueInvariantError("INVALID_DISCOUNT_TARGET_SCOPE", "综合折扣目标层级无效")
        if target.period_type not in {"ANNUAL", "SEASON"}:
            raise RevenueInvariantError("INVALID_DISCOUNT_TARGET_PERIOD", "综合折扣目标周期无效")
        if (target.scope_level == "DEPARTMENT") != (target.track is None):
            raise RevenueInvariantError(
                "INVALID_DISCOUNT_TARGET_TRACK", "部门目标不带赛道，赛道目标必须指定赛道"
            )
        if (target.period_type == "ANNUAL") != (target.fiscal_season is None):
            raise RevenueInvariantError(
                "INVALID_DISCOUNT_TARGET_SEASON", "年度目标不带财季，季度目标必须指定财季"
            )
        if target.fiscal_season is not None and target.fiscal_season not in expected_seasons:
            raise RevenueInvariantError("INVALID_DISCOUNT_TARGET_SEASON", "综合折扣目标财季不属于当前财年")
        if not 0 <= target.target_rate_ppm <= 1_000_000:
            raise RevenueInvariantError("INVALID_DISCOUNT_TARGET_RATE", "综合折扣目标必须在 0% 至 100% 之间")
        key = target.scope_level, target.track, target.period_type, target.fiscal_season
        if key in result:
            raise RevenueInvariantError("DUPLICATE_DISCOUNT_TARGET", "综合折扣目标键不得重复")
        result[key] = target
    return result


def _unavailable_line(
    line: RevenueLineInput,
    discounted_tuition: Decimal | None = None,
    list_price_revenue: Decimal | None = None,
    business_revenue: Decimal | None = None,
) -> RevenueLineResult:
    return RevenueLineResult(
        source=line,
        calculation_status="BLOCKED",
        discounted_tuition_yuan=discounted_tuition,
        list_price_revenue_yuan=list_price_revenue,
        business_quarter_revenue_yuan=business_revenue,
        net_of_tax_base_yuan=None,
        current_recognized_revenue_yuan=None,
        incoming_carryover_revenue_yuan=None,
        cross_season_revenue_yuan=None,
        quarter_recognized_revenue_yuan=None,
        next_fiscal_year_opening_yuan=None,
    )


def _line_sort_key(item: RevenueLineInput) -> tuple[int, str, str, str, str, str, str, str]:
    return (
        item.fiscal_season,
        item.track,
        item.product,
        item.grade,
        item.subject,
        item.campus,
        item.entry_attribute,
        item.combination_stable_key,
    )


def _aggregate(
    rows: list[RevenueLineResult],
    targets: dict[tuple[str, str | None, str, int | None], CompositeDiscountTargetInput],
    validations: list[RevenueValidation],
) -> list[RevenueAggregate]:
    tracks = sorted({row.source.track for row in rows})
    seasons = sorted({row.source.fiscal_season for row in rows})
    group_specs: list[tuple[str, str | None, str, int | None]] = []
    for scope_level, track in [("DEPARTMENT", None), *(("TRACK", item) for item in tracks)]:
        group_specs.append((scope_level, track, "ANNUAL", None))
        group_specs.extend((scope_level, track, "SEASON", season) for season in seasons)

    aggregates: list[RevenueAggregate] = []
    for scope_level, track, period_type, fiscal_season in group_specs:
        selected = [
            row
            for row in rows
            if (track is None or row.source.track == track)
            and (fiscal_season is None or row.source.fiscal_season == fiscal_season)
        ]
        complete = all(row.calculation_status == "CALCULATED" for row in selected)
        list_price_revenue = money(
            sum((row.list_price_revenue_yuan or Decimal(0) for row in selected), Decimal(0))
        )
        discounted_revenue = money(
            sum((row.business_quarter_revenue_yuan or Decimal(0) for row in selected), Decimal(0))
        )
        recognized_revenue = money(
            sum((row.quarter_recognized_revenue_yuan or Decimal(0) for row in selected), Decimal(0))
        )
        target = targets.get((scope_level, track, period_type, fiscal_season))
        if not complete or list_price_revenue == 0:
            composite_rate = None
            variance = None
            target_status = "N/A"
            if target is not None:
                code = (
                    "COMPOSITE_DISCOUNT_DENOMINATOR_ZERO"
                    if complete
                    else "COMPOSITE_DISCOUNT_INPUT_INCOMPLETE"
                )
                validations.append(
                    RevenueValidation(
                        fiscal_season,
                        "AGGREGATE",
                        code,
                        "综合折扣分母为零或明细不完整，目标不可判定",
                        {
                            "scope_level": scope_level,
                            "track": track or "",
                            "period_type": period_type,
                            "fiscal_season": fiscal_season or 0,
                        },
                    )
                )
        else:
            with localcontext() as context:
                context.prec = 38
                composite_rate = discounted_revenue * PPM_BASE / list_price_revenue
            variance = composite_rate - Decimal(target.target_rate_ppm) if target is not None else None
            target_status = (
                "PASS"
                if variance is not None and abs(variance) <= COMPOSITE_DISCOUNT_TOLERANCE_PPM
                else "OUT_OF_TOLERANCE"
                if variance is not None
                else "TARGET_MISSING"
            )
            if target_status == "OUT_OF_TOLERANCE":
                validations.append(
                    RevenueValidation(
                        fiscal_season,
                        "AGGREGATE",
                        "COMPOSITE_DISCOUNT_OUT_OF_TOLERANCE",
                        "综合折扣与目标的绝对差超过 0.05 个百分点",
                        {
                            "scope_level": scope_level,
                            "track": track or "",
                            "period_type": period_type,
                            "fiscal_season": fiscal_season or 0,
                            "variance_ppm": format(variance, "f"),
                        },
                    )
                )
        aggregates.append(
            RevenueAggregate(
                scope_level=scope_level,
                track=track,
                period_type=period_type,
                fiscal_season=fiscal_season,
                list_price_revenue_yuan=list_price_revenue,
                discounted_revenue_yuan=discounted_revenue,
                business_revenue_yuan=discounted_revenue,
                recognized_revenue_yuan=recognized_revenue,
                composite_discount_rate_ppm=composite_rate,
                composite_discount_target_id=target.target_id if target is not None else None,
                target_rate_ppm=target.target_rate_ppm if target is not None else None,
                variance_ppm=variance,
                target_status=target_status,
            )
        )
    return aggregates
