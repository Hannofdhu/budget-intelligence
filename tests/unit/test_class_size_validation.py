from packages.class_size.validation import (
    ClassSizeInput,
    HeadcountInput,
    validate_class_sizes,
)


def _headcount(
    stable_key: str,
    season: int,
    total: int | None,
    *,
    subject: str = "数学",
    status: str = "CALCULATED",
) -> HeadcountInput:
    return HeadcountInput(
        result_id=f"{stable_key}-{season}",
        combination_stable_key=stable_key,
        fiscal_season=season,
        track="智慧空间",
        product="THINK",
        entry_attribute="ENTRY",
        grade="七年级",
        subject=subject,
        campus="中关村",
        calculation_status=status,
        integer_total_students=total,
    )


def _parameter() -> ClassSizeInput:
    return ClassSizeInput("parameter-12", "智慧空间", "THINK", 12)


def test_ab040_uses_rounded_integer_headcount() -> None:
    result = validate_class_sizes(headcounts=[_headcount("a" * 64, 20271, 12)], parameters=[_parameter()])
    assert result.rows[0].status == "PASS"
    assert result.rows[0].class_size_gap == 0
    assert result.publishable is True


def test_ab041_zero_is_no_planned_class_but_four_zeros_block_publication() -> None:
    rows = [_headcount("a" * 64, 20270 + index, 0) for index in range(1, 5)]
    result = validate_class_sizes(headcounts=rows, parameters=[_parameter()])
    assert {row.status for row in result.rows} == {"NO_PLANNED_CLASS"}
    assert [item.code for item in result.validations] == ["ENABLED_COMBINATION_ALL_SEASONS_ZERO"]


def test_ab043_does_not_merge_across_subjects() -> None:
    result = validate_class_sizes(
        headcounts=[
            _headcount("a" * 64, 20271, 8, subject="数学"),
            _headcount("b" * 64, 20271, 8, subject="英语"),
        ],
        parameters=[_parameter()],
    )
    assert [row.class_size_gap for row in result.rows] == [4, 4]
    assert [row.status for row in result.rows] == [
        "BELOW_MINIMUM_CLASS_SIZE",
        "BELOW_MINIMUM_CLASS_SIZE",
    ]


def test_ab044_reports_gap_without_auto_filling_headcount() -> None:
    result = validate_class_sizes(headcounts=[_headcount("a" * 64, 20271, 11)], parameters=[_parameter()])
    assert result.rows[0].headcount.integer_total_students == 11
    assert result.rows[0].class_size_gap == 1
    assert result.validations[0].code == "BELOW_MINIMUM_CLASS_SIZE"


def test_blocked_headcount_does_not_default_to_zero() -> None:
    result = validate_class_sizes(
        headcounts=[_headcount("a" * 64, 20271, None, status="BLOCKED")],
        parameters=[_parameter()],
    )
    assert result.rows[0].status == "HEADCOUNT_UNAVAILABLE"
    assert result.rows[0].class_size_gap is None
    assert result.validations[0].code == "HEADCOUNT_UNAVAILABLE_FOR_CLASS_SIZE"
