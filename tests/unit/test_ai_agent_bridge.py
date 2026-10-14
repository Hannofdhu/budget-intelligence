import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from packages.ai_agent.bridge import BridgeHandler
from packages.ai_agent.config import ModelConfig, ProviderConfig


def _config() -> ModelConfig:
    return ModelConfig(
        {
            "google": ProviderConfig(
                "google",
                "openai-completions",
                "https://models.test/v1",
                "private-test-key",
                {"User-Agent": "expected-agent"},
                {"primary": {}, "backup": {}},
            ),
            "custom": ProviderConfig(
                "custom",
                "openai-completions",
                "https://backup.test/v1",
                "private-backup-key",
                {},
                {"third": {}},
            ),
        },
        "google/primary",
        ("google/backup", "custom/third"),
    )


async def _budget(resource: str) -> dict[str, Any]:
    return {"resource": resource, "read_only": True}


def _request(model: str = "primary", *, provider: str = "google", stream: bool = True) -> dict[str, Any]:
    body = json.dumps({"model": model, "stream": stream, "messages": [{"role": "user", "content": "Hi"}]})
    return {
        "id": "request-1",
        "method": "POST",
        "path": f"/model/{provider}/v1/chat/completions",
        "headers": {"Authorization": "client-credential", "User-Agent": "untrusted-client"},
        "body": base64.b64encode(body.encode()).decode(),
    }


def _body(events: list[dict[str, Any]]) -> bytes:
    return b"".join(base64.b64decode(v["data"]) for v in events if v["type"] == "response_chunk")


