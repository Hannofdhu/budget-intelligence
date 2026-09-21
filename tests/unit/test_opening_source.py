from decimal import Decimal

import pytest

from packages.source_snapshot.adapter import SourceAdapterError
from packages.source_snapshot.aggregation import aggregate_opening_enrollment


def row(count: object = 10, marker: object = "数据") -> dict[str, object]:
    return {
        "季节财年": 20274,
        "季度": "春季",
        "赛道": "智慧空间",
        "产品": "普通",
        "年级": "七年级",
        "科目": "数学",
        "教学区": "花园桥",
        "招生人次": count,
        "招生人次(三分之二出勤口径)": 999,
        "目标标识(目标,数据)": marker,
    }


def test_sums_actual_enrollment_not_attendance_and_excludes_targets() -> None:
    values = aggregate_opening_enrollment([row(10), row(20), row(900, "目标")], 20274)
    assert len(values) == 1
    value = next(iter(values.values()))
    assert value.numerator == Decimal(30)
    assert value.denominator == 0


@pytest.mark.parametrize("count", [-1, 0.5, None, "NaN", "Infinity"])
def test_invalid_enrollment_blocks(count: object) -> None:
    with pytest.raises(SourceAdapterError):
        aggregate_opening_enrollment([row(count)], 20274)


def test_missing_actual_marker_blocks() -> None:
    with pytest.raises(SourceAdapterError):
        aggregate_opening_enrollment([row(marker=None)], 20274)


def test_missing_count_does_not_fall_back_to_attendance() -> None:
    item = row()
    del item["招生人次"]
    with pytest.raises(SourceAdapterError):
        aggregate_opening_enrollment([item], 20274)


def test_other_season_is_not_used_and_explicit_zero_is_retained() -> None:
    item = row(900)
    item["季节财年"] = 20271
    values = aggregate_opening_enrollment([item, row(0)], 20274)
    assert next(iter(values.values())).numerator == 0


async def test_live_adapter_refuses_fy27_before_any_external_request() -> None:
    from unittest.mock import AsyncMock

    from packages.config.settings import Settings
    from packages.source_snapshot.adapter import GuanCliSourceAdapter

    adapter = GuanCliSourceAdapter(Settings())
    call = AsyncMock()
    adapter._metadata = call  # type: ignore[method-assign]
    with pytest.raises(SourceAdapterError) as error:
        await adapter.extract(2027)
    assert error.value.code == "FY27_FINALIZED"
    call.assert_not_awaited()
