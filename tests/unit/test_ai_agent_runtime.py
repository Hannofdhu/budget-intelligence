"""No Docker daemon, provider, budget database or product files are used here."""

from __future__ import annotations

import asyncio
import base64
import json
import signal
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from packages.ai_agent.bridge import BridgeHandler
from packages.ai_agent.config import ModelConfig, ProviderConfig
from packages.ai_agent.runtime import (
    MAX_HISTORY,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    RuntimeErrorSafe,
    RuntimeManager,
    Session,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Bridge:
    async def handle(self, frame: dict[str, Any]) -> Any:
        yield {"type": "response_start", "id": frame["id"], "status": 200, "headers": {}}
        yield {"type": "response_chunk", "id": frame["id"], "data": base64.b64encode(b"safe").decode()}
        yield {"type": "response_end", "id": frame["id"]}


@pytest.fixture
def manager(tmp_path: Path) -> RuntimeManager:
    provider = ProviderConfig(
        "local-model",
        "openai-completions",
        "https://example.invalid/v1",
        "do-not-copy-secret",
        {},
        {"model-a": {"name": "Model A"}},
    )
    config = ModelConfig({provider.id: provider}, "local-model/model-a", ())
    return RuntimeManager(tmp_path / "runtime", "budget-ai:test", config, Bridge())


def test_container_is_network_and_filesystem_isolated(manager: RuntimeManager, tmp_path: Path) -> None:
    session = Session("abc", tmp_path / "session")
    session._container = "budget-ai-test"
    workspace, agent = manager._prepare_directory(session)
    args = manager._container_args(session, workspace, agent)
    joined = " ".join(args)
    for required in (
        "--network none",
        "--cap-drop ALL",
        "--security-opt no-new-privileges:true",
        "--user node",
        "--read-only",
        "--tmpfs /tmp:",
        "--pids-limit 128",
        "--memory 1g",
    ):
        assert required in joined
    mounts = [args[index + 1] for index, value in enumerate(args) if value == "--mount"]
    assert len(mounts) == 2
    assert mounts == [f"type=bind,src={workspace},dst=/workspace", f"type=bind,src={agent},dst=/agent"]
    for forbidden in ("--privileged", "--publish", "--network host", "docker.sock", ".openclaw", "BUDGET_"):
        assert forbidden not in joined
    assert args[-1] == "budget-ai:test"


def test_only_placeholder_model_configuration_is_persisted(manager: RuntimeManager, tmp_path: Path) -> None:
    session = Session("abc", tmp_path / "session")
    workspace, agent = manager._prepare_directory(session)
    models = json.loads((agent / "models.json").read_text())
    assert models["providers"]["local-model"]["apiKey"] == "budget-agent-bridge"
    assert "127.0.0.1:4081/model/local-model/v1" in (agent / "models.json").read_text()
    assert "do-not-copy-secret" not in repr(list(agent.iterdir())) + (agent / "models.json").read_text()
    assert "example.invalid" not in (agent / "models.json").read_text()
    assert list(workspace.iterdir()) == []
    assert manager.root.stat().st_mode & 0o777 == 0o700
    assert session.directory.stat().st_mode & 0o777 == 0o700


def test_shared_profile_keeps_native_sessions_and_user_model_choice(
    manager: RuntimeManager,
    tmp_path: Path,
) -> None:
    first = Session("first", tmp_path / "first")
    workspace_a, agent_a = manager._prepare_directory(first)
    sessions = agent_a / "sessions"
    sessions.mkdir()
    history = sessions / "previous.jsonl"
    history.write_text('{"type":"session","id":"previous"}\n')
    settings = json.loads((agent_a / "settings.json").read_text())
    settings["theme"] = "light"
    (agent_a / "settings.json").write_text(json.dumps(settings))
    second = Session("second", tmp_path / "second")
    workspace_b, agent_b = manager._prepare_directory(second)
    assert agent_a == agent_b == manager.root / "agent"
    assert workspace_a != workspace_b
    assert history.exists()
    assert json.loads((agent_b / "settings.json").read_text())["theme"] == "light"


def test_guest_configuration_symlink_cannot_overwrite_host_file(
    manager: RuntimeManager,
    tmp_path: Path,
) -> None:
    _, agent = manager._prepare_directory(Session("first", tmp_path / "first"))
    protected = tmp_path / "protected.json"
    protected.write_text("unchanged")
    (agent / "models.json").unlink()
    (agent / "models.json").symlink_to(protected)
    (agent / "settings.json").unlink()
    (agent / "settings.json").symlink_to(protected)
    manager._prepare_directory(Session("second", tmp_path / "second"))
    assert protected.read_text() == "unchanged"
    assert not (agent / "models.json").is_symlink()
    assert not (agent / "settings.json").is_symlink()


def test_shared_agent_root_symlink_is_rejected(manager: RuntimeManager, tmp_path: Path) -> None:
    manager.root.mkdir()
    (manager.root / "agent").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeErrorSafe, match="符号链接"):
        manager._prepare_directory(Session("first", tmp_path / "first"))


