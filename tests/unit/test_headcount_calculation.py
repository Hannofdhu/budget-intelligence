from fractions import Fraction

from packages.headcount.calculation import (
    CombinationInput,
    ManualPlanInput,
    RateInput,
    calculate_headcounts,
)


def _combination(stable_id: str, *, entry: bool, entry_value: int = 0) -> CombinationInput:
    return CombinationInput(
        stable_id=stable_id,
        track="智慧伴学",
        product="普通",
        entry_attribute="ENTRY" if entry else "NON_ENTRY",
        grade="七年级",
        subject="数学",
        campus="中关村",
        entry_initial_value=entry_value,
    )


def _rates(stable_combinations: list[CombinationInput]) -> list[RateInput]:
    rates: list[RateInput] = []
    for season in (20272, 20273, 20274):
        seen: set[tuple[str, str, str, str]] = set()
        for item in stable_combinations:
            key = item.track, item.product, item.grade, item.campus
            if key in seen:
                continue
            seen.add(key)
            rates.extend(
                [
                    RateInput(
                        f"renew-{season}-{item.stable_id}",
                        "RENEWAL_RATE",
                        season,
                        item.track,
                        item.product,
                        item.grade,
                        item.campus,
                        800_000,
                    ),
                    RateInput(
                        f"share-{season}-{item.stable_id}",
                        "NEW_STUDENT_SHARE",
                        season,
                        item.track,
                        item.product,
                        item.grade,
                        item.campus,
                        200_000,
                    ),
                ]
            )
    return rates


def test_four_seasons_use_previous_integer_total_and_reverse_share() -> None:
    item = _combination("a" * 64, entry=True, entry_value=100)
    result = calculate_headcounts(
        fiscal_year=2027,
        combinations=[item],
        rates=_rates([item]),
        plans=[],
    )
    assert result.publishable is True
    first, second, third, fourth = result.rows
    assert (first.integer_old_students, first.integer_new_students, first.integer_total_students) == (
        0,
        100,
        100,
    )
    assert second.theoretical_old_students == Fraction(80)
    assert second.theoretical_new_students == Fraction(20)
    assert second.integer_total_students == 100
    assert third.integer_total_students == 100
    assert fourth.integer_total_students == 100
    assert second.realized_retention_rate_ppm == 800_000
    assert second.realized_new_student_share_ppm == 200_000


def test_old_and_new_largest_remainder_are_grouped_separately_with_stable_tie_break() -> None:
    first = _combination("a" * 64, entry=True, entry_value=1)
    second = _combination("b" * 64, entry=True, entry_value=1)
    rates = [
        RateInput("renew", "RENEWAL_RATE", 20272, "智慧伴学", "普通", "七年级", "中关村", 500_000),
        RateInput("share", "NEW_STUDENT_SHARE", 20272, "智慧伴学", "普通", "七年级", "中关村", 500_000),
    ]
    plans = [
        ManualPlanInput(f"p-{season}-{item.stable_id}", item.stable_id, season, 0, True)
        for season in (20273, 20274)
        for item in (first, second)
    ]
    result = calculate_headcounts(
        fiscal_year=2027,
        combinations=[second, first],
        rates=rates,
        plans=plans,
    )
    second_season = [row for row in result.rows if row.fiscal_season == 20272]
    by_id = {row.combination.stable_id: row for row in second_season}
    assert by_id[first.stable_id].theoretical_old_students == Fraction(1, 2)
    assert by_id[first.stable_id].integer_old_students == 1
    assert by_id[second.stable_id].integer_old_students == 0
    assert by_id[first.stable_id].integer_new_students == 1
    assert by_id[second.stable_id].integer_new_students == 0
    first_trace = by_id[first.stable_id].old_rounding_trace
    assert first_trace is not None
    assert first_trace.tail_assigned is True