async def test_proxy_streams_and_replaces_auth_without_forwarding_client_headers() -> None:
    observed: list[httpx.Request] = []

    async def serve(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            content=b"data: hello\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream", "set-cookie": "private-cookie"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        bridge = BridgeHandler(_config(), _budget, client=client)
        events = [v async for v in bridge.handle(_request())]
    assert events[0]["status"] == 200 and events[-1]["type"] == "response_end"
    assert _body(events).startswith(b"data: hello")
    assert observed[0].headers["Authorization"] == "Bearer private-test-key"
    assert observed[0].headers["User-Agent"] == "expected-agent"
    assert observed[0].url == "https://models.test/v1/chat/completions"
    assert "set-cookie" not in events[0]["headers"]
    assert "private-test-key" not in json.dumps(events)
    assert bridge.last_actual_model == "google/primary"


@pytest.mark.parametrize("failure", [404, 429, 502, 503, 504, "connection", "timeout"])
async def test_primary_fallback_follows_configured_order_before_any_response_data(failure: int | str) -> None:
    called: list[str] = []

    async def serve(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        called.append(model)
        if model == "primary":
            if failure == "connection":
                raise httpx.ConnectError("private-test-key https://secret.test/?key=private")
            if failure == "timeout":
                raise httpx.ConnectTimeout("private-timeout")
            assert isinstance(failure, int)
            return httpx.Response(failure, text="private-provider-error")
        if model == "backup":
            return httpx.Response(503, text="private-provider-error")
        assert request.headers["Authorization"] == "Bearer private-backup-key"
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        bridge = BridgeHandler(_config(), _budget, client=client)
        events = [v async for v in bridge.handle(_request())]
    assert called == ["primary", "backup", "third"]
    assert events[0]["status"] == 200 and _body(events) == b'{"ok":true}'
    assert bridge.last_actual_model == "custom/third"
    assert "private" not in json.dumps(events)


@pytest.mark.parametrize("status", [400, 401, 403, 500])
async def test_non_retryable_failure_does_not_change_selected_model_or_echo_upstream_error(
    status: int,
) -> None:
    called: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        called.append(json.loads(request.content)["model"])
        return httpx.Response(status, text="Authorization: private-test-key https://secret.test")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        events = [v async for v in BridgeHandler(_config(), _budget, client=client).handle(_request())]
    assert called == ["primary"] and events[0]["status"] == status
    assert "private-test-key" not in _body(events).decode() and "secret.test" not in _body(events).decode()


async def test_explicit_nonprimary_selection_never_silently_switches_models() -> None:
    called: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        called.append(json.loads(request.content)["model"])
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        bridge = BridgeHandler(_config(), _budget, client=client)
        events = [v async for v in bridge.handle(_request("backup"))]
    assert called == ["backup"] and events[0]["status"] == 503


class _BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"data: first-token\n\n"
        raise httpx.ReadTimeout("private-stream-error")


class _EmptyTimedOutStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx.ReadTimeout("private-first-token-timeout")
        yield b""  # pragma: no cover - retain the AsyncIterator protocol.


async def test_timeout_before_first_stream_byte_can_use_backup_without_emitting_primary_headers() -> None:
    called: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        called.append(model)
        if model == "primary":
            return httpx.Response(200, stream=_EmptyTimedOutStream())
        return httpx.Response(200, content=b"backup-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        bridge = BridgeHandler(_config(), _budget, client=client)
        events = [v async for v in bridge.handle(_request())]
    assert called == ["primary", "backup"] and _body(events) == b"backup-token"
    assert len([v for v in events if v["type"] == "response_start"]) == 1
    assert bridge.last_actual_model == "google/backup"


async def test_stream_failure_after_first_data_does_not_retry_or_duplicate_output() -> None:
    called: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        called.append(json.loads(request.content)["model"])
        return httpx.Response(200, stream=_BrokenStream(), headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        events = [v async for v in BridgeHandler(_config(), _budget, client=client).handle(_request())]
    assert called == ["primary"] and _body(events).count(b"first-token") == 1
    assert events[-1]["type"] == "response_end" and "private" not in json.dumps(events)


@pytest.mark.parametrize(
    "path,method,status",
    [
        ("https://evil.test/v1/chat/completions", "POST", 404),
        ("/model/google/v1/chat/completions?url=https://evil.test", "POST", 404),
        ("/model/google/v1/chat/completions", "GET", 405),
        ("/model/unknown/v1/chat/completions", "POST", 400),
        ("/budget/fy28-budget", "POST", 405),
        ("/budget/../secrets", "GET", 404),
        ("/budget/unknown", "GET", 404),
        ("/budget/fy28-budget-version:../secrets", "GET", 404),
        ("/budget/fy28-budget-version:id?query=1", "GET", 404),
        ("/budget/fy28-budget-version:id.with.dots", "GET", 404),
        ("/budget/fy28-budget-version%3A..%2Fsecrets", "GET", 404),
        ("/budget/fy28-budget-version%3Aid%3Fquery%3D1", "GET", 404),
    ],
)
async def test_allowlisted_paths_and_methods_only(path: str, method: str, status: int) -> None:
    request = dict(_request(), path=path, method=method)
    events = [v async for v in BridgeHandler(_config(), _budget).handle(request)]
    assert events[0]["status"] == status


@pytest.mark.parametrize(
    "body,status",
    [
        ("not-base64", 400),
        (base64.b64encode(b"[]").decode(), 400),
        (base64.b64encode(b'{"model":"primary","x":NaN}').decode(), 400),
        (base64.b64encode(b'{"model":"not-configured"}').decode(), 400),
        ("a" * (12 * 1024 * 1024), 413),
    ],
)
async def test_invalid_or_oversized_model_body_is_rejected_before_network(body: str, status: int) -> None:
    events = [v async for v in BridgeHandler(_config(), _budget).handle(dict(_request(), body=body))]
    assert events[0]["status"] == status


async def test_budget_callback_receives_only_resource_and_never_mutates() -> None:
    resources: list[str] = []

    async def read(resource: str) -> dict[str, Any]:
        resources.append(resource)
        return {"rows": 12, "resource": resource}

    events = [
        v
        async for v in BridgeHandler(_config(), read).handle(
            {
                "id": 4,
                "method": "GET",
                "path": "/budget/fy28-budget",
                "headers": {"Authorization": "ignored"},
            }
        )
    ]
    assert resources == ["fy28-budget"] and events[0]["status"] == 200
    assert json.loads(_body(events)) == {"rows": 12, "resource": "fy28-budget"}


@pytest.mark.parametrize(
    "resource",
    [
        "help",
        "fy27-budget",
        "fy27-forecast",
        "fy28-budget",
        "next-year-catalog",
        "fy27-forecast-versions",
        "fy28-budget-versions",
        "fy27-forecast-version:01a1-abc_DEF",
        "fy28-budget-version:01a1-abc_DEF",
    ],
)
async def test_budget_version_resources_have_safe_identifier_whitelist(resource: str) -> None:
    events = [
        v
        async for v in BridgeHandler(_config(), _budget).handle(
            {
                "id": "version",
                "method": "GET",
                "path": f"/budget/{resource}",
            }
        )
    ]
    assert events[0]["status"] == 200
    assert json.loads(_body(events))["resource"] == resource


async def test_encoded_budget_version_resource_is_decoded_before_safe_identifier_check() -> None:
    events = [
        v
        async for v in BridgeHandler(_config(), _budget).handle(
            {
                "id": "encoded",
                "method": "GET",
                "path": "/budget/fy28-budget-version%3A01a1-abc_DEF",
            }
        )
    ]
    assert events[0]["status"] == 200
    assert json.loads(_body(events))["resource"] == "fy28-budget-version:01a1-abc_DEF"


async def test_budget_error_does_not_echo_exception() -> None:
    async def read(resource: str) -> dict[str, Any]:
        raise ValueError("private-budget-data")

    events = [
        v
        async for v in BridgeHandler(_config(), read).handle(
            {
                "id": "a",
                "method": "GET",
                "path": "/budget/help",
            }
        )
    ]
    assert events[0]["status"] == 503 and "private" not in json.dumps(events)
