import pytest

from packages.entry_planning.allocation import (
    CampusAllocationInput,
    EntryAllocationError,
    allocate_campus_entry_suggestions,
)


def _campus(
    name: str,
    key: str,
    history: int,
    manual: int | None = None,
    reason: str | None = None,
) -> CampusAllocationInput:
    return CampusAllocationInput(
        campus=name,
        campus_stable_key=key * 64,
        historical_business_new_students=history,
        classroom_resource={"rooms": 10},
        sales_resource={"consultants": 5},
        manual_reserved_suggestion=manual,
        manual_suggestion_reason=reason,
    )


def test_manual_reservation_precedes_historical_largest_remainder() -> None:
    result = allocate_campus_entry_suggestions(
        fiscal_year_entry_total=1000,
        campuses=[
            _campus("新校区", "a", 0, 100, "新校区人工计划"),
            _campus("甲", "b", 200),
            _campus("乙", "c", 600),
        ],
    )
    by_name = {item.campus: item for item in result}
    assert by_name["新校区"].suggested_entry_value == 100
    assert by_name["甲"].suggested_entry_value == 225
    assert by_name["乙"].suggested_entry_value == 675
    assert sum(item.suggested_entry_value for item in result) == 1000


def test_largest_remainder_tie_uses_stable_key() -> None:
    result = allocate_campus_entry_suggestions(
        fiscal_year_entry_total=1,
        campuses=[_campus("乙", "b", 1), _campus("甲", "a", 1)],
    )
    by_name = {item.campus: item for item in result}
    assert by_name["甲"].suggested_entry_value == 1
    assert by_name["甲"].suggestion_tail_assigned is True
    assert by_name["乙"].suggested_entry_value == 0


def test_all_no_history_requires_manual_total_to_match() -> None:
    with pytest.raises(EntryAllocationError) as caught:
        allocate_campus_entry_suggestions(
            fiscal_year_entry_total=10,
            campuses=[_campus("新校区", "a", 0, 9, "人工计划")],
        )
    assert caught.value.code == "ALL_CAMPUSES_MANUAL_TOTAL_MISMATCH"


def test_resources_and_no_history_reason_are_required() -> None:
    value = _campus("新校区", "a", 0, 10, None)
    with pytest.raises(EntryAllocationError) as caught:
        allocate_campus_entry_suggestions(fiscal_year_entry_total=10, campuses=[value])
    assert caught.value.code == "MANUAL_RESERVATION_REASON_REQUIRED"
