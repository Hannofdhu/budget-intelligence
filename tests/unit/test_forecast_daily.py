import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from apps.api.routes import forecast as route
from packages.config.settings import Settings
from packages.domain.identity.models import Role
from packages.forecast import daily
from packages.forecast.refresh_lock import refresh_lock_id
from packages.persistence.jobs import JobRepository
from packages.source_snapshot.adapter import SourceAdapterError


def configured(**overrides: Any) -> Settings:
    return Settings(
        **{
            "_env_file": None,
            "forecast_daily_enabled": True,
            "environment": "local",
            "auth_mode": "dev_headers",
            "local_trial_enabled": True,
            "local_trial_department_id": "opening-api-synthetic-daily",
            "database_url": "postgresql+asyncpg://test@localhost/budget_acceptance_daily_test",
            **overrides,
        }
    )


def job_for_today(*, attempt: int = 1) -> Any:
    department = "opening-api-synthetic-daily"
    day = datetime.now(UTC).astimezone(daily.TIMEZONE).date()
    key, request = daily.run_identity(department, day)
    return SimpleNamespace(
        job_id="synthetic",
        dedupe_key=key,
        attempt_no=attempt,
        payload={
            "department_id": department,
            "fiscal_year": 2027,
            "day": day.isoformat(),
            "request_id": request,
            "scheduled_for": daily.due_time(datetime.now(UTC)).isoformat(),
        },
    )


def test_shanghai_schedule_fixed_instant_and_stable_department_day_identity() -> None:
    now = datetime(2026, 10, 16, 1, 29, tzinfo=UTC)
    assert daily.due_time(now).isoformat() == "2026-10-16T09:30:00+08:00"
    assert daily.due_time(now).astimezone(UTC) > now
    assert daily.due_time(now) == daily.due_time(now + timedelta(seconds=60))
    with pytest.raises(ValueError, match="timezone-aware"):
        daily.due_time(now.replace(tzinfo=None))
    day = now.date()
    assert daily.run_identity("a", day) == daily.run_identity("a", day)
    assert daily.run_identity("a", day) != daily.run_identity("b", day)
    assert daily.run_identity("a", day) != daily.run_identity("a", day + timedelta(days=1))
    assert refresh_lock_id("a") != refresh_lock_id("b")
    assert refresh_lock_id("a", 2027) != refresh_lock_id("a", 2028)


@pytest.mark.parametrize(
    "overrides",
    [
        {"local_trial_enabled": False},
        {"environment": "test"},
        {"auth_mode": "oidc"},
        {"local_trial_department_id": "different-department"},
        {"database_url": "postgresql+asyncpg://test@localhost/business"},
    ],
)
def test_daily_cannot_grant_unbound_or_production_access(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="explicitly enabled local trial"):
        configured(**overrides)


async def test_disabled_scheduler_never_opens_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("no scheduled SQL when disabled")

    monkeypatch.setattr(daily, "get_session_factory", forbidden)
    await daily.enqueue_due(configured(forecast_daily_enabled=False))


@pytest.fixture
def generation(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"refreshes": [], "reads": [], "locks": []}
    parent = {
        "id": "parent",
        "baseline_hash": "immutable",
        "assumptions": route.Assumptions().model_dump(mode="json"),
    }
    seen["parent"] = parent

    @asynccontextmanager
    async def slot(department: str) -> Any:
        seen["locks"].append(department)
        yield

    async def store_call(method: str, *args: Any, **kwargs: Any) -> Any:
        assert method == "request_result"
        return seen.get("replay")

    async def current(actor: Any) -> Any:
        seen["reads"].append(actor)
        return {"version": seen["parent"]}

    async def baseline(department: str) -> Any:
        return {"sha256": "immutable"}

    async def refresh(baseline: Any, body: Any, actor: Any, request: Any, digest: Any, **kwargs: Any) -> Any:
        seen["refreshes"].append((body, actor, request, kwargs))
        return {"version": {"id": "new", "sequence": 20, "as_of": "2026-10-15"}}

    monkeypatch.setattr(daily, "refresh_slot", slot)
    monkeypatch.setattr(daily, "read_database_review", baseline)
    monkeypatch.setattr(route, "_current", current)
    monkeypatch.setattr(route, "_store_call", store_call)
    monkeypatch.setattr(route, "_refresh_from_source", refresh)
    return seen


