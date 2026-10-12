"""Import historical forecast snapshots without recalculation or source refresh.

Dry run is the default. --apply requires an explicit actor and reason. The local
desktop database is the only permitted destination; no seed/reset is performed.
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


def verify_imported_versions(expected: list[dict[str, Any]], historical: list[dict[str, Any]]) -> None:
    from packages.forecast.store import fingerprint

    def immutable(version: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in version.items() if key not in ("status", "confirmation")}

    for version in expected:
        stored = next((item for item in historical if item["id"] == version["id"]), None)
        # A newer runtime confirmation may validly change the projected status;
        # the import transaction already verified the original confirmation events.
        if stored is None or fingerprint(immutable(stored)) != fingerprint(immutable(version)):
            raise ValueError("预测迁移读取结果与原始历史不一致")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from packages.config.settings import get_settings
    from packages.forecast.database_store import PostgreSQLForecastStore, import_ledger, prepare_ledger
    from packages.forecast.store import ForecastStore
    from packages.persistence.database import dispose_engine, get_session_factory

    settings = get_settings()
    if settings.database_url != DATABASE_URL or settings.environment != "local":
        raise ValueError("仅允许向现有专用本机试用数据库导入预测历史")
    department = json.loads(FIXTURE.read_bytes())["department_id"]
    prepared = prepare_ledger(department)
    before = ForecastStore()._view(prepared.events)
    receipt = {
        "apply": args.apply,
        "department_id": department,
        "event_count": len(prepared.events),
        "version_count": prepared.version_count,
        "chain_hash": prepared.chain_hash,
        "latest_version_id": before["version"]["id"] if before["version"] else None,
        "latest_snapshot_hash": before["version"]["snapshot_hash"] if before["version"] else None,
        "latest_status": before["version"]["status"] if before["version"] else None,
    }
    if not args.apply:
        return receipt
    try:
        async with get_session_factory()() as session, session.begin():
            receipt.update(await import_ledger(session, prepared, actor=args.actor, reason=args.reason))
        database = PostgreSQLForecastStore()
        historical = await database.versions(department)
        expected = list(reversed(ForecastStore._versions(prepared.events)))
        verify_imported_versions(expected, historical)
        # Preserve valid newer runtime versions if an importer is repeated after go-live.
        receipt["readback_verified"] = True
        receipt["all_imported_snapshot_hashes"] = [version["snapshot_hash"] for version in reversed(expected)]
        return receipt
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    args = parser.parse_args()
    if args.apply and (
        not args.actor or not args.actor.strip() or not args.reason or not args.reason.strip()
    ):
        parser.error("--apply requires --actor and --reason")
    os.environ.update(environment())
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
