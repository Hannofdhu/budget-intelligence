from decimal import Decimal

import pytest

from packages.revenue.calculation import (
    ActualNetRevenue,
    CompositeDiscountTargetInput,
    RevenueInvariantError,
    RevenueLineInput,
    actual_net_revenue,
    calculate_revenue,
)


def _line(
    stable_key: str,
    season: int,
    *,
    students: int | None = 1,
    price: str = "1060",
    discount_ppm: int = 1_000_000,
    recognition_ppm: int = 750_000,
    opening: str = "0",
    track: str = "智慧空间",
) -> RevenueLineInput:
    return RevenueLineInput(
        result_id=f"{stable_key}-{season}",
        combination_stable_key=stable_key,
        fiscal_season=season,
        track=track,
        product="THINK",
        entry_attribute="ENTRY",
        grade="七年级",
        subject="数学",
        campus="中关村",
        integer_total_students=students,
        list_price_yuan=Decimal(price),
        detail_discount_rate_ppm=discount_ppm,
        current_season_recognition_rate_ppm=recognition_ppm,
        opening_carryover_revenue_yuan=Decimal(opening),
    )


def _targets(rate_ppm: int = 1_000_000) -> list[CompositeDiscountTargetInput]:
    targets = [CompositeDiscountTargetInput("dept-year", "DEPARTMENT", None, "ANNUAL", None, rate_ppm)]
    targets.extend(
        CompositeDiscountTargetInput(f"dept-{season}", "DEPARTMENT", None, "SEASON", season, rate_ppm)
        for season in range(20271, 20275)
    )
    targets.append(CompositeDiscountTargetInput("track-year", "TRACK", "智慧空间", "ANNUAL", None, rate_ppm))
    targets.extend(
        CompositeDiscountTargetInput(f"track-{season}", "TRACK", "智慧空间", "SEASON", season, rate_ppm)
        for season in range(20271, 20275)
    )
    return targets


def test_ab045_composite_discount_is_list_price_revenue_weighted() -> None:
    rows = [
        _line("a" * 64, season, students=1_000 if season == 20271 else 0, price="1", discount_ppm=500_000)
        for season in range(20271, 20275)
    ]
    rows.extend(
        _line("b" * 64, season, students=9_000 if season == 20271 else 0, price="1", discount_ppm=900_000)
        for season in range(20271, 20275)
    )
    result = calculate_revenue(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal("1.06"),
        lines=rows,
        composite_targets=_targets(860_000),
    )
    quarter = next(
        item for item in result.aggregates if item.scope_level == "DEPARTMENT" and item.fiscal_season == 20271
    )
    assert quarter.composite_discount_rate_ppm == Decimal("860000")
    assert quarter.target_status == "PASS"


def test_ab046_zero_denominator_is_not_zero_percent() -> None:
    result = calculate_revenue(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal("1.06"),
        lines=[_line("a" * 64, season, students=0) for season in range(20271, 20275)],
        composite_targets=_targets(),
    )
    assert {item.composite_discount_rate_ppm for item in result.aggregates} == {None}
    assert {item.target_status for item in result.aggregates} == {"N/A"}
    assert "COMPOSITE_DISCOUNT_DENOMINATOR_ZERO" in {item.code for item in result.validations}


def test_ab047_enforces_step_and_exact_tolerance() -> None:
    rows = [_line("a" * 64, season, discount_ppm=899_000) for season in range(20271, 20275)]
    result = calculate_revenue(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal("1"),
        lines=rows,
        composite_targets=_targets(899_500),
    )
    assert result.publishable is True
    assert {item.target_status for item in result.aggregates} == {"PASS"}
    with pytest.raises(RevenueInvariantError, match="0.1"):
        calculate_revenue(
            fiscal_year=2027,
            budget_tax_conversion_factor=Decimal("1"),
            lines=[_line("a" * 64, season, discount_ppm=899_500) for season in range(20271, 20275)],
            composite_targets=_targets(899_500),
        )


def test_ab049c_budget_revenue_divides_tax_factor_once_and_rolls_forward() -> None:
    result = calculate_revenue(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal("1.06"),
        lines=[_line("a" * 64, season) for season in range(20271, 20275)],
        composite_targets=_targets(),
    )
    q1, q2, q3, q4 = result.rows
    assert q1.business_quarter_revenue_yuan == Decimal("1060.000000")
    assert q1.net_of_tax_base_yuan == Decimal("1000.000000")
    assert q1.current_recognized_revenue_yuan == Decimal("750.000000")
    assert q1.cross_season_revenue_yuan == Decimal("250.000000")
    assert q1.quarter_recognized_revenue_yuan == Decimal("750.000000")
    assert q2.incoming_carryover_revenue_yuan == Decimal("250.000000")
    assert q2.quarter_recognized_revenue_yuan == Decimal("1000.000000")
    assert q4.next_fiscal_year_opening_yuan == Decimal("250.000000")
    annual = next(
        item
        for item in result.aggregates
        if item.scope_level == "DEPARTMENT" and item.period_type == "ANNUAL"
    )
    assert annual.recognized_revenue_yuan == Decimal("3750.000000")


def test_ab049d_actual_net_revenue_is_not_divided_again() -> None:
    assert actual_net_revenue(ActualNetRevenue(Decimal("6826.1234567"))) == Decimal("6826.123457")


def test_ab049e_opening_carryover_enters_q1_and_q4_cross_is_excluded_from_year() -> None:
    lines = [
        _line("a" * 64, season, opening="125" if season == 20271 else "0") for season in range(20271, 20275)
    ]
    result = calculate_revenue(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal("1.06"),
        lines=lines,
        composite_targets=_targets(),
    )
    assert result.rows[0].incoming_carryover_revenue_yuan == Decimal("125.000000")
    assert result.rows[0].quarter_recognized_revenue_yuan == Decimal("875.000000")
    annual = next(
        item
        for item in result.aggregates
        if item.scope_level == "DEPARTMENT" and item.period_type == "ANNUAL"
    )
    assert annual.recognized_revenue_yuan == Decimal("3875.000000")
    assert result.rows[-1].next_fiscal_year_opening_yuan == Decimal("250.000000")


def test_blocked_headcount_does_not_default_to_zero_revenue() -> None:
    result = calculate_revenue(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal("1.06"),
        lines=[
            _line("a" * 64, 20271, students=None),
            *[_line("a" * 64, season) for season in range(20272, 20275)],
        ],
        composite_targets=_targets(),
    )
    assert result.rows[0].calculation_status == "BLOCKED"
    assert result.rows[0].business_quarter_revenue_yuan is None
    assert result.publishable is False
