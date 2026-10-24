"""Pi 1.2 JSONL transport. Runs only inside RuntimeManager's isolated guest."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Protocol

MAX_RPC_LINE = 8 * 1024 * 1024


class RpcRuntime(Protocol):
    """Small transport mixin; no model keys, host shell or browser-supplied commands."""

    def get(self, session_id: str) -> Any: ...
    def _task(self, session: Any, coroutine: Any) -> asyncio.Task[Any]: ...
    async def _watch_terminal(self, session: Any) -> None: ...
    async def _emit(self, session: Any, kind: str, **values: Any) -> None: ...

    async def _start_rpc(self, session: Any, system_prompt: str) -> None:
        session._process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            session._container,
            "pi",
            "--mode",
            "rpc",
            "--session",
            "/workspace/.budget-chat.jsonl",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--append-system-prompt",
            system_prompt,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_RPC_LINE,
            start_new_session=True,
        )
        self._task(session, self._rpc_reader(session))
        self._task(session, self._watch_terminal(session))
        await self.rpc_command(session.id, "get_state")

    async def rpc_command(self, session_id: str, command: str, **values: Any) -> dict[str, Any]:
        if command not in {"get_state", "prompt", "abort"}:
            raise ValueError("不支持这个对话操作")
        session = self.get(session_id)
        process = session._process
        if (
            session.mode != "chat"
            or process is None
            or process.stdin is None
            or process.returncode is not None
        ):
            raise RuntimeError("对话运行连接已结束")
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        session._rpc_pending[identifier] = future
        try:
            encoded = (
                json.dumps({"id": identifier, "type": command, **values}, ensure_ascii=False).encode() + b"\n"
            )
            if len(encoded) > 256 * 1024:
                raise ValueError("单条消息过长")
            async with session._rpc_write_lock:
                process.stdin.write(encoded)
                await process.stdin.drain()
            response = await asyncio.wait_for(future, 30 if command == "abort" else 20)
            if not response.get("success"):
                raise RuntimeError("Pi 未接受本次操作，请检查连接后重试")
            return response.get("data") or {}
        finally:
            session._rpc_pending.pop(identifier, None)

    async def _rpc_reader(self, session: Any) -> None:
        assert session._process and session._process.stdout
        try:
            # Binary readline splits only LF, preserving Unicode U+2028/U+2029 in strings.
            while line := await session._process.stdout.readline():
                if len(line) > MAX_RPC_LINE:
                    raise ValueError("RPC record too large")
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Invalid RPC record")
                if event.get("type") == "response":
                    future = session._rpc_pending.get(event.get("id"))
                    if future is not None and not future.done():
                        future.set_result(event)
                else:
                    await self._emit(session, "rpc", data=event)
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError):
            await self._emit(session, "notice", message="对话连接中断，已保留收到的内容。")
        finally:
            for future in tuple(session._rpc_pending.values()):
                if not future.done():
                    future.set_exception(RuntimeError("对话连接已结束"))
