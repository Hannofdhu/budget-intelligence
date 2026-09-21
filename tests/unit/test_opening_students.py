from datetime import date
from fractions import Fraction

import pytest

from packages.headcount.calculation import HeadcountInvariantError
from packages.headcount.opening import OpeningKey, SpringSource, prepare_bi_opening


def source(grade: str = "七年级", subject: str = "数学", count: int = 183) -> SpringSource:
    return SpringSource(OpeningKey("智慧空间", "普通", grade, subject, "花园桥"), count, 628155, "rate-v1")


def test_grade_progression_and_exclusion_keep_source_trace() -> None:
    result = prepare_bi_opening(
        fiscal_year=2028,
        source_fiscal_season=20274,
        snapshot_date=date(2026, 9, 7),
        source_batch="batch-1",
        source_snapshot_id="snapshot-1",
        rows=[
            source(),
            source("八年级"),
            source("九年级"),
            source("八年级", "地理"),
            source("八年级", "生物"),
            source("七年级", "地理"),
        ],
    )
    by_key = {row.source_key: row for row in result.rows}
    first = by_key[source().key]
    assert first.target_key == OpeningKey("智慧空间", "普通", "八年级", "数学", "花园桥")
    assert first.theoretical_old_students == Fraction(183 * 628155, 1000000)
    assert by_key[source("八年级").key].target_key == OpeningKey(
        "智慧空间", "普通", "九年级", "数学", "花园桥"
    )
    assert by_key[source("七年级", "地理").key].target_key is not None
    for grade, subject, reason in [
        ("九年级", "数学", "GRADUATED"),
        ("八年级", "地理", "SUBJECT_ENDED"),
        ("八年级", "生物", "SUBJECT_ENDED"),
    ]:
        row = by_key[source(grade, subject).key]
        assert row.target_key is None
        assert row.exclusion == reason
        assert row.theoretical_old_students == 0
        assert row.enrollment == 183
    assert result.source_batch == "batch-1"


@pytest.mark.parametrize(
    "year,season,rows,code",
    [
        (2027, 20264, [source()], "FY27_FINALIZED"),
        (2028, 20264, [source()], "OPENING_SOURCE_SEASON_INVALID"),
        (2028, 20274, [source(), source()], "OPENING_DUPLICATE_SOURCE"),
        (2028, 20274, [source(count=-1)], "OPENING_ENROLLMENT_INVALID"),
        (2028, 20274, [source("高一")], "OPENING_GRADE_UNSUPPORTED"),
    ],
)
def test_invalid_inputs_block(year: int, season: int, rows: list[SpringSource], code: str) -> None:
    with pytest.raises(HeadcountInvariantError) as error:
        prepare_bi_opening(
            fiscal_year=year,
            source_fiscal_season=season,
            snapshot_date=date(2026, 9, 7),
            source_batch="batch",
            source_snapshot_id="snapshot",
            rows=rows,
        )
    assert error.value.code == code


def test_empty_source_does_not_invent_zero_rows() -> None:
    result = prepare_bi_opening(
        fiscal_year=2028,
        source_fiscal_season=20274,
        snapshot_date=date(2026, 9, 7),
        source_batch="batch",
        source_snapshot_id="snapshot",
        rows=[],
    )
    assert result.rows == ()
