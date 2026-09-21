import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from packages.contracts.opening import OpeningCreateRequest, OpeningSourceInput
from packages.contracts.versioning import RevisionCommandResponse
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.persistence import opening
from packages.persistence.models import (
    BudgetCombinationSetVersion,
    RateParameter,
    RateParameterVersion,
    SourceSnapshot,
    SourceSnapshotComponent,
)


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    ids = [uuid.uuid4() for _ in range(8)]
    revision_id, combo_id, rate_id, component_id, snapshot_id, rate_row_id, target_id, budget_id = ids
    budget = SimpleNamespace(fiscal_year=2028, annual_budget_id=budget_id, department_id="department")
    parent = SimpleNamespace(source_snapshot_id=snapshot_id)
    combo = SimpleNamespace(target_fiscal_year=2028, budget_revision_id=revision_id, content_hash="a" * 64)
    rate_version = SimpleNamespace(fiscal_year=2028, budget_revision_id=revision_id, content_hash="b" * 64)
    component = SimpleNamespace(
        source_snapshot_id=snapshot_id,
        extracted_at=datetime.now(UTC),
        task_batch_id="batch",
        content_hash="c" * 64,
        dataset_id="roster",
        partition_value="batch",
    )
    snapshot = SimpleNamespace(source_snapshot_id=snapshot_id, department_id="department", status="FROZEN")
    rate = SimpleNamespace(
        rate_parameter_version_id=rate_id,
        rate_type="RENEWAL_RATE",
        target_fiscal_season=20281,
        source_fiscal_season=20274,
        track="智慧空间",
        product="普通",
        grade="七年级",
        campus="花园桥",
        current_value_ppm=800000,
    )
    target = SimpleNamespace(
        combination_id=target_id,
        track="智慧空间",
        product="普通",
        grade="八年级",
        subject="数学",
        campus="花园桥",
    )
    lookup = {
        (BudgetCombinationSetVersion, combo_id): combo,
        (RateParameterVersion, rate_id): rate_version,
        (SourceSnapshotComponent, component_id): component,
        (SourceSnapshot, snapshot_id): snapshot,
        (RateParameter, rate_row_id): rate,
    }

    @asynccontextmanager
    async def begin() -> Any:
        yield

    session = MagicMock()
    session.begin = begin
    session.get = AsyncMock(side_effect=lambda kind, key: lookup.get((kind, key)))
    frozen = SimpleNamespace(
        track="智慧空间",
        product="普通",
        grade="七年级",
        subject="数学",
        campus="花园桥",
        numerator=Decimal(100),
        denominator=Decimal(0),
        content_hash="d" * 64,
    )
    session.scalars = AsyncMock(
        side_effect=lambda query: [frozen] if "source_metric_aggregate" in str(query) else [target]
    )
    session.flush = AsyncMock()
    replay = AsyncMock(return_value=None)
    monkeypatch.setattr(opening, "_idempotency_replay", replay)
    monkeypatch.setattr(opening, "_lock_current_revision", AsyncMock(return_value=(parent, budget)))
    monkeypatch.setattr(
        opening,
        "_manifest_uuid",
        lambda _, key, message: combo_id if key.startswith("combination") else rate_id,
    )
    monkeypatch.setattr(opening, "_assert_version_access", AsyncMock(return_value=(parent, budget)))
    new_id = uuid.uuid4()
    child = AsyncMock(
        return_value=(
            SimpleNamespace(budget_revision_id=new_id),
            RevisionCommandResponse(
                annual_budget_id=budget_id,
                previous_revision_id=revision_id,
                new_revision_id=new_id,
                links={},
            ),
        )
    )
    monkeypatch.setattr(opening, "_create_child_revision", child)
    payload = OpeningCreateRequest(
        source_component_id=component_id,
        source_fiscal_season=20274,
        rows=[
            OpeningSourceInput(
                track="智慧空间",
                product="普通",
                grade="七年级",
                subject="数学",
                campus="花园桥",
                enrollment=100,
                rate_parameter_id=rate_row_id,
            )
        ],
        zero_combination_ids=[],
        reason="测试来源汇总",
    )
    return SimpleNamespace(
        session=session,
        actor=Actor("test", "department", frozenset({Role.DATA_MAINTAINER})),
        revision_id=revision_id,
        payload=payload,
        idempotency_key=uuid.uuid4(),
        budget=budget,
        snapshot=snapshot,
        rate=rate,
        target_id=target_id,
        replay=replay,
        child=child,
    )


