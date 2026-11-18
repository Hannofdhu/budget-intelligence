import base64
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.routes import ai_agent as route
from tests.contract.test_ai_agent_api import HEADERS, ORIGIN, PREFIX, AiApiFixture
from tests.contract.test_ai_agent_api import ai_api as ai_api
from tests.contract.test_ai_chat_api import chat_api as chat_api
from tests.contract.test_ai_chat_api import create

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9F0mMAAAAASUVORK5CYII="
)


def prepare(fixture: AiApiFixture, *, identifier: str = "a" * 32) -> tuple[dict[str, Any], Path, str]:
    _, client, service, _, settings = fixture
    record = create(client, identifier)
    owner = client.cookies.get(route.COOKIE)
    saved = service.chats().store.read(owner, record["id"])
    saved["workspace_id"] = identifier
    service.chats().store.save(owner, saved)
    directory = Path(settings.ai_agent_runtime_root) / identifier / "workspace"
    directory.mkdir(parents=True)
    (directory / "分析.xlsx").write_bytes(b"synthetic workbook")
    (directory / "中文图.png").write_bytes(PNG)
    return saved, directory, PREFIX + f"/conversations/{record['id']}/files"


def test_existing_files_download_and_preview_without_starting_agent_or_writing_chat(
    chat_api: AiApiFixture,
) -> None:
    _, client, service, runtime, _ = chat_api
    record, directory, endpoint = prepare(chat_api)
    owner = client.cookies.get(route.COOKIE)
    before = service.chats().store.read(owner, record["id"])
    listing = client.get(endpoint)
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    items = {item["name"]: item for item in listing.json()["files"]}
    assert str(directory) not in listing.text and "workspace_id" not in listing.text
    response = client.get(endpoint + f"/{items['分析.xlsx']['id']}/download")
    assert response.status_code == 200 and response.content == b"synthetic workbook"
    assert "attachment;" in response.headers["content-disposition"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    image = client.get(endpoint + f"/{items['中文图.png']['id']}/preview")
    assert image.content == PNG and image.headers["content-type"] == "image/png"
    assert "sandbox" in image.headers["content-security-policy"]
    assert client.get(endpoint + f"/{items['分析.xlsx']['id']}/preview").status_code == 415
    assert service.chats().store.read(owner, record["id"]) == before
    assert runtime.sessions == [] and runtime.writes == []


def test_other_browser_and_other_conversation_cannot_read_file(chat_api: AiApiFixture) -> None:
    app, client, _, runtime, _ = chat_api
    _, _, endpoint = prepare(chat_api)
    identifier = client.get(endpoint).json()["files"][0]["id"]
    _, _, other = prepare(chat_api, identifier="b" * 32)
    assert client.get(other + f"/{identifier}/download").status_code == 404
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as outsider:
        assert outsider.get(PREFIX + "/status").status_code == 200
        assert outsider.get(endpoint).status_code == 404
        assert outsider.get(endpoint + f"/{identifier}/download").status_code == 404
    assert runtime.sessions == []


@pytest.mark.parametrize("headers", [{"origin": "https://evil.invalid"}, {"sec-fetch-site": "cross-site"}])
def test_file_routes_retain_origin_and_proxy_guards(chat_api: AiApiFixture, headers: dict[str, str]) -> None:
    _, client, _, _, _ = chat_api
    _, _, endpoint = prepare(chat_api)
    identifier = client.get(endpoint).json()["files"][0]["id"]
    for path in (endpoint, endpoint + f"/{identifier}/download", endpoint + f"/{identifier}/preview"):
        assert client.get(path, headers=headers).status_code == 403


def test_busy_empty_and_rewritten_workspaces_have_honest_states(chat_api: AiApiFixture) -> None:
    _, client, service, runtime, _ = chat_api
    record, directory, endpoint = prepare(chat_api)
    owner = client.cookies.get(route.COOKIE)
    identifier = client.get(endpoint).json()["files"][0]["id"]
    record["state"] = "running"
    service.chats().store.save(owner, record)
    assert client.get(endpoint).json()["writing"] is True
    assert client.get(endpoint + f"/{identifier}/download").status_code == 409
    record["state"] = "idle"
    service.chats().store.save(owner, record)
    for path in directory.iterdir():
        path.write_bytes(b"replaced")
    assert client.get(endpoint + f"/{identifier}/download").status_code == 404
    record["workspace_id"] = None
    service.chats().store.save(owner, record)
    assert client.get(endpoint).json()["files"] == []
    record["workspace_id"] = "../../private"
    service.chats().store.save(owner, record)
    assert client.get(endpoint).status_code == 404
    assert runtime.sessions == []
