"""Native Pi PTYs in network-isolated containers; never an unsandboxed fallback.

The host alone owns credentials and read-only product access. Container HTTP
requests travel over a Docker exec Unix-socket relay, not a published port.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import fcntl
import json
import os
import pty
import re
import signal
import struct
import termios
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from packages.ai_agent.config import ModelConfig
from packages.ai_agent.knowledge_checks import BudgetEvidence
from packages.ai_agent.rpc import RpcRuntime

MAX_OUTPUT_BYTES = 1_048_576
MAX_EVENTS = 2048
MAX_HISTORY = 64
MAX_INPUT_BYTES = 65_536
MAX_RELAY_LINE = 16 * 1024 * 1024
SYSTEM_PROMPT = (
    "你是预算智解中的 AI智解，面向业务人员分析预算、预测、招生、续班和收入。"
    "优先直接完成用户的问题，使用简洁中文，明确区分预算、实际、预测和假设。"
    "面向用户，季度和全年预算、预测中的对应收入指标统一称为“结转收入”，保留原单位；"
    "不要与业务收入（含税）、原价收入或结转比例混称。"
    "面向用户，将预算业务范围统一称为 SKU；内部字段、工具和数据键保持原样。"
    "你在独立的隔离工作区 /workspace 中运行，不是本产品源码开发助手。"
    "先通过 bash 执行 budget-api get help 了解本产品预算、预测、下一年预算的用途与口径。"
    "再执行 budget-api list、budget-api get 读取产品已保存的数据。"
    "可以在工作区分析数据、编写并执行分析脚本、生成报告，不能修改产品预算、预测、定版或规则。"
    "不要猜测未读取的数据，不要将示例、建议或估算写成已保存的产品事实。"
    "模型和预算接口经受限桥接访问；其他网络访问在隔离环境中不可用。"
)


class RuntimeErrorSafe(RuntimeError):
    """User-facing runtime errors deliberately exclude process stderr and secrets."""


class RuntimeCapacityError(RuntimeErrorSafe):
    """No guest or model request was started; an accepted chat may safely wait."""


class BridgeProtocol(Protocol):
    def handle(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...


@dataclass
class Session:
    id: str
    directory: Path = field(repr=False)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    status: str = "starting"
    cols: int = 100
    rows: int = 30
    exit_code: int | None = None
    cleanup_error: str | None = None
    _master: int | None = field(default=None, repr=False)
    _process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _relay: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _container: str = field(default="", repr=False)
    _events: deque[dict[str, Any]] = field(default_factory=deque, repr=False)
    _seq: int = field(default=0, repr=False)
    _output_bytes: int = field(default=0, repr=False)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    _requests: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    _relay_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _stopping: bool = field(default=False, repr=False)
    _stop_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _attached: int = field(default=0, repr=False)
    mode: str = "terminal"
    workspace_id: str = ""
    _rpc_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict, repr=False)
    _rpc_write_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _budget_evidence: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "status": self.status,
            "cols": self.cols,
            "rows": self.rows,
            "exit_code": self.exit_code,
            "cleanup_error": self.cleanup_error,
            "last_seq": self._seq,
            "isolated": True,
        }


class RuntimeManager(RpcRuntime):
    def __init__(
        self,
        runtime_root: Path,
        image: str,
        model_config: ModelConfig,
        handler: BridgeProtocol,
        *,
        max_sessions: int = 3,
        idle_seconds: float = 1800,
    ) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/@-]{0,255}", image):
            raise RuntimeErrorSafe("AI 运行镜像配置无效。")
        if runtime_root.is_symlink():
            raise RuntimeErrorSafe("AI 工作目录不能是符号链接。")
        self.root = runtime_root.resolve()
        self.image = image
        self.config = model_config
        self.handler = handler
        self.max_sessions = max_sessions
        self.idle_seconds = idle_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._cleaner: asyncio.Task[None] | None = None
        self._closed = False
        self._manager_id = uuid.uuid4().hex[:12]

    async def _command(self, *args: str, deadline_seconds: float = 15) -> tuple[int, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            raise RuntimeErrorSafe("Docker 不可用；AI智解不会在未隔离的主机上运行。") from None
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), deadline_seconds)
            return process.returncode or 0, stdout
        except (TimeoutError, asyncio.CancelledError):
            await self._terminate(process)
            raise

    async def status(self) -> dict[str, Any]:
        try:
            code, _ = await self._command("info", "--format", "{{.ServerVersion}}", deadline_seconds=5)
            if code:
                return {
                    "available": False,
                    "isolated": True,
                    "pi_version": "1.2.0",
                    "reason": "Docker 尚未启动。",
                }
            code, _ = await self._command(
                "image",
                "inspect",
                self.image,
                "--format",
                "{{.Id}}",
                deadline_seconds=5,
            )
            return {
                "available": code == 0,
                "isolated": True,
                "pi_version": "1.2.0",
                "reason": None if code == 0 else "AI 隔离镜像尚未构建。",
                "active_sessions": sum(
                    s.status in {"starting", "running", "cleanup_failed"} for s in self._sessions.values()
                ),
                "max_sessions": self.max_sessions,
            }
        except (RuntimeErrorSafe, TimeoutError):
            return {
                "available": False,
                "isolated": True,
                "pi_version": "1.2.0",
                "reason": "Docker 不可用或响应超时。",
            }

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise RuntimeErrorSafe("AI 会话不存在或已过期。") from None

    def list_sessions(self) -> list[dict[str, Any]]:
        ordered = sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)
        return [s.to_dict() for s in ordered]

    def _prepare_directory(self, session: Session) -> tuple[Path, Path]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        if session.mode != "chat" or not session.workspace_id or session.workspace_id == session.id:
            session.directory.mkdir(mode=0o700, exist_ok=False)
        home = (
            self.root / (session.workspace_id or session.id) if session.mode == "chat" else session.directory
        )
        if session.mode == "chat" and (home.is_symlink() or home.resolve().parent != self.root):
            raise RuntimeErrorSafe("AI 工作区无效。")
        workspace = home / "workspace"
        # Chat profiles are per conversation, never the legacy shared Pi profile.
        agent = home / "chat-agent" if session.mode == "chat" else self.root / "agent"
        if agent.is_symlink():
            raise RuntimeErrorSafe("AI 会话配置目录不能是符号链接。")
        # The private 0700 parent is not mounted. The guest's fixed node UID
        # needs write access to these two otherwise empty, per-session roots.
        for directory in (workspace, agent):
            if directory.is_symlink():
                raise RuntimeErrorSafe("AI 工作目录不能是符号链接。")
            directory.mkdir(mode=0o777, exist_ok=directory == agent or session.mode == "chat")
            directory.chmod(0o777)
        configs = (("models.json", self.config.pi_models()), ("settings.json", self.config.pi_settings()))
        for name, payload in configs:
            target = agent / name
            if name == "settings.json" and target.exists() and not target.is_symlink():
                continue  # Preserve the user's native Pi model/appearance selection.
            # A guest can create symlinks within its mount. Atomic replacement
            # must not follow one into an arbitrary host path on the next start.
            temporary = agent / f".config-{uuid.uuid4().hex}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            # Guest node must be able to change native Pi settings. The host
            # parent's 0700 permissions keep this separate profile private.
            temporary.chmod(0o666)
            temporary.replace(target)
        return workspace, agent

    def _container_args(self, session: Session, workspace: Path, agent: Path) -> list[str]:
        return [
            "run",
            "--detach",
            "--name",
            session._container,
            "--init",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "node",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--pids-limit",
            "128",
            "--memory",
            "1g",
            "--cpus",
            "2",
            "--stop-timeout",
            "3",
            "--label",
            f"budget.ai.manager={self._manager_id}",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
            "--mount",
            f"type=bind,src={agent},dst=/agent",
            "--env",
            "PI_CODING_AGENT_DIR=/agent",
            "--env",
            "TERM=xterm-256color",
            "--workdir",
            "/workspace",
            self.image,
        ]

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeErrorSafe("AI 运行服务正在关闭。")

    async def start(
        self, *, cols: int = 100, rows: int = 30, mode: str = "terminal", workspace_id: str | None = None
    ) -> Session:
        self._validate_dimensions(cols, rows)
        if mode not in {"terminal", "chat"} or (
            workspace_id is not None
            and (mode != "chat" or re.fullmatch(r"[a-f0-9]{32}", workspace_id) is None)
        ):
            raise RuntimeErrorSafe("AI 工作区无效。")
        if workspace_id and not (self.root / workspace_id / "workspace").is_dir():
            raise RuntimeErrorSafe("原对话工作区不可用，记录仍保留，未创建替代上下文。")
        async with self._lock:
            self._require_open()
            if (
                sum(s.status in {"starting", "running", "cleanup_failed"} for s in self._sessions.values())
                >= self.max_sessions
            ):
                raise RuntimeCapacityError("同时运行的 AI 会话已达上限，请先结束一个会话。")
            state = await self.status()
            self._require_open()
            if not state.get("available"):
                raise RuntimeErrorSafe(str(state.get("reason", "AI 隔离环境不可用。")))
            session_id = uuid.uuid4().hex
            session = Session(session_id, self.root / session_id, cols=cols, rows=rows)
            session.mode, session.workspace_id = mode, workspace_id or session_id
            session._container = f"budget-ai-{self._manager_id}-{session_id[:12]}"
            self._sessions[session_id] = session
            try:
                workspace, agent = self._prepare_directory(session)
                code, _ = await self._command(
                    *self._container_args(session, workspace, agent),
                    deadline_seconds=30,
                )
                self._require_open()
                if code:
                    raise RuntimeErrorSafe("AI 隔离容器启动失败；未使用主机运行作为替代。")
                await self._start_relay(session)
                self._require_open()
                if mode == "chat":
                    await self._start_rpc(
                        session,
                        SYSTEM_PROMPT
                        + "你通过产品的对话界面与用户交流，直接回答，不要求用户输入Pi命令或粘贴终端。"
                        "面向用户称定版预算、全年预测、预算编制，具体财年以读取结果为准。"
                        "分析可用Markdown标题、列表和表格呈现，保留金额单位。"
                        "引用数据时注明实际读取的财年、模块、已保存版本和数据截至时间；"
                        "版本序号使用第几版，不将P/N技术编号作为主要称呼。工具开始运行不代表任务已经完成。",
                    )
                else:
                    await self._start_terminal(session)
                self._require_open()
                session.status = "running"
                if self._cleaner is None:
                    self._cleaner = asyncio.create_task(self._cleanup_idle())
                return session
            except BaseException:
                await self._stop_session(session)
                raise

    async def _start_relay(self, session: Session) -> None:
        session._relay = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            session._container,
            "node",
            "/opt/budget-ai/relay.mjs",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_RELAY_LINE,
            start_new_session=True,
        )
        assert session._relay.stdout is not None
        try:
            line = await asyncio.wait_for(session._relay.stdout.readline(), 12)
            if json.loads(line) != {"type": "ready"}:
                raise ValueError
        except (TimeoutError, ValueError, json.JSONDecodeError):
            raise RuntimeErrorSafe("AI 隔离桥接未就绪。") from None
        self._task(session, self._relay_reader(session))

    async def _start_terminal(self, session: Session) -> None:
        master, slave = pty.openpty()
        session._master = master
        os.set_blocking(master, False)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", session.rows, session.cols, 0, 0))
        try:
            session._process = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "-it",
                "--env",
                "TERM=xterm-256color",
                session._container,
                "pi",
                "--append-system-prompt",
                SYSTEM_PROMPT,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
            )
        finally:
            os.close(slave)
        self._task(session, self._pty_reader(session))
        self._task(session, self._watch_terminal(session))
        try:
            async with session._condition:
                await asyncio.wait_for(
                    session._condition.wait_for(
                        lambda: (
                            any(e["type"] == "output" for e in session._events) or session.status == "stopped"
                        )
                    ),
                    20,
                )
        except TimeoutError:
            raise RuntimeErrorSafe("原生 Pi 终端未响应，请检查 Docker 运行状态后重试。") from None
        if session.status == "stopped":
            raise RuntimeErrorSafe("原生 Pi 终端未能启动。")

    def _task(self, session: Session, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        session._tasks.add(task)
        task.add_done_callback(session._tasks.discard)
        return task

    async def _relay_reader(self, session: Session) -> None:
        assert session._relay is not None and session._relay.stdout is not None
        try:
            while line := await session._relay.stdout.readline():
                if len(line) > MAX_RELAY_LINE or len(session._requests) >= 32:
                    break
                frame = json.loads(line)
                if not isinstance(frame, dict) or frame.get("type") != "request":
                    break
                task = asyncio.create_task(self._dispatch(session, frame))
                session._requests.add(task)
                task.add_done_callback(session._requests.discard)
        except (ValueError, OSError):
            pass
        if not session._stopping:
            await self._emit(session, "notice", message="AI 数据桥接已断开，请重新创建会话。")
            await self._stop_session(session)

    async def _dispatch(self, session: Session, frame: dict[str, Any]) -> None:
        path = frame.get("path", "")
        collect = (
            session.mode == "chat"
            and frame.get("method") == "GET"
            and isinstance(path, str)
            and path.startswith("/budget/")
            and path != "/budget/help"
        )
        evidence = BudgetEvidence(path) if collect else None
        try:
            async for response in self.handler.handle(frame):
                if evidence and (contract := evidence.feed(response)) is not None:
                    session._budget_evidence = [*session._budget_evidence, contract][-12:]
                if session._relay is None or session._relay.stdin is None:
                    return
                encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b"\n"
                if len(encoded) > MAX_RELAY_LINE:
                    raise ValueError
                async with session._relay_lock:
                    session._relay.stdin.write(encoded)
                    await session._relay.stdin.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            if session._relay is not None and session._relay.stdin is not None:
                payload = {
                    "type": "response_error",
                    "id": frame.get("id"),
                    "status": 502,
                    "message": "AI 数据桥接暂时不可用。",
                }
                with contextlib.suppress(BrokenPipeError, ConnectionError):
                    async with session._relay_lock:
                        session._relay.stdin.write(json.dumps(payload).encode() + b"\n")
                        await session._relay.stdin.drain()

    async def _read_fd(self, fd: int) -> bytes:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()

        def ready() -> None:
            if future.done():
                return
            try:
                future.set_result(os.read(fd, 65_536))
            except BlockingIOError:
                return
            except OSError:
                future.set_result(b"")
            loop.remove_reader(fd)

        loop.add_reader(fd, ready)
        try:
            return await future
        finally:
            loop.remove_reader(fd)

    async def _pty_reader(self, session: Session) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while session._master is not None:
            try:
                data = await self._read_fd(session._master)
            except (OSError, ValueError):
                break
            if not data:
                break
            text = decoder.decode(data)
            if text:
                await self._emit(session, "output", data=text)
        tail = decoder.decode(b"", final=True)
        if tail:
            await self._emit(session, "output", data=tail)

    async def _emit(self, session: Session, kind: str, **values: Any) -> None:
        async with session._condition:
            session._seq += 1
            event = {"type": kind, "seq": session._seq, **values}
            session._events.append(event)
            session._output_bytes += len(str(event.get("data", "")).encode())
            limit = 16 * 1024 * 1024 if session.mode == "chat" else MAX_OUTPUT_BYTES
            while len(session._events) > MAX_EVENTS or session._output_bytes > limit:
                dropped = session._events.popleft()
                session._output_bytes -= len(str(dropped.get("data", "")).encode())
            session._condition.notify_all()

    async def output(self, session_id: str, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        session = self.get(session_id)
        if type(after) is not int or after < 0:
            raise RuntimeErrorSafe("输出位置无效。")
        session._attached += 1
        session.last_activity = time.time()
        cursor = after
        try:
            while True:
                async with session._condition:
                    if session._events and cursor < session._events[0]["seq"] - 1:
                        cursor = session._events[0]["seq"] - 1
                        events = [{"type": "gap", "seq": cursor, "message": "较早终端输出已滚动清理。"}]
                    else:
                        events = [e for e in session._events if e["seq"] > cursor]
                    if not events:
                        if session.status in {"stopped", "cleanup_failed"}:
                            return
                        await session._condition.wait()
                        continue
                for event in events:
                    cursor = event["seq"]
                    yield event
        finally:
            session._attached -= 1
            session.last_activity = time.time()

    async def write(self, session_id: str, text: str) -> None:
        session = self.get(session_id)
        if session.status != "running" or session._master is None:
            raise RuntimeErrorSafe("AI 会话已结束。")
        if not isinstance(text, str) or len(text.encode()) > MAX_INPUT_BYTES:
            raise RuntimeErrorSafe("单次终端输入超过大小限制。")
        data = text.encode()
        while data:
            try:
                length = os.write(session._master, data)
                data = data[length:]
            except BlockingIOError:
                await asyncio.sleep(0.01)
            except OSError:
                raise RuntimeErrorSafe("AI 终端连接已结束。") from None
        session.last_activity = time.time()

    @staticmethod
    def _validate_dimensions(cols: int, rows: int) -> None:
        if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 400 or not 5 <= rows <= 160:
            raise RuntimeErrorSafe("终端窗口尺寸无效。")

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        self._validate_dimensions(cols, rows)
        session = self.get(session_id)
        if session._master is None or session.status != "running":
            raise RuntimeErrorSafe("AI 会话已结束。")
        fcntl.ioctl(session._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        # This PTY is not the Docker CLI's controlling terminal: TIOCSWINSZ
        # alone does not reliably generate SIGWINCH. Notify the exec client's
        # own process group so it forwards the resize to the guest PTY.
        if session._process is not None and session._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(session._process.pid, signal.SIGWINCH)
        session.cols, session.rows = cols, rows
        session.last_activity = time.time()

    async def _watch_terminal(self, session: Session) -> None:
        assert session._process is not None
        code = await session._process.wait()
        if not session._stopping:
            session.exit_code = code
            await self._stop_session(session)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process | None) -> None:
        if process is None or process.returncode is not None:
            return
        # Removing the guest normally makes `docker exec` exit on its own.
        # Give asyncio's child watcher a chance to observe that before sending
        # signals to a process group that may already be disappearing.
        if process.stdin is not None:
            with contextlib.suppress(OSError, RuntimeError):
                process.stdin.close()
        if await RuntimeManager._wait_exit(process, 0.2):
            return
        RuntimeManager._signal_child(process, signal.SIGTERM)
        if await RuntimeManager._wait_exit(process, 3):
            return
        RuntimeManager._signal_child(process, signal.SIGKILL)
        if not await RuntimeManager._wait_exit(process, 1):
            raise RuntimeErrorSafe("AI 终端连接已关闭，但未能确认运行进程退出；请重试结束会话。")

    @staticmethod
    async def _wait_exit(process: asyncio.subprocess.Process, seconds: float) -> bool:
        try:
            await asyncio.wait_for(process.wait(), seconds)
            return True
        except TimeoutError:
            return False

    @staticmethod
    def _signal_child(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        except OSError:
            # Some host environments disallow group signals even for a child
            # spawned by this process. Fall back only to that known child's
            # subprocess handle, never a discovered or arbitrary PID.
            with contextlib.suppress(OSError, RuntimeError):
                if sig == signal.SIGTERM:
                    process.terminate()
                else:
                    process.kill()

    async def _stop_session(self, session: Session) -> None:
        if session._stop_task is None or (session._stop_task.done() and session.status == "cleanup_failed"):
            session._stopping = True
            session._stop_task = asyncio.create_task(self._finish_stop(session))
        # A disconnected HTTP request / cancelled idle cleaner must not cancel
        # cleanup midway. All callers join the same completion task.
        await asyncio.shield(session._stop_task)

    async def _finish_stop(self, session: Session) -> None:
        session.cleanup_error = None
        failures: list[str] = []
        tasks = list(session._tasks | session._requests)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Kill the container as well as its exec clients. Closing a Docker exec
        # connection alone does not guarantee the guest child has terminated.
        if not await self._remove_container(session):
            failures.append("container")
        # Close the slave's last host peer before waiting for Docker clients.
        # EOF/HUP also lets clients exit when host signal delivery is denied.
        if session._master is not None:
            with contextlib.suppress(OSError):
                os.close(session._master)
            session._master = None
        for attribute in ("_process", "_relay"):
            process = getattr(session, attribute)
            try:
                await self._terminate(process)
            except (RuntimeErrorSafe, OSError):
                failures.append(attribute)
            else:
                setattr(session, attribute, None)
        session._tasks.clear()
        session._requests.clear()
        # Live browser terminals already hold their own display. Stopped
        # sessions retain only metadata + the exit marker, not 1 MiB each.
        session._events.clear()
        session._output_bytes = 0
        if failures:
            session.status = "cleanup_failed"
            session.cleanup_error = "AI 终端已断开，但运行进程退出尚未确认；请重试结束会话。"
            await self._emit(session, "notice", message=session.cleanup_error)
            raise RuntimeErrorSafe(session.cleanup_error)
        session.status = "stopped"
        await self._emit(session, "exit", code=session.exit_code)
        self._prune_history()

    async def _remove_container(self, session: Session) -> bool:
        if not session._container:
            return True  # No container was assigned / launch was never attempted.
        # Names are generated by this manager, never provided by the browser.
        # Validate again before using an exact anchored Docker name filter.
        if re.fullmatch(r"budget-ai-[a-z0-9-]{1,100}", session._container) is None:
            return False
        try:
            code, _ = await self._command("rm", "--force", session._container, deadline_seconds=10)
            if code == 0:
                return True
        except (RuntimeErrorSafe, TimeoutError, OSError):
            pass
        # A --rm container may already have removed itself. An unsuccessful
        # delete is only idempotent success if a responding daemon confirms
        # the exact container is absent; a timeout is not that confirmation.
        try:
            code, output = await self._command(
                "ps",
                "--all",
                "--filter",
                f"name=^{session._container}$",
                "--quiet",
                deadline_seconds=5,
            )
            return code == 0 and not output.strip()
        except (RuntimeErrorSafe, TimeoutError, OSError):
            return False

    def _prune_history(self) -> None:
        stopped = sorted(
            (s for s in self._sessions.values() if s.status == "stopped"),
            key=lambda s: s.created_at,
            reverse=True,
        )
        for session in stopped[MAX_HISTORY:]:
            self._sessions.pop(session.id, None)

    async def stop(self, session_id: str) -> None:
        await self._stop_session(self.get(session_id))

    async def _cleanup_idle(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(min(30, max(1, self.idle_seconds / 2)))
                for session in list(self._sessions.values()):
                    if (
                        session.status == "running"
                        and time.time() - session.last_activity > self.idle_seconds
                    ):
                        with contextlib.suppress(RuntimeErrorSafe):
                            await self._stop_session(session)
        except asyncio.CancelledError:
            return

    async def close(self) -> None:
        self._closed = True
        # Set the flag before waiting for an in-flight start. That start checks
        # the flag after every asynchronous launch stage and cleans its own
        # partial container. Holding the same lock makes the final snapshot
        # complete: no later start can escape this shutdown.
        async with self._lock:
            if self._cleaner is not None:
                self._cleaner.cancel()
                await asyncio.gather(self._cleaner, return_exceptions=True)
            results = await asyncio.gather(
                *(self._stop_session(s) for s in self._sessions.values()),
                return_exceptions=True,
            )
            if any(isinstance(result, BaseException) for result in results):
                raise RuntimeErrorSafe("部分 AI 进程退出尚未确认；所有终端连接已关闭，请检查后重试结束会话。")
