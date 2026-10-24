"""Join saved facts, not budget engines. Never calculate, confirm or save a budget."""

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from packages.forecast.store import fingerprint
from packages.forecast.trend import _actual

Json = dict[str, Any]
DIMENSIONS = ("season", "track", "campus", "product", "grade", "subject")
BUSINESS = ("track", "product", "grade", "subject", "campus")
SEASONS = ("暑假", "秋季", "寒假", "春季")
METRICS = ("budget", "actual", "forecast", "next_budget")


class CockpitProjectionError(ValueError):
    pass


def amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() in {"", "-"}:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _dimensions(row: Json) -> Json:
    return {key: str(row.get(key) or "未标注").strip() for key in DIMENSIONS}


def _key(row: Json) -> tuple[str, ...]:
    return tuple(_dimensions(row).values())


def _business(row: Json) -> tuple[str, ...]:
    return tuple(str(row.get(key) or "").strip() for key in BUSINESS)


def _sum(values: list[object]) -> str | None:
    parsed = [amount(value) for value in values]
    return (
        str(sum((value for value in parsed if value is not None), Decimal(0)))
        if parsed and all(value is not None for value in parsed)
        else None
    )


def totals(rows: list[Json]) -> Json:
    return {metric: _sum([row[metric] for row in rows if row["covered"][metric]]) for metric in METRICS}


