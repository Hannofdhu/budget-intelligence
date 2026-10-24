"""AI boundary contracts use an in-memory runtime: no Docker, model or product DB."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.api.routes import ai_agent as route
from packages.ai_agent.bridge import BridgeHandler
from packages.ai_agent.config import ModelConfig, ModelConfigError, ProviderConfig
from packages.ai_agent.runtime import RuntimeErrorSafe, RuntimeManager, Session

ORIGIN = "http://127.0.0.1:5188"
PREFIX = "/api/v1/ai-agent"
HEADERS = {
    "host": "127.0.0.1:5188",
    "x-budget-client-address": "127.0.0.1",
    "x-budget-ai-proxy": "fixture-proxy-secret",
}
MUTATION_HEADERS = {"origin": ORIGIN}


class FakeRuntime(RuntimeManager):
    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.writes: list[tuple[str, str]] = []
        self.resizes: list[tuple[str, int, int]] = []
        self.stops: list[str] = []

    async def status(self) -> dict[str, Any]:
        return {"available": True, "isolated": True, "pi_version": "1.2.0"}

    async def start(
        self,
        *,
        cols: int = 100,
        rows: int = 30,
        mode: str = "terminal",
        workspace_id: str | None = None,
    ) -> Session:
        session = Session(
            f"session_{len(self.sessions) + 1}",
            Path("/unused-fixture"),
            status="running",
            cols=cols,
            rows=rows,
            mode=mode,
            workspace_id=workspace_id or "",
        )
        self.sessions.append(session)
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.sessions]

    def get(self, session_id: str) -> Session:
        for session in self.sessions:
            if session.id == session_id:
                return session
        raise RuntimeErrorSafe("AI 会话不存在或已过期。")

    async def stop(self, session_id: str) -> None:
        self.stops.append(session_id)
        self.get(session_id).status = "stopped"

    async def output(self, session_id: str, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "output", "seq": after + 1, "data": "Pi isolated fixture"}
        await asyncio.Event().wait()

    async def write(self, session_id: str, data: str) -> None:
        self.writes.append((session_id, data))

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        self.resizes.append((session_id, cols, rows))


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        ai_agent_enabled=True,
        environment="local",
        ai_agent_browser_origin=ORIGIN,
        ai_agent_proxy_secret="fixture-proxy-secret",
        local_trial_department_id="fixture-department",
    )


AiApiFixture = tuple[FastAPI, TestClient, route.AiService, FakeRuntime, SimpleNamespace]


@pytest.fixture
def ai_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[AiApiFixture]:
    settings = _settings()
    monkeypatch.setattr(route, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    runtime = FakeRuntime()
    service = route.AiService(app)
    service.runtime = runtime
    config = ModelConfig(
        {
            "fixture": ProviderConfig(
                "fixture",
                "openai-completions",
                "https://private-gateway.test/v1",
                "private-fixture-key",
                {},
                {"test-model": {"name": "Fixture"}},
            )
        },
        "fixture/test-model",
        (),
    )
    service.handler = BridgeHandler(config, service.read_budget)
    app.state.ai_agent_service = service
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as client:
        yield app, client, service, runtime, settings


def _start(client: TestClient) -> str:
    assert client.get(PREFIX + "/status").status_code == 200
    response = client.post(PREFIX + "/sessions", headers=MUTATION_HEADERS)
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    assert isinstance(session_id, str)
    return session_id


def _ticket(client: TestClient, session_id: str) -> str:
    response = client.post(PREFIX + f"/sessions/{session_id}/attach-ticket", headers=MUTATION_HEADERS)
    assert response.status_code == 200, response.text
    ticket = response.json()["ticket"]
    assert isinstance(ticket, str)
    return ticket


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.test"},
        {"origin": "https://evil.test"},
        {"x-budget-client-address": "192.168.1.3"},
        {"x-budget-ai-proxy": "wrong"},
        {"x-budget-ai-proxy": ""},
        {"sec-fetch-site": "cross-site"},
    ],
)
def test_status_rejects_untrusted_host_origin_proxy_and_remote_client(
    ai_api: AiApiFixture,
    headers: dict[str, str],
) -> None:
    _, client, _, runtime, _ = ai_api
    response = client.get(PREFIX + "/status", headers=headers)
    assert response.status_code == 403 and runtime.sessions == []
    assert "private-fixture-key" not in response.text


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("ai_agent_enabled", False),
        ("environment", "production"),
    ],
)
def test_agent_is_not_available_outside_explicit_local_mode(
    ai_api: AiApiFixture,
    attribute: str,
    value: object,
) -> None:
    _, client, _, _, settings = ai_api
    setattr(settings, attribute, value)
    assert client.get(PREFIX + "/status").status_code == 404


async def test_proxy_proof_and_loopback_claim_cannot_override_actual_remote_peer(
    ai_api: AiApiFixture,
) -> None:
    app, _, _, runtime, _ = ai_api
    transport = httpx.ASGITransport(app=app, client=("192.168.1.99", 32100))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        response = await client.get(PREFIX + "/status", headers=HEADERS)
    assert response.status_code == 403 and runtime.sessions == []


@pytest.mark.parametrize("origin", ["https://127.0.0.1:5188", "http://192.168.1.3:5188"])
def test_nonlocal_or_unsupported_origin_configuration_fails_closed(
    ai_api: AiApiFixture,
    origin: str,
) -> None:
    _, client, _, _, settings = ai_api
    settings.ai_agent_browser_origin = origin
    assert client.get(PREFIX + "/status").status_code == 503


def test_status_is_nostore_and_sets_httponly_strict_scoped_cookie_without_secrets(
    ai_api: AiApiFixture,
) -> None:
    _, client, _, _, _ = ai_api
    response = client.get(PREFIX + "/status")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and f"Path={PREFIX}" in cookie
    assert "private-fixture-key" not in response.text and "private-gateway.test" not in response.text
    assert response.json()["permissions"] == {"budget": "read-only", "source_files": "none"}
    assert response.json()["default_model"] == "fixture/test-model"


def test_start_requires_both_browser_cookie_and_exact_origin(ai_api: AiApiFixture) -> None:
    _, client, _, runtime, _ = ai_api
    assert client.post(PREFIX + "/sessions", headers=MUTATION_HEADERS).status_code == 401
    client.get(PREFIX + "/status")
    assert client.post(PREFIX + "/sessions").status_code == 403
    assert client.post(PREFIX + "/sessions", headers={"origin": "http://localhost:5188"}).status_code == 403
    assert runtime.sessions == []
    assert client.post(PREFIX + "/sessions", headers=MUTATION_HEADERS).status_code == 201


def test_browser_ownership_filters_session_list_and_blocks_other_browser_mutation(
    ai_api: AiApiFixture,
) -> None:
    app, first, _, runtime, _ = ai_api
    session_id = _start(first)
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as second:
        response = second.get(PREFIX + "/status")
        assert response.json()["sessions"] == []
        assert second.delete(PREFIX + f"/sessions/{session_id}", headers=MUTATION_HEADERS).status_code == 404
        assert (
            second.post(
                PREFIX + f"/sessions/{session_id}/attach-ticket",
                headers=MUTATION_HEADERS,
            ).status_code
            == 404
        )
    assert runtime.stops == []
    assert first.get(PREFIX + "/status").json()["sessions"][0]["id"] == session_id


def test_expired_browser_cannot_mutate_old_session(ai_api: AiApiFixture) -> None:
    _, client, service, runtime, _ = ai_api
    session_id = _start(client)
    cookie = client.cookies.get(route.COOKIE)
    assert cookie is not None
    service.browsers[cookie] = time.monotonic() - 1
    assert client.delete(PREFIX + f"/sessions/{session_id}", headers=MUTATION_HEADERS).status_code == 401
    assert runtime.stops == []


def test_attach_ticket_is_one_use_and_not_in_websocket_url(ai_api: AiApiFixture) -> None:
    _, client, service, _, _ = ai_api
    session_id = _start(client)
    ticket = _ticket(client, session_id)
    url = f"ws://127.0.0.1:5188{PREFIX}/sessions/{session_id}/terminal"
    with client.websocket_connect(url, headers=MUTATION_HEADERS) as websocket:
        websocket.send_json({"type": "attach", "ticket": ticket, "after": 0})
        assert websocket.receive_json() == {"type": "state", "state": "running"}
        assert websocket.receive_json()["type"] == "output"
        websocket.close()
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()
    assert ticket not in service.tickets
    with client.websocket_connect(url, headers=MUTATION_HEADERS) as websocket:
        websocket.send_json({"type": "attach", "ticket": ticket, "after": 0})
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1008


def test_ticket_creation_is_bounded_and_expired_tickets_are_pruned(ai_api: AiApiFixture) -> None:
    _, client, service, _, _ = ai_api
    session_id = _start(client)
    owner = client.cookies.get(route.COOKIE)
    assert owner is not None
    expiry = time.monotonic() + 20
    service.tickets = {str(i): (owner, session_id, expiry) for i in range(128)}
    path = PREFIX + f"/sessions/{session_id}/attach-ticket"
    assert client.post(path, headers=MUTATION_HEADERS).status_code == 429
    service.tickets = {str(i): (owner, session_id, time.monotonic() - 1) for i in range(128)}
    assert client.post(path, headers=MUTATION_HEADERS).status_code == 200
    assert len(service.tickets) == 1


@pytest.mark.parametrize("invalid", ["expired", "wrong-session", "invalid-after", "wrong-type"])
def test_websocket_rejects_invalid_attach_credentials(ai_api: AiApiFixture, invalid: str) -> None:
    _, client, service, _, _ = ai_api
    session_id = _start(client)
    ticket = _ticket(client, session_id)
    owner, _, expiry = service.tickets[ticket]
    if invalid == "expired":
        service.tickets[ticket] = (owner, session_id, time.monotonic() - 1)
    elif invalid == "wrong-session":
        service.tickets[ticket] = (owner, "another-session", expiry)
    hello = {
        "type": "wrong" if invalid == "wrong-type" else "attach",
        "ticket": ticket,
        "after": -1 if invalid == "invalid-after" else 0,
    }
    url = f"ws://127.0.0.1:5188{PREFIX}/sessions/{session_id}/terminal"
    with client.websocket_connect(url, headers=MUTATION_HEADERS) as websocket:
        websocket.send_json(hello)
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1008


def test_websocket_rejects_bad_origin_before_attach(ai_api: AiApiFixture) -> None:
    _, client, _, _, _ = ai_api
    session_id = _start(client)
    url = f"ws://127.0.0.1:5188{PREFIX}/sessions/{session_id}/terminal"
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(url, headers={"origin": "https://evil.test"}):
            pytest.fail("Untrusted origin must never attach")
    assert error.value.code == 1008


def test_oversized_terminal_input_never_reaches_runtime(ai_api: AiApiFixture) -> None:
    _, client, _, runtime, _ = ai_api
    session_id = _start(client)
    ticket = _ticket(client, session_id)
    url = f"ws://127.0.0.1:5188{PREFIX}/sessions/{session_id}/terminal"
    with client.websocket_connect(url, headers=MUTATION_HEADERS) as websocket:
        websocket.send_json({"type": "attach", "ticket": ticket})
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json({"type": "input", "data": "a" * 65537})
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()
    assert runtime.writes == []


@pytest.mark.parametrize("failure", [None, RuntimeErrorSafe, OSError, ValueError])
@pytest.mark.parametrize("emitted_output", [False, True])
def test_output_failure_or_early_end_closes_websocket_without_waiting_for_input(
    ai_api: AiApiFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception] | None,
    emitted_output: bool,
) -> None:
    _, client, _, runtime, _ = ai_api
    session_id = _start(client)
    ticket = _ticket(client, session_id)

    async def interrupted(session_id: str, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        if emitted_output:
            yield {"type": "output", "seq": after + 1, "data": "last visible frame"}
        if failure is not None:
            raise failure("synthetic output interruption")

    monkeypatch.setattr(runtime, "output", interrupted)
    url = f"ws://127.0.0.1:5188{PREFIX}/sessions/{session_id}/terminal"
    with client.websocket_connect(url, headers=MUTATION_HEADERS) as websocket:
        websocket.send_json({"type": "attach", "ticket": ticket})
        assert websocket.receive_json() == {"type": "state", "state": "running"}
        if emitted_output:
            assert websocket.receive_json()["data"] == "last visible frame"
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1000
    assert runtime.writes == [] and runtime.stops == []


def test_stopped_session_attach_reports_stopped_without_fake_running_state(ai_api: AiApiFixture) -> None:
    _, client, _, runtime, _ = ai_api
    session_id = _start(client)
    ticket = _ticket(client, session_id)
    runtime.get(session_id).status = "stopped"
    url = f"ws://127.0.0.1:5188{PREFIX}/sessions/{session_id}/terminal"
    with client.websocket_connect(url, headers=MUTATION_HEADERS) as websocket:
        websocket.send_json({"type": "attach", "ticket": ticket})
        assert websocket.receive_json() == {"type": "state", "state": "stopped"}
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1000
    assert runtime.writes == [] and runtime.resizes == []


def test_pruned_session_attach_closes_without_fake_running_state(ai_api: AiApiFixture) -> None:
    _, client, _, runtime, _ = ai_api
    session_id = _start(client)
    ticket = _ticket(client, session_id)
    runtime.sessions.clear()
    url = f"ws://127.0.0.1:5188{PREFIX}/sessions/{session_id}/terminal"
    with client.websocket_connect(url, headers=MUTATION_HEADERS) as websocket:
        websocket.send_json({"type": "attach", "ticket": ticket})
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1000
    assert runtime.writes == []


def test_runtime_start_failure_does_not_echo_host_paths_keys_or_stderr(
    ai_api: AiApiFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client, _, runtime, _ = ai_api

    async def fail() -> None:
        raise RuntimeError("private-fixture-key /Users/hann/.openclaw/private")

    monkeypatch.setattr(runtime, "start", fail)
    client.get(PREFIX + "/status")
    response = client.post(PREFIX + "/sessions", headers=MUTATION_HEADERS)
    assert response.status_code == 503
    assert "private-fixture-key" not in response.text and "/Users/hann" not in response.text


def test_configuration_failure_returns_only_safe_setup_message(
    ai_api: AiApiFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client, service, _, _ = ai_api
    service.runtime = None

    def fail() -> None:
        raise ModelConfigError("private-fixture-key https://private-gateway.test")

    monkeypatch.setattr(service, "_build_manager", fail)
    response = client.get(PREFIX + "/status")
    assert response.status_code == 200 and not response.json()["available"]
    assert "private-fixture-key" not in response.text and "private-gateway.test" not in response.text


@pytest.mark.parametrize(
    "resource",
    [
        "https://evil.test",
        "fy28-budget-version:../private",
        "fy28-budget-version:id?secret=1",
        "fy28-budget-version:id.with.dots",
        "unknown-resource",
    ],
)
async def test_budget_reader_rejects_arbitrary_resources_before_asgi(
    resource: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    service = route.AiService(app)
    monkeypatch.setattr(route, "get_settings", _settings)
    with pytest.raises(ValueError):
        await service.read_budget(resource)


async def test_budget_reader_only_gets_existing_product_resources_with_server_owned_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    observed: list[dict[str, Any]] = []

    @app.get("/api/v1/forecasts/fy27/current")
    async def current(request: Request) -> dict[str, Any]:
        observed.append({"method": request.method, "headers": dict(request.headers)})
        return {"history": [{"id": "saved-version"}], "version": {"id": "current"}}

    monkeypatch.setattr(route, "get_settings", _settings)
    service = route.AiService(app)
    result = await service.read_budget("fy27-forecast-versions")
    assert result == {"history": [{"id": "saved-version"}]}
    assert observed[0]["method"] == "GET"
    assert observed[0]["headers"]["x-user-id"] == "local-budget-owner"
    assert observed[0]["headers"]["x-department-id"] == "fixture-department"
    assert observed[0]["headers"]["x-budget-client-address"] == "127.0.0.1"


async def test_budget_help_uses_product_revenue_terminology() -> None:
    result = await route.AiService(FastAPI()).read_budget("help")
    rules = result["rules"]
    assert any("将预算业务范围统一称为 SKU；内部字段、工具和数据键保持原样" in rule for rule in rules)
    assert any(
        "季度和全年预算、预测中的对应收入指标统一称为“结转收入”，保留原单位" in rule
        and "不要与业务收入（含税）、原价收入或结转比例混称" in rule
        for rule in rules
    )


async def test_cockpit_reader_preserves_both_exact_pins_and_server_owned_get_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    observed: list[dict[str, Any]] = []

    @app.get("/api/v1/budget-cockpit")
    async def cockpit(request: Request) -> dict[str, Any]:
        observed.append(
            {
                "method": request.method,
                "params": dict(request.query_params),
                "department": request.headers.get("x-department-id"),
            }
        )
        return {"read_only": True, "sources": {"forecast": {"id": "p26"}}}

    monkeypatch.setattr(route, "get_settings", _settings)
    result = await route.AiService(app).read_budget("cockpit:p26:n25")
    assert result["read_only"] is True
    assert observed == [
        {
            "method": "GET",
            "params": {"forecast_version": "p26", "next_year_version": "n25"},
            "department": "fixture-department",
        }
    ]
    for resource in ("cockpit:p26", "cockpit:p26:n25:extra", "cockpit:../p26:n25", "cockpit:p26:n25?write=1"):
        with pytest.raises(ValueError):
            await route.AiService(app).read_budget(resource)
    assert len(observed) == 1
