import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from weakref import ReferenceType, WeakKeyDictionary, ref

from packages.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class _QueryPool:
    limit: int
    semaphore: asyncio.BoundedSemaphore
    in_use: int = 0


# The API has one running event loop per process. Every adapter in it shares a
# pool, not a separate limit per refresh. Weak references avoid retaining closed
# test loops or an idle pool after its queries have finished.
_QUERY_POOLS: WeakKeyDictionary[asyncio.AbstractEventLoop, ReferenceType[_QueryPool]] = WeakKeyDictionary()


def _query_pool(limit: int) -> _QueryPool:
    loop = asyncio.get_running_loop()
    existing = _QUERY_POOLS.get(loop)
    pool = existing() if existing is not None else None
    if pool is None or (pool.limit != limit and pool.in_use == 0):
        pool = _QueryPool(limit, asyncio.BoundedSemaphore(limit))
        _QUERY_POOLS[loop] = ref(pool)
    elif pool.limit != limit:
        raise SourceAdapterError(
            "SOURCE_CONCURRENCY_CONFIGURATION_CHANGED",
            "观远查询并发配置已变更，请等待当前更新结束或重启服务",
        )
    return pool


DIMENSION_FIELDS = ("季节财年", "季度", "赛道", "产品", "年级", "科目", "教学区")
ROSTER_METRIC_FIELDS = (
    "业务新生人次",
    "招生人次(三分之二出勤口径)",
    "原班人次",
    "续班人次",
    "专项班续班人次",
)
SUMMARY_METRIC_FIELDS = (
    "新生人次",
    "招生人次(三分之二出勤口径)",
    "原班人次",
    "续班人次",
    "专项班续班人次",
)


@dataclass(frozen=True)
class DatasetMetadata:
    dataset_id: str
    name: str
    version: int
    row_count: int
    updated_at: datetime
    last_execution_at: datetime


@dataclass(frozen=True)
class SourceExtract:
    roster: DatasetMetadata
    summary: DatasetMetadata
    latest_summary_partition: str
    fiscal_season_start: int
    fiscal_season_end: int
    roster_rows: list[dict[str, object]]
    summary_rows: list[dict[str, object]]


class SourceAdapterError(Exception):
    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
        diagnostic_id: str | None = None,
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.diagnostic_id = diagnostic_id
        self.diagnostics = diagnostics or {}


class SourceAdapter(Protocol):
    async def extract(self, fiscal_year: int) -> SourceExtract: ...


class GuanCliSourceAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._deadline: float | None = None
        self._active_queries: set[asyncio.Task[Any]] = set()
        self._refresh_id = uuid.uuid4().hex[:16]

    @asynccontextmanager
    async def query_budget(self, total_seconds: float | None = None) -> AsyncIterator[None]:
        """Bound the entire read-only extraction, including parallel queries and retry waits."""
        previous_deadline = self._deadline
        previous_refresh_id = self._refresh_id
        if previous_deadline is None:
            self._refresh_id = uuid.uuid4().hex[:16]
        deadline = asyncio.get_running_loop().time() + (
            total_seconds if total_seconds is not None else self.settings.forecast_refresh_timeout_seconds
        )
        self._deadline = min(previous_deadline, deadline) if previous_deadline else deadline
        try:
            async with asyncio.timeout_at(self._deadline):
                yield
        except TimeoutError:
            raise self._error(
                "SOURCE_REFRESH_TIMEOUT",
                "本次观远取数超时，已保留上次预测，请重试更新",
                category="timeout",
                stage="full_extract",
                attempt=0,
                retryable=True,
            ) from None
        finally:
            # asyncio.gather does not cancel siblings after an ordinary query failure.
            # Never let those commands outlive the failed refresh or its request deadline.
            if previous_deadline is None:
                pending = tuple(task for task in self._active_queries if not task.done())
                for task in pending:
                    if task is not asyncio.current_task():
                        task.cancel()
                if pending:
                    await asyncio.gather(
                        *(task for task in pending if task is not asyncio.current_task()),
                        return_exceptions=True,
                    )
            self._deadline = previous_deadline
            self._refresh_id = previous_refresh_id

    async def preflight(self, dataset_ids: tuple[str, ...]) -> None:
        """Read live resource metadata; this checks access, not preview-service readiness."""
        for dataset_id in dict.fromkeys(dataset_ids):
            payload = await self._run_json("--raw", "ds", "get", dataset_id, "--brief")
            if not isinstance(payload, dict):
                raise self._error(
                    "SOURCE_RESPONSE_INVALID",
                    "观远连接检查未返回有效数据，请重试更新",
                    category="response",
                    stage="preflight",
                    attempt=1,
                )

    def _environment(self) -> dict[str, str]:
        """Apply only connector-scoped proxy settings; never change the host environment."""
        environment = dict(os.environ)
        proxy = self.settings.guancli_proxy_url
        if proxy is not None:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                environment[name] = proxy
        bypass = ",".join(
            dict.fromkeys(
                value.strip()
                for value in (
                    environment.get("NO_PROXY", "")
                    + ","
                    + environment.get("no_proxy", "")
                    + ",127.0.0.1,localhost,::1"
                ).split(",")
                if value.strip()
            )
        )
        environment["NO_PROXY"] = bypass
        environment["no_proxy"] = bypass
        return environment

    def _error(
        self,
        code: str,
        message: str,
        *,
        category: str,
        stage: str,
        attempt: int,
        retryable: bool = False,
        diagnostic_id: str | None = None,
        elapsed_ms: int = 0,
        return_code: int | None = None,
        attempt_ms: int = 0,
        queue_ms: int = 0,
        resource_alias: str | None = None,
    ) -> SourceAdapterError:
        diagnostic_id = diagnostic_id or uuid.uuid4().hex[:16]
        # Strict allowlist: raw stderr/stdout, query values, URLs, tokens and rows never enter logs.
        diagnostics: dict[str, object] = {
            "category": category,
            "stage": stage,
            "attempt": attempt,
            "elapsed_ms": elapsed_ms,
        }
        if return_code is not None:
            diagnostics["return_code"] = return_code
        log_fields = {
            "source_code": code,
            "diagnostic_id": diagnostic_id,
            "refresh_id": self._refresh_id,
            "attempt_ms": attempt_ms,
            "queue_ms": queue_ms,
            "resource_alias": resource_alias,
            **diagnostics,
        }
        logger.warning(
            "guancli_query_failed %s",
            json.dumps(log_fields, ensure_ascii=False, sort_keys=True),
            extra=log_fields,
        )
        return SourceAdapterError(
            code,
            message,
            retryable=retryable,
            diagnostic_id=diagnostic_id,
            diagnostics=diagnostics,
        )

    async def extract(self, fiscal_year: int) -> SourceExtract:
        if fiscal_year < 2028:
            raise SourceAdapterError("FY27_FINALIZED", "FY27 及以前已定版，不从 BI 重建预算来源")
        prior_fiscal_year = fiscal_year - 1
        fiscal_season_start = prior_fiscal_year * 10 + 1
        fiscal_season_end = prior_fiscal_year * 10 + 4
        roster = await self._metadata(self.settings.roster_dataset_id)
        summary = await self._metadata(self.settings.summary_dataset_id)
        latest_rows = await self._preview(
            self.settings.summary_dataset_id,
            columns=("dtdate",),
            limit=1,
            sort_desc="dtdate",
        )
        if not latest_rows or not latest_rows[0].get("dtdate"):
            raise SourceAdapterError("SUMMARY_PARTITION_MISSING", "指标汇总宽表没有可用 dtdate 分区")
        latest_partition = str(latest_rows[0]["dtdate"])

        roster_rows: list[dict[str, object]] = []
        summary_rows: list[dict[str, object]] = []
        for fiscal_season in range(fiscal_season_start, fiscal_season_end + 1):
            roster_rows.extend(
                await self._complete_partition(
                    self.settings.roster_dataset_id,
                    columns=(
                        *DIMENSION_FIELDS,
                        *ROSTER_METRIC_FIELDS,
                        *(("招生人次", "目标标识(目标,数据)") if fiscal_year >= 2028 else ()),
                    ),
                    filters=(f"季节财年 EQ {fiscal_season}",),
                    split_fields=("年级", "赛道", "产品", "科目", "教学区"),
                )
            )
            summary_rows.extend(
                await self._complete_partition(
                    self.settings.summary_dataset_id,
                    columns=(*DIMENSION_FIELDS, *SUMMARY_METRIC_FIELDS),
                    filters=(
                        f"季节财年 EQ {fiscal_season}",
                        f"dtdate EQ {latest_partition}",
                    ),
                    split_fields=("年级", "赛道", "产品", "科目", "教学区"),
                )
            )
            if max(len(roster_rows), len(summary_rows)) > self.settings.source_preview_max_rows:
                raise SourceAdapterError(
                    "SOURCE_PREVIEW_LIMIT_REACHED",
                    "来源行数超过只读预览安全上限，不能证明抽取完整",
                )
        return SourceExtract(
            roster=roster,
            summary=summary,
            latest_summary_partition=latest_partition,
            fiscal_season_start=fiscal_season_start,
            fiscal_season_end=fiscal_season_end,
            roster_rows=roster_rows,
            summary_rows=summary_rows,
        )

    async def _metadata(self, dataset_id: str) -> DatasetMetadata:
        payload = await self._run_json("--raw", "ds", "preview", dataset_id, "--limit", "0")
        response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response, dict):
            raise SourceAdapterError("SOURCE_METADATA_INVALID", "观远数据集元数据返回结构无效")
        config = response.get("config")
        last_execution = config.get("lastExecution") if isinstance(config, dict) else None
        end_time = last_execution.get("endTime") if isinstance(last_execution, dict) else None
        if not isinstance(end_time, str):
            raise SourceAdapterError("SOURCE_BATCH_MISSING", "观远数据集缺少最近成功任务批次时间")
        try:
            return DatasetMetadata(
                dataset_id=dataset_id,
                name=str(response["name"]),
                version=int(response["version"]),
                row_count=int(response["rowCount"]),
                updated_at=_parse_datetime(str(response["utime"])),
                last_execution_at=_parse_datetime(end_time),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceAdapterError("SOURCE_METADATA_INVALID", "观远数据集元数据字段不完整") from exc

    async def _preview_count(self, dataset_id: str, filters: tuple[str, ...]) -> int:
        arguments = ["--raw", "ds", "preview", dataset_id, "--limit", "0"]
        for filter_value in filters:
            arguments.extend(("--filter", filter_value))
        payload = await self._run_json(*arguments)
        response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response, dict):
            raise SourceAdapterError("SOURCE_COUNT_INVALID", "观远数据集筛选行数返回结构无效")
        try:
            count = int(response["rowCount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceAdapterError("SOURCE_COUNT_INVALID", "观远数据集缺少筛选行数") from exc
        if count < 0:
            raise SourceAdapterError("SOURCE_COUNT_INVALID", "观远数据集筛选行数无效")
        return count

    async def _complete_chunk(
        self,
        dataset_id: str,
        *,
        columns: tuple[str, ...],
        filters: tuple[str, ...],
        expected_count: int | None = None,
    ) -> list[dict[str, object]]:
        count = await self._preview_count(dataset_id, filters) if expected_count is None else expected_count
        if count < 0:
            raise SourceAdapterError("SOURCE_COUNT_INVALID", "观远数据集筛选行数无效")
        if count > self.settings.source_preview_chunk_max_rows:
            raise SourceAdapterError(
                "SOURCE_PREVIEW_CHUNK_LIMIT_REACHED",
                "单季度来源行数超过观远只读预览 60000 行上限",
            )
        rows = await self._preview(dataset_id, columns=columns, filters=filters, limit=count) if count else []
        if len(rows) != count:
            raise SourceAdapterError(
                "SOURCE_PREVIEW_INCOMPLETE",
                "只读预览返回行数与服务端筛选总数不一致",
            )
        return rows

    async def _complete_partition(
        self,
        dataset_id: str,
        *,
        columns: tuple[str, ...],
        filters: tuple[str, ...],
        split_fields: tuple[str, ...],
    ) -> list[dict[str, object]]:
        count = await self._preview_count(dataset_id, filters)
        if count <= self.settings.source_preview_target_rows:
            return await self._complete_chunk(
                dataset_id, columns=columns, filters=filters, expected_count=count
            )
        if not split_fields:
            return await self._complete_chunk(
                dataset_id, columns=columns, filters=filters, expected_count=count
            )
        split_field, *remaining = split_fields
        values = await self._distinct_values(
            dataset_id, field=split_field, filters=filters, expected_count=count
        )
        rows: list[dict[str, object]] = []
        for value in values:
            split_filter = f"{split_field} IS_NULL" if value is None else f"{split_field} EQ {value}"
            rows.extend(
                await self._complete_partition(
                    dataset_id,
                    columns=columns,
                    filters=(*filters, split_filter),
                    split_fields=tuple(remaining),
                )
            )
        if len(rows) != count:
            raise SourceAdapterError(
                "SOURCE_PARTITION_INCOMPLETE",
                "动态分块合计行数与服务端筛选总数不一致",
            )
        return rows

    async def _distinct_values(
        self,
        dataset_id: str,
        *,
        field: str,
        filters: tuple[str, ...],
        expected_count: int,
    ) -> list[str | None]:
        if expected_count > self.settings.source_preview_chunk_max_rows:
            raise SourceAdapterError(
                "SOURCE_PREVIEW_CHUNK_LIMIT_REACHED",
                "用于动态分块的单列行数超过观远只读预览上限",
            )
        rows = await self._preview(
            dataset_id,
            columns=(field,),
            filters=filters,
            limit=expected_count,
        )
        if len(rows) != expected_count:
            raise SourceAdapterError(
                "SOURCE_PREVIEW_INCOMPLETE",
                "分块维度返回行数与服务端筛选总数不一致",
            )
        values: set[str | None] = set()
        for row in rows:
            value = row.get(field)
            if value is None or not str(value).strip():
                values.add(None)
            else:
                values.add(str(value).strip())
        return sorted(values, key=lambda value: (value is not None, value or ""))

    async def _preview(
        self,
        dataset_id: str,
        *,
        columns: tuple[str, ...],
        limit: int,
        filters: tuple[str, ...] = (),
        sort_desc: str | None = None,
    ) -> list[dict[str, object]]:
        arguments = [
            "ds",
            "preview",
            dataset_id,
            "--limit",
            str(limit),
            "--columns",
            ",".join(columns),
            "-f",
            "json",
        ]
        for filter_value in filters:
            arguments.extend(("--filter", filter_value))
        if sort_desc is not None:
            arguments.extend(("--sort-desc", sort_desc))
        payload = await self._run_json(*arguments)
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise SourceAdapterError("SOURCE_PREVIEW_INVALID", "观远数据集预览返回结构无效")
        return [{str(key): value for key, value in item.items()} for item in payload]

    async def _run_json(self, *arguments: str) -> Any:
        task = asyncio.current_task()
        if task is not None:
            self._active_queries.add(task)
        try:
            return await self._run_json_with_retries(arguments)
        finally:
            if task is not None:
                self._active_queries.discard(task)

    async def _run_json_with_retries(self, arguments: tuple[str, ...]) -> Any:
        stage = _query_stage(arguments)
        if stage == "unsupported":
            raise self._error(
                "SOURCE_QUERY_UNSUPPORTED",
                "预算取数仅支持只读查询",
                category="configuration",
                stage=stage,
                attempt=0,
            )
        command = [
            self.settings.guancli_binary,
            "--profile",
            self.settings.guancli_profile,
            *arguments,
        ]
        diagnostic_id = uuid.uuid4().hex[:16]
        resource_alias = _query_alias(arguments, self.settings)
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        last_error: SourceAdapterError | None = None
        for attempt in range(1, self.settings.guancli_retry_count + 2):
            remaining = self._deadline - loop.time() if self._deadline is not None else float("inf")
            if remaining <= 0:
                raise self._error(
                    "SOURCE_REFRESH_TIMEOUT",
                    "本次观远取数超时，已保留上次预测，请重试更新",
                    category="timeout",
                    stage=stage,
                    attempt=attempt,
                    retryable=True,
                    diagnostic_id=diagnostic_id,
                    resource_alias=resource_alias,
                )
            try:
                pool = _query_pool(self.settings.guancli_max_concurrent_queries)
            except SourceAdapterError as exc:
                raise self._error(
                    exc.code,
                    exc.safe_message,
                    category="configuration",
                    stage=stage,
                    attempt=attempt,
                    diagnostic_id=diagnostic_id,
                    resource_alias=resource_alias,
                ) from None
            pool.in_use += 1
            process: asyncio.subprocess.Process | None = None
            acquired = False
            queued_at = loop.time()
            attempt_started_at: float | None = None

            def log_cancellation(attempt: int, attempt_started_at: float | None, queued_at: float) -> None:
                cancellation = {
                    "diagnostic_id": diagnostic_id,
                    "refresh_id": self._refresh_id,
                    "stage": stage,
                    "resource_alias": resource_alias,
                    "attempt": attempt,
                    "elapsed_ms": int((loop.time() - started_at) * 1000),
                    "attempt_ms": int((loop.time() - attempt_started_at) * 1000)
                    if attempt_started_at is not None
                    else 0,
                    "queue_ms": int(((attempt_started_at or loop.time()) - queued_at) * 1000),
                }
                logger.info(
                    "guancli_query_cancelled %s",
                    json.dumps(cancellation, sort_keys=True),
                    extra=cancellation,
                )

            try:
                # Waiting for a shared connector slot is part of the refresh's
                # deadline, not extra unbounded time before the attempt starts.
                async with asyncio.timeout_at(self._deadline):
                    await pool.semaphore.acquire()
                    acquired = True
                    attempt_started_at = loop.time()
                    remaining = self._deadline - loop.time() if self._deadline is not None else float("inf")
                    timeout = min(
                        self.settings.guancli_timeout_seconds,
                        self.settings.guancli_retry_attempt_timeout_seconds,
                        remaining,
                    )
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=self._environment(),
                        start_new_session=os.name == "posix",
                    )
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except TimeoutError:
                if process is not None:
                    try:
                        await _terminate_process(process)
                    except asyncio.CancelledError:
                        log_cancellation(attempt, attempt_started_at, queued_at)
                        raise
                deadline_expired = self._deadline is not None and loop.time() >= self._deadline
                last_error = self._error(
                    "SOURCE_REFRESH_TIMEOUT" if deadline_expired else "SOURCE_QUERY_TIMEOUT",
                    "本次观远取数超时，已保留上次预测，请重试更新"
                    if deadline_expired
                    else "观远查询等待过久，请重试更新",
                    category="timeout",
                    stage=stage,
                    attempt=attempt,
                    retryable=True,
                    diagnostic_id=diagnostic_id,
                    elapsed_ms=int((loop.time() - started_at) * 1000),
                    attempt_ms=int((loop.time() - attempt_started_at) * 1000)
                    if attempt_started_at is not None
                    else 0,
                    queue_ms=int(((attempt_started_at or loop.time()) - queued_at) * 1000),
                    resource_alias=resource_alias,
                )
            except asyncio.CancelledError:
                try:
                    if process is not None:
                        await _terminate_process(process)
                finally:
                    log_cancellation(attempt, attempt_started_at, queued_at)
                raise
            except OSError:
                raise self._error(
                    "SOURCE_CLI_UNAVAILABLE",
                    "本机观远连接工具不可用，请联系管理员检查",
                    category="configuration",
                    stage=stage,
                    attempt=attempt,
                    diagnostic_id=diagnostic_id,
                    attempt_ms=int((loop.time() - attempt_started_at) * 1000)
                    if attempt_started_at is not None
                    else 0,
                    queue_ms=int(((attempt_started_at or loop.time()) - queued_at) * 1000),
                    resource_alias=resource_alias,
                ) from None
            else:
                elapsed_ms = int((loop.time() - started_at) * 1000)
                assert attempt_started_at is not None
                attempt_ms = int((loop.time() - attempt_started_at) * 1000)
                queue_ms = int((attempt_started_at - queued_at) * 1000)
                if process.returncode != 0:
                    code, message, category, retryable = _classify_failure(stderr + b"\n" + stdout)
                    last_error = self._error(
                        code,
                        message,
                        category=category,
                        stage=stage,
                        attempt=attempt,
                        retryable=retryable,
                        diagnostic_id=diagnostic_id,
                        elapsed_ms=elapsed_ms,
                        return_code=process.returncode,
                        attempt_ms=attempt_ms,
                        queue_ms=queue_ms,
                        resource_alias=resource_alias,
                    )
                else:
                    try:
                        payload = _decode_json(stdout.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        code, message, category, retryable = _classify_failure(stdout + b"\n" + stderr)
                        if code == "SOURCE_QUERY_FAILED":
                            code, message, category, retryable = (
                                "SOURCE_RESPONSE_INVALID",
                                "观远返回的数据格式异常，请重试更新",
                                "response",
                                False,
                            )
                        last_error = self._error(
                            code,
                            message,
                            category=category,
                            stage=stage,
                            attempt=attempt,
                            retryable=retryable,
                            diagnostic_id=diagnostic_id,
                            elapsed_ms=elapsed_ms,
                            attempt_ms=attempt_ms,
                            queue_ms=queue_ms,
                            resource_alias=resource_alias,
                        )
                    else:
                        response_error = _response_error(payload)
                        if response_error is None:
                            success = {
                                "diagnostic_id": diagnostic_id,
                                "refresh_id": self._refresh_id,
                                "stage": stage,
                                "resource_alias": resource_alias,
                                "attempt": attempt,
                                "elapsed_ms": int((loop.time() - started_at) * 1000),
                                "attempt_ms": int((loop.time() - attempt_started_at) * 1000),
                                "queue_ms": queue_ms,
                                "response_bytes": len(stdout),
                            }
                            logger.info(
                                "guancli_query_succeeded %s",
                                json.dumps(success, sort_keys=True),
                                extra=success,
                            )
                            return payload
                        code, message, category, retryable = _classify_failure(response_error)
                        last_error = self._error(
                            code,
                            message,
                            category=category,
                            stage=stage,
                            attempt=attempt,
                            retryable=retryable,
                            diagnostic_id=diagnostic_id,
                            elapsed_ms=elapsed_ms,
                            attempt_ms=attempt_ms,
                            queue_ms=queue_ms,
                            resource_alias=resource_alias,
                        )
            finally:
                if acquired:
                    pool.semaphore.release()
                pool.in_use -= 1
            assert last_error is not None
            if not last_error.retryable or attempt > self.settings.guancli_retry_count:
                raise last_error from None
            delay = self.settings.guancli_retry_backoff_seconds * (2 ** (attempt - 1))
            remaining = self._deadline - loop.time() if self._deadline is not None else float("inf")
            if remaining <= delay:
                raise last_error from None
            await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Kill the group, discard both pipes, and reap within a bounded cleanup window."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    # A killed process can still block wait() when a PIPE paused at its buffer
    # limit. Drain both streams concurrently without retaining any source data.
    cleanup = asyncio.create_task(_drain_and_reap(process))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # A second request cancellation must not abandon process cleanup or
            # release the connector slot while its child is still running.
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


async def _discard_stream(stream: asyncio.StreamReader | None) -> None:
    if stream is not None:
        while await stream.read(65_536):
            pass


async def _drain_and_reap(process: asyncio.subprocess.Process) -> None:
    try:
        async with asyncio.timeout(2):
            await asyncio.gather(_discard_stream(process.stdout), _discard_stream(process.stderr))
            await process.wait()
            return
    except TimeoutError:
        # An escaped descendant may retain a PIPE after its wrapper is killed.
        # asyncio exposes no public Process.close(); closing the transport is
        # the bounded fallback that stops those readers without storing output.
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()
    try:
        async with asyncio.timeout(1):
            await process.wait()
    except TimeoutError:
        logger.warning("guancli_cleanup_timeout")


def _query_stage(arguments: tuple[str, ...]) -> str:
    commands = arguments[1:] if arguments[:1] == ("--raw",) else arguments
    if len(commands) >= 2:
        resource, command = commands[:2]
        if resource == "etl" and command == "get":
            return "etl_definition"
        if resource == "ds" and command == "get":
            return "preflight"
        if resource == "ds" and command == "preview":
            limit = _argument_value(commands, "--limit")
            columns = _argument_value(commands, "--columns")
            if limit == "0":
                return "partition_count" if "--filter" in commands else "metadata"
            if columns == "dtdate" and _argument_value(commands, "--sort-desc") == "dtdate":
                return "latest_partition"
            if columns and "," not in columns:
                return "distinct_values"
            return "partition_rows"
    return "unsupported"


def _argument_value(arguments: tuple[str, ...], flag: str) -> str | None:
    try:
        index = arguments.index(flag)
    except ValueError:
        return None
    return arguments[index + 1] if index + 1 < len(arguments) else None


def _query_alias(arguments: tuple[str, ...], settings: Settings) -> str:
    commands = arguments[1:] if arguments[:1] == ("--raw",) else arguments
    resource_id = commands[2] if len(commands) > 2 else ""
    known = {
        settings.summary_dataset_id: "summary",
        settings.roster_dataset_id: "roster",
        settings.roster_etl_id: "roster_rules",
    }
    if resource_id in known:
        return known[resource_id]
    # A short stable alias is enough to correlate an additional resource. Never
    # log raw identifiers, filter values, column values or command arguments.
    return "resource_" + hashlib.sha256(resource_id.encode("utf-8")).hexdigest()[:10]


def _response_error(payload: Any) -> bytes | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    code = payload.get("code", payload.get("status"))
    if (
        error
        or payload.get("success") is False
        or payload.get("ok") is False
        or str(code) in {"401", "403", "408", "429", "500", "502", "503", "504"}
    ):
        return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return None


def _classify_failure(output: bytes) -> tuple[str, str, str, bool]:
    # Inspect in memory only; retain categories, not potentially sensitive CLI output.
    text = output[:65_536].decode("utf-8", errors="replace").lower()
    if re.search(
        r"\b401\b|unauthori[sz]ed|token.{0,30}(expir|invalid)|expired token|"
        r"not logged in|login required|登录.{0,10}(过期|失效)",
        text,
    ):
        return "SOURCE_AUTH_REQUIRED", "观远登录已失效，请重新登录后更新预测", "authentication", False
    if re.search(r"\b403\b|forbidden|permission denied|access denied|无权限|权限不足", text):
        return (
            "SOURCE_PERMISSION_DENIED",
            "当前观远账号无权读取预算数据，请联系管理员授权",
            "permission",
            False,
        )
    if re.search(r"certificate|x509|unknown authority|self.signed|证书", text):
        return "SOURCE_TLS_CERTIFICATE_INVALID", "观远连接证书校验失败，请检查代理或证书配置", "tls", False
    if re.search(r"tls|ssl|handshake", text):
        return "SOURCE_TLS_CONNECTION_FAILED", "观远安全连接失败，请检查网络或代理设置", "tls", True
    if re.search(r"\b429\b|too many requests|rate.limit", text):
        return "SOURCE_RATE_LIMITED", "观远查询过于频繁，请稍后重试", "rate_limit", True
    if re.search(r"\b(500|502|503|504)\b|bad gateway|service unavailable|internal server error", text):
        return "SOURCE_SERVICE_UNAVAILABLE", "观远查询服务暂时不可用，请稍后重试", "service", True
    if re.search(r"\b408\b|timed? out|timeout|deadline exceeded|超时", text):
        return "SOURCE_QUERY_TIMEOUT", "观远查询等待过久，请重试更新", "timeout", True
    if re.search(
        r"\beof\b|connection (reset|refused|closed)|broken pipe|no such host|"
        r"network is unreachable|connection aborted|temporary failure|dns",
        text,
    ):
        return "SOURCE_NETWORK_ERROR", "观远连接中断，已保留上次预测，可重试更新", "network", True
    return "SOURCE_QUERY_FAILED", "观远只读查询失败，请联系管理员检查", "query", False


def _decode_json(output: str) -> Any:
    for index, character in enumerate(output):
        if character not in "[{":
            continue
        try:
            return json.loads(output[index:], parse_float=Decimal)
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("no JSON document found", output, 0)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("+0800", "+08:00"))
