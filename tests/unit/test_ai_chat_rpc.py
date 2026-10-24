"""The structured Pi transport preserves the same container isolation."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from packages.ai_agent.runtime import RuntimeManager, Session
from tests.unit.test_ai_agent_runtime import manager as manager


def test_chat_profile_is_private_to_one_conversation_and_resume_preserves_files(
    manager: RuntimeManager,
) -> None:
    first_id, second_id = "a" * 32, "b" * 32
    first = Session(first_id, manager.root / first_id, mode="chat", workspace_id=first_id)
    workspace, profile = manager._prepare_directory(first)
    (workspace / ".budget-chat.jsonl").write_text("native history stays here")
    resume = Session(second_id, manager.root / second_id, mode="chat", workspace_id=first_id)
    workspace_b, profile_b = manager._prepare_directory(resume)
    assert workspace == workspace_b and profile == profile_b
    assert profile != manager.root / "agent"
    assert (workspace_b / ".budget-chat.jsonl").read_text() == "native history stays here"
    third_id = "c" * 32
    third = Session(third_id, manager.root / third_id, mode="chat", workspace_id=third_id)
    _, profile_c = manager._prepare_directory(third)
    assert profile_c != profile
    args = manager._container_args(first, workspace, profile)
    assert args[args.index("--network") + 1] == "none"
    mounts = [args[i + 1] for i, item in enumerate(args) if item == "--mount"]
    assert len(mounts) == 2
    assert all("chat-history" not in mount for mount in mounts)
    assert "--privileged" not in args and "--publish" not in args


async def test_rpc_starts_with_pipes_and_persisted_session_never_a_pty(
    manager: RuntimeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def spawn(*args: str, **options: Any) -> Any:
        captured.update(args=args, options=options)
        return SimpleNamespace()

    def task(session: Session, coroutine: Any) -> Any:
        coroutine.close()

    async def command(identifier: str, kind: str) -> dict[str, Any]:
        captured["command"] = kind
        return {}

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(manager, "_task", task)
    monkeypatch.setattr(manager, "rpc_command", command)
    session = Session("a" * 32, manager.root / ("a" * 32), mode="chat")
    session._container = "budget-ai-test"
    await manager._start_rpc(session, "fixture prompt")
    args = captured["args"]
    assert args[:4] == ("docker", "exec", "-i", "budget-ai-test")
    assert "-it" not in args and "--no-session" not in args
    assert args[args.index("--mode") + 1] == "rpc"
    assert args[args.index("--session") + 1] == "/workspace/.budget-chat.jsonl"
    assert captured["options"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["command"] == "get_state"


async def test_rpc_lf_framing_preserves_unicode_and_correlates_responses(manager: RuntimeManager) -> None:
    session = Session("a" * 32, manager.root / ("a" * 32), mode="chat")
    reader = asyncio.StreamReader()
    session._process = SimpleNamespace(stdout=reader)  # type: ignore[assignment]
    future = asyncio.get_running_loop().create_future()
    session._rpc_pending["request"] = future
    frames = [
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "中\u2028文\u2029🙂"},
        },
        {"id": "request", "type": "response", "success": True, "data": {"accepted": True}},
    ]
    reader.feed_data("\n".join(json.dumps(frame, ensure_ascii=False) for frame in frames).encode() + b"\n")
    reader.feed_eof()
    await manager._rpc_reader(session)
    assert session._events[0]["data"]["assistantMessageEvent"]["delta"] == "中\u2028文\u2029🙂"
    assert (await future)["data"]["accepted"]
    with pytest.raises(ValueError):
        await manager.rpc_command(session.id, "arbitrary-host-command", value="rm")
