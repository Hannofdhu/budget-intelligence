import asyncio
import multiprocessing
import signal
import time

import pytest

from packages.optimization.process_runner import run_isolated


def _uncooperative_cpu_work(**_options: object) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        pass


def _add(*, left: int, right: int) -> int:
    return left + right


def _fail() -> None:
    raise ValueError("private solver details must not escape")


async def test_real_cpu_hard_timeout_keeps_loop_responsive_and_reaps_child() -> None:
    before = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    task = asyncio.create_task(ticker())
    try:
        with pytest.raises(TimeoutError):
            await run_isolated(_uncooperative_cpu_work, kwargs={}, timeout_seconds=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert 1.8 <= time.monotonic() - started < 6
    assert ticks >= 10
    assert {child.pid for child in multiprocessing.active_children()} == before


async def test_process_result_and_failure_are_transferred() -> None:
    assert await run_isolated(_add, kwargs={"left": 2, "right": 3}, timeout_seconds=10) == 5
    with pytest.raises(RuntimeError, match="^SOLVER_PROCESS_FAILED$"):
        await run_isolated(_fail, kwargs={}, timeout_seconds=10)


async def test_cancelled_request_reaps_solver_child() -> None:
    before = {child.pid for child in multiprocessing.active_children()}
    task = asyncio.create_task(run_isolated(_uncooperative_cpu_work, kwargs={}, timeout_seconds=30))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert {child.pid for child in multiprocessing.active_children()} == before
