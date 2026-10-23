"""Read-only daily projections of immutable forecast versions, never recalculations."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal
from zoneinfo import ZoneInfo

from packages.forecast.store import ForecastStoreError, fingerprint, forecast_generation

Json = dict[str, Any]
TrendDays = Literal["30", "90", "all"]
TIMEZONE = ZoneInfo("Asia/Shanghai")
SEASONS = ("暑假", "秋季", "寒假", "春季")


class TrendVersionNotFound(ForecastStoreError):
    pass


def _amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() in {"", "-"}:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _timestamp(version: Json) -> datetime:
    try:
        timestamp = datetime.fromisoformat(version["created_at"])
        if timestamp.tzinfo is None:
            raise ValueError("naive timestamp")
        return timestamp.astimezone(TIMEZONE)
    except (KeyError, TypeError, ValueError) as exc:
        raise ForecastStoreError("预测保存时间无效，未使用该趋势") from exc


def _actual(snapshot: Json, season: str) -> Decimal | None:
    totals = snapshot.get("actual_totals")
    if not isinstance(totals, dict) or not isinstance(totals.get(season), dict):
        return None
    # A retained failed reconciliation is not a trustworthy actual observation.
    # Do not relabel calculated quarter forecasts or zero defaults as actuals.
    provenance = snapshot.get("provenance")
    proof = provenance.get("count_proof") if isinstance(provenance, dict) else None
    checks = proof.get("department_financial_reconciliation") if isinstance(proof, dict) else None
    check = checks.get(season) if isinstance(checks, dict) else None
    if isinstance(check, dict) and check.get("matched") is False:
        return None
    issues = snapshot.get("issues")
    for issue in issues if isinstance(issues, list) else []:
        if not isinstance(issue, dict):
            continue
        if issue.get("code") in {
            "FORECAST_FINANCIAL_EMPTY",
            "FINANCIAL_PARTITION_MISMATCH",
            "FINANCIAL_DEPARTMENT_MISMATCH",
            "SUMMARY_ACTUAL_REVENUE_INVALID",
            "ACTUAL_REVENUE_SOURCE_INVALID",
            "FINANCIAL_DIMENSIONS_INCOMPLETE",
        } and issue.get("season") in {None, season}:
            return None
    return _amount(totals[season].get("actual_revenue"))


def _value(forecast: object, actual: Decimal | None) -> Json:
    forecast_amount = _amount(forecast)
    return {
        "forecast_revenue": str(forecast_amount) if forecast_amount is not None else None,
        "actual_revenue": str(actual) if actual is not None else None,
        "actual_status": "AVAILABLE" if actual is not None else "UNAVAILABLE",
    }


def _periods(version: Json) -> Json:
    snapshot, calculation = version["snapshot"], version["calculation"]
    quarters = calculation.get("quarters")
    quarters = quarters if isinstance(quarters, list) else []
    actuals = [_actual(snapshot, season) for season in SEASONS]
    periods: Json = {}
    for season, actual in zip(SEASONS, actuals, strict=True):
        matches = [q for q in quarters if isinstance(q, dict) and q.get("season") == season]
        periods[season] = _value(matches[0].get("forecast_revenue") if len(matches) == 1 else None, actual)
    annual_actual = None
    if all(amount is not None for amount in actuals):
        amounts = [amount for amount in actuals if amount is not None]
        with localcontext() as context:
            # Preserve the retained decimal precision rather than summing floats
            # or previously rounded graph labels.
            context.prec = max(
                28,
                max(amount.adjusted() for amount in amounts)
                - min(int(amount.as_tuple().exponent) for amount in amounts)
                + len(str(len(amounts)))
                + 2,
            )
            annual_actual = sum(amounts, Decimal(0))
    annual = calculation.get("annual")
    periods["全年"] = _value(
        annual.get("forecast_revenue") if isinstance(annual, dict) else None, annual_actual
    )
    return periods


def project_forecast_versions(versions: list[Json], *, baseline_hash: str) -> list[Json]:
    """Validate once and discard raw source rows from the compact read projection."""
    projected: list[Json] = []
    for version in versions:
        if version.get("baseline_hash") != baseline_hash:
            raise ForecastStoreError("FY27 定版档案与预测基准不一致")
        if not isinstance(version.get("snapshot"), dict) or fingerprint(version["snapshot"]) != version.get(
            "snapshot_hash"
        ):
            raise ForecastStoreError("预测来源快照完整性校验失败")
        calculation = version.get("calculation")
        complete = isinstance(calculation, dict) and calculation.get("ready") is True
        projected.append(
            {
                "date": _timestamp(version).date().isoformat(),
                "version_id": version["id"],
                "sequence": version["sequence"],
                "status": version["status"],
                "created_at": version["created_at"],
                "as_of": version["as_of"],
                "generation": forecast_generation(version),
                "periods": _periods(version) if complete else None,
            }
        )
    # A daily point may omit intraday revisions. Compare with the saved version's
    # exact parent, never the preceding day selected for the chart. Keep only a
    # bounded projection; no snapshots, source rows or recursive parent chain.
    by_id = {point["version_id"]: point for point in projected}
    for version, point in zip(versions, projected, strict=True):
        parent = by_id.get(version.get("parent_id"))
        point["previous_version"] = (
            {key: parent[key] for key in ("version_id", "sequence", "created_at", "as_of", "periods")}
            if parent is not None and parent["sequence"] + 1 == point["sequence"]
            else None
        )
    return projected


def select_forecast_trend(
    projected: list[Json], *, days: TrendDays = "30", through_version: str | None = None
) -> Json:
    """Choose the last complete saved version per Shanghai day before the cutoff.

    An empty day is not a zero. The window ends at the selected saved version,
    so viewing an older archive never injects today's date or later versions.
    The complete version ledger remains intact, including intraday revisions.
    """
    result: Json = {
        "baseline_year": 2027,
        "timezone": TIMEZONE.key,
        "points": [],
        "omitted_incomplete_count": 0,
    }
    if days not in {"30", "90", "all"}:
        raise ValueError("unsupported trend window")
    selected = (
        next((v for v in projected if v.get("version_id") == through_version), None)
        if through_version
        else None
    )
    if through_version and selected is None:
        raise TrendVersionNotFound("本部门不存在这次预测")
    if not projected:
        return result
    selected = selected or max(projected, key=lambda v: v["sequence"])
    end = _timestamp(selected).date()
    start = end - timedelta(days=int(days) - 1) if days != "all" else date.min
    daily: dict[date, tuple[datetime, Json]] = {}
    for version in projected:
        if version["sequence"] > selected["sequence"]:
            continue
        saved = _timestamp(version)
        if not start <= saved.date() <= end:
            continue
        if version["periods"] is None:
            result["omitted_incomplete_count"] += 1
            continue
        previous = daily.get(saved.date())
        if previous is None or (saved, version["sequence"]) > (previous[0], previous[1]["sequence"]):
            daily[saved.date()] = saved, version
    result["points"] = [version for _, (_, version) in sorted(daily.items())]
    return result


def build_forecast_trend(
    versions: list[Json], *, baseline_hash: str, days: TrendDays = "30", through_version: str | None = None
) -> Json:
    return select_forecast_trend(
        project_forecast_versions(versions, baseline_hash=baseline_hash),
        days=days,
        through_version=through_version,
    )
