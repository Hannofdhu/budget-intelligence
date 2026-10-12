"""Run one complete, read-only forecast extraction with safe query timings.

Never append or confirm a forecast. Output contains only resource aliases,
operation names, timing, row counts, fingerprints and safe error identifiers.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.local_trial import environment  # noqa: E402


def emit(**fields: Any) -> None:
    print(json.dumps(fields, ensure_ascii=False), flush=True)


async def run() -> int:
    from packages.config.settings import get_settings
    from packages.forecast.source import FINANCE_DATASET_ID, extract_forecast_source
    from packages.forecast.store import fingerprint
    from packages.source_snapshot.adapter import GuanCliSourceAdapter, SourceAdapterError

    settings = get_settings()

    class TimedAdapter(GuanCliSourceAdapter):
        def __init__(self) -> None:
            super().__init__(settings)
            self.started = 0
            self.completed = 0

        async def _run_json(self, *arguments: str) -> Any:
            self.started += 1
            query = self.started
            args = list(arguments)
            if args[:1] == ["--raw"]:
                args = args[1:]
            resource = {
                settings.summary_dataset_id: "summary",
                settings.roster_dataset_id: "roster",
                FINANCE_DATASET_ID: "finance",
            }.get(args[2] if len(args) > 2 else "", "formula")
            operation = "_".join(args[:2])
            if "--limit" in args:
                limit = args[args.index("--limit") + 1]
                operation += "_count" if limit == "0" else "_rows"
            else:
                limit = None
            started = time.monotonic()
            emit(event="query_started", query=query, resource=resource, operation=operation, limit=limit)
            try:
                result = await super()._run_json(*arguments)
            except BaseException as exc:
                emit(
                    event="query_ended",
                    query=query,
                    resource=resource,
                    operation=operation,
                    elapsed=round(time.monotonic() - started, 3),
                    status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                    code=exc.code if isinstance(exc, SourceAdapterError) else type(exc).__name__,
                )
                raise
            self.completed += 1
            emit(
                event="query_ended",
                query=query,
                resource=resource,
                operation=operation,
                elapsed=round(time.monotonic() - started, 3),
                status="success",
                returned_rows=len(result) if isinstance(result, list) else None,
            )
            return result

    adapter = TimedAdapter()
    started = time.monotonic()
    try:
        snapshot = await extract_forecast_source(settings, adapter=adapter)
    except SourceAdapterError as exc:
        emit(
            event="extraction_ended",
            status="failed",
            code=exc.code,
            diagnostic_id=exc.diagnostic_id,
            elapsed=round(time.monotonic() - started, 3),
            queries_started=adapter.started,
            queries_completed=adapter.completed,
        )
        return 1
    verified = snapshot["metric_verified"] and not snapshot["issues"]
    emit(
        event="extraction_ended",
        status="success" if verified else "complete_with_validation_issues",
        elapsed=round(time.monotonic() - started, 3),
        queries_started=adapter.started,
        queries_completed=adapter.completed,
        as_of=snapshot["as_of"],
        summary_rows=len(snapshot["source_data"]["summary_rows"]),
        finance_rows=len(snapshot["source_data"]["financial_rows"]),
        aggregate_rows=len(snapshot["rows"]),
        issues=len(snapshot["issues"]),
        metric_verified=snapshot["metric_verified"],
        source_versions_stable=snapshot["provenance"]["metadata_stable_after_extraction"],
        source_rows_hash=fingerprint(snapshot["source_data"]),
        snapshot_hash=fingerprint(snapshot),
    )
    return 0 if verified else 2


if __name__ == "__main__":
    os.environ.update(environment())
    raise SystemExit(asyncio.run(run()))
