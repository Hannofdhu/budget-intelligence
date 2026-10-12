"""Adapt a confirmed forecast to the existing annual calculation engines.

This is a local planning draft, not a fabricated mature BI snapshot. Inputs are
frozen separately from FY27 and the existing FY28 publication. No confirmation,
lock, target balancing or publication command is executed here.
"""

from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from packages.class_size.validation import ClassSizeInput, HeadcountInput, validate_class_sizes
from packages.forecast.calculation import DIMS, SEASONS, _decimal, _integer, _key, _scope_rows, _source_index
from packages.forecast.store import fingerprint
from packages.headcount.calculation import (
    CombinationInput,
    ManualPlanInput,
    RateInput,
    RateType,
    _largest_remainder,
    calculate_headcounts,
)
from packages.headcount.opening import OpeningKey, SpringSource, prepare_forecast_opening
from packages.revenue.calculation import RevenueLineInput, calculate_revenue
from packages.target_closure.calculation import TargetRevenueInput, calculate_target_closure

Json = dict[str, Any]
RULE_VERSION = "forecast-to-next-year-draft-v2"
Season = Literal["暑假", "秋季", "寒假", "春季"]


class NextYearError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Combination(StrictModel):
    id: StrictStr
    track: StrictStr
    product: StrictStr
    grade: StrictStr
    subject: StrictStr
    campus: StrictStr
    entry: StrictBool
    enabled: StrictBool
    source_entry_weight: StrictInt = Field(ge=0, le=10_000_000)
    source_new_by_season: dict[Season, StrictInt]
    opening_carryover_yuan: StrictStr
    entry_override: StrictBool | None = None
    entry_students: StrictInt | None = Field(default=None, ge=0, le=10_000_000)
    entry_locked: StrictBool = False
    added: StrictBool = False
    reason: StrictStr = Field(default="", max_length=1000)


class Rate(StrictModel):
    id: StrictStr
    season: Season
    track: StrictStr
    product: StrictStr
    grade: StrictStr
    campus: StrictStr
    renewal_ppm: StrictInt = Field(ge=0, le=1_000_000, multiple_of=100)
    share_ppm: StrictInt = Field(ge=0, le=999_900, multiple_of=100)
    locked: StrictBool = False
    renewal_lower_ppm: StrictInt = Field(default=0, ge=0, le=1_000_000, multiple_of=100)
    renewal_upper_ppm: StrictInt = Field(default=1_000_000, ge=0, le=1_000_000, multiple_of=100)
    share_lower_ppm: StrictInt = Field(default=0, ge=0, le=999_900, multiple_of=100)
    share_upper_ppm: StrictInt = Field(default=999_900, ge=0, le=999_900, multiple_of=100)


class Finance(StrictModel):
    id: StrictStr
    combination_id: StrictStr
    season: Season
    price_yuan: StrictStr
    discount_ppm: StrictInt = Field(ge=0, le=1_000_000, multiple_of=1000)
    recognition_ppm: StrictInt = Field(ge=0, le=1_000_000)


class ClassSize(StrictModel):
    track: StrictStr
    product: StrictStr
    minimum: StrictInt = Field(ge=1, le=10000)
    planned_class_size: StrictInt | None = Field(default=None, ge=1, le=10000)


class Plan(StrictModel):
    combination_id: StrictStr
    season: Season
    students: StrictInt = Field(ge=0, le=10_000_000)
    locked: StrictBool = False
    reason: StrictStr = Field(default="", max_length=1000)


class OpeningSource(StrictModel):
    track: StrictStr
    product: StrictStr
    grade: StrictStr
    subject: StrictStr
    campus: StrictStr
    students: StrictInt = Field(ge=0, le=10_000_000)
    target_id: StrictStr | None
    exclusion: StrictStr | None


class Inputs(StrictModel):
    fiscal_year: StrictInt = Field(ge=2028, le=2100)
    source_as_of: StrictStr
    target_revenue_yuan: StrictStr | None
    track_targets: dict[StrictStr, StrictStr | None]
    entry_total: StrictInt = Field(ge=0, le=10_000_000)
    tax_factor: StrictStr
    combinations: list[Combination] = Field(min_length=1, max_length=2000)
    rates: list[Rate] = Field(min_length=1, max_length=8000)
    finance: list[Finance] = Field(min_length=4, max_length=8000)
    class_sizes: list[ClassSize] = Field(min_length=1, max_length=2000)
    plans: list[Plan] = Field(min_length=4, max_length=8000)
    opening_sources: list[OpeningSource] = Field(max_length=2000)
    notes: list[Json] = Field(max_length=10000)

    @field_validator("target_revenue_yuan", "tax_factor")
    @classmethod
    def finite_decimal(cls, value: str | None) -> str | None:
        if value is not None:
            number(value)
        return value


