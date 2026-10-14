import json
from pathlib import Path
from typing import Any

import pytest

from packages.ai_agent.config import ModelConfigError, load_model_config


def _source() -> dict[str, Any]:
    return {
        "gateway": {"auth": {"token": "unrelated-private-token"}},
        "models": {
            "providers": {
                "google": {
                    "api": "openai-completions",
                    "baseUrl": "https://models.test/v1",
                    "apiKey": "private-test-only-key",
                    "headers": {"User-Agent": "local-user-agent"},
                    "models": [
                        {
                            "id": "primary",
                            "name": "Primary",
                            "reasoning": True,
                            "input": ["text"],
                            "contextWindow": 2000000,
                            "maxTokens": 8192,
                            "unknown": "discard-me",
                            "headers": {"Authorization": "never-copy"},
                            "compat": {"supportsReasoningEffort": True, "unknown": "discard-me"},
                        },
                        {"id": "backup", "input": ["text", "image"]},
                    ],
                },
                "custom-provider": {
                    "api": "openai-completions",
                    "baseUrl": "https://backup.test/v1/",
                    "apiKey": "private-backup-test-key",
                    "models": [{"id": "third"}],
                },
            }
        },
        "agents": {
            "defaults": {
                "model": {
                    "primary": "google/primary",
                    "fallbacks": ["google/backup", "custom-provider/third"],
                }
            }
        },
    }


def _write(tmp_path: Path, value: dict[str, Any]) -> Path:
    path = tmp_path / "openclaw.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_only_model_configuration_is_read_and_credentials_remain_private(tmp_path: Path) -> None:
    source = _source()
    path = _write(tmp_path, source)
    config = load_model_config(path)
    serialized = json.dumps(config.public_status()) + repr(config)
    for secret in ("private-test-only-key", "private-backup-test-key", "unrelated-private-token"):
        assert secret not in serialized
    assert "models.test" not in json.dumps(config.public_status())
    assert config.default_model == "google/primary"
    assert config.fallbacks == ("google/backup", "custom-provider/third")
    assert config.providers["google"].api_key == "private-test-only-key"
    assert json.loads(path.read_text()) == source


def test_pi_only_receives_loopback_proxy_and_placeholder_credentials(tmp_path: Path) -> None:
    config = load_model_config(_write(tmp_path, _source()))
    result = config.pi_models()
    google = result["providers"]["google"]
    assert google["baseUrl"] == "http://127.0.0.1:4081/model/google/v1"
    assert google["apiKey"] == "budget-agent-bridge"
    assert "headers" not in google
    assert "headers" not in google["models"][0]
    assert "unknown" not in google["models"][0]
    assert google["models"][0]["compat"] == {
        "supportsDeveloperRole": False,
        "supportsReasoningEffort": True,
    }
    assert config.pi_settings() == {
        "defaultProvider": "google",
        "defaultModel": "primary",
        "enabledModels": ["google/primary", "google/backup", "custom-provider/third"],
    }
    serialized = json.dumps(result)
    assert "private-test-only-key" not in serialized and "models.test" not in serialized


def test_loopback_bridge_conservative_role_preserves_explicit_model_override(tmp_path: Path) -> None:
    source = _source()
    source["models"]["providers"]["google"]["models"][0]["compat"]["supportsDeveloperRole"] = True
    config = load_model_config(_write(tmp_path, source))
    models = config.pi_models()["providers"]["google"]["models"]
    assert models[0]["compat"]["supportsDeveloperRole"] is True
    assert models[1]["compat"]["supportsDeveloperRole"] is False
    assert "compat" not in source["models"]["providers"]["google"]["models"][1]


@pytest.mark.parametrize(
    "address",
    [
        "https://user:password@models.test/v1",
        "https://models.test/v1?api_key=private",
        "https://models.test/v1#private",
        "file:///etc/passwd",
        "ftp://models.test/v1",
    ],
)
def test_credential_urls_and_non_http_protocols_are_rejected(tmp_path: Path, address: str) -> None:
    source = _source()
    source["models"]["providers"]["google"]["baseUrl"] = address
    with pytest.raises(ModelConfigError) as error:
        load_model_config(_write(tmp_path, source))
    assert address not in str(error.value) and "private" not in str(error.value)


@pytest.mark.parametrize("credential", [None, "", {"source": "exec", "id": "private"}, "!echo private"])
def test_unsupported_credentials_never_execute_commands(tmp_path: Path, credential: object) -> None:
    source = _source()
    source["models"]["providers"]["google"]["apiKey"] = credential
    with pytest.raises(ModelConfigError):
        load_model_config(_write(tmp_path, source))


def test_environment_reference_is_resolved_only_server_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    source["models"]["providers"]["google"]["apiKey"] = "${BUDGET_TEST_MODEL_KEY}"
    monkeypatch.setenv("BUDGET_TEST_MODEL_KEY", "private-env-test-key")
    config = load_model_config(_write(tmp_path, source))
    assert config.providers["google"].api_key == "private-env-test-key"
    assert "private-env-test-key" not in json.dumps(config.pi_models())
    monkeypatch.delenv("BUDGET_TEST_MODEL_KEY")
    with pytest.raises(ModelConfigError):
        load_model_config(_write(tmp_path, source))


def test_missing_fallback_is_rejected_instead_of_silently_changing_order(tmp_path: Path) -> None:
    source = _source()
    source["agents"]["defaults"]["model"]["fallbacks"].append("google/not-configured")
    with pytest.raises(ModelConfigError):
        load_model_config(_write(tmp_path, source))


def test_invalid_json_has_no_sensitive_error_echo(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"apiKey":"private"', encoding="utf-8")
    with pytest.raises(ModelConfigError) as error:
        load_model_config(path)
    assert "private" not in str(error.value)


def test_client_controlled_security_headers_are_not_imported(tmp_path: Path) -> None:
    source = _source()
    source["models"]["providers"]["google"]["headers"] = {
        "Authorization": "never-copy",
        "Host": "evil.test",
        "Content-Length": "123",
        "User-Agent": "expected-agent",
    }
    config = load_model_config(_write(tmp_path, source))
    assert config.providers["google"].headers == {"User-Agent": "expected-agent"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("api", "anthropic-messages"),
        ("models", []),
        ("models", [{"id": "same"}, {"id": "same"}]),
        ("models", [{"id": "invalid", "input": ["audio"]}]),
        ("models", [{"id": "invalid", "maxTokens": -1}]),
    ],
)
def test_invalid_provider_metadata_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    source = _source()
    source["models"]["providers"]["google"][field] = value
    with pytest.raises(ModelConfigError):
        load_model_config(_write(tmp_path, source))
