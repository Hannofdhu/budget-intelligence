import uuid
from dataclasses import asdict
from fractions import Fraction
from unittest.mock import AsyncMock

import pytest

from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.headcount.calculation import (
    CombinationInput,
    HeadcountInvariantError,
    RateInput,
    RateType,
    calculate_headcounts,
)
from packages.headcount.opening import OpeningKey
from packages.persistence.models import (
    BudgetCombination,
    BudgetRevision,
    OpeningStudentVersion,
    SourceSnapshotComponent,
)
from packages.persistence.opening import load_opening_headcounts


def test_opening_rounds_as_group_and_then_uses_previous_integer() -> None:
    combos = [
        CombinationInput(key, "智慧空间", "普通", "NON_ENTRY", "八年级", subject, "花园桥")
        for key, subject in (("a", "数学"), ("b", "语文"))
    ]
    kinds: list[tuple[RateType, int]] = [("RENEWAL_RATE", 800000), ("NEW_STUDENT_SHARE", 200000)]
    rates = [
        RateInput(f"{season}-{kind}", kind, season, "智慧空间", "普通", "八年级", "花园桥", value)
        for season in range(20281, 20285)
        for kind, value in kinds
    ]
    result = calculate_headcounts(
        fiscal_year=2028,
        combinations=combos,
        rates=rates,
        plans=[],
        opening_old_students={"a": Fraction(5, 2), "b": Fraction(5, 2)},
    )
    assert result.publishable
    assert result.rule_version == "phase1-headcount-opening-v2"
    first, second = result.rows[:2]
    assert (first.integer_old_students, second.integer_old_students) == (3, 2)
    assert first.theoretical_new_students == Fraction(5, 8)
    assert first.old_rounding_trace is not None and first.old_rounding_trace.group_target == 5
    assert first.integer_total_students is not None
    assert result.rows[2].theoretical_old_students == Fraction(first.integer_total_students * 4, 5)


def test_fy28_missing_opening_is_not_zero() -> None:
    with pytest.raises(HeadcountInvariantError) as error:
        calculate_headcounts(
            fiscal_year=2028,
            combinations=[CombinationInput("a", "智慧空间", "普通", "NON_ENTRY", "八年级", "数学", "花园桥")],
            rates=[],
            plans=[],
        )
    assert error.value.code == "OPENING_VERSION_REQUIRED"


@pytest.mark.parametrize("invalid", [{}, {"a": Fraction(-1)}, {"a": Fraction(1), "b": Fraction(1)}])
def test_opening_coverage_and_negative_values_block(invalid: dict[str, Fraction]) -> None:
    combo = CombinationInput("a", "智慧空间", "普通", "NON_ENTRY", "八年级", "数学", "花园桥")
    with pytest.raises(HeadcountInvariantError):
        calculate_headcounts(
            fiscal_year=2028, combinations=[combo], rates=[], plans=[], opening_old_students=invalid
        )


@pytest.mark.parametrize("failure", [None, "rate", "combination", "snapshot", "unverified", "hash", "owner"])
async def test_version_loading_and_stale_inputs(failure: str | None) -> None:
    version_id, owner_id, budget_id, component_id, snapshot_id, combo_id, rate_id = [
        uuid.uuid4() for _ in range(7)
    ]
    revision = BudgetRevision(
        annual_budget_id=budget_id,
        source_snapshot_id=snapshot_id,
        component_manifest={"opening_student_version_id": str(version_id)},
    )
    data = {
        "source_verification": "FROZEN_AGGREGATES_MATCHED",
        "fiscal_year": 2028,
        "source_fiscal_season": 20274,
        "zero_combination_ids": [],
        "rows": [
            {
                "target_key": asdict(OpeningKey("智慧空间", "普通", "八年级", "数学", "花园桥")),
                "theoretical_old_students": "5/2",
            }
        ],
    }
    version = OpeningStudentVersion(
        budget_revision_id=owner_id,
        source_component_id=component_id,
        combination_set_version_id=combo_id,
        rate_parameter_version_id=rate_id,
        content=data,
        content_hash=content_hash(data),
    )
    owner = BudgetRevision(annual_budget_id=budget_id)
    component = SourceSnapshotComponent(source_snapshot_id=snapshot_id)
    if failure == "rate":
        version.rate_parameter_version_id = uuid.uuid4()
    elif failure == "combination":
        version.combination_set_version_id = uuid.uuid4()
    elif failure == "snapshot":
        component.source_snapshot_id = uuid.uuid4()
    elif failure == "owner":
        owner.annual_budget_id = uuid.uuid4()
    elif failure == "unverified":
        data["source_verification"] = "DECLARED_ROWS_NOT_EXTRACT_VERIFIED"
        version.content_hash = content_hash(data)
    elif failure == "hash":
        version.content_hash = "a" * 64
    lookup = {
        (OpeningStudentVersion, version_id): version,
        (BudgetRevision, owner_id): owner,
        (SourceSnapshotComponent, component_id): component,
    }
    session = AsyncMock()
    session.get.side_effect = lambda kind, key: lookup[(kind, key)]
    combo = BudgetCombination(
        track="智慧空间",
        product="普通",
        grade="八年级",
        subject="数学",
        campus="花园桥",
        combination_id=uuid.uuid4(),
        combination_stable_key="a",
    )
    if failure:
        with pytest.raises(DomainError):
            await load_opening_headcounts(
                session,
                revision=revision,
                fiscal_year=2028,
                combinations=[combo],
                combination_version_id=combo_id,
                rate_version_id=rate_id,
            )
    else:
        values, fingerprint = await load_opening_headcounts(
            session,
            revision=revision,
            fiscal_year=2028,
            combinations=[combo],
            combination_version_id=combo_id,
            rate_version_id=rate_id,
        )
        assert values == {"a": Fraction(5, 2)}
        assert fingerprint == version.content_hash