async def run(state: SimpleNamespace) -> Any:
    return await opening.create_opening_version(
        state.session,
        actor=state.actor,
        revision_id=state.revision_id,
        expected_revision_id=state.revision_id,
        payload=state.payload,
        idempotency_key=state.idempotency_key,
        request_id="test",
    )


async def test_save_creates_revision_invalidates_and_replays(setup: SimpleNamespace) -> None:
    # Historical rate observation season is not the opening enrollment season.
    setup.rate.source_fiscal_season = 20271
    response = await run(setup)
    assert response.content["rows"][0]["theoretical_old_students"] == "80/1"
    assert response.content["status"] == "SOURCE_VERIFIED"
    assert response.content["source_verification"] == "FROZEN_AGGREGATES_MATCHED"
    assert response.content["verified_aggregate_hashes"] == ["d" * 64]
    removed = setup.child.call_args.kwargs["component_removals"]
    assert {"headcount_result_version_id", "optimization_run_id", "formal_confirmation_id"} <= set(removed)
    setup.replay.return_value = response.model_dump(mode="json")
    assert await run(setup) == response
    assert setup.child.await_count == 1


@pytest.mark.parametrize("failure", ["fy27", "unfrozen", "department", "rate", "coverage", "zero_overlap"])
async def test_invalid_basis_never_writes(setup: SimpleNamespace, failure: str) -> None:
    if failure == "fy27":
        setup.budget.fiscal_year = 2027
    elif failure == "unfrozen":
        setup.snapshot.status = "DRAFT"
    elif failure == "department":
        setup.snapshot.department_id = "other"
    elif failure == "rate":
        setup.rate.grade = "八年级"
    elif failure == "coverage":
        setup.payload.rows = []
    else:
        setup.payload.zero_combination_ids = [setup.target_id]
    with pytest.raises(DomainError):
        await run(setup)
    setup.child.assert_not_awaited()
    setup.session.add.assert_not_called()


async def test_role_is_checked_before_replay(setup: SimpleNamespace) -> None:
    setup.actor = Actor("test", "department", frozenset())
    with pytest.raises(DomainError) as error:
        await run(setup)
    assert error.value.status_code == 403
    setup.replay.assert_not_awaited()


async def test_missing_frozen_metric_blocks_without_writing(setup: SimpleNamespace) -> None:
    setup.session.scalars.side_effect = None
    setup.session.scalars.return_value = []
    with pytest.raises(DomainError) as error:
        await run(setup)
    assert error.value.code == "OPENING_FROZEN_ENROLLMENT_MISSING"
    setup.child.assert_not_awaited()


async def test_changed_enrollment_cannot_be_verified(setup: SimpleNamespace) -> None:
    setup.payload.rows[0].enrollment = 101
    with pytest.raises(DomainError) as error:
        await run(setup)
    assert error.value.code == "OPENING_FROZEN_ENROLLMENT_MISMATCH"
    setup.child.assert_not_awaited()


@pytest.mark.parametrize("value", [-1, 1.2, True, "100"])
def test_source_count_is_strict_integer(value: Any) -> None:
    with pytest.raises(ValidationError):
        OpeningSourceInput(
            track="智慧空间",
            product="普通",
            grade="七年级",
            subject="数学",
            campus="花园桥",
            enrollment=value,
            rate_parameter_id=uuid.uuid4(),
        )
