"""Chat routes retain the existing local/origin/cookie read-only boundaries."""

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.routes import ai_agent as route
from tests.contract.test_ai_agent_api import HEADERS, MUTATION_HEADERS, ORIGIN, PREFIX, AiApiFixture
from tests.contract.test_ai_agent_api import ai_api as ai_api


@pytest.fixture
def chat_api(ai_api: AiApiFixture, tmp_path: Path) -> AiApiFixture:
    ai_api[4].ai_agent_runtime_root = str(tmp_path / "runtime")
    return ai_api


def create(client: TestClient, identifier: str = "a" * 32) -> dict[str, Any]:
    assert client.get(PREFIX + "/status").status_code == 200
    response = client.post(
        PREFIX + "/conversations", json={"request_id": identifier}, headers=MUTATION_HEADERS
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_create_list_read_and_metadata_do_not_start_a_model(chat_api: AiApiFixture) -> None:
    _, client, _, runtime, _ = chat_api
    record = create(client)
    assert runtime.sessions == []
    assert create(client)["id"] == record["id"]
    response = client.get(PREFIX + "/conversations")
    assert response.headers["cache-control"] == "no-store"
    assert len(response.json()["conversations"]) == 1
    assert "messages" not in response.json()["conversations"][0]
    changed = client.patch(
        PREFIX + f"/conversations/{record['id']}",
        headers=MUTATION_HEADERS,
        json={"title": "我的预算分析", "pinned": True, "archived": True},
    )
    assert changed.status_code == 200
    assert changed.json()["title"] == "我的预算分析" and changed.json()["archived"]
    assert runtime.sessions == []


def test_cockpit_source_can_create_an_unsent_chat_without_expanding_external_access(
    chat_api: AiApiFixture,
) -> None:
    _, client, _, runtime, _ = chat_api
    client.get(PREFIX + "/status")
    context = {
        "label": "预算驾驶舱",
        "scope": "赛道：空间",
        "prompt": "仅分析同一组快照",
        "href": "/budget/cockpit?forecast_version=p26&nyVersion=n25&cc_group=campus",
    }
    response = client.post(
        PREFIX + "/conversations", headers=MUTATION_HEADERS, json={"request_id": "b" * 32, "context": context}
    )
    assert response.status_code == 201, response.text
    assert response.json()["context"] == context
    assert response.json()["messages"] == [] and runtime.sessions == []
    for href in ("https://outside.test/budget/cockpit", "/budget/cockpit/unknown"):
        denied = client.post(
            PREFIX + "/conversations",
            headers=MUTATION_HEADERS,
            json={"request_id": "c" * 32, "context": {**context, "href": href}},
        )
        assert denied.status_code == 400
    assert runtime.sessions == []


def test_chat_ownership_survives_restart_but_other_browser_cannot_read_it(chat_api: AiApiFixture) -> None:
    app, first, service, runtime, _ = chat_api
    record = create(first)
    owner = first.cookies.get(route.COOKIE)
    replacement = route.AiService(app)
    replacement.runtime, replacement.handler = runtime, service.handler
    app.state.ai_agent_service = replacement
    assert first.get(PREFIX + "/conversations").status_code == 401
    assert first.get(PREFIX + "/status").status_code == 200
    assert first.cookies.get(route.COOKIE) == owner
    assert first.get(PREFIX + f"/conversations/{record['id']}").json()["id"] == record["id"]
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as second:
        assert second.get(PREFIX + "/status").status_code == 200
        assert second.get(PREFIX + "/conversations").json()["conversations"] == []
        assert second.get(PREFIX + f"/conversations/{record['id']}").status_code == 404
        assert (
            second.patch(
                PREFIX + f"/conversations/{record['id']}",
                json={"title": "跨浏览器"},
                headers=MUTATION_HEADERS,
            ).status_code
            == 409
        )
    assert first.get(PREFIX + f"/conversations/{record['id']}").json()["title"] == "新对话"


@pytest.mark.parametrize("action", ["create", "rename", "send", "stop", "read"])
def test_chat_mutations_require_exact_browser_origin(chat_api: AiApiFixture, action: str) -> None:
    _, client, _, runtime, _ = chat_api
    identifier = create(client)["id"]
    root = PREFIX + f"/conversations/{identifier}"
    if action == "create":
        response = client.post(PREFIX + "/conversations", json={"request_id": "b" * 32})
    elif action == "rename":
        response = client.patch(root, json={"title": "bad"})
    elif action == "send":
        response = client.post(root + "/messages", json={"text": "hello", "request_id": "c" * 32})
    elif action == "stop":
        response = client.post(root + "/stop", json={})
    else:
        response = client.post(root + "/read", json={"completion_id": "f" * 32})
    assert response.status_code == 403 and runtime.sessions == []


def test_history_read_does_not_depend_on_docker_or_model_availability(chat_api: AiApiFixture) -> None:
    _, client, service, _, _ = chat_api
    record = create(client)
    service.runtime = None

    async def unavailable() -> Any:
        raise RuntimeError("runtime unavailable")

    service.manager = unavailable  # type: ignore[method-assign]
    assert client.get(PREFIX + "/conversations").status_code == 200
    assert client.get(PREFIX + f"/conversations/{record['id']}").status_code == 200


def test_chat_endpoint_rejects_internal_fields_and_expired_authority(chat_api: AiApiFixture) -> None:
    _, client, service, runtime, _ = chat_api
    identifier = create(client)["id"]
    root = PREFIX + f"/conversations/{identifier}"
    response = client.patch(root, json={"workspace_id": "/host/secret"}, headers=MUTATION_HEADERS)
    assert response.status_code == 422
    response = client.post(
        PREFIX + "/conversations",
        json={"request_id": "b" * 32, "context": {"href": "https://outside.test"}},
        headers=MUTATION_HEADERS,
    )
    assert response.status_code == 400
    owner = client.cookies.get(route.COOKIE)
    assert owner is not None
    service.browsers[owner] = time.monotonic() - 1
    assert client.get(root).status_code == 401
    assert (
        client.post(
            root + "/messages", json={"text": "hello", "request_id": "c" * 32}, headers=MUTATION_HEADERS
        ).status_code
        == 401
    )
    assert runtime.sessions == []


def test_completion_ack_is_owner_scoped_idempotent_and_cannot_hide_a_newer_result(
    chat_api: AiApiFixture,
) -> None:
    app, first, service, _, _ = chat_api
    record = create(first)
    owner = first.cookies.get(route.COOKIE)
    saved = service.chats().store.read(owner, record["id"])
    saved.update(completion={"id": "d" * 32, "state": "idle", "at": "2026-10-24T01:00:00Z"})
    service.chats().store.save(owner, saved)
    root = PREFIX + f"/conversations/{record['id']}"
    assert first.get(root).json()["unread"]
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as other:
        assert other.get(PREFIX + "/status").status_code == 200
        assert (
            other.post(root + "/read", json={"completion_id": "d" * 32}, headers=MUTATION_HEADERS).status_code
            == 404
        )
    response = first.post(root + "/read", json={"completion_id": "e" * 32}, headers=MUTATION_HEADERS)
    assert response.status_code == 200 and response.json()["unread"]
    for _ in range(2):
        response = first.post(root + "/read", json={"completion_id": "d" * 32}, headers=MUTATION_HEADERS)
        assert response.status_code == 200 and not response.json()["unread"]
    assert (
        first.post(root + "/read", json={"completion_id": "../bad"}, headers=MUTATION_HEADERS).status_code
        == 422
    )
