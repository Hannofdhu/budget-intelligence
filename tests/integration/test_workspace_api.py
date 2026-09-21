"""The workspace projects existing contracts without inventing new command semantics."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app
from apps.api.routes.workspace import FORMS
from tests.integration.test_rebalancing_real_journey import prepare

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1", reason="requires isolated PostgreSQL"
)


async def test_workspace_roundtrips_existing_forms_and_enforces_department() -> None:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        journey = await prepare(client, 400000)
        before = await journey.get(f"revisions/{journey.revision}")
        data = await journey.get(f"revisions/{journey.revision}/workspace")
        assert data["source"]["status"] == "FROZEN"
        assert data["is_current_draft"] is True
        assert len(data["sections"]) == 15
        for section in data["sections"]:
            key = section["key"]
            if key not in FORMS or key == "planned":
                continue
            values = section["value"]
            if key in {"targets", "resources", "discount"}:
                result = await client.post(
                    f"/api/v1/workspace/forms/{key}:prepare", json=values, headers=journey.headers
                )
                assert result.status_code == 200, result.text
                values = result.json()
                assert len(values["batch_content_hash"]) == 64
            FORMS[key][3].model_validate(values)
        assert await journey.get(f"revisions/{journey.revision}") == before
        budgets = await client.get("/api/v1/workspace/budgets", headers=journey.headers)
        assert budgets.status_code == 200 and len(budgets.json()) == 1
        denied = await client.get(
            f"/api/v1/revisions/{journey.revision}/workspace",
            headers={**journey.headers, "X-Department-ID": "unrelated"},
        )
        assert denied.status_code == 404
        other = await client.get(
            "/api/v1/workspace/budgets", headers={**journey.headers, "X-Department-ID": "unrelated"}
        )
        assert other.json() == []
        invalid = await client.post(
            "/api/v1/workspace/forms/discount:prepare", json={}, headers=journey.headers
        )
        assert invalid.status_code == 422
        assert (await client.get("/api/v1/workspace/budgets")).status_code == 401