def project_cockpit(baseline: Json, forecast: Json | None, draft: Json | None) -> Json:
    """Preserve six-dimensional identity, missingness and exact saved source versions."""
    year = baseline["fiscal_year"]
    if forecast is not None:
        if forecast.get("baseline_hash") != baseline["sha256"]:
            raise CockpitProjectionError("预测与定版预算不是同一基准，未混合展示")
        if fingerprint(forecast.get("snapshot")) != forecast.get("snapshot_hash"):
            raise CockpitProjectionError("预测快照完整性校验失败，未展示驾驶舱")
        # Stores validate their immutable event chain. Legacy VERSION records have
        # no separate calculation_hash; the archive export likewise fingerprints it.
        if (
            forecast.get("calculation_hash")
            and fingerprint(forecast.get("calculation")) != forecast["calculation_hash"]
        ):
            raise CockpitProjectionError("预测结果完整性校验失败，未展示驾驶舱")
    if draft is not None and draft["inputs"]["fiscal_year"] != year + 1:
        raise CockpitProjectionError("下一年预算财年与当前基准不匹配，未混合展示")
    facts: dict[tuple[str, ...], Json] = {}
    buckets: dict[tuple[tuple[str, ...], str], list[object]] = defaultdict(list)
    notes: list[str] = []

    def add(row: Json, metric: str, value: object) -> None:
        if row.get("season") not in SEASONS:
            raise CockpitProjectionError("保存结果含无效季度，未丢弃明细或改写合计")
        key = _key(row)
        facts.setdefault(key, {**_dimensions(row), "covered": dict.fromkeys(METRICS, False)})
        buckets[key, metric].append(value)

    baseline_rows = baseline["rows"]
    for row in baseline_rows:
        value = row.get("revenue")
        add(row, "budget", value.get("value") if isinstance(value, dict) else value)
    current_keys = set(facts)
    if forecast is not None:
        calculation = forecast["calculation"]
        ready = calculation.get("ready") is True
        for row in calculation.get("rows", []):
            if row.get("season") not in SEASONS:
                raise CockpitProjectionError("保存结果含无效季度，未丢弃明细或改写合计")
            facts.setdefault(_key(row), {**_dimensions(row), "covered": dict.fromkeys(METRICS, False)})
            # Saved empty-budget confirmation can explicitly exclude missing actual rows.
            # Exclusion is not a zero-valued prediction and must not poison other sums.
            if not (
                row.get("forecast_revenue") is None and row.get("included_in_closed_actual_total") is False
            ):
                add(row, "forecast", row.get("forecast_revenue") if ready else None)
            current_keys.add(_key(row))
        if not ready:
            notes.append("所选全年预测计算不完整，预计结转收入暂不展示；未用其他版本替代。")
        if calculation.get("trial_mode") or forecast.get("assumptions", {}).get("trial_mode"):
            notes.append("所选全年预测为试用情景，不是已确认预测。")

        # Reuse explicit mappings saved with this exact forecast. No fuzzy aliases.
        baseline_business = {_business(row) for row in baseline_rows} | {
            _business(row) for row in calculation.get("rows", [])
        }
        mapping: dict[tuple[str, ...], Json] = {}
        for item in forecast.get("assumptions", {}).get("mappings", []):
            source, target = item.get("source", {}), item.get("target", {})
            key = _business(source)
            if not all(key) or not all(_business(target)) or not item.get("reason"):
                raise CockpitProjectionError("预测保存的维度对应不完整，未猜测校区别名")
            if key in mapping or _business(target) not in baseline_business:
                raise CockpitProjectionError("预测保存的维度对应存在冲突，未合并或重复计数")
            mapping[key] = target

        snapshot = forecast["snapshot"]
        for season in SEASONS:
            observed = _actual(snapshot, season)
            members = [row for row in snapshot.get("rows", []) if row.get("season") == season]
            raw_total = amount(_sum([row.get("actual_revenue") for row in members]))
            valid = (
                observed is not None and raw_total is not None and abs(observed - raw_total) <= Decimal(".01")
            )
            if not valid:
                notes.append(f"{season}实际收入快照缺失或明细未勾稽，显示待核对，不按零处理。")
            for row in members:
                mapped = {**row, **mapping.get(_business(row), {})}
                add(mapped, "actual", row.get("actual_revenue") if valid else None)
            # Full reconciled extraction permits explicit zero for existing current-year SKUs.
            # A FY28-only SKU is not evidence of a FY27 zero observation.
            actual_keys = {key for key, metric in buckets if metric == "actual"}
            for key in current_keys:
                if facts[key]["season"] == season and key not in actual_keys:
                    add(facts[key], "actual", "0" if valid else None)
    if draft is not None:
        for row in draft["calculation"]["rows"]:
            add(row, "next_budget", row.get("revenue_yuan"))

    rows = []
    for key, row in sorted(facts.items()):
        for metric in METRICS:
            row["covered"][metric] = (key, metric) in buckets
            row[metric] = _sum(buckets[key, metric]) if row["covered"][metric] else None
        row["id"] = fingerprint(key)[:24]
        rows.append(row)
    forecast_source = (
        None
        if forecast is None
        else {
            **{
                key: forecast.get(key)
                for key in (
                    "id",
                    "sequence",
                    "status",
                    "as_of",
                    "created_at",
                    "snapshot_hash",
                    "calculation_hash",
                )
            },
            "fiscal_year": year,
            "calculation_hash": fingerprint(forecast["calculation"]),
            "ready": forecast["calculation"].get("ready") is True,
        }
    )
    next_source = (
        None
        if draft is None
        else {
            **{key: draft.get(key) for key in ("id", "sequence", "status", "created_at", "reason")},
            "fiscal_year": draft["inputs"]["fiscal_year"],
            "source": draft["source"],
            "target_revenue_yuan": draft["inputs"].get("target_revenue_yuan"),
            "total_students": draft["calculation"]["annual"].get("total_students"),
            "class_count": draft["calculation"]["annual"].get("class_count"),
        }
    )
    if forecast is None:
        notes.append("尚无已保存全年预测，实际与预计暂不可用。")
    if draft is None:
        notes.append("尚无下一年预算草稿，不将未编制当成零预算。")
    return {
        "read_only": True,
        "unit": "yuan",
        "fiscal_year": year,
        "next_fiscal_year": year + 1,
        "read_at": datetime.now(UTC).isoformat(),
        "sources": {
            "budget": {"id": baseline["sha256"], "fiscal_year": year, "status": "FINALIZED"},
            "actual": forecast_source,
            "forecast": forecast_source,
            "next_budget": next_source,
        },
        "definitions": {
            "budget": "当前财年定版预算结转收入；细分维度沿用定版明细。",
            "actual": "所选预测保存快照中，截至其数据日期的累计实际结转收入；不是季末预测。",
            "forecast": "所选预测版预计该财年最终结转收入；草稿或试用状态单独标注。",
            "next_budget": "下一财年已保存预算草稿的测算结转收入；不是实际完成或已定版。",
        },
        "rows": rows,
        "totals": totals(rows),
        "notes": notes,
    }
