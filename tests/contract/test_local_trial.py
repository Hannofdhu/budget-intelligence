import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app
from packages.config.settings import Settings, get_settings


@pytest.mark.parametrize(
    "enabled,environment,database,department,expected",
    [
        (False, "local", "budget_acceptance_local_trial", "opening-api-example", 404),
        (True, "staging", "budget_acceptance_local_trial", "opening-api-example", 404),
        (True, "local", "budget", "opening-api-example", 404),
        (True, "local", "budget_acceptance_local_trial", "real-department", 404),
        (True, "local", "budget_acceptance_local_trial", "opening-api-example", 200),
    ],
)
async def test_trial_bootstrap_is_explicitly_gated(
    enabled: bool, environment: str, database: str, department: str, expected: int
) -> None:
    app = create_app()
    settings = Settings.model_validate(
        {
            "environment": environment,
            "local_trial_enabled": enabled,
            "local_trial_department_id": department,
            "database_url": f"postgresql+asyncpg://budget:budget@127.0.0.1:54329/{database}",
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings
    from unittest.mock import patch

    with patch("apps.api.routes.local_trial.get_settings", return_value=settings):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/local-trial/session")
            assert response.status_code == expected
