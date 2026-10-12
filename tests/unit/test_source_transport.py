import asyncio
import json
import logging
import os
import signal
import sys
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from packages.config.settings import Settings
from packages.source_snapshot.adapter import (
    GuanCliSourceAdapter,
    SourceAdapterError,
    _query_stage,
    _terminate_process,
)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, guancli_retry_backoff_seconds=0, **overrides)


class FinishedProcess:
    def __init__(self, stdout: bytes = b"[]", stderr: bytes = b"", returncode: int = 0) -> None:
        self.output = stdout, stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.output


@pytest.mark.parametrize(
    ("stderr", "code", "retryable"),
    [
        (b"HTTP 401 unauthorized token expired", "SOURCE_AUTH_REQUIRED", False),
        (b"HTTP 403 forbidden", "SOURCE_PERMISSION_DENIED", False),
        (b"x509: certificate signed by unknown authority", "SOURCE_TLS_CERTIFICATE_INVALID", False),
        (b"TLS handshake: EOF", "SOURCE_TLS_CONNECTION_FAILED", True),
        (b"HTTP 429 too many requests", "SOURCE_RATE_LIMITED", True),
        (b"HTTP 503 service unavailable", "SOURCE_SERVICE_UNAVAILABLE", True),
        (b"context deadline exceeded", "SOURCE_QUERY_TIMEOUT", True),
        (b"Post https://bi.example/query?token=SECRET: EOF", "SOURCE_NETWORK_ERROR", True),
        (b"unrecognized query", "SOURCE_QUERY_FAILED", False),
    ],
)
async def test_classifies_readonly_failure_without_leaking_output(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stderr: bytes,
    code: str,
    retryable: bool,
) -> None:
    process = FinishedProcess(stderr=stderr + b"\npassword=PRIVATE cookie=PRIVATE", returncode=1)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    caplog.set_level(logging.WARNING)
    adapter = GuanCliSourceAdapter(settings(guancli_retry_count=0))
    with pytest.raises(SourceAdapterError) as caught:
        await adapter._run_json("--raw", "ds", "preview", "dataset", "--limit", "0")
    error = caught.value
    assert error.code == code
    assert error.retryable is retryable
    assert error.diagnostic_id
    assert error.diagnostics["stage"] == "metadata"
    assert set(error.diagnostics) == {"category", "stage", "attempt", "elapsed_ms", "return_code"}
    for record in caplog.records:
        assert "PRIVATE" not in repr(record.__dict__)
        assert "SECRET" not in repr(record.__dict__)
        assert "https://" not in repr(record.__dict__)
        assert error.diagnostic_id in record.getMessage()
        assert code in record.getMessage()
    assert "PRIVATE" not in str(error)


