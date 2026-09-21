"""A parent-owned wall-clock deadline for CPU-bound, database-free solving."""

import asyncio
import multiprocessing
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

from packages.optimization.engine import OptimizationOutcome, OptimizationStage, SolveStatus
from packages.optimization.evaluator import BudgetEvaluationContext, optimize_budget


def _child(connection: Connection, function: Callable[..., Any], kwargs: dict[str, Any]) -> None:
    try:
        connection.send(("RESULT", function(**kwargs)))
    except Exception:
        connection.send(("FAILED", None))
    finally:
        connection.close()


async def run_isolated(
    function: Callable[..., Any], *, kwargs: dict[str, Any], timeout_seconds: float
) -> Any:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(send, function, kwargs), daemon=True)
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    process.start()
    send.close()
    try:
        # recv runs off-loop and unblocks with EOF when a timed-out child is killed.
        remaining = max(0, deadline - asyncio.get_running_loop().time())
        kind, value = await asyncio.wait_for(asyncio.to_thread(receive.recv), timeout=remaining)
        if kind != "RESULT":
            raise RuntimeError("SOLVER_PROCESS_FAILED")
        return value
    except EOFError as exc:
        raise RuntimeError("SOLVER_PROCESS_EXITED") from exc
    finally:
        if process.is_alive():
            process.kill()
        await asyncio.to_thread(process.join)
        receive.close()
        process.close()


async def optimize_budget_isolated(context: BudgetEvaluationContext, **options: Any) -> OptimizationOutcome:
    try:
        result = await run_isolated(
            optimize_budget,
            kwargs={"context": context, **options},
            timeout_seconds=float(options["maximum_solver_seconds"]),
        )
        if not isinstance(result, OptimizationOutcome):
            raise RuntimeError("SOLVER_PROCESS_INVALID_RESULT")
        return result
    except TimeoutError:
        return OptimizationOutcome(
            status=SolveStatus.INCOMPLETE,
            terminal_stage=OptimizationStage.ENTRY_ONLY,
            candidates=(),
            attempts=(),
            discount_unlock_required=False,
            reason_code="HARD_TIME_LIMIT",
        )
    except RuntimeError:
        return OptimizationOutcome(
            status=SolveStatus.FAILED,
            terminal_stage=OptimizationStage.ENTRY_ONLY,
            candidates=(),
            attempts=(),
            discount_unlock_required=False,
            reason_code="SOLVER_PROCESS_FAILED",
        )