async def test_automatic_generation_reuses_refresh_with_same_states_and_service_provenance(
    generation: dict[str, Any],
) -> None:
    generation["parent"]["assumptions"]["states"]["秋季"] = "ENDED"
    result = await daily._generate(job_for_today(), configured())
    assert result["status"] == "DRAFT" and result["version_id"] == "new"
    body, actor, _, kwargs = generation["refreshes"][0]
    assert body.states == generation["parent"]["assumptions"]["states"]
    assert actor.user_id == daily.SERVICE_ACTOR
    assert not actor.has_role(Role.DEPARTMENT_BUDGET_OWNER)
    assert kwargs == {"automatic": True}
    assert generation["locks"] == [actor.department_id]


async def test_saved_daily_replay_avoids_source_and_baseline_reads(generation: dict[str, Any]) -> None:
    generation["replay"] = {"version": {"id": "existing", "sequence": 20, "as_of": "2026-10-15"}}
    assert (await daily._generate(job_for_today(), configured()))["version_id"] == "existing"
    assert generation["reads"] == [] and generation["refreshes"] == []


@pytest.mark.parametrize(
    "kind,code", [("none", "FORECAST_DAILY_PARENT_REQUIRED"), ("hash", "FORECAST_DAILY_BASELINE_CHANGED")]
)
async def test_automatic_requires_previously_established_parent_and_identical_baseline(
    generation: dict[str, Any], kind: str, code: str
) -> None:
    if kind == "none":
        generation["parent"] = None
    else:
        generation["parent"]["baseline_hash"] = "changed"
    with pytest.raises(SourceAdapterError) as exc:
        await daily._generate(job_for_today(), configured())
    assert exc.value.code == code and generation["refreshes"] == []


async def test_background_rejects_other_department_old_day_or_tampered_identity(
    generation: dict[str, Any],
) -> None:
    for field, value, code in [
        ("department_id", "other", "FORECAST_DAILY_DISABLED"),
        ("day", "2020-01-01", "FORECAST_DAILY_EXPIRED"),
        ("request_id", "tampered", "JOB_PAYLOAD_INVALID"),
    ]:
        job = job_for_today()
        job.payload[field] = value
        with pytest.raises(SourceAdapterError) as exc:
            await daily._generate(job, configured())
        assert exc.value.code == code
    assert not generation["locks"] and not generation["refreshes"]


class Session:
    def __init__(self, statements: list[dict[str, Any]]) -> None:
        self.statements = statements

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def begin(self) -> "Session":
        return self

    async def execute(self, statement: Any) -> None:
        self.statements.append(statement.compile().params)

    async def scalar(self, statement: Any) -> str:
        self.statements.append(statement.compile().params)
        return "synthetic"


@pytest.mark.parametrize(
    "attempt,retryable,expected", [(1, True, "QUEUED"), (3, True, "FAILED"), (1, False, "FAILED")]
)
async def test_job_failures_have_finite_retries_and_no_saved_result(
    monkeypatch: pytest.MonkeyPatch, attempt: int, retryable: bool, expected: str
) -> None:
    statements: list[dict[str, Any]] = []
    repository = SimpleNamespace(session_factory=lambda: Session(statements), worker_id="test-worker")

    async def fail(*_args: Any) -> Any:
        raise SourceAdapterError("BI_TEMPORARY", "观远暂时不可用；已保留结果", retryable=retryable)

    monkeypatch.setattr(daily, "_generate", fail)
    await daily.execute_job(job_for_today(attempt=attempt), cast(JobRepository, repository), configured())
    outcome = statements[-1]
    assert outcome["status"] == expected
    assert outcome["last_error_code"] == "BI_TEMPORARY" and outcome["result"] is None
    assert (outcome["finished_at"] is None) == (expected == "QUEUED")
    assert "attempt_no_1" in outcome  # old attempt cannot complete a recovered lease


async def test_cancellation_propagates_and_leaves_recoverable_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[dict[str, Any]] = []
    repository = SimpleNamespace(session_factory=lambda: Session(statements), worker_id="test-worker")

    async def cancelled(*_args: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(daily, "_generate", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await daily.execute_job(job_for_today(), cast(JobRepository, repository), configured())
    assert len(statements) == 1 and statements[0]["timeout_at"] is not None
