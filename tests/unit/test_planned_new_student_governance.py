from packages.planned_new_students.governance import (
    PlannedNewStudentKey,
    handling_status,
    planned_new_student_stable_key,
    quarter_name,
)


def test_stable_key_uses_every_required_dimension() -> None:
    base = PlannedNewStudentKey(2027, 20271, "赛道", "产品", "NON_ENTRY", "八年级", "数学", "校区")
    changed = PlannedNewStudentKey(2027, 20271, "赛道", "产品", "NON_ENTRY", "八年级", "英语", "校区")
    assert planned_new_student_stable_key(base) != planned_new_student_stable_key(changed)


def test_quarter_must_belong_to_budget_year() -> None:
    assert quarter_name(2027, 20271) == "暑假"
    assert quarter_name(2027, 20274) == "春季"
    assert quarter_name(2027, 20281) is None
    assert quarter_name(2027, 20275) is None


def test_zero_and_absence_are_not_conflated() -> None:
    assert handling_status(planned_new_students=0, locked=True) == "LOCKED_ZERO"
    assert handling_status(planned_new_students=13, locked=True) == "LOCKED_POSITIVE"
    assert handling_status(planned_new_students=0, locked=False) == "FILLED_PENDING_LOCK"
