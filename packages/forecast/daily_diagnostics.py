"""Small, allowlisted operational diagnostics; never serialize exception text.

These records live on the existing async job, not in a budget or forecast ledger.
The context also marks the established HTTP refresh stages without a new engine.
"""

import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

STAGES = frozenset(
    {
        "runtime_import",
        "validate_job",
        "refresh_lock",
        "request_replay",
        "baseline_read",
        "parent_read",
        "validate_assumptions",
        "source_extract",
        "fixed_assumptions",
        "calculation",
        "append_version",
        "result_projection",
        "job_acknowledgement",
        "unknown",
    }
)


@dataclass
class DailyProgress:
    stage: str = "runtime_import"


_progress: ContextVar[DailyProgress | None] = ContextVar("forecast_daily_progress", default=None)


@contextmanager
def diagnostic_scope() -> Iterator[DailyProgress]:
    progress = DailyProgress()
    token = _progress.set(progress)
    try:
        yield progress
    finally:
        _progress.reset(token)


def mark_stage(stage: str) -> None:
    progress = _progress.get()
    if progress is not None:
        progress.stage = stage if stage in STAGES else "unknown"


def safe_error_code(value: object) -> str:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value)
        else "FORECAST_DAILY_FAILED"
    )


def safe_diagnostic(value: object) -> dict[str, Any]:
    """Revalidate stored metadata before exposing it; ignore arbitrary JSON keys."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    if isinstance(value.get("stage"), str) and value["stage"] in STAGES:
        result["stage"] = value["stage"]
    identifier = value.get("diagnostic_id")
    if isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{16}", identifier):
        result["diagnostic_id"] = identifier
    exception_type = value.get("exception_type")
    if isinstance(exception_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", exception_type):
        result["exception_type"] = exception_type
    for key in ("attempt", "elapsed_ms"):
        if type(value.get(key)) is int and value[key] >= 0:
            result[key] = value[key]
    finished_at = value.get("finished_at")
    if isinstance(finished_at, str):
        try:
            timestamp = datetime.fromisoformat(finished_at)
            if timestamp.tzinfo is not None:
                result["finished_at"] = timestamp.isoformat()
        except ValueError:
            pass
    if isinstance(value.get("outcome"), str) and value["outcome"] in {
        "SUCCEEDED",
        "QUEUED",
        "FAILED",
        "CANCELLED",
        "LEASE_LOST",
    }:
        result["outcome"] = value["outcome"]
    if type(value.get("retry")) is bool:
        result["retry"] = value["retry"]
    if "error_code" in value:
        result["error_code"] = safe_error_code(value["error_code"])
    issues = value.get("blocking_issues")
    if isinstance(issues, list):
        checked = []
        for issue in issues[:12]:
            if not isinstance(issue, dict):
                continue
            code, count, season = issue.get("code"), issue.get("count"), issue.get("season")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
                continue
            if type(count) is not int or not 0 < count <= 1_000_000:
                continue
            checked.append(
                {
                    "code": code,
                    "count": count,
                    **({"season": season} if season in ("暑假", "秋季", "寒假", "春季") else {}),
                }
            )
        if checked:
            result["blocking_issues"] = checked
    return result


def blocking_issue_summary(calculation: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain the reason for rejection, without business rows, amounts or exception text."""
    counts = Counter(
        (issue.get("code"), issue.get("season"))
        for issue in calculation.get("issues", [])
        if isinstance(issue, dict)
        and issue.get("severity") == "BLOCKING"
        and isinstance(issue.get("code"), str)
        and (issue.get("season") is None or isinstance(issue.get("season"), str))
    )
    return cast(
        list[dict[str, Any]],
        safe_diagnostic(
            {
                "blocking_issues": [
                    {"code": code, "season": season, "count": count}
                    for (code, season), count in counts.most_common(12)
                ]
            }
        ).get("blocking_issues", []),
    )


def attempt_history(result: object) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or not isinstance(result.get("attempts"), list):
        return []
    # Settings bound jobs to at most five attempts. Never propagate old payloads.
    return [record for value in result["attempts"][-5:] if (record := safe_diagnostic(value))]