def test_locked_manual_count_is_fixed_before_reverse_inferred_tail() -> None:
    manual = CombinationInput(
        stable_id="a" * 64,
        track="智慧伴学",
        product="人工计划",
        entry_attribute="NON_ENTRY",
        grade="七年级",
        subject="数学",
        campus="中关村",
    )
    inferred_first = CombinationInput(
        stable_id="b" * 64,
        track="智慧伴学",
        product="白金",
        entry_attribute="ENTRY",
        grade="七年级",
        subject="数学",
        campus="中关村",
        entry_initial_value=1,
    )
    inferred_second = CombinationInput(
        **{
            **inferred_first.__dict__,
            "stable_id": "c" * 64,
            "subject": "英语",
        }
    )
    rates = [
        RateInput(
            "manual-renew-20272",
            "RENEWAL_RATE",
            20272,
            manual.track,
            manual.product,
            manual.grade,
            manual.campus,
            0,
        ),
        RateInput(
            "inferred-renew-20272",
            "RENEWAL_RATE",
            20272,
            inferred_first.track,
            inferred_first.product,
            inferred_first.grade,
            inferred_first.campus,
            500_000,
        ),
        RateInput(
            "inferred-share-20272",
            "NEW_STUDENT_SHARE",
            20272,
            inferred_first.track,
            inferred_first.product,
            inferred_first.grade,
            inferred_first.campus,
            500_000,
        ),
    ]
    plans = [
        ManualPlanInput("manual-first", manual.stable_id, 20271, 0, True),
        ManualPlanInput("manual-second", manual.stable_id, 20272, 13, True),
    ]
    result = calculate_headcounts(
        fiscal_year=2027,
        combinations=[manual, inferred_first, inferred_second],
        rates=rates,
        plans=plans,
    )
    second_rows = {row.combination.stable_id: row for row in result.rows if row.fiscal_season == 20272}
    fixed = second_rows[manual.stable_id]
    assert fixed.integer_new_students == 13
    assert fixed.new_rounding_trace is not None
    assert fixed.new_rounding_trace.fixed_input is True
    assert fixed.new_rounding_trace.remainder_rank is None
    assert fixed.new_rounding_trace.fixed_total == 13
    assert fixed.new_rounding_trace.group_target == 14
    assert second_rows[inferred_first.stable_id].integer_new_students == 1
    assert second_rows[inferred_second.stable_id].integer_new_students == 0


def test_zero_old_requires_explicit_locked_plan_and_does_not_default_to_zero() -> None:
    item = _combination("a" * 64, entry=False)
    result = calculate_headcounts(fiscal_year=2027, combinations=[item], rates=[], plans=[])
    first = result.rows[0]
    assert result.publishable is False
    assert first.new_student_source == "LOCKED_MANUAL_PLAN"
    assert first.theoretical_new_students is None
    assert first.integer_new_students is None
    assert result.validations[0].code == "LOCKED_PLANNED_NEW_STUDENTS_REQUIRED"


def test_locked_positive_plan_yields_one_hundred_percent_result_and_locked_zero_is_na() -> None:
    positive = _combination("a" * 64, entry=False)
    zero = _combination("b" * 64, entry=False)
    plans = [
        ManualPlanInput("p-positive", positive.stable_id, 20271, 30, True),
        ManualPlanInput("p-zero", zero.stable_id, 20271, 0, True),
    ]
    result = calculate_headcounts(
        fiscal_year=2027,
        combinations=[positive, zero],
        rates=[],
        plans=plans,
    )
    first_rows = {row.combination.stable_id: row for row in result.rows if row.fiscal_season == 20271}
    assert first_rows[positive.stable_id].realized_new_student_share_ppm == 1_000_000
    assert first_rows[zero.stable_id].realized_new_student_share_ppm is None


def test_same_inputs_are_deterministic_across_one_hundred_runs() -> None:
    item = _combination("a" * 64, entry=True, entry_value=100)
    results = {
        calculate_headcounts(
            fiscal_year=2027,
            combinations=[item],
            rates=_rates([item]),
            plans=[],
        )
        for _ in range(100)
    }
    assert len(results) == 1
