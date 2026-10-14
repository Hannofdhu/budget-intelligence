"""Restricted HTTP relay for a network-isolated Pi process."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import unquote

import httpx

from packages.ai_agent.config import ModelConfig

_MODEL_PATH = re.compile(r"/model/([^/]+)/v1/chat/completions\Z")
_BUDGET_PATH = re.compile(
    r"/budget/(help|fy27-budget|fy27-forecast|fy28-budget|next-year-catalog|"
    r"fy27-forecast-versions|fy28-budget-versions|"
    r"(?:fy27-forecast-version|fy28-budget-version):[A-Za-z0-9_-]{1,128})\Z"
)
_MAX_BODY = 8 * 1024 * 1024
_RETRY_STATUSES = {404, 429, 502, 503, 504}
_CONNECT_FAILURES = (httpx.ConnectError, httpx.TimeoutException)
BudgetReader = Callable[[str], Awaitable[dict[str, Any]]]


class BridgeHandler:
    def __init__(
        self,
        config: ModelConfig,
        budget_reader: BudgetReader,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.budget_reader = budget_reader
        self.client = client
        self.last_actual_model: str | None = None

    async def handle(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        request_id = request.get("id", "")
        if type(request_id) not in (str, int) or len(str(request_id)) > 128:
            request_id = ""
        path = request.get("path", "")
        method = request.get("method", "")
        if not isinstance(path, str) or len(path) > 512:
            async for event in self._error(request_id, 400, "请求无效。"):
                yield event
            return
        budget = _BUDGET_PATH.fullmatch(unquote(path)) if path.startswith("/budget/") else None
        if budget:
            if method != "GET":
                async for event in self._error(request_id, 405, "预算数据仅允许读取。"):
                    yield event
                return
            try:
                result = await self.budget_reader(budget.group(1))
                payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
            except Exception:
                async for event in self._error(request_id, 503, "预算数据暂时无法读取。"):
                    yield event
                return
            async for event in self._response(request_id, 200, payload):
                yield event
            return
        match = _MODEL_PATH.fullmatch(path)
        if not match:
            async for event in self._error(request_id, 404, "不支持的代理地址。"):
                yield event
            return
        if method != "POST":
            async for event in self._error(request_id, 405, "模型请求仅允许 POST。"):
                yield event
            return
        provider = unquote(match.group(1))
        if provider not in self.config.providers:
            async for event in self._error(request_id, 400, "模型服务不在允许列表中。"):
                yield event
            return
        encoded = request.get("body", "")
        if not isinstance(encoded, str) or len(encoded) > ((_MAX_BODY + 2) // 3) * 4:
            async for event in self._error(request_id, 413, "模型请求超过大小限制。"):
                yield event
            return
        try:
            body = base64.b64decode(encoded, validate=True)
            if len(body) > _MAX_BODY:
                raise OverflowError
            payload = json.loads(body, parse_constant=self._reject_constant)
            if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
                raise ValueError
        except OverflowError:
            async for event in self._error(request_id, 413, "模型请求超过大小限制。"):
                yield event
            return
        except (ValueError, UnicodeError, binascii.Error):
            async for event in self._error(request_id, 400, "模型请求格式无效。"):
                yield event
            return
        selected = f"{provider}/{payload['model']}"
        if not self.config.has_model(selected):
            async for event in self._error(request_id, 400, "模型不在允许列表中。"):
                yield event
            return
        candidates = [selected]
        if selected == self.config.default_model:
            candidates.extend(v for v in self.config.fallbacks if v not in candidates)
        if self.client is not None:
            async for event in self._models(request_id, payload, candidates, self.client):
                yield event
            return
        timeout = httpx.Timeout(300.0, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async for event in self._models(request_id, payload, candidates, client):
                yield event

    async def _models(
        self,
        request_id: str | int,
        payload: dict[str, Any],
        candidates: list[str],
        client: httpx.AsyncClient,
    ) -> AsyncIterator[dict[str, Any]]:
        for index, reference in enumerate(candidates):
            provider_id, model_id = reference.split("/", 1)
            provider = self.config.providers[provider_id]
            model_payload = dict(payload, model=model_id)
            headers = dict(provider.headers)
            headers["Authorization"] = f"Bearer {provider.api_key}"
            headers["Content-Type"] = "application/json"
            started = False
            try:
                async with client.stream(
                    "POST",
                    f"{provider.base_url}/chat/completions",
                    headers=headers,
                    json=model_payload,
                    follow_redirects=False,
                ) as response:
                    if response.status_code not in range(200, 300):
                        if response.status_code in _RETRY_STATUSES and index + 1 < len(candidates):
                            continue
                        async for event in self._error(
                            request_id,
                            response.status_code,
                            "模型服务暂时不可用，请稍后重试。",
                        ):
                            yield event
                        return
                    content_type = response.headers.get("content-type", "application/json")
                    iterator = response.aiter_bytes()
                    first = await anext(iterator, b"")
                    self.last_actual_model = reference
                    started = True
                    yield {
                        "type": "response_start",
                        "id": request_id,
                        "status": response.status_code,
                        "headers": {"content-type": content_type},
                    }
                    if first:
                        yield self._chunk(request_id, first)
                    async for chunk in iterator:
                        yield self._chunk(request_id, chunk)
                    yield {"type": "response_end", "id": request_id}
                    return
            except httpx.HTTPError as error:
                if not started and isinstance(error, _CONNECT_FAILURES) and index + 1 < len(candidates):
                    continue
                if started:
                    if payload.get("stream") is True:
                        safe_error = b'data: {"error":{"message":"Model stream interrupted"}}\n\n'
                        yield self._chunk(request_id, safe_error)
                    yield {"type": "response_end", "id": request_id}
                else:
                    async for event in self._error(request_id, 502, "模型连接失败，请稍后重试。"):
                        yield event
                return
        async for event in self._error(request_id, 502, "所有备用模型暂时不可用。"):
            yield event

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError("Invalid JSON number")

    @staticmethod
    def _chunk(request_id: str | int, value: bytes) -> dict[str, Any]:
        return {"type": "response_chunk", "id": request_id, "data": base64.b64encode(value).decode("ascii")}

    async def _response(
        self,
        request_id: str | int,
        status: int,
        payload: bytes,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "response_start",
            "id": request_id,
            "status": status,
            "headers": {"content-type": "application/json; charset=utf-8"},
        }
        yield self._chunk(request_id, payload)
        yield {"type": "response_end", "id": request_id}

    async def _error(self, request_id: str | int, status: int, message: str) -> AsyncIterator[dict[str, Any]]:
        payload = json.dumps({"error": {"message": message}}, ensure_ascii=False).encode()
        async for event in self._response(request_id, status, payload):
            yield event
