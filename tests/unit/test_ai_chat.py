"""Durable chat behavior, using fake Pi events; no model or budget database."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from packages.ai_agent.chat import ChatError, ChatService, ChatStore, public_chat, source_context

OWNER = "browser_one_" + "a" * 32
OTHER = "browser_two_" + "b" * 32
MESSAGE_ID = "c" * 32


def test_cockpit_context_is_an_exact_local_reference_not_a_model_start(tmp_path: Path) -> None:
    context = {
        "label": "预算驾驶舱",
        "scope": "赛道：空间",
        "prompt": "只读同版本",
        "href": "/budget/cockpit?forecast_version=p26&nyVersion=n25&cc_group=campus",
    }
    assert source_context(context) == context
    record = ChatStore(tmp_path / "history").create(OWNER, context=context, identifier="a" * 32)
    assert record["context"] == context and not record["messages"]
    for href in (
        "https://outside.test/budget/cockpit",
        "//outside.test/budget/cockpit",
        "/budget/cockpit/unknown",
        "/budget/%63ockpit",
    ):
        with pytest.raises(ChatError):
            source_context({**context, "href": href})


class Runtime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.prompts: list[str] = []
        self.stops: list[str] = []
        self.events: list[dict[str, Any]] = [
            {"type": "message_start", "message": {"role": "assistant"}},
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "private-thought"},
            },
            {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "partial"}},
            {
                "type": "tool_execution_start",
                "toolCallId": "tool-1",
                "toolName": "bash",
                "args": {"command": "printf hello"},
            },
            {
                "type": "tool_execution_end",
                "toolCallId": "tool-1",
                "isError": False,
                "result": {"content": [{"type": "text", "text": "hello"}]},
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "你好\u2028完整回答"}],
                    "model": "fixture",
                    "stopReason": "stop",
                },
            },
            {"type": "agent_end", "willRetry": False},
            {"type": "agent_settled", "aborted": False},
        ]
        self.hold: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def start(self, **values: Any) -> Any:
        self.calls.append(values)
        return SimpleNamespace(id="d" * 32, workspace_id=values.get("workspace_id") or "d" * 32)

    async def rpc_command(self, identifier: str, command: str, **values: Any) -> dict[str, Any]:
        if command == "prompt":
            self.prompts.append(values["message"])
            self.started.set()
        elif command == "abort":
            self.events = [{"type": "agent_settled", "aborted": True}]
            if self.hold:
                self.hold.set()
        return {"disposition": "started"}

    async def output(self, identifier: str):  # type: ignore[no-untyped-def]
        if self.hold:
            await self.hold.wait()
        for index, event in enumerate(self.events):
            yield {"type": "rpc", "seq": index + 1, "data": event}

    async def stop(self, identifier: str) -> None:
        self.stops.append(identifier)


def service(tmp_path: Path) -> tuple[ChatService, Runtime]:
    runtime = Runtime()

    async def manager() -> Any:
        return runtime

    return ChatService(ChatStore(tmp_path / "history"), manager), runtime


def test_owned_store_survives_reinstantiation_and_never_lists_other_browser(tmp_path: Path) -> None:
    store = ChatStore(tmp_path / "history")
    record = store.create(OWNER)
    assert store.known_owner(OWNER) and not store.known_owner(OTHER)
    assert store.list(OTHER) == []
    with pytest.raises(ChatError):
        store.read(OTHER, record["id"])
    again = ChatStore(tmp_path / "history")
    assert again.read(OWNER, record["id"])["id"] == record["id"]
    assert again.create(OWNER, identifier=record["id"])["id"] == record["id"]
    assert len(again.list(OWNER)) == 1
    assert "messages" not in again.list(OWNER)[0]
    assert "workspace_id" not in public_chat(record)
    assert store.root.stat().st_mode & 0o777 == 0o700
    path = next(store.root.glob("*/*.json"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert OWNER not in str(path)


@pytest.mark.parametrize("bad", ["../a", "/tmp/record", "", "a" * 33])
def test_history_paths_fail_closed(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ChatError):
        ChatStore(tmp_path).read(OWNER, bad)


def test_history_symlink_cannot_read_or_overwrite_other_host_file(tmp_path: Path) -> None:
    store = ChatStore(tmp_path / "history")
    record = store.create(OWNER)
    path = next(store.root.glob("*/*.json"))
    outside = tmp_path / "protected.json"
    outside.write_text('{"private":"untouched"}')
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ChatError):
        store.read(OWNER, record["id"])
    store.save(OWNER, record)
    assert outside.read_text() == '{"private":"untouched"}'
    assert not path.is_symlink()


@pytest.mark.parametrize(
    "href",
    [
        "https://outside.test",
        "//outside.test",
        "/budget/forecast\\x",
        "/api/v1/secrets",
        "/budget/forecast\n",
    ],
)
def test_context_is_only_a_product_reference_not_an_arbitrary_url(href: str) -> None:
    with pytest.raises(ChatError):
        source_context({"href": href})


async def test_send_stream_final_reconciliation_and_native_workspace_reuse(tmp_path: Path) -> None:
    chats, runtime = service(tmp_path)
    record = chats.store.create(OWNER)
    identifier = record["id"]
    await chats.send(OWNER, identifier, "你好", MESSAGE_ID)
    await chats.tasks[(OWNER, identifier)]
    saved = chats.read(OWNER, identifier)
    assert saved["state"] == "idle" and len(saved["messages"]) == 2
    answer = saved["messages"][-1]
    assert answer["text"] == "你好\u2028完整回答"
    assert "private-thought" not in json.dumps(saved)
    assert answer["activities"][0]["state"] == "complete"
    assert answer["activities"][0]["output"] == "hello"
    assert runtime.calls == [{"mode": "chat", "workspace_id": None}]
    await chats.send(OWNER, identifier, "你好", MESSAGE_ID)
    assert len(runtime.prompts) == 1 and len(chats.read(OWNER, identifier)["messages"]) == 2
    with pytest.raises(ChatError):
        await chats.send(OWNER, identifier, "不同的消息", MESSAGE_ID)
    await chats.send(OWNER, identifier, "继续", "e" * 32)
    await chats.tasks[(OWNER, identifier)]
    assert runtime.calls[-1] == {"mode": "chat", "workspace_id": "d" * 32}
    assert len(chats.read(OWNER, identifier)["messages"]) == 4


async def test_metadata_during_answer_is_not_overwritten_and_busy_cannot_double_send(tmp_path: Path) -> None:
    chats, runtime = service(tmp_path)
    runtime.hold = asyncio.Event()
    record = chats.store.create(OWNER)
    identifier = record["id"]
    await chats.send(OWNER, identifier, "问题", MESSAGE_ID)
    await runtime.started.wait()
    with pytest.raises(ChatError):
        await chats.send(OWNER, identifier, "重复启动", "e" * 32)
    with pytest.raises(ChatError):
        await chats.update(OWNER, identifier, {"archived": True})
    renamed = await chats.update(OWNER, identifier, {"title": "我的分析", "pinned": True})
    revision = renamed["revision"]
    runtime.hold.set()
    await chats.tasks[(OWNER, identifier)]
    saved = chats.read(OWNER, identifier)
    assert saved["title"] == "我的分析" and saved["pinned"] and saved["revision"] > revision
    await chats.update(OWNER, identifier, {"archived": True})
    with pytest.raises(ChatError):
        await chats.send(OWNER, identifier, "不能给归档发送", "f" * 32)
    await chats.update(OWNER, identifier, {"archived": False})
    assert not chats.read(OWNER, identifier)["archived"]


async def test_stop_keeps_transcript_and_does_not_delete_the_conversation(tmp_path: Path) -> None:
    chats, runtime = service(tmp_path)
    runtime.hold = asyncio.Event()
    record = chats.store.create(OWNER)
    await chats.send(OWNER, record["id"], "问题", MESSAGE_ID)
    await runtime.started.wait()
    stopped = await chats.stop(OWNER, record["id"])
    assert stopped["state"] == "interrupted"
    assert stopped["messages"][0]["text"] == "问题"
    assert len(chats.store.list(OWNER)) == 1


async def test_stop_before_run_starts_is_a_retained_queue_cancellation(
    tmp_path: Path,
) -> None:
    chats, runtime = service(tmp_path)
    record = chats.store.create(OWNER)
    await chats.send(OWNER, record["id"], "立即停止", MESSAGE_ID)
    stopped = await chats.stop(OWNER, record["id"])
    assert stopped["state"] == "cancelled"
    assert stopped["messages"][0]["text"] == "立即停止"
    assert stopped["messages"][-1]["state"] == "cancelled"
    assert not runtime.prompts


async def test_same_conversation_id_in_another_browser_cannot_stop_the_owners_task(tmp_path: Path) -> None:
    chats, runtime = service(tmp_path)
    runtime.hold = asyncio.Event()
    identifier = "e" * 32
    chats.store.create(OWNER, identifier=identifier)
    chats.store.create(OTHER, identifier=identifier)
    await chats.send(OWNER, identifier, "正在回答的问题", MESSAGE_ID)
    await runtime.started.wait()
    original_task = chats.tasks[(OWNER, identifier)]
    stopped = await chats.stop(OTHER, identifier)
    assert stopped["state"] == "idle" and stopped["messages"] == []
    assert not original_task.done() and not runtime.stops
    assert chats.read(OWNER, identifier)["state"] == "running"
    runtime.hold.set()
    await original_task
    assert chats.read(OWNER, identifier)["messages"][-1]["state"] == "complete"


async def test_provider_error_is_not_success_and_never_auto_resubmits(tmp_path: Path) -> None:
    chats, runtime = service(tmp_path)
    runtime.events[-3]["message"]["stopReason"] = "error"
    record = chats.store.create(OWNER)
    await chats.send(OWNER, record["id"], "问题", MESSAGE_ID)
    await chats.tasks[(OWNER, record["id"])]
    saved = chats.read(OWNER, record["id"])
    assert saved["state"] == "error" and saved["messages"][-1]["error"]
    assert len(runtime.prompts) == 1 and runtime.stops


def test_restart_marks_orphaned_generation_interrupted_without_losing_text(tmp_path: Path) -> None:
    chats, _ = service(tmp_path)
    record = chats.store.create(OWNER)
    record.update(
        state="running",
        messages=[{"id": MESSAGE_ID, "role": "assistant", "text": "已保存片段", "state": "running"}],
    )
    chats.store.save(OWNER, record)
    again, _ = service(tmp_path)
    recovered = again.read(OWNER, record["id"])
    assert recovered["state"] == "interrupted"
    assert recovered["messages"][0]["text"] == "已保存片段"


async def test_cleanup_failure_is_visible_and_retry_stop_is_required_before_resend(tmp_path: Path) -> None:
    chats, runtime = service(tmp_path)
    original = runtime.stop
    failures = 1

    async def stop(identifier: str) -> None:
        nonlocal failures
        if failures:
            failures -= 1
            raise RuntimeError("container removal unconfirmed")
        await original(identifier)

    runtime.stop = stop  # type: ignore[method-assign]
    record = chats.store.create(OWNER)
    await chats.send(OWNER, record["id"], "问题", MESSAGE_ID)
    await chats.tasks[(OWNER, record["id"])]
    saved = chats.read(OWNER, record["id"])
    assert saved["state"] == "error" and public_chat(saved)["needs_stop"]
    with pytest.raises(ChatError):
        await chats.send(OWNER, record["id"], "不能假设已退出", "f" * 32)
    stopped = await chats.stop(OWNER, record["id"])
    assert not public_chat(stopped)["needs_stop"]
    assert stopped["messages"][-1]["text"] == "你好\u2028完整回答"


async def test_reference_is_retained_exactly_not_replaced_by_current_page(tmp_path: Path) -> None:
    chats, runtime = service(tmp_path)
    context = {
        "label": "FY28预算编制 · 第23版",
        "scope": "春季",
        "href": "/budget/next-year?nyVersion=exact23",
        "prompt": "精确来源版本exact23；只读，未保存改动不在上下文",
    }
    record = chats.store.create(OWNER, context)
    await chats.send(OWNER, record["id"], "分析这个版本", MESSAGE_ID)
    await chats.tasks[(OWNER, record["id"])]
    assert "exact23" in runtime.prompts[0]
    assert chats.read(OWNER, record["id"])["context"] == context
