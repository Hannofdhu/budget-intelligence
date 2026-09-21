import pytest
from pydantic import ValidationError

from packages.contracts.versioning import (
    CampusEntryReplaceDraftRequest,
    CombinationReplaceDraftRequest,
    PlannedNewStudentChangeAndLockRequest,
)


def _combination(*, entry_attribute: str = "ENTRY") -> dict[str, object]:
    return {
        "track": "智慧伴学",
        "product": "普通",
        "grade": "七年级",
        "subject": "数学",
        "campus": "西直门",
        "entry_attribute": entry_attribute,
        "enabled": True,
    }


def test_combination_public_dimensions_cannot_repeat_for_entry_attribute() -> None:
    with pytest.raises(ValidationError, match="公共五维不得重复"):
        CombinationReplaceDraftRequest.model_validate(
            {"items": [_combination(), _combination(entry_attribute="NON_ENTRY")]}
        )


def test_combination_dimensions_cannot_be_whitespace() -> None:
    value = _combination()
    value["subject"] = "   "
    with pytest.raises(ValidationError, match="所有维度均须填写具体值"):
        CombinationReplaceDraftRequest.model_validate({"items": [value]})


def test_campus_entry_draft_cannot_repeat_campus() -> None:
    item = {
        "campus": "西直门",
        "determined_entry_value": 100,
        "performance_evidence": {"history": 80},
        "classroom_resource": {"rooms": 10},
        "sales_resource": {"consultants": 5},
    }
    with pytest.raises(ValidationError, match="校区入口确定值不得重复"):
        CampusEntryReplaceDraftRequest.model_validate({"items": [item, item]})


def _planned(value: object = 0) -> dict[str, object]:
    return {
        "fiscal_season": 20271,
        "track": "初中",
        "product": "智慧空间",
        "entry_attribute": "NON_ENTRY",
        "grade": "八年级",
        "subject": "数学",
        "campus": "西直门",
        "planned_new_students": value,
        "reason": "人工计划依据",
    }


def test_planned_new_students_require_strict_non_negative_integer() -> None:
    for value in (12.5, -1, "12"):
        with pytest.raises(ValidationError):
            PlannedNewStudentChangeAndLockRequest.model_validate({"items": [_planned(value)]})
    accepted = PlannedNewStudentChangeAndLockRequest.model_validate({"items": [_planned(0)]})
    assert accepted.items[0].planned_new_students == 0


def test_planned_new_students_require_concrete_unique_dimensions() -> None:
    missing = _planned()
    missing["subject"] = "   "
    with pytest.raises(ValidationError, match="全部具体预算维度"):
        PlannedNewStudentChangeAndLockRequest.model_validate({"items": [missing]})
    with pytest.raises(ValidationError, match="不得重复"):
        PlannedNewStudentChangeAndLockRequest.model_validate({"items": [_planned(), _planned()]})
