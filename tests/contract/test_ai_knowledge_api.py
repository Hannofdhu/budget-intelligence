"""Knowledge mutations have authority only over the browser-owned AI metadata."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.routes import ai_agent as route
from tests.contract.test_ai_agent_api import HEADERS, MUTATION_HEADERS, ORIGIN, PREFIX, AiApiFixture
from tests.contract.test_ai_agent_api import ai_api as ai_api
from tests.contract.test_ai_chat_api import chat_api as chat_api
from tests.contract.test_ai_chat_api import create


def prepared(fixture: AiApiFixture) -> tuple[dict[str, Any], str]:
    _, client, service, _, _ = fixture
    record = create(client)
    owner = client.cookies.get(route.COOKIE)
    record["messages"] = [
        {"id": "c" * 32, "role": "user", "text": "以后先给结论，再解释。", "state": "complete"},
        {"id": "d" * 32, "role": "assistant", "text": "可以整理为候选偏好。", "state": "complete"},
    ]
    service.chats().store.save(owner, record)
    return record, PREFIX + f"/conversations/{record['id']}/knowledge-proposals"


def test_full_lifecycle_changes_only_knowledge_metadata_without_model_or_budget_actions(
    chat_api: AiApiFixture,
) -> None:
    _, client, _, runtime, _ = chat_api
    _, path = prepared(chat_api)
    result = client.post(path, json={"message_id": "d" * 32}, headers=MUTATION_HEADERS)
    assert result.status_code == 201, result.text
    item = result.json()
    assert item["state"] == "pending" and item["source"]["message_id"] == "c" * 32
    listed = client.get(PREFIX + "/knowledge")
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    assert listed.json()["builtin"] == []
    assert sum(item.get("preset", False) for item in listed.json()["items"]) == 2
    root = PREFIX + "/knowledge/" + item["id"]
    active = client.post(
        root + "/actions", json={"action": "approve", "revision": 1}, headers=MUTATION_HEADERS
    )
    assert active.status_code == 200 and active.json()["state"] == "active"
    stale = client.post(
        root + "/actions", json={"action": "disable", "revision": 1}, headers=MUTATION_HEADERS
    )
    assert stale.status_code == 409
    draft = {key: item[key] for key in ("title", "content", "kind", "module", "fiscal_year", "keywords")}
    edited = client.patch(
        root,
        json={**draft, "content": "回答简洁，先给结论。", "revision": active.json()["revision"]},
        headers=MUTATION_HEADERS,
    )
    assert edited.status_code == 200 and edited.json()["state"] == "pending"
    assert edited.json()["source"] == item["source"]
    rejected = client.patch(
        root,
        json={**draft, "content": "当前收入100万元", "revision": edited.json()["revision"]},
        headers=MUTATION_HEADERS,
    )
    assert rejected.status_code == 422
    removed = client.post(
        root + "/actions",
        json={"action": "delete", "revision": edited.json()["revision"]},
        headers=MUTATION_HEADERS,
    )
    assert removed.status_code == 200 and not removed.json()["content"]
    assert runtime.sessions == [] and runtime.writes == []


@pytest.mark.parametrize("operation", ["propose", "edit", "approve", "delete"])
def test_every_knowledge_write_requires_the_exact_browser_origin(
    chat_api: AiApiFixture, operation: str
) -> None:
    _, client, _, runtime, _ = chat_api
    _, path = prepared(chat_api)
    if operation == "propose":
        response = client.post(path, json={"message_id": "d" * 32})
    else:
        item = client.post(path, json={"message_id": "d" * 32}, headers=MUTATION_HEADERS).json()
        root = PREFIX + "/knowledge/" + item["id"]
        if operation == "edit":
            response = client.patch(root, json={"title": "偏好", "content": "先给结论", "revision": 1})
        else:
            response = client.post(root + "/actions", json={"action": operation, "revision": 1})
    assert response.status_code == 403 and runtime.sessions == []


def test_no_browser_can_forge_sources_or_publish_another_browsers_knowledge(chat_api: AiApiFixture) -> None:
    app, first, service, runtime, _ = chat_api
    _, path = prepared(chat_api)
    forged = first.post(
        path, json={"message_id": "d" * 32, "source": "forged", "state": "active"}, headers=MUTATION_HEADERS
    )
    assert forged.status_code == 422
    item = first.post(path, json={"message_id": "d" * 32}, headers=MUTATION_HEADERS).json()
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as second:
        second.get(PREFIX + "/status")
        assert all(item.get("preset") for item in second.get(PREFIX + "/knowledge").json()["items"])
        assert second.post(path, json={"message_id": "d" * 32}, headers=MUTATION_HEADERS).status_code == 409
        assert (
            second.post(
                PREFIX + f"/knowledge/{item['id']}/actions",
                json={"action": "approve", "revision": 1},
                headers=MUTATION_HEADERS,
            ).status_code
            == 409
        )
    assert runtime.sessions == []
    first.cookies.clear()
    assert first.get(PREFIX + "/knowledge").status_code == 401


def test_restart_retains_knowledge_identity_and_does_not_need_docker(chat_api: AiApiFixture) -> None:
    app, client, service, runtime, _ = chat_api
    _, path = prepared(chat_api)
    item = client.post(path, json={"message_id": "d" * 32}, headers=MUTATION_HEADERS).json()
    owner = client.cookies.get(route.COOKIE)
    replacement = route.AiService(app)
    replacement.runtime, replacement.handler = runtime, service.handler
    app.state.ai_agent_service = replacement
    client.get(PREFIX + "/status")
    assert client.cookies.get(route.COOKIE) == owner
    assert client.get(PREFIX + "/knowledge").json()["items"][0]["id"] == item["id"]


def test_manual_record_has_guarded_source_pending_confirmation_and_real_delete(
    chat_api: AiApiFixture,
) -> None:
    app, client, service, runtime, _ = chat_api
    client.get(PREFIX + "/status")
    body = {
        "request_id": "f" * 32,
        "title": "偏好验收",
        "content": "回答先给结论，再解释。",
        "kind": "preference",
    }
    path = PREFIX + "/knowledge"
    assert client.post(path, json=body).status_code == 403
    for forged in ({"source": {"label": "伪造来源"}}, {"state": "active"}, {"builtin": True}):
        assert client.post(path, json={**body, **forged}, headers=MUTATION_HEADERS).status_code == 422
    assert (
        client.post(path, json={**body, "content": "收入100万元"}, headers=MUTATION_HEADERS).status_code
        == 422
    )
    response = client.post(path, json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["state"] == "pending" and item["source"]["label"] == "手动记录"
    assert "conversation_id" not in item["source"]
    assert client.post(path, json=body, headers=MUTATION_HEADERS).json()["id"] == item["id"]
    assert (
        client.post(
            path, json={**body, "content": "先解释，再给结论。"}, headers=MUTATION_HEADERS
        ).status_code
        == 409
    )
    assert len(client.get(path).json()["items"]) == 3
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as other:
        other.get(PREFIX + "/status")
        assert all(item.get("preset") for item in other.get(path).json()["items"])
        assert (
            other.post(
                path + f"/{item['id']}/actions",
                json={"action": "delete", "revision": 1},
                headers=MUTATION_HEADERS,
            ).status_code
            == 409
        )
    active = client.post(
        path + f"/{item['id']}/actions", json={"action": "approve", "revision": 1}, headers=MUTATION_HEADERS
    ).json()
    owner = client.cookies.get(route.COOKIE)
    assert service.chats().knowledge.snapshot(owner, "你好", None)["items"][-1]["id"] == item["id"]
    deleted = client.post(
        path + f"/{item['id']}/actions",
        json={"action": "delete", "revision": active["revision"]},
        headers=MUTATION_HEADERS,
    )
    assert (
        deleted.status_code == 200 and deleted.json()["state"] == "deleted" and not deleted.json()["content"]
    )
    assert len(service.chats().knowledge.snapshot(owner, "你好", None)["items"]) == 2
    assert runtime.sessions == [] and runtime.writes == []


def test_every_preset_has_real_edit_delete_and_persistent_owner_scoped_management(
    chat_api: AiApiFixture,
) -> None:
    app, client, service, runtime, _ = chat_api
    client.get(PREFIX + "/status")
    path = PREFIX + "/knowledge"
    presets = client.get(path).json()["items"]
    assert len(presets) == 2 and all(item["preset"] and not item["builtin"] for item in presets)
    first = next(item for item in presets if item["id"] == "core-dimensions")
    draft = {key: first[key] for key in ("title", "content", "kind", "module", "fiscal_year", "keywords")}
    edited = client.patch(
        path + "/core-dimensions",
        json={**draft, "title": "我的分组口径", "revision": first["revision"]},
        headers=MUTATION_HEADERS,
    )
    assert edited.status_code == 200 and edited.json()["state"] == "pending"
    assert (
        client.post(
            path + "/core-dimensions/actions",
            json={"action": "delete", "revision": first["revision"]},
            headers=MUTATION_HEADERS,
        ).status_code
        == 409
    )
    for item in client.get(path).json()["items"]:
        result = client.post(
            path + f"/{item['id']}/actions",
            json={"action": "delete", "revision": item["revision"]},
            headers=MUTATION_HEADERS,
        )
        assert result.status_code == 200 and result.json()["state"] == "deleted"
    owner = client.cookies.get(route.COOKIE)
    replacement = route.AiService(app)
    replacement.runtime, replacement.handler = runtime, service.handler
    app.state.ai_agent_service = replacement
    client.get(PREFIX + "/status")
    assert client.get(path).json()["builtin"] == []
    assert client.cookies.get(route.COOKIE) == owner
    assert all(item["state"] == "deleted" for item in client.get(path).json()["items"])
    assert replacement.chats().knowledge.snapshot(owner, "你好", None)["items"] == []
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as other:
        other.get(PREFIX + "/status")
        assert all(item["state"] == "active" for item in other.get(path).json()["items"])
    assert runtime.sessions == [] and runtime.writes == []
