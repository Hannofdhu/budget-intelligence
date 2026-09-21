import pytest
from pydantic import ValidationError

from packages.config.settings import Settings


def test_local_environment_can_use_development_headers() -> None:
    settings = Settings(environment="local", auth_mode="dev_headers")

    assert settings.auth_mode == "dev_headers"


def test_production_rejects_development_headers() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", auth_mode="dev_headers")
