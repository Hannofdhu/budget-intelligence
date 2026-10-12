"""Persist the accepted local FY27 budget without changing Excel or existing versions.

Default is a read-only dry run. --apply requires a recorded actor and reason.
Uses the dedicated desktop trial database, never seeds or resets it.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.local_trial import DATABASE_URL, FIXTURE, environment  # noqa: E402


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from packages.config.settings import get_settings
    from packages.persistence.database import dispose_engine, get_session_factory
    from packages.source_snapshot.fy27_review import EXTRACTION, SOURCE
    from packages.source_snapshot.historical_archive import (
        import_archive,
        prepare_archive,
        read_database_review,
        verify_database_archive,
    )

    settings = get_settings()
    if settings.database_url != DATABASE_URL or settings.environment != "local":
        raise ValueError("仅允许向现有专用本机试用数据库导入 FY27")
    fixture = json.loads(FIXTURE.read_bytes())
    department = fixture["department_id"]
    prepared = prepare_archive(SOURCE, EXTRACTION, EXTRACTION.parent / "accepted-source.json")
    receipt = {
        "apply": args.apply,
        "department_id": department,
        "baseline_hash": prepared.baseline_hash,
        "source_file_hash": prepared.source_file_hash,
        "scope_count": prepared.review["scope_count"],
        "season_count": prepared.review["season_count"],
        "annual_revenue": prepared.review["annual_revenue"],
        "entry_total": prepared.review["entry_total"],
    }
    if not args.apply:
        return receipt
    try:
        async with get_session_factory()() as session, session.begin():
            archive_id, created = await import_archive(
                session, prepared, department_id=department, actor=args.actor, reason=args.reason
            )
            receipt.update(await verify_database_archive(session, archive_id))
            receipt["created"] = created
        loaded = await read_database_review(department)
        if loaded != prepared.review:
            raise ValueError("数据库读取结果未与导入前结果对齐")
        return receipt
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    args = parser.parse_args()
    if args.apply and (not args.actor or not args.reason):
        parser.error("--apply requires --actor and --reason")
    # Match the running dedicated trial, not the repository's default database.
    os.environ.update(environment())
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
