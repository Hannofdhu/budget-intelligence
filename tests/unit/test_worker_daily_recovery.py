import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from apps.worker import main as worker
from packages.config.settings import Settings
from packages.forecast.daily import JOB_TYPE
from tests.unit.test_forecast_daily import configured


async def test_worker_recovers_after_storage_outage_without_exiting_or_busy_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, forecast_daily_enabled=False)
    event = asyncio.Event()
    attempts: list[int] = []
    waits: list[float] = []
    disposed: list[bool] = []

    async def handle(_repository: Any, **_filters: Any) -> bool:
        attempts.append(1)
        if len(attempts) == 1:
            raise SQLAlchemyError("synthetic storage interruption")
        event.set()
        return True

    async def enqueue(_settings: Any) -> None:
        return None

    async def wait_for(awaitable: Any, **options: Any) -> None:
        waits.append(options["timeout"])
        awaitable.close()
        raise TimeoutError

    async def dispose() -> None:
        disposed.append(True)

    monkeypatch.setattr(
        worker,
        "asyncio",
        SimpleNamespace(
            Event=lambda: event,
            get_running_loop=lambda: SimpleNamespace(add_signal_handler=lambda *_: None),
            wait_for=wait_for,
        ),
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "configure_logging", lambda *_: None)
    monkeypatch.setattr(worker, "JobRepository", lambda **_: object())
    monkeypatch.setattr(worker, "handle_one", handle)
    monkeypatch.setattr(worker, "enqueue_due", enqueue)
    monkeypatch.setattr(worker, "dispose_engine", dispose)
    await worker.worker_loop(once=False)
    assert len(attempts) == 2 and waits == [30] and disposed == [True]


async def test_daily_consumer_runs_while_general_job_is_waiting_for_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    callbacks: list[Any] = []
    order: list[str] = []

    async def handle(_repository: Any, **filters: Any) -> bool:
        if filters.get("exclude_job_type") == JOB_TYPE:
            order.append("general-start")
            started.set()
            await release.wait()
            order.append("general-end")
        else:
            assert filters["job_type"] == JOB_TYPE
            await started.wait()
            order.append("daily")
            callbacks[0]()
            release.set()
        return True

    async def noop(*_args: Any) -> None:
        return None

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda _signal, callback: callbacks.append(callback))
    monkeypatch.setattr(worker, "get_settings", configured)
    monkeypatch.setattr(worker, "configure_logging", lambda *_: None)
    monkeypatch.setattr(worker, "JobRepository", lambda **_: object())
    monkeypatch.setattr(worker, "handle_one", handle)
    monkeypatch.setattr(worker, "enqueue_due", noop)
    monkeypatch.setattr(worker, "dispose_engine", noop)
    async with asyncio.timeout(1):
        await worker.worker_loop(once=False)
    assert order == ["general-start", "daily", "general-end"]


async def test_daily_job_acknowledgement_error_is_safe_and_never_logged_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from structlog.testing import capture_logs

    job = SimpleNamespace(job_id="synthetic", job_type=JOB_TYPE, attempt_no=1)

    class Repository:
        async def claim_next(self, **_filters: Any) -> Any:
            return job

        async def mark_failed(self, *_args: Any) -> None:
            raise AssertionError("uncertain acknowledgement must leave the recoverable lease untouched")

    async def fail(*_args: Any) -> None:
        raise SQLAlchemyError("raw SQL token=credential-secret")

    monkeypatch.setattr(worker, "execute_daily_forecast_job", fail)
    monkeypatch.setattr(worker, "get_settings", configured)
    with capture_logs() as logs:
        assert await worker.handle_one(Repository()) is True  # type: ignore[arg-type]
    assert logs[-1]["event"] == "forecast_daily_acknowledgement_unavailable"
    assert logs[-1]["exception_type"] == "SQLAlchemyError" and logs[-1]["outcome"] == "FAILED"
    assert "credential-secret" not in repr(logs)
    assert not any(entry["event"] in {"job_succeeded", "forecast_daily_attempt_finished"} for entry in logs)
