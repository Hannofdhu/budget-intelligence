from decimal import Decimal

import pytest

from packages.source_snapshot.adapter import SourceAdapterError
from packages.source_snapshot.aggregation import (
    NEW_STUDENT_SHARE_REFERENCE,
    RENEWAL_RATE_REFERENCE,
    aggregate_source_rows,
    mismatch_count,
)


def _row(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "季节财年": 20261,
        "季度": "春季",
        "赛道": "初中",
        "产品": "智慧空间",
        "年级": "七年级",
        "科目": "数学",
        "教学区": "西直门",
        "业务新生人次": Decimal("10"),
        "新生人次": Decimal("10"),
        "招生人次(三分之二出勤口径)": Decimal("20"),
        "原班人次": Decimal("100"),
        "续班人次": Decimal("90"),
        "专项班续班人次": Decimal("5"),
    }
    result.update(overrides)
    return result


def test_aggregates_numerators_and_denominators_before_comparison() -> None:
    roster = [_row(), _row(**{"业务新生人次": 5, "招生人次(三分之二出勤口径)": 10})]
    summary = [_row(), _row(**{"新生人次": 5, "招生人次(三分之二出勤口径)": 10})]
    result = aggregate_source_rows(
        roster_rows=roster,
        summary_rows=summary,
        entry_fiscal_season=20261,
    )
    renewal = next(iter(result.roster_metrics[RENEWAL_RATE_REFERENCE].values()))
    new_student = next(iter(result.roster_metrics[NEW_STUDENT_SHARE_REFERENCE].values()))
    assert renewal.numerator == Decimal("170")
    assert renewal.denominator == Decimal("200")
    assert new_student.numerator == Decimal("15")
    assert new_student.denominator == Decimal("30")
    assert sum(result.entry_history.values()) == 15
    assert (
        mismatch_count(
            result.roster_metrics[NEW_STUDENT_SHARE_REFERENCE],
            result.summary_metrics[NEW_STUDENT_SHARE_REFERENCE],
        )
        == 0
    )


def test_exact_dimension_mismatch_is_not_hidden_by_equal_department_total() -> None:
    roster = [_row(), _row(**{"教学区": "中关村", "业务新生人次": 5})]
    summary = [_row(**{"新生人次": 5}), _row(**{"教学区": "中关村"})]
    result = aggregate_source_rows(
        roster_rows=roster,
        summary_rows=summary,
        entry_fiscal_season=20261,
    )
    assert (
        mismatch_count(
            result.roster_metrics[NEW_STUDENT_SHARE_REFERENCE],
            result.summary_metrics[NEW_STUDENT_SHARE_REFERENCE],
        )
        == 2
    )


def test_all_dimensions_are_required() -> None:
    with pytest.raises(SourceAdapterError) as error:
        aggregate_source_rows(
            roster_rows=[_row(**{"科目": ""})],
            summary_rows=[_row()],
            entry_fiscal_season=20261,
        )
    assert error.value.code == "SOURCE_DIMENSION_MISSING"


def test_zero_metric_row_with_incomplete_dimensions_is_excluded_and_counted() -> None:
    zero_fields = {
        "年级": None,
        "业务新生人次": 0,
        "新生人次": 0,
        "招生人次(三分之二出勤口径)": 0,
        "原班人次": 0,
        "续班人次": 0,
        "专项班续班人次": 0,
    }
    result = aggregate_source_rows(
        roster_rows=[_row(**zero_fields)],
        summary_rows=[_row(**zero_fields)],
        entry_fiscal_season=20261,
    )
    assert result.excluded_zero_incomplete_rows == {"roster": 1, "summary": 1}
    assert not result.entry_history
