import pytest

from packages.entry_planning.detail_allocation import (
    EntryDetailAllocationError,
    EntryDetailAllocationInput,
    allocate_entry_details,
)


def _item(
    key: str, *, origin: str, history: int = 0, ratio_ppm: int | None = None
) -> EntryDetailAllocationInput:
    return EntryDetailAllocationInput(
        combination_stable_key=key * 64,
        origin_type=origin,
        historical_business_new_students=history,
        manual_reservation_ratio_ppm=ratio_ppm,
    )


def test_manual_segment_precedes_historical_segment() -> None:
    result = allocate_entry_details(
        campus_entry_value=100,
        items=[
            _item("a", origin="MANUAL", ratio_ppm=200_000),
            _item("b", origin="HISTORICAL", history=60),
            _item("c", origin="HISTORICAL", history=40),
        ],
    )
    by_key = {item.combination_stable_key: item for item in result}
    assert by_key["a" * 64].entry_initial_value == 20
    assert by_key["b" * 64].entry_initial_value == 48
    assert by_key["c" * 64].entry_initial_value == 32


def test_each_segment_uses_largest_remainder_and_stable_key() -> None:
    result = allocate_entry_details(
        campus_entry_value=1,
        items=[
            _item("b", origin="HISTORICAL", history=1),
            _item("a", origin="HISTORICAL", history=1),
        ],
    )
    by_key = {item.combination_stable_key: item for item in result}
    assert by_key["a" * 64].entry_initial_value == 1
    assert by_key["a" * 64].tail_assigned is True
    assert by_key["b" * 64].entry_initial_value == 0


def test_manual_ratios_cannot_exceed_one_hundred_percent() -> None:
    with pytest.raises(EntryDetailAllocationError) as caught:
        allocate_entry_details(
            campus_entry_value=100,
            items=[
                _item("a", origin="MANUAL", ratio_ppm=600_000),
                _item("b", origin="MANUAL", ratio_ppm=450_000),
            ],
        )
    assert caught.value.code == "MANUAL_ENTRY_RATIO_EXCEEDS_TOTAL"


def test_remaining_value_without_history_is_blocked() -> None:
    with pytest.raises(EntryDetailAllocationError) as caught:
        allocate_entry_details(
            campus_entry_value=100,
            items=[_item("a", origin="MANUAL", ratio_ppm=20_000)],
        )
    assert caught.value.code == "ENTRY_DETAIL_HISTORY_MISSING"
