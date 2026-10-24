"""Semantic naming stays independent from answers, manual names and budget tools."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from packages.ai_agent.chat import ChatService, ChatStore
from packages.ai_agent.chat_title import clean_title, generate_chat_title
from packages.ai_agent.config import ModelConfig, ProviderConfig
from tests.unit.test_ai_chat import MESSAGE_ID, OWNER, Runtime


@pytest.mark.parametrize("value", ["太长" * 13, "两行\n标题", "<script>", "", None])
def test_invalid_titles_are_rejected_instead_of_truncated(value: object) -> None:
    assert clean_title(value) is None


async def test_model_titles_use_existing_credentials_and_only_bounded_first_exchange() -> None:
    config = ModelConfig(
        {
            "local": ProviderConfig(
                "local",
                "openai-completions",
                "https://model.test/v1",
                "test-secret",
                {},
                {"glm-fixture": {"compat": {"maxTokensField": "max_completion_tokens"}}},
            )
        },
        "local/glm-fixture",
        (),
    )
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["max_completion_tokens"] == 2048
        assert payload["thinking"] == {"type": "disabled"}
        assert "tools" not in payload and payload["stream"] is False
        assert len(payload["messages"][1]["content"]) < 6100
        return httpx.Response(200, json={"choices": [{"message": {"content": "查看影响的计算逻辑"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        title = await generate_chat_title(config, "问" * 8000, "答" * 8000, client=client)
    assert title == "查看影响的计算逻辑"
    assert str(calls[0].url) == "https://model.test/v1/chat/completions"
    assert calls[0].headers["Authorization"] == "Bearer test-secret"


def make_service(
    tmp_path: Path,
    generator: Callable[[str, str], Awaitable[str | None]],
) -> tuple[ChatService, Runtime]:
    runtime = Runtime()

    async def manager() -> Any:
        return runtime

    return ChatService(ChatStore(tmp_path / "history"), manager, title_generator=generator), runtime


async def wait_titles(chats: ChatService) -> None:
    await asyncio.gather(*list(chats.title_tasks.values()))


async def test_first_complete_answer_gets_semantic_title_once_and_followups_keep_it(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    async def generator(question: str, answer: str) -> str:
        calls.append((question, answer))
        return "查看影响的计算逻辑"

    chats, runtime = make_service(tmp_path, generator)
    record = chats.store.create(OWNER)
    sent = await chats.send(OWNER, record["id"], "请用很简单的话讲清楚查看影响怎么算", MESSAGE_ID)
    assert sent["title"] == "新对话"
    await chats.tasks[(OWNER, record["id"])]
    await wait_titles(chats)
    saved = chats.read(OWNER, record["id"])
    assert saved["title"] == "查看影响的计算逻辑" and saved["title_source"] == "ai"
    assert saved["state"] == "idle" and saved["messages"][-1]["state"] == "complete"
    await chats.send(OWNER, record["id"], "继续讲", "e" * 32)
    await chats.tasks[(OWNER, record["id"])]
    await wait_titles(chats)
    assert len(calls) == 1 and len(runtime.prompts) == 2
    await chats.close()


async def test_slow_title_does_not_hold_answer_and_manual_rename_wins(tmp_path: Path) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def generator(question: str, answer: str) -> str:
        started.set()
        await release.wait()
        return "AI生成的标题"

    chats, runtime = make_service(tmp_path, generator)
    record = chats.store.create(OWNER)
    await chats.send(OWNER, record["id"], "问题", MESSAGE_ID)
    await chats.tasks[(OWNER, record["id"])]
    await started.wait()
    assert chats.read(OWNER, record["id"])["state"] == "idle"
    await chats.update(OWNER, record["id"], {"title": "我命名的标题"})
    release.set()
    await wait_titles(chats)
    assert chats.read(OWNER, record["id"])["title"] == "我命名的标题"
    await chats.close()


async def test_title_failure_does_not_damage_answer_or_repeat_on_every_poll(tmp_path: Path) -> None:
    calls: list[str] = []

    async def generator(question: str, answer: str) -> str:
        calls.append(question)
        raise httpx.ConnectError("private provider details")

    chats, runtime = make_service(tmp_path, generator)
    record = chats.store.create(OWNER)
    await chats.send(OWNER, record["id"], "问题", MESSAGE_ID)
    await chats.tasks[(OWNER, record["id"])]
    await wait_titles(chats)
    for _ in range(3):
        saved = chats.read(OWNER, record["id"], summarize_title=True)
    assert saved["state"] == "idle" and saved["title"] == "新对话"
    assert saved["messages"][-1]["text"] == "你好\u2028完整回答"
    assert len(calls) == 1 and not chats.title_tasks
    await chats.close()


async def test_only_selected_legacy_prompt_copy_is_backfilled_and_custom_names_survive(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def generator(question: str, answer: str) -> str:
        calls.append(question)
        return "查看影响的用途"

    chats, runtime = make_service(tmp_path, generator)
    for manual in (False, True):
        record = chats.store.create(OWNER)
        record.pop("title_source")
        question = "请用第一次接触预算的人也能听懂的话，解释查看影响用什么数据如何计算点击之后会发生什么"
        record.update(
            title="我的预算分析" if manual else question[:40],
            messages=[
                {"role": "user", "text": question, "state": "complete"},
                {"role": "assistant", "text": "先算一次，只预览不保存。", "state": "complete"},
            ],
        )
        chats.store.save(OWNER, record)
        chats.list(OWNER)
        assert not chats.title_tasks
        chats.read(OWNER, record["id"], summarize_title=True)
        chats.read(OWNER, record["id"], summarize_title=True)
        await wait_titles(chats)
        saved = chats.read(OWNER, record["id"])
        assert saved["title"] == ("我的预算分析" if manual else "查看影响的用途")
    assert len(calls) == 1
    await chats.close()


async def test_manual_name_before_first_send_and_shutdown_cancellation_are_safe(tmp_path: Path) -> None:
    async def generator(question: str, answer: str) -> str | None:
        await asyncio.Event().wait()
        return None

    chats, runtime = make_service(tmp_path, generator)
    record = chats.store.create(OWNER)
    await chats.update(OWNER, record["id"], {"title": "自定义主题"})
    await chats.send(OWNER, record["id"], "问题", MESSAGE_ID)
    await chats.tasks[(OWNER, record["id"])]
    assert not chats.title_tasks and chats.read(OWNER, record["id"])["title"] == "自定义主题"
    second = chats.store.create(OWNER)
    await chats.send(OWNER, second["id"], "问题", "e" * 32)
    await chats.tasks[(OWNER, second["id"])]
    await chats.close()
    assert not chats.title_tasks and chats.store.read(OWNER, second["id"])["state"] == "idle"
