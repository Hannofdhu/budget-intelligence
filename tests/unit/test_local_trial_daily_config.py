from pathlib import Path

import pytest

from scripts import local_trial


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("BUDGET_FORECAST_DAILY_ENABLED", raising=False)
    monkeypatch.setattr(local_trial, "DAILY_CONFIG", tmp_path / "forecast-daily.json")
    monkeypatch.setattr(local_trial, "FIXTURE", tmp_path / "fixture.json")
    monkeypatch.setattr(local_trial, "ai_proxy_secret", lambda: "synthetic-not-a-real-secret")
    return tmp_path


def test_daily_desktop_config_is_opt_in_and_requires_fixture_department(desktop: Path) -> None:
    assert local_trial.environment()["BUDGET_FORECAST_DAILY_ENABLED"] == "false"
    (desktop / "forecast-daily.json").write_text('{"enabled":true}')
    assert local_trial.environment()["BUDGET_FORECAST_DAILY_ENABLED"] == "false"
    (desktop / "fixture.json").write_text('{"department_id":"opening-api-synthetic-test"}')
    env = local_trial.environment()
    assert env["BUDGET_FORECAST_DAILY_ENABLED"] == "true"
    assert env["BUDGET_LOCAL_TRIAL_DEPARTMENT_ID"] == "opening-api-synthetic-test"
    (desktop / "forecast-daily.json").write_text('{"enabled":false}')
    assert local_trial.environment()["BUDGET_FORECAST_DAILY_ENABLED"] == "false"


def test_explicit_environment_can_disable_without_reading_local_flag(
    desktop: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (desktop / "forecast-daily.json").write_text("not JSON and must not be read")
    monkeypatch.setenv("BUDGET_FORECAST_DAILY_ENABLED", "false")
    assert local_trial.environment()["BUDGET_FORECAST_DAILY_ENABLED"] == "false"


@pytest.mark.parametrize(
    "content", ['{"enabled":"true"}', '{"enabled":1}', '{"enabled":true,"other":1}', "invalid", " " * 1025]
)
def test_desktop_flag_rejects_malformed_or_oversized_content(desktop: Path, content: str) -> None:
    (desktop / "forecast-daily.json").write_text(content)
    with pytest.raises(RuntimeError, match="配置无效"):
        local_trial.local_daily_forecast_setting()


def test_desktop_flag_rejects_symlink(desktop: Path) -> None:
    target = desktop / "target.json"
    target.write_text('{"enabled":true}')
    (desktop / "forecast-daily.json").symlink_to(target)
    with pytest.raises(RuntimeError, match="符号链接"):
        local_trial.local_daily_forecast_setting()
