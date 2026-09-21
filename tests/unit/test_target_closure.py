from decimal import Decimal

import pytest

from packages.target_closure.calculation import (
    TargetClosureInvariantError,
    TargetRevenueInput,
    calculate_target_closure,
)


def _input(
    target_id: str,
    scope_level: str,
    track: str | None,
    target: str,
    calculated: str,
) -> TargetRevenueInput:
    return TargetRevenueInput(
        target_id=target_id,
        scope_level=scope_level,
        track=track,
        target_revenue_yuan=Decimal(target),
        calculated_revenue_yuan=Decimal(calculated),
    )


def test_exact_positive_and_negative_ten_thousand_boundaries_pass() -> None:
    result = calculate_target_closure(
        [
            _input("department", "DEPARTMENT", None, "113000000", "113010000"),
            _input("track-a", "TRACK", "智慧伴学", "37000000", "36990000"),
            _input("track-b", "TRACK", "智慧空间", "76000000", "76000000"),
        ]
    )
    assert result.targets_within_tolerance is True
    assert {item.check_status for item in result.items} == {"WITHIN_TOLERANCE"}


def test_one_cent_outside_boundary_fails_without_track_offsetting() -> None:
    result = calculate_target_closure(
        [
            _input("department", "DEPARTMENT", None, "113000000", "113000000"),
            _input("track-a", "TRACK", "智慧伴学", "37000000", "36989999.99"),
            _input("track-b", "TRACK", "智慧空间", "76000000", "76010000.01"),
        ]
    )
    by_track = {item.source.track: item.check_status for item in result.items}
    assert result.closure_status == "MIXED_PENDING_REBALANCE"
    assert by_track["智慧伴学"] == "GAP"
    assert by_track["智慧空间"] == "OVER_TARGET"


def test_department_and_concrete_track_targets_are_required() -> None:
    with pytest.raises(TargetClosureInvariantError) as error:
        calculate_target_closure([_input("department", "DEPARTMENT", None, "113000000", "113000000")])
    assert error.value.code == "TARGET_SCOPE_INVALID"