@pytest.mark.parametrize("image", ["--privileged", "bad image", "x\n--publish=80", ""])
def test_image_is_not_a_command_fragment(manager: RuntimeManager, tmp_path: Path, image: str) -> None:
    with pytest.raises(RuntimeErrorSafe):
        RuntimeManager(tmp_path, image, manager.config, manager.handler)


def test_reject_symlink_runtime_root(manager: RuntimeManager, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeErrorSafe):
        RuntimeManager(alias, manager.image, manager.config, manager.handler)


@pytest.mark.asyncio
async def test_docker_missing_never_falls_back_to_host(manager: RuntimeManager) -> None:
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError) as spawn:
        state = await manager.status()
        assert state["available"] is False
        with pytest.raises(RuntimeErrorSafe, match="Docker"):
            await manager.start()
    assert all(call.args[0] == "docker" for call in spawn.call_args_list)
    assert manager.list_sessions() == []


@pytest.mark.asyncio
async def test_missing_image_is_not_pulled_or_replaced(manager: RuntimeManager) -> None:
    manager._command = AsyncMock(side_effect=[(0, b"24"), (1, b"")])  # type: ignore[method-assign]
    state = await manager.status()
    assert state["available"] is False
    assert state["pi_version"] == "1.2.0"
    assert [call.args[0] for call in manager._command.call_args_list] == ["info", "image"]


