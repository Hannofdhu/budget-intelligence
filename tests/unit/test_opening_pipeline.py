import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages import calculation_pipeline as pipeline
from packages.domain.identity.models import Actor, Role


@pytest.mark.parametrize(
    "year,rule",
    [
        (2027, "phase1-headcount-v1"),
        (2028, "phase1-headcount-opening-v2"),
        (2029, "phase1-headcount-opening-v2"),
    ],
)
async def test_worker_selects_rule_from_source_budget(
    monkeypatch: pytest.MonkeyPatch,
    year: int,
    rule: str,
) -> None:
    factory = MagicMock()
    context = AsyncMock()
    factory.return_value = context
    monkeypatch.setattr(pipeline, "get_session_factory", lambda: factory)
    access = AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace(fiscal_year=year)))
    monkeypatch.setattr(pipeline, "_assert_version_access", access)
    calculate = AsyncMock()
    monkeypatch.setattr(pipeline, "calculate_headcount_result", calculate)
    job, revision = uuid.uuid4(), uuid.uuid4()
    actor = Actor("test", "department", frozenset({Role.DATA_MAINTAINER}))
    await pipeline._calculate_headcount(job, actor, revision, "request", Role.DATA_MAINTAINER)
    assert calculate.call_args.kwargs["payload"].calculation_rule_version == rule
    assert calculate.call_args.kwargs["idempotency_key"] == uuid.uuid5(job, "HEADCOUNT")
    assert access.call_args.kwargs["revision_id"] == revision
    assert context.__aexit__.await_count == 2
