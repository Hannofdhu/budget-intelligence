"""Read the local OpenClaw model catalogue without persisting its credentials."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

_PROVIDER_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_ENV_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))\Z")
_MODEL_FIELDS = {"name", "reasoning", "input", "contextWindow", "maxTokens", "cost", "api"}
_COMPAT_FIELDS = {
    "supportsStore",
    "supportsDeveloperRole",
    "supportsReasoningEffort",
    "reasoningEffortMap",
    "supportsUsageInStreaming",
    "maxTokensField",
    "requiresToolResultName",
    "requiresAssistantAfterToolResult",
    "requiresThinkingAsText",
    "thinkingFormat",
    "supportsStrictMode",
    "requiresReasoningContentOnAssistantMessages",
}
_BLOCKED_HEADERS = {"authorization", "host", "content-length", "connection", "transfer-encoding"}
_COMPAT_ENUMS = {
    "maxTokensField": {"max_completion_tokens", "max_tokens"},
    "thinkingFormat": {"openai", "openrouter", "deepseek", "together", "zai", "qwen", "qwen-chat-template"},
}


class ModelConfigError(ValueError):
    """A deliberately credential-free configuration error."""


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    api: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    headers: dict[str, str] = field(repr=False)
    models: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ModelConfig:
    providers: dict[str, ProviderConfig]
    default_model: str
    fallbacks: tuple[str, ...]

    def public_status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "default_model": self.default_model,
            "fallbacks": list(self.fallbacks),
            "providers": [{"id": p.id, "api": p.api} for p in self.providers.values()],
            "models": [
                {
                    "id": f"{p.id}/{model_id}",
                    "provider": p.id,
                    "model": model_id,
                    "name": model.get("name", model_id),
                    "api": model.get("api", p.api),
                    "reasoning": model.get("reasoning", False),
                    "input": model.get("input", ["text"]),
                }
                for p in self.providers.values()
                for model_id, model in p.models.items()
            ],
        }

    def pi_models(self) -> dict[str, Any]:
        return {
            "providers": {
                p.id: {
                    "api": p.api,
                    "baseUrl": f"http://127.0.0.1:4081/model/{quote(p.id, safe='')}/v1",
                    "apiKey": "budget-agent-bridge",
                    # The loopback bridge hides upstream auto-detection from Pi.
                    # OpenClaw's compatible gateways accept system messages,
                    # not necessarily OpenAI's newer developer role. Explicit
                    # per-model compatibility still takes precedence.
                    "models": [
                        dict(
                            model,
                            id=model_id,
                            compat={"supportsDeveloperRole": False, **model.get("compat", {})},
                        )
                        for model_id, model in p.models.items()
                    ],
                }
                for p in self.providers.values()
            }
        }

    def pi_settings(self) -> dict[str, Any]:
        provider, model = self.default_model.split("/", 1)
        order = dict.fromkeys(
            (
                self.default_model,
                *self.fallbacks,
                *(f"{p.id}/{model_id}" for p in self.providers.values() for model_id in p.models),
            )
        )
        return {"defaultProvider": provider, "defaultModel": model, "enabledModels": list(order)}

    def has_model(self, reference: str) -> bool:
        provider, separator, model = reference.partition("/")
        return bool(separator and provider in self.providers and model in self.providers[provider].models)


def _credential(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value.startswith("!"):
        raise ModelConfigError("模型凭据未配置或使用了不支持的凭据来源。")
    match = _ENV_REFERENCE.fullmatch(value)
    if match:
        resolved = os.environ.get(match.group(1) or match.group(2), "")
        if not resolved:
            raise ModelConfigError("模型凭据环境变量尚未配置。")
        return resolved
    return value


def _model(model: Any, api: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"]:
        raise ModelConfigError("模型定义不完整。")
    if len(model["id"]) > 256 or any(ord(c) < 32 for c in model["id"]):
        raise ModelConfigError("模型标识无效。")
    result = {key: model[key] for key in _MODEL_FIELDS if key in model}
    result.setdefault("api", api)
    if result["api"] != "openai-completions":
        raise ModelConfigError("当前隔离桥接仅支持 OpenAI Chat Completions 协议。")
    if "compat" in model:
        if not isinstance(model["compat"], dict):
            raise ModelConfigError("模型兼容参数无效。")
        compat = {k: v for k, v in model["compat"].items() if k in _COMPAT_FIELDS}
        for key, value in compat.items():
            if key in _COMPAT_ENUMS:
                if not isinstance(value, str) or value not in _COMPAT_ENUMS[key]:
                    raise ModelConfigError("模型兼容参数无效。")
            elif key == "reasoningEffortMap":
                if not isinstance(value, dict) or any(
                    k not in {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
                    or not isinstance(v, str)
                    for k, v in value.items()
                ):
                    raise ModelConfigError("模型兼容参数无效。")
            elif type(value) is not bool:
                raise ModelConfigError("模型兼容参数无效。")
        result["compat"] = compat
    if "input" in result and (
        not isinstance(result["input"], list)
        or not result["input"]
        or any(v not in {"text", "image"} for v in result["input"])
    ):
        raise ModelConfigError("模型输入类型无效。")
    for key in ("contextWindow", "maxTokens"):
        if key in result and (type(result[key]) is not int or result[key] <= 0):
            raise ModelConfigError("模型容量参数无效。")
    if "name" in result and not isinstance(result["name"], str):
        raise ModelConfigError("模型名称无效。")
    if "reasoning" in result and type(result["reasoning"]) is not bool:
        raise ModelConfigError("模型推理参数无效。")
    if "cost" in result:
        cost = result["cost"]
        names = ("input", "output", "cacheRead", "cacheWrite")
        if not isinstance(cost, dict) or any(
            type(cost.get(k, 0)) not in (int, float)
            or not math.isfinite(cost.get(k, 0))
            or cost.get(k, 0) < 0
            for k in names
        ):
            raise ModelConfigError("模型计费参数无效。")
        result["cost"] = {k: cost.get(k, 0) for k in names}
    return model["id"], result


def load_model_config(path: Path | None = None) -> ModelConfig:
    source = path if path is not None else Path.home() / ".openclaw" / "openclaw.json"
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        provider_values = raw["models"]["providers"]
        defaults = raw["agents"]["defaults"]["model"]
        if not isinstance(provider_values, dict) or not provider_values:
            raise ModelConfigError("尚未配置模型服务。")
        providers: dict[str, ProviderConfig] = {}
        for provider_id, value in provider_values.items():
            if not _PROVIDER_ID.fullmatch(provider_id) or not isinstance(value, dict):
                raise ModelConfigError("模型服务标识无效。")
            api = value.get("api", "openai-completions")
            if api != "openai-completions":
                raise ModelConfigError("当前隔离桥接仅支持 OpenAI Chat Completions 协议。")
            base_url = value.get("baseUrl", "")
            url = urlsplit(base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or any(ord(c) < 32 for c in base_url)
            ):
                raise ModelConfigError("模型服务地址无效。")
            raw_headers = value.get("headers", {})
            if not isinstance(raw_headers, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) or "\r" in k + v or "\n" in k + v
                for k, v in raw_headers.items()
            ):
                raise ModelConfigError("模型服务请求头无效。")
            headers = {k: v for k, v in raw_headers.items() if k.lower() not in _BLOCKED_HEADERS}
            model_values = value.get("models", [])
            if not isinstance(model_values, list) or not model_values:
                raise ModelConfigError("模型服务尚未定义可用模型。")
            models = dict(_model(model, api) for model in model_values)
            if len(models) != len(model_values):
                raise ModelConfigError("模型标识重复。")
            providers[provider_id] = ProviderConfig(
                provider_id,
                api,
                base_url.rstrip("/"),
                _credential(value.get("apiKey")),
                headers,
                models,
            )
        primary = defaults if isinstance(defaults, str) else defaults.get("primary", "")
        fallbacks = defaults.get("fallbacks", []) if isinstance(defaults, dict) else []
        if (
            not isinstance(primary, str)
            or not isinstance(fallbacks, list)
            or any(not isinstance(v, str) for v in fallbacks)
        ):
            raise ModelConfigError("默认模型或备用顺序无效。")
        config = ModelConfig(providers, primary, tuple(dict.fromkeys(fallbacks)))
        if not config.has_model(primary) or any(not config.has_model(v) for v in config.fallbacks):
            raise ModelConfigError("默认模型或备用模型不在当前可用列表中。")
        return config
    except ModelConfigError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ModelConfigError("无法读取本机模型配置，请检查 OpenClaw 模型设置。") from None