@pytest.mark.asyncio
async def test_start_respects_concurrency_limit_before_docker(manager: RuntimeManager) -> None:
    for index in range(3):
        session = Session(str(index), manager.root / str(index), status="running")
        manager._sessions[session.id] = session
    manager.status = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(RuntimeErrorSafe, match="上限"):
        await manager.start()
    manager.status.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_start_failure_cleans_container_and_does_not_run_pi(manager: RuntimeManager) -> None:
    manager.status = AsyncMock(return_value={"available": True})  # type: ignore[method-assign]
    manager._command = AsyncMock(return_value=(0, b"container-id"))  # type: ignore[method-assign]
    manager._start_relay = AsyncMock(side_effect=RuntimeErrorSafe("relay failed"))  # type: ignore[method-assign]
    manager._start_terminal = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(RuntimeErrorSafe, match="relay failed"):
        await manager.start()
    manager._start_terminal.assert_not_awaited()
    calls = manager._command.call_args_list
    assert calls[0].args[0] == "run"
    assert calls[-1].args[:2] == ("rm", "--force")
    assert manager.list_sessions()[0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_start_relay_ready_precedes_native_tui(manager: RuntimeManager) -> None:
    operations: list[str] = []
    manager.status = AsyncMock(return_value={"available": True})  # type: ignore[method-assign]
    manager._command = AsyncMock(return_value=(0, b"container-id"))  # type: ignore[method-assign]

    async def relay(_: Session) -> None:
        operations.append("relay-ready")

    async def terminal(_: Session) -> None:
        operations.append("native-pi")

    manager._start_relay = relay  # type: ignore[method-assign,assignment]
    manager._start_terminal = terminal  # type: ignore[method-assign,assignment]
    session = await manager.start(cols=80, rows=24)
    assert operations == ["relay-ready", "native-pi"]
    assert session.status == "running"
    assert (session.cols, session.rows) == (80, 24)
    await manager.close()


@pytest.mark.asyncio
async def test_output_reconnect_replays_only_after_cursor(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    manager._sessions[session.id] = session
    await manager._emit(session, "output", data="one")
    await manager._emit(session, "output", data="two")
    stream = cast(AsyncGenerator[dict[str, Any], None], manager.output(session.id, after=1))
    assert await anext(stream) == {"type": "output", "seq": 2, "data": "two"}
    await stream.aclose()
    assert session.status == "running"  # Disconnect does not kill native Pi.
    assert session._attached == 0
    assert session.to_dict()["last_seq"] == 2


@pytest.mark.asyncio
async def test_output_is_bounded_and_reports_lost_cursor(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="stopped")
    manager._sessions[session.id] = session
    for _ in range(30):
        await manager._emit(session, "output", data="x" * 65_536)
    assert session._output_bytes <= MAX_OUTPUT_BYTES
    stream = cast(AsyncGenerator[dict[str, Any], None], manager.output(session.id))
    assert (await anext(stream))["type"] == "gap"
    assert (await anext(stream))["type"] == "output"
    await stream.aclose()


@pytest.mark.asyncio
async def test_output_does_not_extend_idle_timeout(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running", last_activity=123)
    await manager._emit(session, "output", data="loop forever")
    assert session.last_activity == 123


@pytest.mark.asyncio
async def test_stopped_output_returns_after_exit(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="stopped")
    manager._sessions[session.id] = session
    await manager._emit(session, "exit", code=0)
    events = [event async for event in manager.output(session.id)]
    assert events == [{"type": "exit", "seq": 1, "code": 0}]


@pytest.mark.asyncio
@pytest.mark.parametrize("after", [-1, True, "1"])
async def test_invalid_output_cursor_rejected(manager: RuntimeManager, after: Any) -> None:
    session = Session("abc", manager.root / "abc", status="stopped")
    manager._sessions[session.id] = session
    with pytest.raises(RuntimeErrorSafe, match="位置"):
        await anext(manager.output(session.id, after))


@pytest.mark.asyncio
async def test_input_limit_and_stopped_session(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    manager._sessions[session.id] = session
    session._master = 123
    with pytest.raises(RuntimeErrorSafe, match="大小"):
        await manager.write(session.id, "x" * (MAX_INPUT_BYTES + 1))
    session.status = "stopped"
    with pytest.raises(RuntimeErrorSafe, match="已结束"):
        await manager.write(session.id, "hello")


@pytest.mark.asyncio
async def test_partial_pty_input_is_fully_written(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running", last_activity=0)
    session._master = 123
    manager._sessions[session.id] = session
    with patch("os.write", side_effect=[2, 3]) as write:
        await manager.write(session.id, "hello")
    assert [call.args for call in write.call_args_list] == [(123, b"hello"), (123, b"llo")]
    assert session.last_activity > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cols,rows", [(0, 20), (401, 20), (80, 4), (80, 161), (True, 20)])
async def test_dimensions_are_bounded(manager: RuntimeManager, cols: int, rows: int) -> None:
    with pytest.raises(RuntimeErrorSafe, match="尺寸"):
        await manager.start(cols=cols, rows=rows)


@pytest.mark.asyncio
async def test_resize_updates_pty_and_public_metadata(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._master = 123
    manager._sessions[session.id] = session
    with patch("fcntl.ioctl") as ioctl:
        await manager.resize(session.id, 120, 40)
    assert ioctl.call_args.args[0] == 123
    assert (session.to_dict()["cols"], session.to_dict()["rows"]) == (120, 40)


@pytest.mark.asyncio
async def test_resize_signals_docker_exec_process_group_after_ioctl(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._master = 123
    session._process = cast(asyncio.subprocess.Process, SimpleNamespace(pid=456, returncode=None))
    manager._sessions[session.id] = session
    operations: list[str] = []
    with (
        patch("fcntl.ioctl", side_effect=lambda *_: operations.append("ioctl")),
        patch("os.killpg", side_effect=lambda *_: operations.append("signal")) as killpg,
    ):
        await manager.resize(session.id, 120, 40)
    assert operations == ["ioctl", "signal"]
    killpg.assert_called_once_with(456, signal.SIGWINCH)


@pytest.mark.asyncio
async def test_resize_does_not_signal_an_exited_process(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._master = 123
    session._process = cast(asyncio.subprocess.Process, SimpleNamespace(pid=456, returncode=0))
    manager._sessions[session.id] = session
    with patch("fcntl.ioctl"), patch("os.killpg") as killpg:
        await manager.resize(session.id, 120, 40)
    killpg.assert_not_called()


@pytest.mark.asyncio
async def test_resize_tolerates_process_exit_between_check_and_signal(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._master = 123
    session._process = cast(asyncio.subprocess.Process, SimpleNamespace(pid=456, returncode=None))
    manager._sessions[session.id] = session
    with patch("fcntl.ioctl"), patch("os.killpg", side_effect=ProcessLookupError):
        await manager.resize(session.id, 120, 40)
    assert (session.cols, session.rows) == (120, 40)


def child_process(*wait_results: Any) -> Any:
    return SimpleNamespace(
        pid=456,
        returncode=None,
        stdin=SimpleNamespace(close=Mock()),
        wait=AsyncMock(side_effect=list(wait_results)),
        terminate=Mock(),
        kill=Mock(),
    )


@pytest.mark.asyncio
async def test_terminate_first_waits_for_natural_exec_exit() -> None:
    process = child_process(0)
    with patch("os.killpg") as killpg:
        await RuntimeManager._terminate(process)
    process.stdin.close.assert_called_once()
    process.wait.assert_awaited_once()
    killpg.assert_not_called()
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


@pytest.mark.asyncio
async def test_group_signal_permission_denied_falls_back_to_known_child() -> None:
    process = child_process(TimeoutError, 0)
    with patch("os.killpg", side_effect=PermissionError(1, "denied")) as killpg:
        await RuntimeManager._terminate(process)
    killpg.assert_called_once_with(456, signal.SIGTERM)
    process.terminate.assert_called_once()
    process.kill.assert_not_called()
    assert process.wait.await_count == 2


@pytest.mark.asyncio
async def test_disappearing_exec_group_is_reaped_without_fallback() -> None:
    process = child_process(TimeoutError, 0)
    with patch("os.killpg", side_effect=ProcessLookupError):
        await RuntimeManager._terminate(process)
    process.terminate.assert_not_called()
    assert process.wait.await_count == 2


@pytest.mark.asyncio
async def test_term_then_kill_have_bounded_exit_waits() -> None:
    process = child_process(TimeoutError, TimeoutError, -9)
    with patch("os.killpg") as killpg:
        await RuntimeManager._terminate(process)
    assert [call.args for call in killpg.call_args_list] == [
        (456, signal.SIGTERM),
        (456, signal.SIGKILL),
    ]
    assert process.wait.await_count == 3


@pytest.mark.asyncio
async def test_kill_falls_back_to_known_child_when_group_signal_is_denied() -> None:
    process = child_process(TimeoutError, TimeoutError, -9)
    with patch("os.killpg", side_effect=PermissionError):
        await RuntimeManager._terminate(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()


@pytest.mark.asyncio
async def test_unreapable_exec_is_explicit_failure_even_when_all_signals_denied() -> None:
    process = child_process(TimeoutError, TimeoutError, TimeoutError)
    process.terminate.side_effect = PermissionError
    process.kill.side_effect = PermissionError
    with patch("os.killpg", side_effect=PermissionError):
        with pytest.raises(RuntimeErrorSafe, match="未能确认"):
            await RuntimeManager._terminate(process)
    process.stdin.close.assert_called_once()
    assert process.wait.await_count == 3


@pytest.mark.asyncio
async def test_stop_removes_container_and_closes_pty_once(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._container = "budget-ai-expected"
    session._master = 123
    manager._sessions[session.id] = session
    manager._command = AsyncMock(return_value=(0, b""))  # type: ignore[method-assign]
    with patch("os.close") as close:
        await manager.stop(session.id)
        await manager.stop(session.id)
    manager._command.assert_awaited_once_with("rm", "--force", "budget-ai-expected", deadline_seconds=10)
    close.assert_called_once_with(123)
    assert session.status == "stopped"
    assert session.directory.parent == manager.root  # Persisted Pi session directory is retained.


@pytest.mark.asyncio
async def test_stopped_session_releases_output_and_keeps_exit_metadata(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    manager._sessions[session.id] = session
    manager._command = AsyncMock(return_value=(0, b""))  # type: ignore[method-assign]
    await manager._emit(session, "output", data="x" * 10_000)
    await manager.stop(session.id)
    assert session._output_bytes == 0
    assert [event["type"] for event in session._events] == ["exit"]
    assert session._process is None and session._relay is None


@pytest.mark.asyncio
async def test_failed_reap_still_closes_pty_and_reaps_other_exec(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._master = 123
    process = cast(asyncio.subprocess.Process, child_process())
    relay = cast(asyncio.subprocess.Process, child_process())
    session._process, session._relay = process, relay
    manager._sessions[session.id] = session
    manager._command = AsyncMock(return_value=(0, b""))  # type: ignore[method-assign]
    termination = AsyncMock(side_effect=[RuntimeErrorSafe("not reaped"), None])
    manager._terminate = termination  # type: ignore[method-assign]
    operations: list[str] = []

    async def reap(_: Any) -> None:
        operations.append("reap")
        if len(operations) == 2:
            raise RuntimeErrorSafe("not reaped")

    termination.side_effect = reap
    with patch("os.close", side_effect=lambda _: operations.append("close")) as close:
        with pytest.raises(RuntimeErrorSafe, match="退出尚未确认"):
            await manager.stop(session.id)
    assert operations == ["close", "reap", "reap"]
    assert [call.args[0] for call in termination.call_args_list] == [process, relay]
    close.assert_called_once_with(123)
    assert session._master is None and session._relay is None
    assert session._process is process  # Keep the known child handle for retry.
    assert session.status == "cleanup_failed"
    assert session.to_dict()["cleanup_error"]
    assert [event["type"] for event in session._events] == ["notice"]
    assert [event async for event in manager.output(session.id)][0]["type"] == "notice"


@pytest.mark.asyncio
async def test_cleanup_failure_can_be_retried_without_reopening_a_terminal(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._process = cast(asyncio.subprocess.Process, child_process())
    manager._sessions[session.id] = session
    manager._command = AsyncMock(return_value=(0, b""))  # type: ignore[method-assign]
    manager._terminate = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeErrorSafe("not reaped"), None, None, None],
    )
    with pytest.raises(RuntimeErrorSafe):
        await manager.stop(session.id)
    first_stop = session._stop_task
    await manager.stop(session.id)
    assert session._stop_task is not first_stop
    assert session.status == "stopped" and session.cleanup_error is None
    assert session._process is None


@pytest.mark.asyncio
async def test_cleanup_failed_session_counts_toward_concurrency_limit(manager: RuntimeManager) -> None:
    for index in range(3):
        session = Session(str(index), manager.root / str(index), status="cleanup_failed")
        manager._sessions[session.id] = session
    manager.status = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(RuntimeErrorSafe, match="上限"):
        await manager.start()
    manager.status.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_rm_is_idempotent_only_when_daemon_confirms_exact_container_absent(
    manager: RuntimeManager,
) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._container = "budget-ai-specific-container"
    manager._sessions[session.id] = session
    manager._command = AsyncMock(side_effect=[(1, b""), (0, b"\n")])  # type: ignore[method-assign]
    await manager.stop(session.id)
    assert session.status == "stopped"
    assert [call.args for call in manager._command.call_args_list] == [
        ("rm", "--force", "budget-ai-specific-container"),
        ("ps", "--all", "--filter", "name=^budget-ai-specific-container$", "--quiet"),
    ]
    assert manager._command.call_args_list[1].kwargs == {"deadline_seconds": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rm_result,confirm_result",
    [
        (TimeoutError, (0, b"container-id\n")),
        (RuntimeErrorSafe("Docker offline"), TimeoutError),
        ((1, b""), (1, b"")),
        ((1, b""), (0, b"container-id\n")),
    ],
)
async def test_unconfirmed_container_removal_never_fakes_stopped_and_still_closes_host_resources(
    manager: RuntimeManager,
    rm_result: Any,
    confirm_result: Any,
) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._container = "budget-ai-specific-container"
    session._master = 123
    manager._sessions[session.id] = session
    manager._command = AsyncMock(  # type: ignore[method-assign]
        side_effect=[rm_result, confirm_result],
    )
    manager._terminate = AsyncMock()  # type: ignore[method-assign]
    with patch("os.close") as close:
        with pytest.raises(RuntimeErrorSafe, match="退出尚未确认"):
            await manager.stop(session.id)
    assert session.status == "cleanup_failed" and session.cleanup_error
    assert session._master is None
    close.assert_called_once_with(123)
    assert manager._terminate.await_count == 2
    assert [event["type"] for event in session._events] == ["notice"]


@pytest.mark.asyncio
async def test_rm_timeout_can_be_confirmed_absent_without_unbounded_wait(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._container = "budget-ai-specific-container"
    manager._sessions[session.id] = session
    manager._command = AsyncMock(side_effect=[TimeoutError, (0, b"")])  # type: ignore[method-assign]
    await manager.stop(session.id)
    assert session.status == "stopped"
    assert [call.kwargs["deadline_seconds"] for call in manager._command.call_args_list] == [10, 5]


@pytest.mark.asyncio
async def test_container_cleanup_failure_is_retryable(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._container = "budget-ai-specific-container"
    manager._sessions[session.id] = session
    manager._command = AsyncMock(  # type: ignore[method-assign]
        side_effect=[TimeoutError, TimeoutError, (0, b"")],
    )
    with pytest.raises(RuntimeErrorSafe):
        await manager.stop(session.id)
    assert session.status == "cleanup_failed"
    await manager.stop(session.id)
    assert session.status == "stopped" and session.cleanup_error is None


@pytest.mark.asyncio
async def test_invalid_container_name_is_never_a_broad_delete_or_filter(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._container = "budget-ai-.*"
    manager._sessions[session.id] = session
    manager._command = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(RuntimeErrorSafe):
        await manager.stop(session.id)
    manager._command.assert_not_awaited()
    assert session.status == "cleanup_failed"


@pytest.mark.asyncio
async def test_close_finishes_all_sessions_before_reporting_reap_failure(manager: RuntimeManager) -> None:
    sessions = [Session(str(i), manager.root / str(i), status="running") for i in range(2)]
    for index, session in enumerate(sessions):
        session._master = 123 + index
        manager._sessions[session.id] = session
    manager._command = AsyncMock(return_value=(0, b""))  # type: ignore[method-assign]
    manager._terminate = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeErrorSafe("not reaped"), None, None, None],
    )
    with patch("os.close") as close:
        with pytest.raises(RuntimeErrorSafe, match="部分 AI 进程退出尚未确认"):
            await manager.close()
    assert close.call_count == 2
    assert [s.status for s in sessions] == ["cleanup_failed", "stopped"]
    assert all(s._master is None for s in sessions)


@pytest.mark.asyncio
async def test_stopped_metadata_is_bounded_without_deleting_native_history(manager: RuntimeManager) -> None:
    manager.root.mkdir()
    history = manager.root / "agent/sessions"
    history.mkdir(parents=True)
    record = history / "native-session.jsonl"
    record.write_text("preserved")
    for index in range(MAX_HISTORY + 5):
        session = Session(str(index), manager.root / str(index), created_at=float(index), status="stopped")
        manager._sessions[session.id] = session
    active = Session("live", manager.root / "live", status="running")
    manager._sessions[active.id] = active
    manager._prune_history()
    assert len(manager._sessions) == MAX_HISTORY + 1
    assert "0" not in manager._sessions and "live" in manager._sessions
    assert record.read_text() == "preserved"


@pytest.mark.asyncio
async def test_no_terminal_output_is_a_start_failure_not_false_running(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc")
    process = AsyncMock()
    process.wait = AsyncMock(side_effect=asyncio.CancelledError)
    manager._task = lambda _, coroutine: coroutine.close()  # type: ignore[method-assign,assignment]
    master, slave = 123, 124

    async def deadline(coroutine: Any, _: float) -> Any:
        coroutine.close()
        raise TimeoutError

    with (
        patch("pty.openpty", return_value=(master, slave)),
        patch("os.set_blocking"),
        patch("fcntl.ioctl"),
        patch("os.close"),
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch("asyncio.wait_for", side_effect=deadline),
    ):
        with pytest.raises(RuntimeErrorSafe, match="未响应"):
            await manager._start_terminal(session)
    assert session.status == "starting"


@pytest.mark.asyncio
async def test_close_prevents_new_sessions_and_stops_all(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    manager._sessions[session.id] = session
    manager._command = AsyncMock(return_value=(0, b""))  # type: ignore[method-assign]
    await manager.close()
    assert session.status == "stopped"
    with pytest.raises(RuntimeErrorSafe, match="关闭"):
        await manager.start()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["status", "run", "relay", "terminal"])
async def test_close_catches_inflight_start_at_every_launch_stage(
    manager: RuntimeManager,
    phase: str,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    operations: list[str] = []

    async def stage(name: str) -> None:
        operations.append(name)
        if name == phase:
            entered.set()
            await release.wait()

    async def status() -> dict[str, Any]:
        await stage("status")
        return {"available": True}

    async def command(*args: str, **_: Any) -> tuple[int, bytes]:
        await stage(args[0])
        return 0, b""

    async def relay(_: Session) -> None:
        await stage("relay")

    async def terminal(_: Session) -> None:
        await stage("terminal")

    manager.status = status  # type: ignore[method-assign]
    manager._command = command  # type: ignore[method-assign]
    manager._start_relay = relay  # type: ignore[method-assign,assignment]
    manager._start_terminal = terminal  # type: ignore[method-assign,assignment]
    starting = asyncio.create_task(manager.start())
    await entered.wait()
    closing = asyncio.create_task(manager.close())
    await asyncio.sleep(0)
    assert manager._closed is True
    assert not closing.done()  # Wait for the launch lock, don't miss its container.
    release.set()
    with pytest.raises(RuntimeErrorSafe, match="关闭"):
        await starting
    await closing
    assert all(s["status"] == "stopped" for s in manager.list_sessions())
    assert manager._cleaner is None
    if phase == "status":
        assert operations == ["status"]  # Close arrived before any container existed.
        assert manager.list_sessions() == []
    else:
        assert operations[-1] == "rm"
        assert operations.count("rm") == 1  # Partial container cleanup is joined, not duplicated.


@pytest.mark.asyncio
async def test_close_rejects_a_start_queued_behind_another_start(manager: RuntimeManager) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def status() -> dict[str, Any]:
        entered.set()
        await release.wait()
        return {"available": True}

    manager.status = status  # type: ignore[method-assign]
    first = asyncio.create_task(manager.start())
    await entered.wait()
    queued = asyncio.create_task(manager.start())
    closing = asyncio.create_task(manager.close())
    await asyncio.sleep(0)
    release.set()
    for starting in (first, queued):
        with pytest.raises(RuntimeErrorSafe, match="关闭"):
            await starting
    await closing
    assert manager.list_sessions() == []


@pytest.mark.asyncio
async def test_cancelled_stop_caller_does_not_cancel_cleanup(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    session._container = "budget-ai-cancelled-stop"
    manager._sessions[session.id] = session
    entered, release = asyncio.Event(), asyncio.Event()

    async def command(*_: str, **__: Any) -> tuple[int, bytes]:
        entered.set()
        await release.wait()
        return 0, b""

    manager._command = command  # type: ignore[method-assign]
    caller = asyncio.create_task(manager.stop(session.id))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert session.status == "running"
    assert session._stop_task is not None and not session._stop_task.cancelled()
    release.set()
    await manager.close()
    assert session.status == "stopped"
    assert session._stop_task.done()


@pytest.mark.asyncio
async def test_stop_and_new_start_use_same_native_history_profile(manager: RuntimeManager) -> None:
    manager.status = AsyncMock(return_value={"available": True})  # type: ignore[method-assign]
    manager._command = AsyncMock(return_value=(0, b"container-id"))  # type: ignore[method-assign]
    manager._start_relay = AsyncMock()  # type: ignore[method-assign]
    manager._start_terminal = AsyncMock()  # type: ignore[method-assign]
    first = await manager.start()
    sessions = manager.root / "agent/sessions"
    sessions.mkdir()
    history = sessions / "native-history.jsonl"
    history.write_text('{"type":"session"}\n')
    await manager.stop(first.id)
    second = await manager.start()
    assert first.directory != second.directory
    assert history.exists()
    run_calls = [call for call in manager._command.call_args_list if call.args[0] == "run"]
    mounts = [call.args[call.args.index("--mount") + 3] for call in run_calls]
    assert mounts == [f"type=bind,src={manager.root / 'agent'},dst=/agent"] * 2
    await manager.close()


@pytest.mark.asyncio
async def test_idle_cleanup_stops_even_attached_runaway_session(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running", last_activity=time.time() - 100)
    session._attached = 1
    manager._sessions[session.id] = session
    manager.idle_seconds = 0.01
    stop_mock = AsyncMock()
    manager._stop_session = stop_mock  # type: ignore[method-assign]

    async def sleep(_: float) -> None:
        if stop_mock.await_count:
            raise asyncio.CancelledError

    with patch("asyncio.sleep", side_effect=sleep):
        await manager._cleanup_idle()
    manager._stop_session.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_bridge_frames_are_serialized_without_stderr(manager: RuntimeManager) -> None:
    session = Session("abc", manager.root / "abc", status="running")
    process = AsyncMock()
    process.stdin = AsyncMock()
    process.stdin.write = lambda _: None
    frames: list[dict[str, Any]] = []
    process.stdin.write = lambda value: frames.append(json.loads(value))
    session._relay = process
    await manager._dispatch(session, {"id": "request-1", "type": "request"})
    assert [frame["type"] for frame in frames] == ["response_start", "response_chunk", "response_end"]
    assert all(frame["id"] == "request-1" for frame in frames)


def test_container_assets_pin_pi_and_disable_install_scripts() -> None:
    root = Path(__file__).resolve().parents[2] / "apps/ai-agent"
    package = json.loads((root / "package.json").read_text())
    lock = json.loads((root / "package-lock.json").read_text())
    assert package["dependencies"]["@earendil-works/pi-coding-agent"] == "1.2.0"
    assert lock["packages"]["node_modules/@earendil-works/pi-coding-agent"]["version"] == "1.2.0"
    dockerfile = (root / "Dockerfile").read_text()
    assert "FROM node:24-bookworm-slim" in dockerfile
    assert "npm ci --omit=dev --ignore-scripts" in dockerfile
    assert "USER node" in dockerfile


def test_budget_cli_lists_known_resources_and_rejects_arbitrary_url() -> None:
    cli = Path(__file__).resolve().parents[2] / "apps/ai-agent/budget-api.mjs"
    listed = subprocess.run(["node", str(cli), "list"], capture_output=True, text=True, check=True)
    assert "fy27-budget" in json.loads(listed.stdout)
    denied = subprocess.run(
        ["node", str(cli), "get", "http://127.0.0.1:8018/api/v1/write"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert denied.returncode == 1
    assert "未知资源" in denied.stderr


def test_budget_cli_rejects_invalid_version_without_any_network() -> None:
    cli = Path(__file__).resolve().parents[2] / "apps/ai-agent/budget-api.mjs"
    denied = subprocess.run(
        ["node", str(cli), "get", "fy27-forecast", "--version", "../write"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert denied.returncode == 1
    assert "版本标识无效" in denied.stderr


@pytest.mark.asyncio
async def test_real_python_bridge_frames_reach_node_http_response(
    manager: RuntimeManager,
    tmp_path: Path,
) -> None:
    """Contract across actual Python response frames and actual JS consumer.

    The broker is ephemeral loopback + a private test Unix socket. This catches
    protocol drift (for example data versus body) without Docker or model calls.
    """

    async def reader(_: str) -> dict[str, Any]:
        return {"message": "测试预算", "revenue": 123.45}

    bridge = BridgeHandler(manager.config, reader)
    frames = [
        frame
        async for frame in bridge.handle(
            {
                "type": "request",
                "id": "test-request",
                "method": "GET",
                "path": "/budget/help",
                "body": "",
            }
        )
    ]
    program = r"""
import fs from 'node:fs';
import net from 'node:net';
import {once} from 'node:events';
const original = fs.readFileSync(process.argv[1], 'utf8');
const socketPath = process.argv[2];
const frames = JSON.parse(process.argv[3]);
const source = original
  .replace('"/tmp/budget-ai-relay.sock"', JSON.stringify(socketPath))
  .replace('server.listen(4081, "127.0.0.1");', 'server.listen(0, "127.0.0.1");')
  + '\nexport {server, socketServer};';
const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const {server, socketServer} = await import(moduleUrl);
if (!server.listening) await once(server, 'listening');
if (!socketServer.listening) await once(socketServer, 'listening');
const socket = net.createConnection(socketPath);
await once(socket, 'connect');
let buffered = '';
socket.on('data', data => {
  buffered += data.toString();
  const end = buffered.indexOf('\n');
  if (end < 0) return;
  const request = JSON.parse(buffered.slice(0, end));
  buffered = buffered.slice(end + 1);
  for (const frame of frames) socket.write(JSON.stringify({...frame, id: request.id}) + '\n');
});
const response = await fetch(`http://127.0.0.1:${server.address().port}/budget/help`);
console.log(JSON.stringify({status:response.status, body:await response.json()}));
socket.destroy();
server.close();
socketServer.close();
"""
    # macOS Unix sockets have a short path limit; pytest's default root may
    # exceed it. This private, uniquely created directory is only test state.
    with tempfile.TemporaryDirectory(prefix="budget-ai-", dir="/tmp") as short_directory:
        process = await asyncio.create_subprocess_exec(
            "node",
            "--input-type=module",
            "-e",
            program,
            str(PROJECT_ROOT / "apps/ai-agent/launch.mjs"),
            str(Path(short_directory) / "relay.sock"),
            json.dumps(frames),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
    assert process.returncode == 0, stderr.decode()
    assert json.loads(stdout) == {"status": 200, "body": {"message": "测试预算", "revenue": 123.45}}
