"""Controlled queue/notification lifecycle; no model, Docker or budget writes."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from packages.ai_agent.chat import ChatError, ChatService, ChatStore, public_chat
from packages.ai_agent.runtime import RuntimeCapacityError
from tests.unit.test_ai_chat import OTHER, OWNER


class ParallelRuntime:
    def __init__(self, limit: int = 3) -> None:
        self.limit = limit
        self.active: set[str] = set()
        self.releases: dict[str, asyncio.Event] = {}
        self.prompts: dict[str, list[str]] = {}
        self.starts: list[str] = []
        self.aborted: set[str] = set()
        self.high_water = 0
        self.fail: set[str] = set()

    async def start(self, **values: Any) -> Any:
        if len(self.active) >= self.limit:
            raise RuntimeCapacityError("capacity")
        identifier = f"{len(self.starts) + 1:032x}"
        self.starts.append(identifier)
        self.active.add(identifier)
        self.high_water = max(self.high_water, len(self.active))
        self.releases[identifier] = asyncio.Event()
        return SimpleNamespace(id=identifier, workspace_id=values.get("workspace_id") or identifier)

    async def rpc_command(self, identifier: str, command: str, **values: Any) -> dict[str, Any]:
        if command == "prompt":
            self.prompts.setdefault(identifier, []).append(values["message"])
        elif command == "abort":
            self.aborted.add(identifier)
            self.releases[identifier].set()
        return {"disposition": "started"}

    async def output(self, identifier: str):  # type: ignore[no-untyped-def]
        yield {"type": "rpc", "data": {"type": "message_start", "message": {"role": "assistant"}}}
        yield {
            "type": "rpc",
            "data": {
                "type": "tool_execution_start",
                "toolCallId": identifier,
                "toolName": "bash",
                "args": {"command": "printf example"},
            },
        }
        await self.releases[identifier].wait()
        yield {
            "type": "rpc",
            "data": {
                "type": "tool_execution_end",
                "toolCallId": identifier,
                "isError": False,
                "result": {"content": []},
            },
        }
        yield {
            "type": "rpc",
            "data": {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "独立结果"}],
                    "stopReason": "error" if identifier in self.fail else "stop",
                },
            },
        }
        yield {"type": "rpc", "data": {"type": "agent_settled", "aborted": identifier in self.aborted}}

    async def stop(self, identifier: str) -> None:
        self.active.discard(identifier)


def build(tmp_path: Path, limit: int = 3) -> tuple[ChatService, ParallelRuntime]:
    runtime = ParallelRuntime(limit)

    async def manager() -> Any:
        return runtime

    return ChatService(ChatStore(tmp_path / "history"), manager), runtime


async def eventually(predicate: Any) -> None:
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110 - bounded polling of the controlled fake scheduler
            await asyncio.sleep(0.001)


async def submit(chats: ChatService, index: int, owner: str = OWNER) -> str:
    record = chats.store.create(owner, identifier=f"{index:032x}")
    await chats.send(owner, record["id"], f"问题{index}", f"{index + 100:032x}")
    return str(record["id"])


async def test_three_parallel_then_fifo_queue_automatically_starts_once(tmp_path: Path) -> None:
    chats, runtime = build(tmp_path)
    ids = [await submit(chats, index) for index in range(1, 6)]
    await eventually(lambda: len(runtime.prompts) == 3)
    assert len(runtime.active) == 3
    assert chats.present(OWNER, chats.read(OWNER, ids[3]))["queue_position"] == 1
    assert chats.present(OWNER, chats.read(OWNER, ids[4]))["queue_position"] == 2
    duplicate = await chats.send(OWNER, ids[3], "问题4", f"{104:032x}")
    assert duplicate["state"] == "queued" and len(chats.pending) == 2
    runtime.releases[runtime.starts[0]].set()
    await eventually(lambda: len(runtime.prompts) == 4)
    assert "问题4" in runtime.prompts[runtime.starts[3]][0]
    assert chats.read(OWNER, ids[4])["state"] == "queued"
    runtime.releases[runtime.starts[1]].set()
    await eventually(lambda: len(runtime.prompts) == 5)
    assert "问题5" in runtime.prompts[runtime.starts[4]][0]
    assert runtime.high_water == 3 and all(len(prompts) == 1 for prompts in runtime.prompts.values())
    await chats.close()


async def test_cancel_queued_never_calls_model_and_next_job_does_not_stall(tmp_path: Path) -> None:
    chats, runtime = build(tmp_path, 1)
    ids = [await submit(chats, index) for index in range(1, 4)]
    await eventually(lambda: len(runtime.prompts) == 1)
    with pytest.raises(ChatError):
        await chats.update(OWNER, ids[1], {"archived": True})
    # Cover a reserved process handle becoming visible before prompt submission.
    chats.processes[(OWNER, ids[1])] = "reserved-before-prompt"
    cancelled = await chats.stop(OWNER, ids[1])
    assert cancelled["state"] == "cancelled"
    assert not runtime.aborted
    assert not public_chat(cancelled)["unread"]
    assert not any("问题2" in text for prompts in runtime.prompts.values() for text in prompts)
    runtime.releases[runtime.starts[0]].set()
    await eventually(lambda: len(runtime.prompts) == 2)
    assert "问题3" in runtime.prompts[runtime.starts[1]][0]
    await chats.close()


async def test_tool_start_is_persisted_before_a_long_tool_returns(tmp_path: Path) -> None:
    chats, runtime = build(tmp_path)
    identifier = await submit(chats, 1)
    await eventually(lambda: chats.read(OWNER, identifier)["messages"][-1].get("progress"))
    assert chats.list(OWNER)[0]["progress"] == "正在执行分析工具"
    assert len(runtime.active) == 1 and not runtime.releases[runtime.starts[0]].is_set()
    await chats.close()


async def test_slow_head_startup_does_not_block_cancelling_another_queued_chat(tmp_path: Path) -> None:
    chats, runtime = build(tmp_path)
    starting, gate = asyncio.Event(), asyncio.Event()
    original = runtime.start

    async def slow_start(**values: Any) -> Any:
        starting.set()
        await gate.wait()
        return await original(**values)

    runtime.start = slow_start  # type: ignore[method-assign]
    await submit(chats, 1)
    await starting.wait()
    second = await submit(chats, 2)
    stopped = await asyncio.wait_for(chats.stop(OWNER, second), 0.3)
    assert stopped["state"] == "cancelled" and not runtime.prompts
    await chats.close()


async def test_queue_works_across_owners_without_sharing_records_or_cancel_authority(tmp_path: Path) -> None:
    chats, runtime = build(tmp_path, 1)
    first = await submit(chats, 1)
    second = await submit(chats, 2, OTHER)
    await eventually(lambda: len(runtime.prompts) == 1)
    assert len(chats.list(OWNER)) == len(chats.list(OTHER)) == 1
    assert chats.list(OTHER)[0]["queue_position"] == 1
    with pytest.raises(ChatError):
        await chats.stop(OWNER, second)
    runtime.releases[runtime.starts[0]].set()
    await eventually(lambda: len(runtime.prompts) == 2)
    assert chats.read(OWNER, first)["state"] == "idle"
    assert public_chat(chats.read(OTHER, second))["completion"] is None
    await chats.close()


async def test_unread_completion_is_persistent_and_stale_ack_cannot_hide_next_result(tmp_path: Path) -> None:
    chats, runtime = build(tmp_path)
    identifier = await submit(chats, 1)
    await eventually(lambda: len(runtime.prompts) == 1)
    runtime.releases[runtime.starts[0]].set()
    await eventually(lambda: not chats.tasks)
    completed = chats.read(OWNER, identifier)
    first_completion = completed["completion"]["id"]
    assert public_chat(completed)["unread"]
    recreated, _ = build(tmp_path)
    assert recreated.list(OWNER)[0]["unread"]
    await chats.acknowledge(OWNER, identifier, first_completion)
    assert not chats.list(OWNER)[0]["unread"]
    await chats.send(OWNER, identifier, "再问一次", "f" * 32)
    await chats.acknowledge(OWNER, identifier, first_completion)  # completion is currently null
    await eventually(lambda: len(runtime.prompts) == 2)
    runtime.releases[runtime.starts[1]].set()
    await eventually(lambda: not chats.tasks)
    await chats.acknowledge(OWNER, identifier, first_completion)
    assert chats.list(OWNER)[0]["unread"]
    with pytest.raises(ChatError):
        await chats.acknowledge(OTHER, identifier, completed["completion"]["id"])


async def test_shutdown_retains_never_started_queue_and_recovery_does_not_replay_running_turn(
    tmp_path: Path,
) -> None:
    chats, runtime = build(tmp_path, 1)
    first, second = [await submit(chats, index) for index in range(1, 3)]
    await eventually(lambda: len(runtime.prompts) == 1)
    await chats.close()
    assert chats.store.read(OWNER, second)["state"] == "queued"
    recovered, next_runtime = build(tmp_path, 1)
    items = recovered.list(OWNER)
    assert next(item for item in items if item["id"] == first)["state"] == "interrupted"
    await eventually(lambda: len(next_runtime.prompts) == 1)
    assert "问题2" in next_runtime.prompts[next_runtime.starts[0]][0]
    assert all("问题1" not in text for prompts in next_runtime.prompts.values() for text in prompts)
    await recovered.close()


async def test_failed_and_stopped_runs_release_slots_and_notify_honestly(tmp_path: Path) -> None:
    chats, runtime = build(tmp_path, 1)
    first, second = [await submit(chats, index) for index in range(1, 3)]
    await eventually(lambda: len(runtime.prompts) == 1)
    runtime.fail.add(runtime.starts[0])
    runtime.releases[runtime.starts[0]].set()
    await eventually(lambda: len(runtime.prompts) == 2)
    item = chats.list(OWNER)
    failed = next(record for record in item if record["id"] == first)
    assert failed["unread"] and failed["completion"]["state"] == "error"
    stopped = await chats.stop(OWNER, second)
    assert stopped["state"] == "interrupted" and not runtime.active


async def test_queue_limit_does_not_append_unaccepted_messages(tmp_path: Path) -> None:
    chats, _ = build(tmp_path, 1)
    for index in range(1, 33):
        await submit(chats, index)
    extra = chats.store.create(OWNER, identifier=f"{99:032x}")
    with pytest.raises(ChatError, match="等待处理"):
        await chats.send(OWNER, extra["id"], "超出队列", "f" * 32)
    assert chats.read(OWNER, extra["id"])["messages"] == []
    await chats.close()