async def test_transient_failure_retries_then_preserves_decimal_precision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn = AsyncMock(
        side_effect=[
            FinishedProcess(stderr=b"EOF", returncode=1),
            FinishedProcess(stdout=b'[{"income": 0.1234567890123456789}]'),
        ]
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = GuanCliSourceAdapter(settings())
    payload = await adapter._run_json("ds", "preview", "dataset", "-f", "json")
    assert payload[0]["income"] == Decimal("0.1234567890123456789")
    assert spawn.await_count == 2


async def test_transient_retries_stop_at_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = AsyncMock(return_value=FinishedProcess(stderr=b"EOF", returncode=1))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(SourceAdapterError) as caught:
        await GuanCliSourceAdapter(settings(guancli_retry_count=2))._run_json("ds", "preview", "dataset")
    assert caught.value.code == "SOURCE_NETWORK_ERROR"
    assert caught.value.diagnostics["attempt"] == 3
    assert spawn.await_count == 3


async def test_retry_delay_never_exceeds_shared_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = AsyncMock(return_value=FinishedProcess(stderr=b"EOF", returncode=1))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = GuanCliSourceAdapter(Settings(_env_file=None, guancli_retry_backoff_seconds=1))
    with pytest.raises(SourceAdapterError) as caught:
        async with adapter.query_budget(0.05):
            await adapter._run_json("ds", "preview", "dataset")
    assert caught.value.code == "SOURCE_NETWORK_ERROR"
    assert spawn.await_count == 1


async def test_missing_cli_reports_configuration_error_without_echoing_path(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spawn = AsyncMock(side_effect=FileNotFoundError("PRIVATE_PATH"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(SourceAdapterError) as caught:
        await GuanCliSourceAdapter(settings())._run_json("ds", "get", "dataset", "--brief")
    assert caught.value.code == "SOURCE_CLI_UNAVAILABLE"
    assert "PRIVATE_PATH" not in caplog.text
    assert spawn.await_count == 1


async def test_authentication_failure_is_not_retried_even_inside_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn = AsyncMock(return_value=FinishedProcess(stdout=b'{"code":401,"message":"unauthorized"}'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = GuanCliSourceAdapter(settings())
    with pytest.raises(SourceAdapterError, match="登录已失效"):
        await adapter._run_json("--raw", "ds", "get", "dataset", "--brief")
    assert spawn.await_count == 1


async def test_preflight_reads_live_resources_without_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = AsyncMock(return_value=FinishedProcess(stdout=b'{"response":{"id":"dataset"}}'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = GuanCliSourceAdapter(settings())
    await adapter.preflight(("dataset", "dataset", "other"))
    assert spawn.await_count == 2
    for call in spawn.call_args_list:
        assert call.args[3:5] == ("--raw", "ds")
        assert "get" in call.args
        assert "preview" not in call.args
        assert "--brief" in call.args


def test_proxy_is_connector_scoped_and_keeps_local_addresses_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://old.example:1234")
    monkeypatch.setenv("no_proxy", "internal.example")
    adapter = GuanCliSourceAdapter(settings(guancli_proxy_url="http://127.0.0.1:12345"))
    environment = adapter._environment()
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:12345"
    assert environment["http_proxy"] == environment["HTTPS_PROXY"]
    assert set(environment["NO_PROXY"].split(",")) >= {"internal.example", "localhost", "127.0.0.1", "::1"}
    assert os.environ["HTTP_PROXY"] == "http://old.example:1234"


@pytest.mark.parametrize(
    "proxy", ["not-a-proxy", "ftp://example.com", "http://example.com/path", "http://example.com:99999"]
)
def test_invalid_proxy_settings_are_rejected(proxy: str) -> None:
    with pytest.raises(ValidationError):
        settings(guancli_proxy_url=proxy)


async def test_invalid_json_is_not_published_or_silently_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = AsyncMock(return_value=FinishedProcess(stdout=b"private invalid response"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(SourceAdapterError) as caught:
        await GuanCliSourceAdapter(settings())._run_json("ds", "preview", "dataset")
    assert caught.value.code == "SOURCE_RESPONSE_INVALID"
    assert caught.value.retryable is False
    assert spawn.await_count == 1


async def test_write_commands_are_never_launched_or_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(SourceAdapterError) as caught:
        await GuanCliSourceAdapter(settings())._run_json("ds", "delete", "dataset")
    assert caught.value.code == "SOURCE_QUERY_UNSUPPORTED"
    spawn.assert_not_awaited()


@pytest.mark.skipif(os.name != "posix", reason="Process-group cleanup is a POSIX guarantee")
@pytest.mark.parametrize("mode", ["query_timeout", "total_timeout", "cancelled"])
async def test_timeout_and_cancellation_kill_wrapper_and_child(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    real_spawn = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []
    children: list[int] = []
    process_started = asyncio.Event()
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(child.pid,flush=True); time.sleep(60)"
    )

    async def spawn(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        process = await real_spawn(sys.executable, "-c", script, **kwargs)
        assert process.stdout is not None
        processes.append(process)
        try:
            children.append(int(await process.stdout.readline()))
        except asyncio.CancelledError:
            await _terminate_process(process)
            raise
        process_started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = GuanCliSourceAdapter(
        settings(
            guancli_retry_count=0,
            guancli_retry_attempt_timeout_seconds=0.05 if mode == "query_timeout" else 60,
        )
    )
    if mode == "query_timeout":
        with pytest.raises(SourceAdapterError) as caught:
            await adapter._run_json("ds", "preview", "dataset")
        assert caught.value.code == "SOURCE_QUERY_TIMEOUT"
    elif mode == "total_timeout":
        with pytest.raises(SourceAdapterError) as caught:
            async with adapter.query_budget(1):
                await adapter._run_json("ds", "preview", "dataset")
        assert caught.value.code == "SOURCE_REFRESH_TIMEOUT"
    else:
        task = asyncio.create_task(adapter._run_json("ds", "preview", "dataset"))
        await asyncio.wait_for(process_started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert processes and all(process.returncode is not None for process in processes)
    for child in children:
        check = await real_spawn("ps", "-o", "stat=", "-p", str(child), stdout=asyncio.subprocess.PIPE)
        output, _ = await check.communicate()
        # A killed grandchild can briefly be a zombie until the OS reaps it; it must never run.
        assert not output.strip() or output.strip().startswith(b"Z")


@pytest.mark.skipif(os.name != "posix", reason="Process-group cleanup is a POSIX guarantee")
@pytest.mark.parametrize("pipe", ["stdout", "stderr", "both"])
@pytest.mark.parametrize("mode", ["query_timeout", "total_timeout", "cancelled"])
async def test_full_pipes_are_discarded_before_reaping_on_timeout_or_cancel(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    pipe: str,
    mode: str,
) -> None:
    real_spawn = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []
    process_started = asyncio.Event()
    descriptors = {"stdout": [1], "stderr": [2], "both": [1, 2]}[pipe]
    script = (
        "import os,threading,time; "
        "print('READY',flush=True); "
        "threads=[threading.Thread(target=os.write,args=(fd,b'PRIVATE_OUTPUT'*100000)) "
        f"for fd in {descriptors}]; "
        "[thread.start() for thread in threads]; time.sleep(60)"
    )

    async def unread_communicate() -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def spawn(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        process = await real_spawn(sys.executable, "-c", script, **kwargs)
        processes.append(process)
        assert process.stdout is not None
        try:
            assert await process.stdout.readline() == b"READY\n"
            # Deliberately stop communicate from reading. Both StreamReaders
            # must reach backpressure before timeout / cancellation occurs.
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            await _terminate_process(process)
            raise
        process.communicate = unread_communicate  # type: ignore[method-assign]
        process_started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    caplog.set_level(logging.INFO)
    adapter = GuanCliSourceAdapter(
        settings(
            guancli_retry_count=0,
            guancli_retry_attempt_timeout_seconds=0.05 if mode == "query_timeout" else 60,
        )
    )

    async def query() -> object:
        if mode == "total_timeout":
            async with adapter.query_budget(0.5):
                return await adapter._run_json("ds", "preview", "dataset")
        return await adapter._run_json("ds", "preview", "dataset")

    task = asyncio.create_task(query())
    try:
        if mode == "cancelled":
            await asyncio.wait_for(process_started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=4)
        else:
            with pytest.raises(SourceAdapterError) as caught:
                await asyncio.wait_for(task, timeout=4)
            assert caught.value.code == (
                "SOURCE_REFRESH_TIMEOUT" if mode == "total_timeout" else "SOURCE_QUERY_TIMEOUT"
            )
        assert processes and all(process.returncode is not None for process in processes)
        for process in processes:
            assert process.stdout is not None and process.stdout.at_eof()
            assert process.stderr is not None and process.stderr.at_eof()
        assert not adapter._active_queries
        assert "PRIVATE_OUTPUT" not in caplog.text
        if mode == "cancelled":
            assert "guancli_query_cancelled" in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Test-failure cleanup must itself stay finite even against the old bug.
        for process in processes:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(asyncio.subprocess.Process.communicate(process), timeout=2)


@pytest.mark.skipif(os.name != "posix", reason="Process-group cleanup is a POSIX guarantee")
@pytest.mark.parametrize("repeated_cancellation", [False, True])
async def test_cleanup_is_bounded_when_escaped_child_keeps_pipe_open(
    repeated_cancellation: bool,
) -> None:
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
        "print(child.pid,flush=True); time.sleep(60)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    child_pid = int(await asyncio.wait_for(process.stdout.readline(), timeout=2))
    started_at = asyncio.get_running_loop().time()
    cleanup = asyncio.create_task(_terminate_process(process))
    try:
        if repeated_cancellation:
            await asyncio.sleep(0.05)
            cleanup.cancel()
            await asyncio.sleep(0.05)
            cleanup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(cleanup, timeout=4)
        else:
            await asyncio.wait_for(cleanup, timeout=4)
        elapsed = asyncio.get_running_loop().time() - started_at
        assert 1.8 <= elapsed < 3.5
        assert process.returncode == -signal.SIGKILL
        assert process.stdout.at_eof()
        assert process.stderr is not None and process.stderr.at_eof()
        assert not any(
            task.get_coro().__name__ == "_drain_and_reap"
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        )
    finally:
        # Only this test's explicit escaped process is killed; connector cleanup
        # never searches for or terminates unrelated host processes.
        try:
            os.killpg(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        await asyncio.wait_for(process.communicate(), timeout=2)


async def test_shared_budget_cancels_parallel_sibling_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    pending_started = asyncio.Event()
    pending_cancelled = asyncio.Event()

    async def query(arguments: tuple[str, ...]) -> object:
        if arguments[-1] == "slow":
            pending_started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                pending_cancelled.set()
                raise
        await pending_started.wait()
        raise SourceAdapterError("SOURCE_AUTH_REQUIRED", "authentication failed")

    adapter = GuanCliSourceAdapter(settings())
    monkeypatch.setattr(adapter, "_run_json_with_retries", query)
    with pytest.raises(SourceAdapterError):
        async with adapter.query_budget(1):
            await asyncio.gather(adapter._run_json("fast"), adapter._run_json("slow"))
    assert pending_cancelled.is_set()
    assert not adapter._active_queries


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("--raw", "ds", "get", "dataset", "--brief"), "preflight"),
        (("--raw", "ds", "preview", "dataset", "--limit", "0"), "metadata"),
        (
            ("--raw", "ds", "preview", "dataset", "--limit", "0", "--filter", "private EQ value"),
            "partition_count",
        ),
        (("ds", "preview", "dataset", "--columns", "dtdate", "--sort-desc", "dtdate"), "latest_partition"),
        (("ds", "preview", "dataset", "--columns", "private_field"), "distinct_values"),
        (("ds", "preview", "dataset", "--columns", "field_a,field_b"), "partition_rows"),
        (("--raw", "etl", "get", "etl_identifier", "--brief"), "etl_definition"),
        (("ds", "delete", "dataset", "ds", "preview"), "unsupported"),
    ],
)
def test_safe_query_stage_distinguishes_source_operations(arguments: tuple[str, ...], expected: str) -> None:
    assert _query_stage(arguments) == expected


async def test_first_success_has_safe_timings_and_refresh_trace(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spawn = AsyncMock(return_value=FinishedProcess(stdout=b'[{"private":"PRIVATE_BUSINESS_VALUE"}]'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    caplog.set_level(logging.INFO)
    adapter = GuanCliSourceAdapter(settings())
    arguments = (
        "ds",
        "preview",
        adapter.settings.summary_dataset_id,
        "--columns",
        "PRIVATE_COLUMN,another",
        "--filter",
        "PRIVATE_FILTER EQ PRIVATE_VALUE",
    )
    async with adapter.query_budget(1):
        await adapter._run_json(*arguments)
        await adapter._run_json(*arguments)
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("guancli_query_succeeded ")
    ]
    assert len(messages) == 2
    fields = [json.loads(message.split(" ", 1)[1]) for message in messages]
    assert fields[0]["refresh_id"] == fields[1]["refresh_id"]
    assert fields[0]["diagnostic_id"] != fields[1]["diagnostic_id"]
    for item in fields:
        assert item["stage"] == "partition_rows"
        assert item["resource_alias"] == "summary"
        assert item["attempt"] == 1
        assert item["attempt_ms"] >= 0 and item["queue_ms"] >= 0
        assert item["elapsed_ms"] >= item["attempt_ms"]
    assert "PRIVATE_" not in caplog.text
    assert adapter.settings.summary_dataset_id not in caplog.text


async def test_all_adapter_instances_share_bounded_query_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    release = asyncio.Event()
    two_active = asyncio.Event()
    active = 0
    peak = 0

    class BlockingProcess(FinishedProcess):
        async def communicate(self) -> tuple[bytes, bytes]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_active.set()
            try:
                await release.wait()
                return self.output
            finally:
                active -= 1

    spawn = AsyncMock(side_effect=lambda *args, **kwargs: BlockingProcess())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapters = [GuanCliSourceAdapter(settings(guancli_max_concurrent_queries=2)) for _ in range(5)]
    tasks = [asyncio.create_task(adapter._run_json("ds", "preview", "dataset")) for adapter in adapters]
    try:
        await asyncio.wait_for(two_active.wait(), timeout=1)
        assert spawn.await_count == 2
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert peak == 2
    assert spawn.await_count == 5


async def test_queue_deadline_does_not_launch_process_or_release_unowned_slot(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    release = asyncio.Event()
    first_started = asyncio.Event()

    class BlockingProcess(FinishedProcess):
        async def communicate(self) -> tuple[bytes, bytes]:
            first_started.set()
            await release.wait()
            return self.output

    spawn = AsyncMock(side_effect=lambda *args, **kwargs: BlockingProcess())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    caplog.set_level(logging.INFO)
    first = GuanCliSourceAdapter(settings(guancli_max_concurrent_queries=1))
    second = GuanCliSourceAdapter(settings(guancli_max_concurrent_queries=1))
    held = asyncio.create_task(first._run_json("ds", "preview", "dataset"))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    third: asyncio.Task[object] | None = None
    try:
        with pytest.raises(SourceAdapterError) as caught:
            async with second.query_budget(0.05):
                await second._run_json("ds", "preview", "dataset")
        assert caught.value.code == "SOURCE_REFRESH_TIMEOUT"
        assert spawn.await_count == 1
        third = asyncio.create_task(second._run_json("ds", "preview", "dataset"))
        await asyncio.sleep(0)
        assert spawn.await_count == 1
    finally:
        release.set()
        await held
        if third is not None:
            await third
    cancelled = [
        record for record in caplog.records if record.getMessage().startswith("guancli_query_cancelled ")
    ]
    assert cancelled
    log = json.loads(cancelled[0].getMessage().split(" ", 1)[1])
    assert log["attempt_ms"] == 0 and log["queue_ms"] > 0


@pytest.mark.parametrize("count", [0, 3])
async def test_leaf_partition_counts_once_and_validates_returned_rows(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    adapter = GuanCliSourceAdapter(settings())
    count_query = AsyncMock(return_value=count)
    rows_query = AsyncMock(return_value=[{"value": index} for index in range(count)])
    monkeypatch.setattr(adapter, "_preview_count", count_query)
    monkeypatch.setattr(adapter, "_preview", rows_query)
    result = await adapter._complete_partition("dataset", columns=("value",), filters=(), split_fields=())
    assert len(result) == count
    count_query.assert_awaited_once_with("dataset", ())
    assert rows_query.await_count == (1 if count else 0)


async def test_count_reuse_does_not_weaken_leaf_completeness(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GuanCliSourceAdapter(settings())
    monkeypatch.setattr(adapter, "_preview_count", AsyncMock(return_value=2))
    monkeypatch.setattr(adapter, "_preview", AsyncMock(return_value=[{"value": 1}]))
    with pytest.raises(SourceAdapterError) as caught:
        await adapter._complete_partition("dataset", columns=("value",), filters=(), split_fields=())
    assert caught.value.code == "SOURCE_PREVIEW_INCOMPLETE"


@pytest.mark.parametrize(
    ("child_counts", "expected_code"), [([1000, 2000], None), ([1000, 1000], "SOURCE_PARTITION_INCOMPLETE")]
)
async def test_recursive_partitions_count_once_per_node_and_keep_parent_proof(
    monkeypatch: pytest.MonkeyPatch,
    child_counts: list[int],
    expected_code: str | None,
) -> None:
    adapter = GuanCliSourceAdapter(settings(source_preview_target_rows=1000))
    count_query = AsyncMock(side_effect=[3000, *child_counts])
    rows_query = AsyncMock(side_effect=[[{}] * count for count in child_counts])
    monkeypatch.setattr(adapter, "_preview_count", count_query)
    monkeypatch.setattr(adapter, "_distinct_values", AsyncMock(return_value=["A", "B"]))
    monkeypatch.setattr(adapter, "_preview", rows_query)
    if expected_code:
        with pytest.raises(SourceAdapterError) as caught:
            await adapter._complete_partition(
                "dataset", columns=("value",), filters=(), split_fields=("group",)
            )
        assert caught.value.code == expected_code
    else:
        result = await adapter._complete_partition(
            "dataset", columns=("value",), filters=(), split_fields=("group",)
        )
        assert len(result) == 3000
    assert count_query.await_count == 3
    assert rows_query.await_count == 2


async def test_count_reuse_keeps_chunk_safety_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GuanCliSourceAdapter(settings())
    monkeypatch.setattr(adapter, "_preview_count", AsyncMock(return_value=60_001))
    rows_query = AsyncMock()
    monkeypatch.setattr(adapter, "_preview", rows_query)
    with pytest.raises(SourceAdapterError) as caught:
        await adapter._complete_partition("dataset", columns=("value",), filters=(), split_fields=())
    assert caught.value.code == "SOURCE_PREVIEW_CHUNK_LIMIT_REACHED"
    rows_query.assert_not_awaited()
