"""Read-only FY27 forecast extraction, isolated from the annual-budget adapter.

Counts come from one latest wide-table partition. Financial facts are read by
financial year, not course season: the latter is blank in the income dataset.
Only aggregated business dimensions leave this module; no student rows or
credentials are persisted here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from packages.config.settings import Settings
from packages.source_snapshot.adapter import DatasetMetadata, GuanCliSourceAdapter, SourceAdapterError

FISCAL_YEAR = 2027
Row = dict[str, Any]
Key = tuple[str, ...]
FINANCE_DATASET_ID = "nab9a01cebb1a4f4da4594e8"
SUMMARY_ETL_ID = "h9917fb6309c4479080ce122"
SEASONS = ("暑假", "秋季", "寒假", "春季")
DIMENSIONS = dict(
    zip(
        ("track", "product", "grade", "subject", "campus"),
        ("赛道", "产品", "年级", "科目", "教学区"),
        strict=True,
    )
)
CURRENT_FIELDS = {
    "enrollment": "招生人次",
    "new_business": "新生人次",
    "new_base": "新生退班基数(在班基数)",
    "old_base": "老生退班基数(在班基数)",
    "total_base": "退班基数(在班基数)",
    "history_total_process_n": "同期退班人次-过程值",
}
HISTORY_FIELDS = {
    "history_new_process_n": ("新生同期退班人次-过程值", "新生退班人次-过程值"),
    "history_new_process_d": ("新生同期退班基数(退班率分母)-过程值", "新生退班基数(退班率分母)-过程值"),
    "history_new_final_n": ("新生同期退班人次", "新生退班人次"),
    "history_new_final_d": ("新生同期退班基数(退班率分母)", "新生退班基数(退班率分母)"),
    "history_old_process_n": ("老生同期退班人次-过程值", "老生退班人次-过程值"),
    "history_old_process_d": ("老生同期退班基数(退班率分母)-过程值", "老生退班基数(退班率分母)-过程值"),
    "history_old_final_n": ("老生同期退班人次", "老生退班人次"),
    "history_old_final_d": ("老生同期退班基数(退班率分母)", "老生退班基数(退班率分母)"),
}
SUMMARY_FIELDS = {**CURRENT_FIELDS, **{key: fields[0] for key, fields in HISTORY_FIELDS.items()}}
SUMMARY_REVENUE_FIELD = "财季结转收入"
FINANCE_COLUMNS = ("财年", "财季", "业务归属年", "业务归属季度", *DIMENSIONS.values(), "预计结转收入")


def _issue(code: str, message: str, *, season: str | None = None, dims: Row | None = None) -> Row:
    result: Row = {"code": code, "message": message, "severity": "BLOCKING"}
    if season is not None:
        result["season"] = season
    if dims is not None:
        result["dims"] = dims
    return result


def _number(value: object, *, count: bool = False) -> Decimal | None:
    if value is None or isinstance(value, bool) or not str(value).strip():
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if count and (number < 0 or number != number.to_integral_value()):
        return None
    return number


def _dims(row: Row) -> dict[str, str]:
    return {key: str(row.get(field) or "").strip() for key, field in DIMENSIONS.items()}


def _key(season: str, dims: Row) -> Key:
    return (season, *(dims[key] for key in DIMENSIONS))


def _metadata(value: DatasetMetadata) -> Row:
    return {
        "dataset_id": value.dataset_id,
        "name": value.name,
        "version": value.version,
        "row_count": value.row_count,
        "updated_at": value.updated_at.isoformat(),
        "batch": value.last_execution_at.isoformat(),
    }


def _normal_sql(value: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"--[^\n]*", "", value)).lower().replace("!=", "<>")


def _expected_roster_formula(key: str) -> str:
    student = "新生" if "_new_" in key else "老生"
    conditions = [f"`业务新老生` = '{student}'", "`离班是否统计标识(1-是,0-否)`=1"]
    if key.endswith("_n"):
        conditions.append("`离班方式`<>'无'")
    if "_process_" in key:
        if key.endswith("_n"):
            conditions.append("`离班日期`<=DATE_SUB(CURRENT_DATE(),366)")
        conditions.append("`进班日期`<=DATE_SUB(CURRENT_DATE(),366)")
    conditions.extend(("`产品名称` not in ('智慧伴学专项','智慧空间专项')", "`会员卡规格名称`!='月卡'"))
    return "case when " + " and ".join(conditions) + " then 1 else 0 end"


def _field_case(sql: str, field: str) -> str | None:
    """Extract the simple CASE expressions used by these eight approved fields.

    This is intentionally not a general SQL parser. A changed/nested expression
    fails the exact approved-expression check instead of becoming auto-verified.
    """
    match = re.search(r"\bend\s+`" + re.escape(field) + r"`\s*(?:,|$)", sql, re.IGNORECASE)
    if match is None:
        return None
    starts = list(re.finditer(r"(?im)^\s*case\s+when\b", sql[: match.start()]))
    if not starts:
        return None
    return sql[starts[-1].start() : match.start() + 3].strip()


def verify_metric_formulas(summary_etl: Row, roster_etl: Row) -> tuple[bool, Row]:
    """Record exact physical-field lineage, not just matching aggregate numbers."""
    summary_data = summary_etl.get("data", {})
    roster_data = roster_etl.get("data", {})
    summary_actions = summary_data.get("actions", [])
    roster_actions = roster_data.get("actions", [])
    candidates = [
        action for action in summary_actions if "`新生同期退班人次-过程值`" in str(action.get("sql", ""))
    ]
    summary_action = candidates[0] if len(candidates) == 1 else {}
    sql = str(summary_action.get("sql", ""))
    normal = _normal_sql(sql)
    historical_join = (
        "round((t1.`业务归属年`-1)*10+t1.`业务归属季度`,0)=round(t3.`业务归属年`*10+t3.`业务归属季度`,0)"
    )
    cohort_dimensions = (*DIMENSIONS.values(), "是否白金标识(白金,普通)")
    historical_dimension_joins = {
        field: (f"t1.`{field}`=t3.`{field}`" in normal or f"t3.`{field}`=t1.`{field}`" in normal)
        for field in cohort_dimensions
    }
    verified = _normal_sql(historical_join) in normal and all(historical_dimension_joins.values())
    mappings = {}
    for key, (physical, upstream) in HISTORY_FIELDS.items():
        projection = f"ifnull(t3.`{upstream}`,0) `{physical}`"
        aggregate = f"sum(`{upstream}`) `{upstream}`"
        definitions = [
            (action, _field_case(str(action.get("sql", "")), upstream)) for action in roster_actions
        ]
        definitions = [(action, expression) for action, expression in definitions if expression is not None]
        action, expression = definitions[0] if len(definitions) == 1 else ({}, None)
        passed = (
            _normal_sql(projection) in normal
            and _normal_sql(aggregate) in normal
            and expression is not None
            and _normal_sql(expression) == _normal_sql(_expected_roster_formula(key))
        )
        verified = verified and passed
        mappings[key] = {
            "physical_field": physical,
            "roster_field": upstream,
            "summary_projection": projection,
            "summary_aggregate": aggregate,
            "roster_expression": expression,
            "approved_roster_expression": _expected_roster_formula(key),
            "formula_verified": passed,
            "roster_node_id": action.get("id"),
            "summary_node_id": summary_action.get("id"),
        }
    evidence = {
        "mapping_version": "fy27-natural-forecast-source-v1",
        "summary_etl_id": SUMMARY_ETL_ID,
        "summary_etl_updated_at": summary_data.get("utime"),
        "roster_etl_id": roster_data.get("dataFlowId"),
        "roster_etl_updated_at": roster_data.get("utime"),
        "historical_join": historical_join,
        "historical_dimension_joins": historical_dimension_joins,
        "history_fields": mappings,
        "new_rate_formula": "SUM(新生同期退班人次-过程值)/SUM(新生同期退班基数(退班率分母)-过程值)",
        "new_final_rate_formula": "SUM(新生同期退班人次)/SUM(新生同期退班基数(退班率分母))",
        "old_rate_formula": "SUM(老生同期退班人次-过程值)/SUM(老生同期退班基数(退班率分母)-过程值)",
        "old_final_rate_formula": "SUM(老生同期退班人次)/SUM(老生同期退班基数(退班率分母))",
        "remaining_rate_formula": "MAX(0,最终退班率-同期过程退班率)",
    }
    evidence["formula_hash"] = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return bool(verified), evidence


def aggregate_forecast_rows(
    summary_rows: list[Row], finance_rows: list[Row]
) -> tuple[list[Row], list[Row], list[Row], Row]:
    """Aggregate N/D and signed financial contributions; never average ratios."""
    issues: list[Row] = []
    values: dict[Key, Row] = {}
    excluded = 0
    checked = 0
    for raw in summary_rows:
        fiscal_season = _number(raw.get("季节财年"), count=True)
        if fiscal_season not in tuple(Decimal(FISCAL_YEAR * 10 + q) for q in range(1, 5)):
            issues.append(_issue("SOURCE_SEASON_INVALID", "预测来源含不属于 FY27 的季度"))
            continue
        season = SEASONS[int(fiscal_season) % 10 - 1]
        dims = _dims(raw)
        numbers: Row = {key: _number(raw.get(field), count=True) for key, field in SUMMARY_FIELDS.items()}
        actual_revenue = _number(raw.get(SUMMARY_REVENUE_FIELD))
        if any(not value for value in dims.values()):
            # Revenue is checked independently below; no nonzero financial row is dropped here.
            if all(number == 0 for number in numbers.values()) and actual_revenue == 0:
                excluded += 1
                continue
            issues.append(
                _issue(
                    "SOURCE_DIMENSIONS_INCOMPLETE", "非零或缺失数据的业务维度不完整", season=season, dims=dims
                )
            )
        missing = [key for key, value in numbers.items() if value is None]
        if missing:
            issues.append(
                _issue("SOURCE_COUNT_INVALID", "来源人次缺失、为负数或不是整数", season=season, dims=dims)
            )
        else:
            checked += 1
            if (
                numbers["new_base"] + numbers["old_base"] != numbers["total_base"]
                or numbers["total_base"] != numbers["enrollment"]
            ):
                issues.append(
                    _issue(
                        "CURRENT_BASE_MISMATCH", "新老生退班基数与招生人次不一致", season=season, dims=dims
                    )
                )
            if (
                numbers["history_new_process_n"] + numbers["history_old_process_n"]
                != numbers["history_total_process_n"]
            ):
                issues.append(
                    _issue(
                        "WITHDRAWAL_COMPLEMENT_MISMATCH",
                        "新老生同期过程退班不满足严格补集",
                        season=season,
                        dims=dims,
                    )
                )
        key = _key(season, dims)
        if key not in values:
            values[key] = {
                "season": season,
                **dims,
                **numbers,
                "actual_revenue": actual_revenue,
                "actual_carryout": Decimal(0),
            }
        else:
            for metric, number in numbers.items():
                prior = values[key][metric]
                values[key][metric] = prior + number if prior is not None and number is not None else None
            prior_revenue = values[key]["actual_revenue"]
            values[key]["actual_revenue"] = (
                prior_revenue + actual_revenue
                if prior_revenue is not None and actual_revenue is not None
                else None
            )
        if actual_revenue is None:
            issues.append(
                _issue(
                    "SUMMARY_ACTUAL_REVENUE_INVALID",
                    "最新分区的财季结转收入缺失或无效",
                    season=season,
                    dims=dims,
                )
            )

    financial: dict[Key, Decimal | None] = {}
    carryout: dict[Key, Decimal | None] = {}
    incoming: dict[tuple[Any, ...], Decimal | None] = {}
    negatives = 0
    for raw in finance_rows:
        year = _number(raw.get("财年"), count=True)
        quarter = str(raw.get("财季") or "").strip()
        origin_year = _number(raw.get("业务归属年"), count=True)
        origin_quarter = str(raw.get("业务归属季度") or "").strip()
        amount = _number(raw.get("预计结转收入"))
        dims = _dims(raw)
        if amount == 0:
            continue
        if (
            year is None
            or quarter not in ("Q1", "Q2", "Q3", "Q4")
            or int(year) not in (FISCAL_YEAR, FISCAL_YEAR + 1)
            or (int(year) == FISCAL_YEAR + 1 and quarter != "Q1")
        ):
            issues.append(_issue("ACTUAL_REVENUE_SOURCE_INVALID", "财务收入缺失或财年财季无效", dims=dims))
            continue
        season = SEASONS[int(quarter[-1]) - 1]
        if amount is None:
            issues.append(
                _issue("ACTUAL_REVENUE_SOURCE_INVALID", "财务收入缺失或无效", season=season, dims=dims)
            )
        if any(not value for value in dims.values()):
            issues.append(
                _issue(
                    "FINANCIAL_DIMENSIONS_INCOMPLETE",
                    "非零财务收入的业务维度不完整",
                    season=season,
                    dims=dims,
                )
            )
        if origin_year is None or origin_quarter not in SEASONS:
            issues.append(
                _issue(
                    "ACTUAL_CARRYIN_ORIGIN_INVALID",
                    "财务结转的业务归属年或季度缺失",
                    season=season,
                    dims=dims,
                )
            )
        if amount is not None and amount < 0:
            negatives += 1  # Keep real signed corrections; do not replace them with zero.
        key = _key(season, dims)
        if int(year) == FISCAL_YEAR:
            prior = financial.get(key, Decimal(0))
            financial[key] = prior + amount if prior is not None and amount is not None else None
            if origin_year is not None and origin_quarter in SEASONS:
                origin_season = int(origin_year) * 10 + SEASONS.index(origin_quarter) + 1
                incoming_key = (origin_season, *key)
                prior = incoming.get(incoming_key, Decimal(0))
                incoming[incoming_key] = prior + amount if prior is not None and amount is not None else None
        if origin_year == FISCAL_YEAR and origin_quarter in SEASONS:
            index = SEASONS.index(origin_quarter)
            expected_year = FISCAL_YEAR + (1 if index == 3 else 0)
            expected_quarter = f"Q{(index + 1) % 4 + 1}"
            if int(year) == expected_year and quarter == expected_quarter:
                origin_key = _key(origin_quarter, dims)
                prior = carryout.get(origin_key, Decimal(0))
                carryout[origin_key] = prior + amount if prior is not None and amount is not None else None
    mismatched_keys = 0
    department_reconciliation = {}
    for key in set(values) | set(financial):
        wide = values.get(key, {}).get("actual_revenue", Decimal(0))
        raw_total = financial.get(key, Decimal(0))
        if wide is None or raw_total is None or abs(wide - raw_total) > Decimal("0.01"):
            mismatched_keys += 1
            issues.append(
                _issue(
                    "FINANCIAL_PARTITION_MISMATCH",
                    "原始财务收入与冻结分区同维度财季收入不一致",
                    season=key[0],
                    dims=dict(zip(DIMENSIONS, key[1:], strict=True)),
                )
            )
    for season in SEASONS:
        wide_members = [value["actual_revenue"] for key, value in values.items() if key[0] == season]
        raw_members = [amount for key, amount in financial.items() if key[0] == season]
        wide = sum(wide_members, Decimal(0)) if all(value is not None for value in wide_members) else None
        raw_total = (
            sum((value for value in raw_members if value is not None), Decimal(0))
            if all(value is not None for value in raw_members)
            else None
        )
        matched = wide is not None and raw_total is not None and abs(wide - raw_total) <= Decimal("0.01")
        department_reconciliation[season] = {"wide": wide, "financial": raw_total, "matched": matched}
        if not matched:
            issues.append(
                _issue(
                    "FINANCIAL_DEPARTMENT_MISMATCH", "原始财务收入与冻结分区部门财季合计不一致", season=season
                )
            )
    for key in set(financial) | set(carryout):
        if key not in values:
            season, *dimension_values = key
            values[key] = {
                "season": season,
                **dict(zip(DIMENSIONS, dimension_values, strict=True)),
                **{field: None for field in SUMMARY_FIELDS},
                "actual_revenue": None,
                "actual_carryout": Decimal(0),
                "financial_only": True,
            }
    for key, value in values.items():
        value["actual_carryout"] = carryout.get(key, Decimal(0))
    frozen = [
        {
            "origin_season": key[0],
            "season": key[1],
            **dict(zip(DIMENSIONS, key[2:], strict=True)),
            "revenue": revenue,
        }
        for key, revenue in sorted(incoming.items())
        if revenue != 0
    ]
    return (
        [values[key] for key in sorted(values)],
        frozen,
        issues,
        {
            "summary_rows": len(summary_rows),
            "excluded_zero_incomplete_rows": excluded,
            "checked_complete_rows": checked,
            "aggregated_rows": len(values),
            "financial_rows": len(finance_rows),
            "signed_financial_adjustment_rows": negatives,
            "financial_partition_mismatch_keys": mismatched_keys,
            "department_financial_reconciliation": department_reconciliation,
            "complement_failure_count": sum(
                issue["code"] == "WITHDRAWAL_COMPLEMENT_MISMATCH" for issue in issues
            ),
            "current_base_failure_count": sum(issue["code"] == "CURRENT_BASE_MISMATCH" for issue in issues),
        },
    )


async def extract_forecast_source(settings: Settings, *, adapter: Any = None) -> Row:
    adapter = adapter or GuanCliSourceAdapter(settings)
    if isinstance(adapter, GuanCliSourceAdapter):
        async with adapter.query_budget(settings.forecast_refresh_timeout_seconds):
            await adapter.preflight(
                (settings.summary_dataset_id, settings.roster_dataset_id, FINANCE_DATASET_ID)
            )
            return await _extract_forecast_source(settings, adapter=adapter)
    return await _extract_forecast_source(settings, adapter=adapter)


async def _gather_source(*coroutines: Any) -> list[Any]:
    """A failing partition must cancel and join its sibling read-only queries."""
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _extract_forecast_source(settings: Settings, *, adapter: Any) -> Row:
    dataset_ids = (settings.summary_dataset_id, settings.roster_dataset_id, FINANCE_DATASET_ID)
    before = await _gather_source(*(adapter._metadata(dataset) for dataset in dataset_ids))
    latest = await adapter._preview(
        settings.summary_dataset_id, columns=("dtdate",), limit=1, sort_desc="dtdate"
    )
    if not latest or not latest[0].get("dtdate"):
        raise SourceAdapterError("SUMMARY_PARTITION_MISSING", "指标汇总宽表没有可用分区")
    partition = str(latest[0]["dtdate"])
    summary_columns = (
        "dtdate",
        "季节财年",
        "财季",
        *DIMENSIONS.values(),
        *SUMMARY_FIELDS.values(),
        SUMMARY_REVENUE_FIELD,
    )
    batches = await _gather_source(
        *(
            adapter._complete_partition(
                settings.summary_dataset_id,
                columns=summary_columns,
                filters=(f"季节财年 EQ {FISCAL_YEAR * 10 + quarter}", f"dtdate EQ {partition}"),
                split_fields=tuple(DIMENSIONS.values()),
            )
            for quarter in range(1, 5)
        )
    )
    summary_rows = [row for batch in batches for row in batch]
    if len(summary_rows) > settings.source_preview_max_rows:
        raise SourceAdapterError("SOURCE_PREVIEW_LIMIT_REACHED", "预测来源超过完整抽取安全上限")
    financial_batches = await _gather_source(
        *(
            adapter._complete_partition(
                FINANCE_DATASET_ID,
                columns=FINANCE_COLUMNS,
                filters=("目标标识(目标,数据) EQ 数据", f"财年 EQ {year}", *quarter_filters),
                split_fields=tuple(DIMENSIONS.values()),
            )
            for year, quarter_filters in ((FISCAL_YEAR, ()), (FISCAL_YEAR + 1, ("财季 EQ Q1",)))
        )
    )
    finance_rows = [row for batch in financial_batches for row in batch]
    if len(finance_rows) > settings.source_preview_max_rows:
        raise SourceAdapterError("SOURCE_PREVIEW_LIMIT_REACHED", "财务来源超过完整抽取安全上限")
    summary_etl, roster_etl = await _gather_source(
        # Raw + brief still returns the complete ETL definition (actions/SQL).
        # Brief skips CLI lineage enrichment, which can fan out across upstream
        # resources and exhaust the extraction deadline even after all rows arrive.
        adapter._run_json("--raw", "etl", "get", SUMMARY_ETL_ID, "--brief"),
        adapter._run_json("--raw", "etl", "get", settings.roster_etl_id, "--brief"),
    )
    formula_verified, mapping = verify_metric_formulas(summary_etl, roster_etl)
    after = await _gather_source(*(adapter._metadata(dataset) for dataset in dataset_ids))
    latest_after = await adapter._preview(
        settings.summary_dataset_id, columns=("dtdate",), limit=1, sort_desc="dtdate"
    )
    if before != after or latest_after != latest:
        raise SourceAdapterError(
            "FORECAST_SOURCE_CHANGED_DURING_EXTRACTION",
            "来源在抽取期间发生更新，请重新生成预测",
            retryable=True,
        )
    if any(str(row.get("dtdate")) != partition for row in summary_rows):
        raise SourceAdapterError("FORECAST_SOURCE_PARTITION_MIXED", "预测来源包含不同分区，未生成快照")
    rows, frozen, issues, checks = aggregate_forecast_rows(summary_rows, finance_rows)
    if not formula_verified:
        issues.append(
            _issue("WITHDRAWAL_MAPPING_UNVERIFIED", "退班字段公式与已确认口径不一致，需数据维护人员核验")
        )
    if not summary_rows:
        issues.append(_issue("FORECAST_SUMMARY_EMPTY", "所选分区没有 FY27 预测数据"))
    if not financial_batches[0]:
        issues.append(_issue("FORECAST_FINANCIAL_EMPTY", "财务来源没有 FY27 实际收入，不能默认为零"))
    # Quality issues carry a season and are consumed according to the selected
    # quarter state. They must not invalidate unrelated future-quarter metrics.
    metric_verified = formula_verified
    actual_totals: Row = {}
    for season in SEASONS:
        members = [row for row in rows if row["season"] == season and not row.get("financial_only")]
        totals: Row = {}
        for field in ("actual_revenue", "enrollment"):
            amounts = [row[field] for row in members]
            totals[field] = (
                sum(amounts, Decimal(0))
                if amounts and all(amount is not None for amount in amounts)
                else None
            )
        actual_totals[season] = totals
    finance_mapping = {
        "actual_revenue": (
            "SUM(指标汇总宽表.财季结转收入) WHERE dtdate=冻结最新分区 AND 季节财年=2027财务落点季度"
        ),
        "financial_equivalence_check": (
            "完整业务维度及部门按财年/财季汇总原始收入，与冻结分区财季结转收入差异均不超过0.01元"
        ),
        "actual_carryout": (
            "SUM(收入明细.预计结转收入) WHERE 业务归属年=2027 "
            "AND 业务归属季度=来源季度 AND 财年/财季=下一季度"
        ),
        "frozen_carryins": (
            "按财年/财季落点、业务归属年/季度来源及完整业务维度 SUM(预计结转收入)，保留真实正负调整"
        ),
        "net_of_tax": True,
        "course_season_filter_used": False,
    }
    return {
        "as_of": partition,
        "fiscal_year": FISCAL_YEAR,
        "rows": rows,
        # Persist the exact, allowlisted business rows used to aggregate this
        # snapshot. No student identity fields are requested or stored.
        "source_data": {
            "summary_rows": [
                {key: row[key] for key in summary_columns if key in row} for row in summary_rows
            ],
            "financial_rows": [
                {key: row[key] for key in FINANCE_COLUMNS if key in row} for row in finance_rows
            ],
        },
        "frozen_carryins": frozen,
        "actual_totals": actual_totals,
        "issues": issues,
        "metric_verified": metric_verified,
        "field_mapping": {
            **CURRENT_FIELDS,
            **{key: fields[0] for key, fields in HISTORY_FIELDS.items()},
            **finance_mapping,
        },
        "provenance": {
            "generated_at": datetime.now(UTC).isoformat(),
            "dtdate": partition,
            "datasets": [_metadata(value) for value in before],
            "count_proof": checks,
            "metadata_stable_after_extraction": True,
            "metric_mapping": mapping,
            "financial_mapping": finance_mapping,
            "financial_filters": [
                "目标标识(目标,数据) EQ 数据",
                "财年 EQ 2027",
                "财年 EQ 2028 AND 财季 EQ Q1",
            ],
        },
    }