def number(value: object, *, maximum: str = "1000000000000", positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise NextYearError("请输入有效数字")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NextYearError("请输入有效数字") from exc
    if not result.is_finite() or result < 0 or result > Decimal(maximum) or (positive and result <= 0):
        raise NextYearError("数字超出允许范围")
    return result


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _id(key: object) -> str:
    return fingerprint(key)[:24]


def _ppm(value: Decimal, *, discount: bool = False) -> int:
    step = 1000 if discount else 100
    return int((value * 1_000_000 / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * step


def forecast_basis(forecast: Json) -> Json:
    return {
        "id": forecast["id"],
        "sequence": forecast["sequence"],
        "status": forecast["status"],
        "as_of": forecast["as_of"],
        "forecast_revenue": forecast["calculation"]["annual"]["forecast_revenue"],
        "forecast_total": forecast["calculation"]["annual"]["forecast_total"],
        "calculation_hash": fingerprint(forecast["calculation"]),
        "snapshot_hash": forecast["snapshot_hash"],
        "baseline_hash": forecast["baseline_hash"],
    }


def prepare_inputs(baseline: Json, forecast: Json, fiscal_year: int | None = None) -> Json:
    source_year = baseline.get("fiscal_year", 2027)
    fiscal_year = source_year + 1 if fiscal_year is None else fiscal_year
    if type(fiscal_year) is not int or not 2028 <= fiscal_year <= 2100 or fiscal_year != source_year + 1:
        raise NextYearError("预算财年必须是已确认预测的下一财年")
    if (
        forecast.get("status") != "CONFIRMED"
        or forecast["assumptions"].get("trial_mode")
        or not forecast["calculation"].get("ready")
        or any(i.get("severity") == "BLOCKING" for i in forecast["calculation"].get("issues", []))
        or forecast["baseline_hash"] != baseline["sha256"]
        or fingerprint(forecast["snapshot"]) != forecast["snapshot_hash"]
    ):
        raise NextYearError("请先确认有效的 FY27 预测")
    notes: list[Json] = []
    parameters: dict[tuple[str, tuple[str, ...]], Json] = {
        (r["season"], _key(r)): deepcopy(r) for r in baseline["rows"]
    }
    scope_issues: list[Json] = []
    parameters.update(
        _scope_rows(
            forecast["assumptions"].get("forecast_scopes", []),
            parameters,
            forecast["assumptions"].get("minimum_sample", 30),
            True,
            notes,
            scope_issues,
        )
    )
    if scope_issues:
        raise NextYearError("已确认预测的业务参数不完整，不能生成下一年草稿")
    forecasts = {(r["season"], _key(r)): r for r in forecast["calculation"]["rows"]}
    all_keys = {_key(r) for r in forecast["calculation"]["rows"]}
    business = {k for k in all_keys if not forecasts.get(("春季", k), {}).get("counts_not_applicable")}
    if any((s, k) not in forecasts for k in business for s in SEASONS):
        raise NextYearError("预测明细未覆盖四个季度")
    active = {
        k
        for k in business
        if any((_integer(forecasts[s, k]["forecast_total"]) or 0) > 0 for s in SEASONS)
        or number(forecasts["春季", k]["forecast_carryout"]) > 0
    }
    springs: list[Json] = []
    for key in sorted(business):
        students = _integer(forecasts["春季", key]["forecast_total"])
        if students is None:
            raise NextYearError("春季预计人次未知，不能默认补零作为下一年期初")
        if not students:
            continue
        source = dict(zip(DIMS, key, strict=True))
        # The shared opening adapter owns promotion/graduation/subject endings.
        transition = prepare_forecast_opening(
            fiscal_year=fiscal_year,
            source_fiscal_season=source_year * 10 + 4,
            snapshot_date=date.fromisoformat(forecast["as_of"]),
            source_batch=forecast["id"],
            source_snapshot_id=forecast["snapshot_hash"],
            rows=[SpringSource(OpeningKey(**source), students, 0, forecast["id"])],
        ).rows[0]
        target_key = tuple(vars(transition.target_key).values()) if transition.target_key else None
        if target_key:
            active.add(target_key)
        springs.append(
            {
                **source,
                "students": students,
                "target_id": _id(target_key) if target_key else None,
                "exclusion": transition.exclusion,
            }
        )
    financial_only = {k for k in all_keys if forecasts.get(("春季", k), {}).get("counts_not_applicable")}
    if any(number(forecasts["春季", k]["forecast_carryout"]) != 0 for k in financial_only):
        raise NextYearError("纯财务业务有跨年结转，需先建立独立财务范围；未丢弃该金额")
    source_issues: list[Json] = []
    actuals, _, _ = _source_index(
        forecast["snapshot"], forecast["assumptions"], all_keys, source_issues, financial_only
    )
    if source_issues:
        raise NextYearError("预测的冻结业务来源未通过校验")
    exempt = {
        (i.get("season"), _key(i.get("dims", {})))
        for i in forecast["calculation"].get("issues", [])
        if i.get("code") == "FINALIZED_ACTUAL_MISSING"
        and i.get("severity") == "INFO"
        and i.get("detail", {}).get("classification") == "EMPTY_BUDGET_SLOT_NO_SOURCE_BUSINESS"
    }
    combinations: list[Json] = []
    finance: list[Json] = []
    cohorts: dict[tuple[str, ...], list[Json]] = defaultdict(list)
    class_sizes: dict[tuple[str, str], int] = {}
    plans: list[Json] = []

    def profile(season: str, key: tuple[str, ...]) -> Json:
        if (season, key) in parameters:
            return parameters[season, key]
        peers: list[Json] = [
            r
            for (s, _), r in parameters.items()
            if s == season and r["track"] == key[0] and r["product"] == key[1] and r["grade"] == key[2]
        ]
        if not peers:
            peers = [
                r
                for (s, _), r in parameters.items()
                if s == season and r["track"] == key[0] and r["product"] == key[1]
            ]
        if not peers:
            raise NextYearError("升年级后的业务缺少同产品参数，请先补充预测参数")
        peers.sort(key=lambda r: (r["campus"] != key[4], r["subject"] != key[3], _key(r)))
        notes.append(
            {
                "code": "PROMOTED_PROFILE",
                "season": season,
                "dims": dict(zip(DIMS, key, strict=True)),
                "message": "升年级的新范围先沿用同产品参数，可在草稿中调整",
            }
        )
        return peers[0]

    for key in sorted(active):
        dims = dict(zip(DIMS, key, strict=True))
        identifier = _id(key)
        # A promoted grade may borrow financial parameters from an entry grade,
        # but must not inherit its entry attribute without an explicit scope row.
        exact_summer = parameters.get(("暑假", key))
        entry = key[2] == "七年级" or bool(exact_summer and exact_summer.get("entry") == "是")
        summer = forecasts.get(("暑假", key))
        summer_source = actuals.get(("暑假", key))
        if entry and summer:
            if forecast["states"]["暑假"] == "FINALIZED":
                entry_weight = _integer(summer_source.get("new_business")) if summer_source else None
                if entry_weight is None and ("暑假", key) in exempt:
                    entry_weight = 0
                if entry_weight is None:
                    raise NextYearError("暑假业务入口新生未知，不能用退班分类新生替代")
            else:
                raise NextYearError("当前入口参考要求暑假已定版；未把预测分类人次冒充业务新生")
        else:
            entry_weight = 0
        new_by_season: dict[str, int] = {}
        for season in SEASONS:
            fr = forecasts.get((season, key))
            new = _integer(fr.get("forecast_new")) if fr else 0
            if new is None and (season, key) in exempt:
                new = 0
            if new is None:
                raise NextYearError("预计新生人次未知，不能默认补零为新年度计划")
            new_by_season[season] = new
        carry = forecasts.get(("春季", key), {}).get("forecast_carryout", "0")
        number(carry)
        combinations.append(
            {
                "id": identifier,
                **dims,
                "entry": entry,
                "enabled": True,
                "source_entry_weight": entry_weight,
                "source_new_by_season": new_by_season,
                "opening_carryover_yuan": str(carry),
            }
        )
        size_values = {
            minimum
            for (_, k), r in parameters.items()
            if k[:2] == key[:2] and (minimum := _integer(r.get("minimum"))) is not None and minimum > 0
        }
        if len(size_values) != 1:
            raise NextYearError("产品班均缺失或冲突，不能静默设置")
        class_sizes[(key[0], key[1])] = next(iter(size_values))
        for season in SEASONS:
            row = profile(season, key)
            price, discount, recognition = (
                _decimal(row.get(f)) for f in ("price", "discount", "recognition")
            )
            if price is None or discount is None or recognition is None:
                raise NextYearError("价格、折扣或结转比例缺失，不能生成完整草稿")
            finance.append(
                {
                    "id": _id((season, key)),
                    "combination_id": identifier,
                    "season": season,
                    "price_yuan": str(price),
                    "discount_ppm": _ppm(discount, discount=True),
                    "recognition_ppm": _ppm(recognition),
                }
            )
            cohorts[(season, key[0], key[1], key[2], key[4])].append(row)
            plans.append({"combination_id": identifier, "season": season, "students": new_by_season[season]})
    rates: list[Json] = []
    for key, peers in sorted(cohorts.items()):
        values: dict[str, int] = {}
        for field in ("renewal", "share"):
            known = [(r, _decimal(r.get(field))) for r in peers if _decimal(r.get(field)) is not None]
            if any(v is not None and not 0 <= v <= 1 for _, v in known):
                raise NextYearError("继承比例超出0%至100%，请先校验预测参数")
            if field == "share" and any(v is not None and Decimal("0.9999") < v < 1 for _, v in known):
                raise NextYearError("继承新生占比超过99.99%，不能截断比例后倒推人数")
            # 100% is an absolute-new row, never a reverse-share parameter.
            valid = [
                (r, value)
                for r, value in known
                if value is not None and 0 <= value <= 1 and (field != "share" or value < 1)
            ]
            if valid:
                unique = {v for _, v in valid}
                if len(unique) == 1:
                    average = next(iter(unique))
                else:
                    # Preserve the existing cohort grain, not a mean of percentages.
                    # These are inherited budget assumptions, not historical actual rates.
                    prior_season = SEASONS[SEASONS.index(key[0]) - 1]
                    weighted = [
                        (
                            v,
                            _integer(r.get("total"))
                            if field == "share"
                            else _integer(parameters.get((prior_season, _key(r)), {}).get("total")),
                        )
                        for r, v in valid
                    ]
                    if any(w is None for _, w in weighted) or sum(w or 0 for _, w in weighted) == 0:
                        raise NextYearError("同组比例参数冲突且无完整基数，请先统一预测参数")
                    denominator = sum(w or 0 for _, w in weighted)
                    average = sum((v * (w or 0) for v, w in weighted), Decimal(0)) / denominator
                values[field] = _ppm(average)
                if len(unique) > 1:
                    notes.append(
                        {
                            "code": "COHORT_WEIGHTED_PARAMETER",
                            "season": key[0],
                            "message": "同赛道产品年级校区参数按原人数加权继承",
                            "field": field,
                        }
                    )
            else:
                opening_cohort = key[0] == "暑假" and any(
                    s["target_id"]
                    and s["target_id"]
                    in {
                        c["id"]
                        for c in combinations
                        if (c["track"], c["product"], c["grade"], c["campus"]) == key[1:]
                    }
                    for s in springs
                )
                # A missing rate is not 100% retention. For newly promoted scope,
                # start from a same-track / same-grade sample, explicitly a draft
                # planning assumption rather than a source product's actual rate.
                pool: list[tuple[Decimal, int]] = []
                if field == "renewal" and not any(r.get("_hold_size") for r in peers):
                    previous = SEASONS[SEASONS.index(key[0]) - 1]
                    for (season, sample_dims), candidate in parameters.items():
                        if season != key[0] or (sample_dims[0], sample_dims[2]) != (key[1], key[3]):
                            continue
                        candidate_rate = _decimal(candidate.get("renewal"))
                        base = _integer(parameters.get((previous, sample_dims), {}).get("total"))
                        if (
                            candidate_rate is not None
                            and 0 <= candidate_rate <= 1
                            and base is not None
                            and base > 0
                        ):
                            pool.append((candidate_rate, base))
                if pool:
                    values[field] = _ppm(sum((v * w for v, w in pool), Decimal(0)) / sum(w for _, w in pool))
                    message = "新范围续班率暂按同赛道同年级样本加权起步，可在招生参数中调整"
                elif field == "renewal" and any(r.get("_hold_size") for r in peers):
                    values[field] = 1_000_000
                    message = "独立业务沿用已生效的规模保持规则，可在草稿中调整"
                else:
                    values[field] = 0
                    message = "无适用比例先设为0；零老生范围使用可编辑的计划新生"
                    if field == "renewal" and opening_cohort:
                        message = "跨年新范围缺续班样本，先设为0，可在招生参数中调整"
                notes.append(
                    {
                        "code": "NO_APPLICABLE_PARAMETER",
                        "season": key[0],
                        "message": message,
                        "field": field,
                        "dims": {"track": key[1], "product": key[2], "grade": key[3], "campus": key[4]},
                        "value_ppm": values[field],
                    }
                )
        rates.append(
            {
                "id": _id(key),
                "season": key[0],
                "track": key[1],
                "product": key[2],
                "grade": key[3],
                "campus": key[4],
                "renewal_ppm": values["renewal"],
                "share_ppm": values["share"],
            }
        )
    track_targets: dict[str, Decimal] = defaultdict(Decimal)
    for row in forecast["calculation"]["rows"]:
        value = _decimal(row.get("forecast_revenue"))
        if value is None and (row["season"], _key(row)) in exempt:
            value = Decimal(0)
        if value is None:
            raise NextYearError("预测收入未知，不能生成完整新年度目标")
        track_targets[row["track"]] += value
    inputs = {
        "fiscal_year": fiscal_year,
        "source_as_of": forecast["as_of"],
        "target_revenue_yuan": None,
        "track_targets": {k: None for k in sorted(track_targets)},
        "entry_total": sum(r["source_entry_weight"] for r in combinations),
        "tax_factor": "1.06",
        "combinations": combinations,
        "rates": rates,
        "finance": finance,
        "class_sizes": [
            {"track": k[0], "product": k[1], "minimum": v} for k, v in sorted(class_sizes.items())
        ],
        "plans": plans,
        "opening_sources": springs,
        "notes": notes,
    }
    validate_plan_inputs(inputs, inputs)
    return inputs


def validate_plan_inputs(inputs: Json, original: Json) -> None:
    try:
        Inputs.model_validate(inputs)
    except ValidationError as exc:
        raise NextYearError("预算输入无效：请检查整数人次、比例步长和必填参数") from exc
    try:
        date.fromisoformat(inputs["source_as_of"])
    except ValueError as exc:
        raise NextYearError("预测日期无效") from exc
    combinations = inputs["combinations"]
    combination_ids = {r["id"] for r in combinations}
    if len(combination_ids) != len(combinations) or len({_key(r) for r in combinations}) != len(combinations):
        raise NextYearError("预算范围重复")
    expected_finance = {(identifier, season) for identifier in combination_ids for season in SEASONS}
    expected_rates = {
        (s, r["track"], r["product"], r["grade"], r["campus"]) for r in combinations for s in SEASONS
    }
    expected_classes = {(r["track"], r["product"]) for r in combinations}
    coverage = (
        ("finance", ("combination_id", "season"), expected_finance),
        ("plans", ("combination_id", "season"), expected_finance),
        ("rates", ("season", "track", "product", "grade", "campus"), expected_rates),
        ("class_sizes", ("track", "product"), expected_classes),
    )
    for name, fields, expected in coverage:
        actual = [tuple(r[field] for field in fields) for r in inputs[name]]
        if len(set(actual)) != len(actual) or set(actual) != expected:
            raise NextYearError("预算参数必须无重复且完整覆盖四季度业务范围")
    for name in ("finance", "rates"):
        if len({r["id"] for r in inputs[name]}) != len(inputs[name]):
            raise NextYearError("预算参数编号重复")
    if len({_key(r) for r in inputs["opening_sources"]}) != len(inputs["opening_sources"]):
        raise NextYearError("春季预计人次来源重复")
    if any(
        r["target_id"] is not None and r["target_id"] not in combination_ids
        for r in inputs["opening_sources"]
    ):
        raise NextYearError("跨年人数映射缺少目标范围")
    for row in combinations:
        if set(row["source_new_by_season"]) != set(SEASONS) or any(
            type(v) is not int or v < 0 for v in row["source_new_by_season"].values()
        ):
            raise NextYearError("参考新生人次必须完整覆盖四季度")
        carry = number(row["opening_carryover_yuan"])
        if not row["enabled"] and carry > 0:
            raise NextYearError("该范围有预计期初结转，不能关闭并丢弃金额")
        if not all(isinstance(row[k], str) and row[k].strip() for k in ("id", *DIMS)):
            raise NextYearError("预算业务维度和编号不能为空")
        if row.get("entry_locked") and row.get("entry_students") is None:
            raise NextYearError("请先填写入口人次再锁定")
        if row.get("entry_students") is not None and not effective_entry(row):
            raise NextYearError("非入口范围不能分配入口人次")
    for row in inputs["rates"]:
        for kind in ("renewal", "share"):
            lower = row.get(kind + "_lower_ppm", 0)
            upper = row.get(kind + "_upper_ppm", 1_000_000 if kind == "renewal" else 999_900)
            if not lower <= row[kind + "_ppm"] <= upper:
                raise NextYearError("当前招生参数必须位于设置的上下限内")
    for row in inputs["plans"]:
        if row.get("locked") and not row.get("reason", "").strip():
            raise NextYearError("锁定手动新生人次时请填写调整原因")
    immutable = ("fiscal_year", "source_as_of", "opening_sources", "notes")
    if any(inputs[k] != original[k] for k in immutable):
        raise NextYearError("预测来源不可修改，请从已确认预测重新生成草稿")
    fixed = {
        "combinations": (
            "id",
            *DIMS,
            "entry",
            "source_entry_weight",
            "source_new_by_season",
            "opening_carryover_yuan",
        ),
        "rates": ("id", "season", "track", "product", "grade", "campus"),
        "finance": ("id", "combination_id", "season"),
        "class_sizes": ("track", "product"),
        "plans": ("combination_id", "season"),
    }

    def project(value: list[Json], keys: tuple[str, ...]) -> list[str]:
        return sorted(fingerprint({k: r[k] for k in keys}) for r in value)

    original_ids = {r["id"] for r in original["combinations"]}
    added_ids = combination_ids - original_ids
    if not original_ids <= combination_ids:
        raise NextYearError("已有预算业务不能删除，请明确关闭业务范围")
    for row in combinations:
        if row["id"] in added_ids and (
            not row.get("added")
            or not row.get("reason", "").strip()
            or row["source_entry_weight"] != 0
            or any(row["source_new_by_season"].values())
            or number(row["opening_carryover_yuan"]) != 0
        ):
            raise NextYearError("新增业务必须说明原因，且不能伪造历史人次或期初结转")
    for name, keys in fixed.items():
        if name == "combinations":
            current = [r for r in inputs[name] if r["id"] in original_ids]
        elif name in {"finance", "plans"}:
            current = [r for r in inputs[name] if r["combination_id"] in original_ids]
        elif name == "rates":
            original_keys = {tuple(r[k] for k in keys[1:]) for r in original[name]}
            current = [r for r in inputs[name] if tuple(r[k] for k in keys[1:]) in original_keys]
        else:
            original_keys = {tuple(r[k] for k in keys) for r in original[name]}
            current = [r for r in inputs[name] if tuple(r[k] for k in keys) in original_keys]
        if project(current, keys) != project(original[name], keys):
            raise NextYearError("预算业务范围或来源已变化，不能静默替换")
    target = number(inputs["target_revenue_yuan"]) if inputs["target_revenue_yuan"] is not None else None
    if set(inputs["track_targets"]) != {r["track"] for r in combinations}:
        raise NextYearError("收入目标必须覆盖全部赛道")
    track_values = list(inputs["track_targets"].values())
    if any(value is not None for value in track_values):
        if target is None:
            raise NextYearError("请先填写全年经营目标")
        if any(value is None for value in track_values):
            raise NextYearError("请完整填写赛道目标，或全部留空")
        if abs(sum((number(v) for v in track_values), Decimal(0)) - target) > Decimal("0.01"):
            raise NextYearError("赛道目标合计必须等于年度目标")
    number(inputs["tax_factor"], maximum="10", positive=True)
    for row in inputs["finance"]:
        number(row["price_yuan"], maximum="1000000000")
    if not any(r["enabled"] for r in inputs["combinations"]):
        raise NextYearError("至少保留一个预算业务范围")


def calculate_plan(inputs: Json) -> Json:
    with localcontext() as context:
        context.prec = 38
        return _calculate_plan(inputs)


def effective_entry(row: Json) -> bool:
    """An editable draft attribute, separate from the immutable source attribute."""
    return bool(row["entry"] if row.get("entry_override") is None else row["entry_override"])


def allocate_entry_counts(inputs: Json) -> tuple[dict[str, int], dict[str, int], Json]:
    entries = [r for r in inputs["combinations"] if r["enabled"] and effective_entry(r)]
    explicit = {r["id"]: r["entry_students"] for r in entries if r.get("entry_students") is not None}
    remaining = inputs["entry_total"] - sum(explicit.values())
    if remaining < 0:
        raise NextYearError("已分配入口人次超过财年入口总量")
    unassigned = [r for r in entries if r["id"] not in explicit]
    weights: dict[str, int] = defaultdict(int)
    for row in entries:
        weights[row["campus"]] += row["source_entry_weight"]
    historical_weights: dict[str, int] = defaultdict(int)
    for row in unassigned:
        historical_weights[row["campus"]] += row["source_entry_weight"]
    total_weight = sum(historical_weights.values())
    if remaining > 0 and total_weight == 0:
        raise NextYearError("剩余入口人次没有分配依据，请填写新增入口范围的人次或调整总量")
    campus_values: dict[str, Fraction | None] = {
        campus: Fraction(remaining * value, total_weight) if total_weight else Fraction(0)
        for campus, value in historical_weights.items()
    }
    campus_allocated = _largest_remainder(campus_values, fixed_ids=set(), student_type="NEW")
    counts = dict(explicit)
    for campus, (students, _) in campus_allocated.items():
        members = [r for r in unassigned if r["campus"] == campus]
        denominator = historical_weights[campus]
        values: dict[str, Fraction | None] = {
            r["id"]: Fraction((students or 0) * r["source_entry_weight"], denominator)
            if denominator
            else Fraction(0)
            for r in members
        }
        counts.update(
            {
                key: value or 0
                for key, (value, _) in _largest_remainder(values, fixed_ids=set(), student_type="NEW").items()
            }
        )
    if sum(counts.values()) != inputs["entry_total"]:
        raise NextYearError("入口分配合计必须等于财年入口总量")
    allocated_by_campus: dict[str, int] = defaultdict(int)
    for row in entries:
        allocated_by_campus[row["campus"]] += counts.get(row["id"], 0)
    campus_result = {campus: (students, None) for campus, students in allocated_by_campus.items()}
    return counts, dict(weights), campus_result


def _calculate_plan(inputs: Json) -> Json:
    validate_plan_inputs(inputs, inputs)
    active = [r for r in inputs["combinations"] if r["enabled"]]
    by_id = {r["id"]: r for r in active}
    rates = {(r["season"], r["track"], r["product"], r["grade"], r["campus"]): r for r in inputs["rates"]}
    fiscal_year = inputs["fiscal_year"]
    entry_counts, weights, campus_allocated = allocate_entry_counts(inputs)
    combinations = [
        CombinationInput(
            r["id"],
            r["track"],
            r["product"],
            "ENTRY" if effective_entry(r) else "NON_ENTRY",
            r["grade"],
            r["subject"],
            r["campus"],
            entry_counts.get(r["id"], 0),
        )
        for r in active
    ]
    opening_old: dict[str, Fraction] = {r["id"]: Fraction(0) for r in active}
    opening_rows: list[SpringSource] = []
    for row in inputs["opening_sources"]:
        target = by_id.get(row["target_id"])
        rate = (
            rates[("暑假", target["track"], target["product"], target["grade"], target["campus"])]
            if target
            else None
        )
        opening_rows.append(
            SpringSource(
                OpeningKey(**{k: row[k] for k in DIMS}),
                row["students"],
                rate["renewal_ppm"] if rate else 0,
                rate["id"] if rate else "excluded",
            )
        )
    prepared = prepare_forecast_opening(
        fiscal_year=fiscal_year,
        source_fiscal_season=(fiscal_year - 1) * 10 + 4,
        snapshot_date=date.fromisoformat(inputs["source_as_of"]),
        source_batch="frozen-forecast-inputs",
        source_snapshot_id=fingerprint(inputs["opening_sources"]),
        rows=opening_rows,
    )
    for row in prepared.rows:
        if row.target_key:
            key = tuple(vars(row.target_key).values())
            identifier = _id(key)
            if identifier in opening_old:
                opening_old[identifier] += row.theoretical_old_students
    engine_rates: list[RateInput] = []
    rate_fields: tuple[tuple[RateType, str], ...] = (
        ("RENEWAL_RATE", "renewal_ppm"),
        ("NEW_STUDENT_SHARE", "share_ppm"),
    )
    for row in inputs["rates"]:
        for kind, field in rate_fields:
            engine_rates.append(
                RateInput(
                    row["id"] + field,
                    kind,
                    fiscal_year * 10 + SEASONS.index(row["season"]) + 1,
                    row["track"],
                    row["product"],
                    row["grade"],
                    row["campus"],
                    row[field],
                )
            )
    seeds = {(r["combination_id"], r["season"]): r["students"] for r in inputs["plans"]}
    planned: dict[tuple[int, str], ManualPlanInput] = {
        (fiscal_year * 10 + SEASONS.index(row["season"]) + 1, row["combination_id"]): ManualPlanInput(
            "manual-" + row["combination_id"] + row["season"],
            row["combination_id"],
            fiscal_year * 10 + SEASONS.index(row["season"]) + 1,
            row["students"],
            True,
        )
        for row in inputs["plans"]
        if row.get("locked") and row["combination_id"] in by_id
    }
    # Supply absolute plans only where the unchanged engine requires zero-old plans.
    # A seed is NOT a formal manual lock; all outputs remain unconfirmed planning drafts.
    for _ in range(5):
        headcounts = calculate_headcounts(
            fiscal_year=fiscal_year,
            combinations=combinations,
            rates=engine_rates,
            plans=list(planned.values()),
            opening_old_students=opening_old,
        )
        required = [v for v in headcounts.validations if v.code == "LOCKED_PLANNED_NEW_STUDENTS_REQUIRED"]
        if not required:
            break
        for item in required:
            identifier = item.combination_stable_id or ""
            season = SEASONS[item.fiscal_season % 10 - 1]
            # An ENTRY with a zero allocation must remain zero, not resurrect a seed.
            value = (
                0
                if item.fiscal_season == fiscal_year * 10 + 1 and effective_entry(by_id[identifier])
                else seeds[identifier, season]
            )
            planned[item.fiscal_season, identifier] = ManualPlanInput(
                "draft-" + identifier + season, identifier, item.fiscal_season, value, True
            )
    finance = {(r["combination_id"], r["season"]): r for r in inputs["finance"]}
    lines: list[RevenueLineInput] = []
    class_headcounts: list[HeadcountInput] = []
    for row in headcounts.rows:
        combo = by_id[row.combination.stable_id]
        season = SEASONS[row.season_index - 1]
        settings = finance[combo["id"], season]
        dims = [combo[k] for k in DIMS]
        line_id = combo["id"] + season
        lines.append(
            RevenueLineInput(
                line_id,
                combo["id"],
                row.fiscal_season,
                dims[0],
                dims[1],
                row.combination.entry_attribute,
                dims[2],
                dims[3],
                dims[4],
                row.integer_total_students,
                number(settings["price_yuan"]),
                settings["discount_ppm"],
                settings["recognition_ppm"],
                number(combo["opening_carryover_yuan"]),
            )
        )
        class_headcounts.append(
            HeadcountInput(
                line_id,
                combo["id"],
                row.fiscal_season,
                dims[0],
                dims[1],
                row.combination.entry_attribute,
                dims[2],
                dims[3],
                dims[4],
                "CALCULATED" if row.integer_total_students is not None else "BLOCKED",
                row.integer_total_students,
            )
        )
    revenue = calculate_revenue(
        fiscal_year=fiscal_year,
        budget_tax_conversion_factor=number(inputs["tax_factor"], positive=True),
        lines=lines,
        composite_targets=[],
    )
    class_sizes = validate_class_sizes(
        headcounts=class_headcounts,
        parameters=[
            ClassSizeInput(_id((r["track"], r["product"])), r["track"], r["product"], r["minimum"])
            for r in inputs["class_sizes"]
        ],
    )
    headcount_index = {(r.combination.stable_id, r.fiscal_season): r for r in headcounts.rows}
    class_index = {
        (r.headcount.combination_stable_key, r.headcount.fiscal_season): r for r in class_sizes.rows
    }
    planned_class_sizes = {
        (r["track"], r["product"]): r.get("planned_class_size") for r in inputs["class_sizes"]
    }
    manual_locks = {(r["combination_id"], r["season"]) for r in inputs["plans"] if r.get("locked")}
    rows: list[Json] = []
    for row in revenue.rows:
        combo = by_id[row.source.combination_stable_key]
        fiscal_season = row.source.fiscal_season
        head = headcount_index[combo["id"], fiscal_season]
        season = SEASONS[fiscal_season % 10 - 1]
        rate = rates[season, combo["track"], combo["product"], combo["grade"], combo["campus"]]
        planned_size = planned_class_sizes[combo["track"], combo["product"]]
        class_count = (
            (head.integer_total_students + planned_size - 1) // planned_size
            if planned_size and head.integer_total_students is not None
            else None
        )
        rows.append(
            {
                "combination_id": combo["id"],
                "season": season,
                **{k: combo[k] for k in DIMS},
                "entry": effective_entry(combo),
                "old_students": head.integer_old_students,
                "new_students": head.integer_new_students,
                "total_students": head.integer_total_students,
                "revenue_yuan": _money(row.quarter_recognized_revenue_yuan)
                if row.quarter_recognized_revenue_yuan is not None
                else None,
                "price_yuan": str(row.source.list_price_yuan),
                "discount_ppm": row.source.detail_discount_rate_ppm,
                "renewal_ppm": rate["renewal_ppm"],
                "share_ppm": head.realized_new_student_share_ppm,
                "class_size_status": class_index[combo["id"], fiscal_season].status,
                "planned_class_size": planned_size,
                "class_count": class_count,
                "manual_locked": (combo["id"], season) in manual_locks,
                "planned_new_applied": (fiscal_season, combo["id"]) in planned,
            }
        )

    def summary(members: list[Json], target: Decimal | None = None) -> Json:
        amount = sum(
            (Decimal(r["revenue_yuan"]) for r in members if r["revenue_yuan"] is not None), Decimal(0)
        )
        complete = all(r["revenue_yuan"] is not None and r["total_students"] is not None for r in members)
        return {
            "revenue_yuan": _money(amount) if complete else None,
            "target_revenue_yuan": _money(target) if target is not None else None,
            "gap_yuan": _money(target - amount) if target is not None and complete else None,
            "attainment": str(amount / target) if target and complete else None,
            **{
                field: sum(r[field] for r in members) if all(r[field] is not None for r in members) else None
                for field in ("total_students", "old_students", "new_students")
            },
            "class_count": sum(r["class_count"] for r in members)
            if all(r["class_count"] is not None for r in members)
            else None,
        }

    target = number(inputs["target_revenue_yuan"]) if inputs["target_revenue_yuan"] is not None else None
    annual = summary(rows, target)
    tracks = [
        {"track": k, **summary([r for r in rows if r["track"] == k], number(v) if v is not None else None)}
        for k, v in inputs["track_targets"].items()
    ]
    issues: list[Json] = []
    for validations in (headcounts.validations, revenue.validations, class_sizes.validations):
        for validation in validations:
            issues.append(
                {
                    "code": validation.code,
                    "message": validation.message,
                    "severity": "WARNING"
                    if validation.code in {"BELOW_MINIMUM_CLASS_SIZE", "ENABLED_COMBINATION_ALL_SEASONS_ZERO"}
                    else "BLOCKING",
                    "season": SEASONS[validation.fiscal_season % 10 - 1]
                    if validation.fiscal_season
                    else None,
                    "combination_id": getattr(validation, "combination_stable_key", None)
                    or getattr(validation, "combination_stable_id", None),
                    "detail": validation.detail,
                }
            )
    closure = None
    if (
        target is not None
        and annual["revenue_yuan"] is not None
        and all(t["target_revenue_yuan"] is not None for t in tracks)
    ):
        closure = calculate_target_closure(
            [
                TargetRevenueInput("department", "DEPARTMENT", None, target, Decimal(annual["revenue_yuan"])),
                *[
                    TargetRevenueInput(
                        t["track"],
                        "TRACK",
                        t["track"],
                        number(t["target_revenue_yuan"]),
                        number(t["revenue_yuan"]),
                    )
                    for t in tracks
                    if t["target_revenue_yuan"] is not None
                ],
            ]
        )
    target_status = "NOT_SET" if target is None else "UNAVAILABLE"
    if target is not None and any(t["target_revenue_yuan"] is None for t in tracks):
        target_status = "TRACK_TARGETS_NOT_SET"
    if closure:
        target_status = closure.closure_status
    return {
        "fiscal_year": fiscal_year,
        "rule_versions": {
            "adapter": RULE_VERSION,
            "headcount": headcounts.rule_version,
            "opening": prepared.rule_version,
            "revenue": "phase1-revenue-v1",
            "class_size": "phase1-class-size-v1",
            "target": "phase1-target-closure-v1",
        },
        "annual": annual,
        "quarters": [{"season": s, **summary([r for r in rows if r["season"] == s])} for s in SEASONS],
        "tracks": tracks,
        "campuses": [
            {"campus": c, **summary([r for r in rows if r["campus"] == c])}
            for c in sorted({r["campus"] for r in rows})
        ],
        "rows": rows,
        "entry_allocations": [
            {"campus": c, "students": v[0], "source_weight": weights[c]}
            for c, v in sorted(campus_allocated.items())
        ],
        "entry_details": [
            {
                "combination_id": r["id"],
                **{key: r[key] for key in DIMS},
                "students": entry_counts.get(r["id"], 0),
                "locked": r.get("entry_locked", False),
            }
            for r in active
            if effective_entry(r)
        ],
        "opening": {
            "spring_students": sum(r["students"] for r in inputs["opening_sources"]),
            "eligible_students": sum(
                r["students"] for r in inputs["opening_sources"] if r["target_id"] in by_id
            ),
            "graduated_students": sum(
                r["students"] for r in inputs["opening_sources"] if r["exclusion"] == "GRADUATED"
            ),
            "subject_ended_students": sum(
                r["students"] for r in inputs["opening_sources"] if r["exclusion"] == "SUBJECT_ENDED"
            ),
            "summer_old_students": sum(r["old_students"] or 0 for r in rows if r["season"] == "暑假"),
            "carryover_yuan": _money(sum((number(r["opening_carryover_yuan"]) for r in active), Decimal(0))),
        },
        "issues": issues,
        "complete": not any(i["severity"] == "BLOCKING" for i in issues),
        "target_status": target_status,
        "publication_available": False,
    }
