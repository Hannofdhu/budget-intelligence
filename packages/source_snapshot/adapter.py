import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from packages.config.settings import Settings

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
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class SourceAdapter(Protocol):
    async def extract(self, fiscal_year: int) -> SourceExtract: ...


class GuanCliSourceAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
    ) -> list[dict[str, object]]:
        count = await self._preview_count(dataset_id, filters)
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
            return await self._complete_chunk(dataset_id, columns=columns, filters=filters)
        if not split_fields:
            return await self._complete_chunk(dataset_id, columns=columns, filters=filters)
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
        command = [
            self.settings.guancli_binary,
            "--profile",
            self.settings.guancli_profile,
            *arguments,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.settings.guancli_timeout_seconds
            )
        except TimeoutError as exc:
            raise SourceAdapterError("SOURCE_QUERY_TIMEOUT", "观远只读查询超时") from exc
        except OSError as exc:
            raise SourceAdapterError("SOURCE_CLI_UNAVAILABLE", "guancli 不可执行") from exc
        if process.returncode != 0:
            raise SourceAdapterError("SOURCE_QUERY_FAILED", "观远只读查询失败")
        try:
            return _decode_json(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceAdapterError("SOURCE_RESPONSE_INVALID", "观远只读查询未返回有效 JSON") from exc


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
