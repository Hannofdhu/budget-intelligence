"""Create an isolated synthetic budget for real-browser acceptance (never production)."""

import asyncio
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from apps.api.main import create_app
from packages.config.settings import get_settings
from packages.persistence.database import dispose_engine
from tests.integration.test_rebalancing_real_journey import prepare


async def main() -> None:
    settings = get_settings()
    database = make_url(settings.database_url).database or ""
    if settings.environment not in ("local", "test") or not database.startswith("budget_acceptance_"):
        raise RuntimeError("Browser fixture requires a dedicated budget_acceptance_ database")
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        journey = await prepare(client, 600_000)
        await journey.unlock_and_solve()
        fixture = {
            "kind": "SYNTHETIC_BROWSER_ACCEPTANCE",
            "revision_id": journey.revision,
            "annual_budget_id": str(journey.budget_id),
            "department_id": journey.headers["X-Department-ID"],
            "user_id": journey.headers["X-User-ID"],
            "url": f"http://127.0.0.1:5173/budget/rebalancing?revision={journey.revision}",
        }
        destination = Path("output/acceptance/browser-fixture.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            destination.write_text, json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(fixture, ensure_ascii=False))
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
