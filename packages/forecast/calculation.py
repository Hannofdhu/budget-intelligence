"""Pure, source-bound natural forecasting; never writes or rebalances a budget.

FY27's saved baseline is an immutable historical input, not an invitation to
replay annual-budget commands. Money is Decimal; all allocation is exact Fraction.
The returned dictionaries contain only JSON-compatible values.
"""

from collections import defaultdict
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any

from packages.forecast.rules import FIXED_RULES_VERSION
from packages.headcount.calculation import _largest_remainder, round_half_up

RULE_VERSION = "natural-forecast-fy27-v3"
CONFIRMATION_POLICY_VERSION = "fy27-empty-budget-actual-v1"
SEASONS = ("暑假", "秋季", "寒假", "春季")
DIMS = ("track", "product", "grade", "subject", "campus")
STATES = {"FINALIZED", "OPEN", "ENDED", "FUTURE"}
MONEY_QUANTUM = Decimal("0.000001")
TAX_FACTOR = Decimal("1.06")
GAP_TOLERANCE = Decimal("10000")
FALLBACKS = (
    ("EXACT", DIMS),
    ("NO_CAMPUS", DIMS[:-1]),
    ("NO_SUBJECT", ("track", "product", "grade")),
    ("NO_GRADE", ("track", "product")),
    ("NO_PRODUCT", ("track",)),
    ("DEPARTMENT", ()),
)
SOURCE_VALUES = (
    "enrollment",
    "new_business",
    "new_base",
    "old_base",
    "actual_revenue",
    "actual_carryout",
    "history_new_process_n",
    "history_new_process_d",
    "history_new_final_n",
    "history_new_final_d",
    "history_old_process_n",
    "history_old_process_d",
    "history_old_final_n",
    "history_old_final_d",
    "history_total_process_n",
)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() in {"", "-"}:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _integer(value: object) -> int | None:
    number = _decimal(value)
    return (
        int(number) if number is not None and number >= 0 and number == number.to_integral_value() else None
    )


def _money(value: Decimal | None) -> str | None:
    return str(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)) if value is not None else None


def _ratio(value: Decimal | Fraction | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Fraction):
        value = Decimal(value.numerator) / Decimal(value.denominator)
    return str(value)


def _sum(values: Sequence[Decimal | int | None]) -> Decimal | None:
    if not values or any(value is None for value in values):
        return None
    return sum((Decimal(value) for value in values if value is not None), Decimal(0))


def _signed_actuals_verified(snapshot: dict[str, Any]) -> bool:
    """Allow real financial corrections only with the adapter's full proof.

    A withdrawal-metric checkbox is not financial evidence. This checks the
    stable full extraction, original-grain count coverage, complete-key finance
    reconciliation, and all four department sums emitted by the source adapter.
    The proof is also bound to the retained snapshot's rows and actual totals.
    """
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        return False
    proof = provenance.get("count_proof")
    rows = snapshot.get("rows")
    totals = snapshot.get("actual_totals")
    if (
        not isinstance(proof, dict)
        or not isinstance(rows, list)
        or not isinstance(totals, dict)
        or provenance.get("metadata_stable_after_extraction") is not True
        or not str(snapshot.get("as_of") or "").strip()
        or provenance.get("dtdate") != snapshot.get("as_of")
        or _integer(snapshot.get("fiscal_year")) != 2027
    ):
        return False
    summary_rows = _integer(proof.get("summary_rows"))
    checked_rows = _integer(proof.get("checked_complete_rows"))
    excluded_rows = _integer(proof.get("excluded_zero_incomplete_rows"))
    financial_rows = _integer(proof.get("financial_rows"))
    if (
        summary_rows is None
        or summary_rows < 1
        or checked_rows is None
        or checked_rows < 1
        or excluded_rows is None
        or summary_rows != checked_rows + excluded_rows
        or _integer(proof.get("aggregated_rows")) != len(rows)
        or financial_rows is None
        or financial_rows < 1
        or _integer(proof.get("financial_partition_mismatch_keys")) != 0
        or _integer(proof.get("complement_failure_count")) != 0
        or _integer(proof.get("current_base_failure_count")) != 0
        or not all(isinstance(row, dict) for row in rows)
    ):
        return False
    departments = proof.get("department_financial_reconciliation")
    if not isinstance(departments, dict) or set(departments) != set(SEASONS):
        return False
    for season in SEASONS:
        evidence = departments.get(season)
        actual_total = totals.get(season)
        if not isinstance(evidence, dict) or not isinstance(actual_total, dict):
            return False
        wide, financial = (_decimal(evidence.get(field)) for field in ("wide", "financial"))
        retained = _decimal(actual_total.get("actual_revenue"))
        members = [
            _decimal(row.get("actual_revenue"))
            for row in rows
            if row.get("season") == season and not row.get("financial_only")
        ]
        detail_sum = _sum(members)
        if (
            evidence.get("matched") is not True
            or wide is None
            or financial is None
            or retained is None
            or detail_sum is None
            or abs(wide - financial) > Decimal("0.01")
            or abs(wide - retained) > Decimal("0.01")
            or abs(wide - detail_sum) > Decimal("0.01")
        ):
            return False
    return True


def _dims(row: dict[str, Any]) -> dict[str, str]:
    return {name: str(row.get(name) or "").strip() for name in DIMS}


def _key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_dims(row).values())


def _stable(row: dict[str, Any]) -> str:
    # An unambiguous stable ordering; no dependence on source row order.
    return "|".join(f"{len(value)}:{value}" for value in _key(row))


def _trial_note(
    notes: list[dict[str, Any]], code: str, message: str, row: dict[str, Any], **detail: object
) -> None:
    item: dict[str, Any] = {"code": code, "message": message, "season": row["season"], "dims": _dims(row)}
    if detail:
        item["detail"] = detail
    if item not in notes:
        notes.append(item)


def _fixed_rule_notes(notes: list[dict[str, Any]], states: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe only applicable defaults; frozen facts are not forecast rules."""
    messages = {
        "PEER_PRODUCT_FINANCIAL_TEMPLATE": "财务参数使用同季度同赛道同产品统一模板",
        "PEER_GRADE_WEIGHTED": "未来人数使用同季度同赛道同产品同年级加权比例",
        "TRIAL_SIZE_HELD": "无可用人数比例：沿用上季规模、新生为零；不填造续班率",
        "UNBUDGETED_ADDITIONAL": "预算未覆盖业务后续新增独立设置，不分摊校区预算缺口",
        "BASELINE_ESTIMATE": "当季来源缺失：使用原预算人数预估，不是实际招生或实际收入",
    }
    return [
        {
            **note,
            "message": "当季来源缺失：预算外业务沿用上季规模、新生为零；未预测退班"
            if note["code"] == "TRIAL_SIZE_HELD" and "当季无来源" in note["message"]
            else messages.get(note["code"], note["message"]),
        }
        for note in notes
        if states.get(str(note.get("season") or "")) != "FINALIZED"
        and note["code"] != "ACTUAL_UNAVAILABLE"
        and (
            note["code"] != "UNBUDGETED_ADDITIONAL"
            or states.get(str(note.get("season") or "")) in {"OPEN", "ENDED"}
        )
        and (
            note["code"] not in {"PEER_GRADE_WEIGHTED", "TRIAL_SIZE_HELD"}
            or states.get(str(note.get("season") or "")) == "FUTURE"
            or "当季无来源" in note["message"]
        )
    ]


def _scope_rows(
    scopes: list[dict[str, Any]],
    baseline: dict[tuple[str, tuple[str, ...]], dict[str, Any]],
    minimum: int,
    trial: bool,
    notes: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> dict[tuple[str, tuple[str, ...]], dict[str, Any]]:
    """Create forecast-only scopes without changing any approved budget row."""
    result: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    baseline_keys = {key for _, key in baseline}
    for scope in scopes:
        dims = scope.get("dims")
        kind = scope.get("kind", "UNBUDGETED_BUSINESS")
        if (
            not isinstance(dims, dict)
            or not all(_key(dims))
            or not str(scope.get("reason") or "").strip()
            or kind not in {"UNBUDGETED_BUSINESS", "FINANCIAL_ONLY"}
        ):
            _issue(issues, "FORECAST_SCOPE_INVALID", "预测专用范围必须指定完整维度、类型和原因")
            continue
        key = _key(dims)
        if key in baseline_keys or any(existing_key == key for _, existing_key in result):
            _issue(issues, "FORECAST_SCOPE_DUPLICATED", "预测专用范围不可覆盖预算或重复", row=dims)
            continue
        financial = scope.get("financial", {})
        additional = scope.get("additional", {})
        if not isinstance(financial, dict) or not isinstance(additional, dict):
            _issue(issues, "FORECAST_SCOPE_INVALID", "预测专用参数必须按季度填写", row=dims)
            continue
        for season in SEASONS:
            params = financial.get(season, {})
            if not isinstance(params, dict):
                _issue(issues, "FORECAST_SCOPE_INVALID", "预测专用财务参数无效", season=season, row=dims)
                params = {}
            row = {
                "season": season,
                **_dims(dims),
                "revenue": 0,
                "total": 0,
                "old": 0,
                "new": 0,
                "opening": 0,
                "_forecast_only": True,
                "_scope_kind": kind,
                "_scope_additional": _integer(additional.get(season, 0)),
                "_financial_method": params.get("method", "EXPLICIT_PARAMETERS"),
                **{
                    field: params.get(field)
                    for field in ("price", "discount", "recognition", "renewal", "share")
                },
            }
            if row["_scope_additional"] is None:
                _issue(
                    issues,
                    "FORECAST_SCOPE_ADDITIONAL_INVALID",
                    "预测专用新增人数必须为非负整数",
                    season=season,
                    row=row,
                )
            if kind == "FINANCIAL_ONLY":
                if row["_scope_additional"] != 0:
                    _issue(
                        issues,
                        "FINANCIAL_ONLY_ADDITIONAL_FORBIDDEN",
                        "纯财务范围不能生成新增学生",
                        season=season,
                        row=row,
                    )
                row["_scope_additional"] = 0
                row["_future_method"] = "FINANCIAL_ONLY"
                result[season, key] = row
                continue
            peers = [
                peer
                for (peer_season, _), peer in baseline.items()
                if peer_season == season and all(peer[name] == row[name] for name in ("track", "product"))
            ]
            if trial and any(row[field] is None for field in ("price", "discount", "recognition")):
                templates = {
                    tuple(_decimal(peer.get(field)) for field in ("price", "discount", "recognition"))
                    for peer in peers
                }
                if len(templates) == 1:
                    template = next(iter(templates))
                    if all(value is not None for value in template):
                        for field, value in zip(("price", "discount", "recognition"), template, strict=True):
                            if row[field] is None:
                                row[field] = value
                        row["_financial_method"] = "PEER_PRODUCT_UNIFORM_TEMPLATE"
                        _trial_note(
                            notes,
                            "PEER_PRODUCT_FINANCIAL_TEMPLATE",
                            "财务参数暂用同季度同赛道同产品统一模板",
                            row,
                            price=str(row["price"]),
                            discount=str(row["discount"]),
                            recognition=str(row["recognition"]),
                        )
            row["_future_method"] = "EXPLICIT_PARAMETERS"
            if trial and (row["renewal"] is None or row["share"] is None):
                grade_peers = [peer for peer in peers if peer["grade"] == row["grade"]]
                prior_season = SEASONS[SEASONS.index(season) - 1] if season != SEASONS[0] else None
                renewal_peers = [
                    peer
                    for peer in grade_peers
                    if _integer(peer.get("total")) is not None
                    and _integer(baseline.get((prior_season or "", _key(peer)), {}).get("total")) is not None
                    and _decimal(peer.get("renewal")) is not None
                    and 0 <= (_decimal(peer.get("renewal")) or Decimal(0)) <= 1
                ]
                rn = sum(
                    (
                        Fraction(_integer(baseline[prior_season or "", _key(peer)]["total"]) or 0)
                        * Fraction(_decimal(peer["renewal"]) or Decimal(0))
                        for peer in renewal_peers
                    ),
                    Fraction(0),
                )
                rd = sum(
                    _integer(baseline[prior_season or "", _key(peer)]["total"]) or 0 for peer in renewal_peers
                )
                share_peers = [
                    peer
                    for peer in grade_peers
                    if _integer(peer.get("new")) is not None and _integer(peer.get("total")) is not None
                ]
                sn = sum(_integer(peer["new"]) or 0 for peer in share_peers)
                sd = sum(_integer(peer["total"]) or 0 for peer in share_peers)
                if row["renewal"] is None and rd >= minimum and 0 <= rn <= rd:
                    row["renewal"] = Decimal(rn.numerator) / Decimal(rn.denominator * rd)
                if row["share"] is None and sd >= minimum and 0 <= sn < sd:
                    row["share"] = Decimal(sn) / Decimal(sd)
                if row["renewal"] is not None and row["share"] is not None:
                    row["_future_method"] = "PEER_GRADE_WEIGHTED"
                    _trial_note(
                        notes,
                        "PEER_GRADE_WEIGHTED",
                        "未来人数暂用同季度同赛道同产品同年级合计人次比例",
                        row,
                        renewal_n=_ratio(rn),
                        renewal_d=rd,
                        share_n=sn,
                        share_d=sd,
                    )
                else:
                    unused_renewal, unused_share = row["renewal"], row["share"]
                    row["renewal"] = row["share"] = None
                    row["_hold_size"] = True
                    row["_future_method"] = "TRIAL_SIZE_HELD"
                    _trial_note(
                        notes,
                        "TRIAL_SIZE_HELD",
                        "无同产品同年级可用比例：暂沿用上季人数，不新增新生；不是续班率",
                        row,
                        unapplied_renewal=_ratio(_decimal(unused_renewal)),
                        unapplied_share=_ratio(_decimal(unused_share)),
                    )
            _trial_note(
                notes,
                "UNBUDGETED_ADDITIONAL",
                "预算未覆盖业务后续新增独立设置，不接受校区预算缺口分摊",
                row,
                additional=row["_scope_additional"],
            )
            result[season, key] = row
    return result


def _issue(
    issues: list[dict[str, Any]],
    code: str,
    message: str,
    *,
    season: str | None = None,
    row: dict[str, Any] | None = None,
    severity: str = "BLOCKING",
    **detail: object,
) -> None:
    item: dict[str, Any] = {"code": code, "message": message, "severity": severity}
    if season is not None:
        item["season"] = season
    if row is not None:
        item["dims"] = _dims(row)
    if detail:
        item["detail"] = detail
    issues.append(item)


def _allocate(
    theories: dict[tuple[str, ...], Fraction], target: int, caps: dict[tuple[str, ...], int] | None = None
) -> dict[tuple[str, ...], int] | None:
    """Capped largest-remainder, with stable tie breaking and an exact target."""
    if target < 0 or any(value < 0 for value in theories.values()):
        return None
    if not theories:
        return {} if target == 0 else None
    if caps is not None and target > sum(caps.get(key, 0) for key in theories):
        return None
    floors = {
        key: min(value.numerator // value.denominator, caps[key])
        if caps
        else value.numerator // value.denominator
        for key, value in theories.items()
    }
    remaining = target - sum(floors.values())
    if remaining < 0:
        return None
    ranked = sorted(theories, key=lambda key: (-(theories[key] - floors[key]), key))
    while remaining:
        eligible = [key for key in ranked if caps is None or floors[key] < caps[key]]
        if not eligible:
            return None
        for key in eligible:
            floors[key] += 1
            remaining -= 1
            if remaining == 0:
                break
    return floors


def _adjustments(
    assumptions: dict[str, Any], issues: list[dict[str, Any]]
) -> dict[tuple[str, str, str], int]:
    result: dict[tuple[str, str, str], int] = {}
    for adjustment in assumptions.get("adjustments", []):
        season, campus, kind = (str(adjustment.get(field) or "") for field in ("season", "campus", "kind"))
        value = _integer(adjustment.get("value"))
        if (
            season not in SEASONS
            or not campus
            or kind not in {"additional", "new_withdrawal", "old_withdrawal"}
            or value is None
            or not str(adjustment.get("reason") or "").strip()
        ):
            _issue(issues, "ADJUSTMENT_INVALID", "预测调整必须指定财季、校区、非负整数和原因", season=season)
            continue
        key = season, campus, kind
        if key in result:
            _issue(issues, "ADJUSTMENT_DUPLICATED", "同一财季校区的同类预测调整不可重复", season=season)
            continue
        result[key] = value
    return result


def _source_index(
    snapshot: dict[str, Any],
    assumptions: dict[str, Any],
    baseline_keys: set[tuple[str, ...]],
    issues: list[dict[str, Any]],
    financial_only_keys: set[tuple[str, ...]] | None = None,
) -> tuple[dict[tuple[str, tuple[str, ...]], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    signed_actuals_verified = _signed_actuals_verified(snapshot)
    mapping: dict[tuple[str, ...], tuple[str, ...]] = {}
    for item in assumptions.get("mappings", []):
        source, target = item.get("source", {}), item.get("target", {})
        if (
            not isinstance(source, dict)
            or not isinstance(target, dict)
            or not all(_key(source))
            or not all(_key(target))
            or not str(item.get("reason") or "").strip()
        ):
            _issue(issues, "MAPPING_INVALID", "来源映射须填写完整的来源和预算维度及原因")
            continue
        skey, tkey = _key(source), _key(target)
        if tkey not in baseline_keys:
            _issue(issues, "MAPPING_TARGET_NOT_FOUND", "来源映射的目标预算明细不存在", row=target)
            continue
        if skey in mapping:
            _issue(issues, "MAPPING_DUPLICATED", "同一来源维度不可重复映射", row=source)
            continue
        mapping[skey] = tkey

    grouped: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, tuple[str, ...]]] = set()
    unmapped: list[dict[str, Any]] = []
    history_pool: list[dict[str, Any]] = []
    for original in snapshot.get("rows", []):
        if not isinstance(original, dict):
            _issue(issues, "SOURCE_ROW_INVALID", "预测来源明细结构无效")
            continue
        season = str(original.get("season") or "")
        if season not in SEASONS:
            _issue(issues, "SOURCE_SEASON_INVALID", "预测来源明细财季无效", row=original)
            continue
        # Future-quarter process rows are retained by the snapshot owner, but
        # they are neither actuals nor a substitute for a propagated forecast.
        if assumptions.get("states", {}).get(season) == "FUTURE":
            continue
        skey = _key(original)
        if not all(skey):
            _issue(
                issues,
                "SOURCE_DIMENSIONS_MISSING",
                "预测来源明细缺少完整业务维度",
                season=season,
                row=original,
            )
        if (season, skey) in seen:
            _issue(
                issues, "SOURCE_KEY_DUPLICATED", "预测来源完整维度存在重复明细", season=season, row=original
            )
            continue
        seen.add((season, skey))
        row = {**original, **_dims(original)}
        target = mapping.get(skey, skey)
        financial_only = target in (financial_only_keys or set())
        row["invalid_financial_only_source"] = financial_only and any(
            _integer(row.get(field)) != 0 for field in ("enrollment", "new_business", "new_base", "old_base")
        )
        if row["invalid_financial_only_source"]:
            _issue(
                issues,
                "FINANCIAL_ONLY_SOURCE_HAS_STUDENTS",
                "纯财务范围的来源学生人次必须明确全为零，不能隐藏正数、负数或缺失人次",
                season=season,
                row=row,
            )
        if not financial_only and assumptions.get("states", {}).get(season) in {"OPEN", "ENDED"}:
            new, old, total = (_integer(row.get(field)) for field in ("new_base", "old_base", "enrollment"))
            row["invalid_current_base"] = new is None or old is None or total is None or new + old != total
            if row["invalid_current_base"]:
                _issue(
                    issues,
                    "SOURCE_CLASS_BASE_MISMATCH",
                    "来源完整维度新老基数与招生人次未严格勾稽",
                    season=season,
                    row=row,
                )
        row["invalid_history_fields"] = []
        for field in SOURCE_VALUES:
            if field.startswith("history_") and assumptions.get("states", {}).get(season) == "FINALIZED":
                continue
            value = _decimal(row.get(field))
            if field.startswith("history_") and value is not None and _integer(value) is None:
                row["invalid_history_fields"].append(field)
                _issue(
                    issues,
                    "HISTORY_COUNT_INVALID",
                    "历史退班分子分母必须为非负整数人次",
                    season=season,
                    row=row,
                    field=field,
                    value=str(value),
                )
            if (
                value is not None
                and value < 0
                and not (signed_actuals_verified and field in {"actual_revenue", "actual_carryout"})
            ):
                _issue(
                    issues,
                    "SOURCE_NEGATIVE_VALUE",
                    "预测来源存在负数，未自动截断或抵消",
                    season=season,
                    row=row,
                    field=field,
                    value=str(value),
                )
                if field.startswith("history_"):
                    row["invalid_history_fields"].append(field)
        total_process = _decimal(row.get("history_total_process_n"))
        new_process = _decimal(row.get("history_new_process_n"))
        old_process = _decimal(row.get("history_old_process_n"))
        row["old_process_complement_valid"] = (
            total_process is not None
            and new_process is not None
            and old_process is not None
            and new_process + old_process == total_process
        )
        if target not in baseline_keys:
            if all(skey):
                history_pool.append(row)
            active_fields = ("enrollment", "new_business", "new_base", "old_base", "actual_revenue")
            if all(skey) and all(_decimal(row.get(field)) == 0 for field in active_fields):
                # Explicitly empty current scopes may still be historical samples.
                continue
            unmapped.append(row)
            _issue(
                issues,
                "SOURCE_UNMAPPED",
                "实际业务明细尚未映射到定版预算；已定版收入仍计入部门合计",
                season=season,
                row=row,
            )
            continue
        row.update(zip(DIMS, target, strict=True))
        if not financial_only:
            history_pool.append(row)
        grouped[season, target].append(row)

    result: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for (season, key), members in grouped.items():
        row = {"season": season, **dict(zip(DIMS, key, strict=True))}
        for field in SOURCE_VALUES:
            row[field] = _sum([_decimal(member.get(field)) for member in members])
        row["source_count"] = len(members)
        row["invalid_current_base"] = any(member.get("invalid_current_base") for member in members)
        row["invalid_financial_only_source"] = any(
            member.get("invalid_financial_only_source") for member in members
        )
        row["invalid_history_fields"] = sorted(
            {field for member in members for field in member.get("invalid_history_fields", [])}
        )
        # Old process complement must hold at the original grain, before any
        # deliberate many-to-one budget mapping can hide offsetting differences.
        row["old_process_complement_valid"] = all(
            bool(member.get("old_process_complement_valid")) for member in members
        )
        result[season, key] = row
    return result, unmapped, history_pool


def _carryins_index(
    snapshot: dict[str, Any],
    assumptions: dict[str, Any],
    baseline_keys: set[tuple[str, ...]],
    fiscal_year: int,
    issues: list[dict[str, Any]],
) -> tuple[dict[tuple[str, tuple[str, ...]], Decimal | None], list[dict[str, Any]]]:
    """Actual finance-quarter arrivals from already frozen business origins.

    A complete fiscal-year financial extract may contain older-origin arrivals
    several quarters later. They are not the previous quarter's budget carryout.
    Open-origin financial plans are deliberately excluded from this index.
    """
    mappings: dict[tuple[str, ...], tuple[str, ...]] = {}
    for item in assumptions.get("mappings", []):
        source, target = item.get("source"), item.get("target")
        if (
            isinstance(source, dict)
            and isinstance(target, dict)
            and all(_key(source))
            and _key(target) in baseline_keys
            and str(item.get("reason") or "").strip()
        ):
            mappings.setdefault(_key(source), _key(target))
    grouped: dict[tuple[str, tuple[str, ...]], list[Decimal | None]] = defaultdict(list)
    unmapped: list[dict[str, Any]] = []
    for original in snapshot.get("frozen_carryins", []):
        season = str(original.get("season") or "")
        origin = _integer(original.get("origin_season"))
        if season not in SEASONS or origin is None or origin % 10 not in {1, 2, 3, 4}:
            _issue(issues, "FROZEN_CARRYIN_INVALID", "财务结转缺少有效业务来源季或财务落点")
            continue
        if assumptions.get("states", {}).get(season) == "FINALIZED":
            # Frozen actual quarter revenue already contains these arrivals.
            continue
        origin_year, origin_index = divmod(origin, 10)
        eligible = origin_year < fiscal_year or (
            origin_year == fiscal_year
            and assumptions.get("states", {}).get(SEASONS[origin_index - 1]) == "FINALIZED"
        )
        if not eligible:
            continue
        dims = original.get("dims", original)
        if not isinstance(dims, dict) or not all(_key(dims)):
            _issue(issues, "FROZEN_CARRYIN_DIMENSIONS_MISSING", "实际结转缺少完整业务维度", season=season)
            continue
        revenue = _decimal(original.get("revenue"))
        if revenue is None:
            _issue(
                issues,
                "FROZEN_CARRYIN_AMOUNT_INVALID",
                "已冻结业务来源的结转收入缺失或无效",
                season=season,
                row=dims,
            )
            revenue = None
        target = mappings.get(_key(dims), _key(dims))
        if target not in baseline_keys:
            unmapped.append({"season": season, **_dims(dims), "revenue": revenue, "origin_season": origin})
            _issue(
                issues,
                "FROZEN_CARRYIN_UNMAPPED",
                "已冻结实际结转未映射到预算；收入计入部门且阻断正式预测",
                season=season,
                row=dims,
            )
            continue
        grouped[season, target].append(revenue)
    return {key: _sum(values) for key, values in grouped.items()}, unmapped


def _history(
    row: dict[str, Any], source_rows: list[dict[str, Any]], kind: str, minimum: int
) -> dict[str, Any] | None:
    fields = [f"history_{kind}_{suffix}" for suffix in ("process_n", "process_d", "final_n", "final_d")]
    for level, dims in FALLBACKS:
        members = [
            item
            for item in source_rows
            if item["season"] == row["season"] and all(item[name] == row[name] for name in dims)
        ]
        if any(set(member.get("invalid_history_fields", [])).intersection(fields) for member in members):
            continue
        sums = [_sum([_decimal(item.get(field)) for item in members]) for field in fields]
        if any(value is None for value in sums):
            continue
        pn, pd, fn, fd = (Fraction(value) for value in sums if value is not None)
        if pd < minimum or fd < minimum or pn < 0 or fn < 0 or pn > pd or fn > fd:
            continue
        # A physically inconsistent old process numerator must not be repaired
        # by subtraction or hidden in a higher-level average.
        if kind == "old":
            reconciles = True
            for member in members:
                total = _decimal(member.get("history_total_process_n"))
                new = _decimal(member.get("history_new_process_n"))
                old = _decimal(member.get("history_old_process_n"))
                if (
                    not member.get("old_process_complement_valid")
                    or total is None
                    or new is None
                    or old is None
                    or new + old != total
                ):
                    reconciles = False
                    break
            if not reconciles:
                continue
        process, final = pn / pd, fn / fd
        return {
            "level": level,
            "process_n": _ratio(pn),
            "process_d": _ratio(pd),
            "final_n": _ratio(fn),
            "final_d": _ratio(fd),
            "process_rate": _ratio(process),
            "final_rate": _ratio(final),
            "remaining_rate": _ratio(max(Fraction(0), final - process)),
            "remaining_exact": max(Fraction(0), final - process),
        }
    return None


def _open_counts(
    season: str,
    state: str,
    baseline_rows: list[dict[str, Any]],
    source: dict[tuple[str, tuple[str, ...]], dict[str, Any]],
    all_source_rows: list[dict[str, Any]],
    adjustments: dict[tuple[str, str, str], int],
    minimum: int,
    verified: bool,
    issues: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    campuses: list[dict[str, Any]],
) -> dict[tuple[str, ...], dict[str, int | None]]:
    result: dict[tuple[str, ...], dict[str, int | None]] = {
        _key(row): {
            "new": None,
            "old": None,
            "total": None,
            "additional": None,
            "new_withdrawal": None,
            "old_withdrawal": None,
        }
        for row in baseline_rows
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in baseline_rows:
        grouped[row["campus"]].append(row)
    if not verified:
        _issue(
            issues,
            "WITHDRAWAL_METRICS_UNVERIFIED",
            "退班字段口径尚未核验，不能生成开放季度终值",
            season=season,
        )
    for campus, budget_rows in sorted(grouped.items()):
        current = {_key(row): source.get((season, _key(row))) for row in budget_rows}
        covered_rows = [row for row in budget_rows if not row.get("_forecast_only")]
        budget_new = _sum([_decimal(row.get("new")) for row in covered_rows]) if covered_rows else Decimal(0)
        business_new = (
            _sum(
                [
                    _decimal((current[_key(row)] or {}).get("new_business")) if current[_key(row)] else None
                    for row in covered_rows
                ]
            )
            if covered_rows
            else Decimal(0)
        )
        default_additional = None
        if budget_new is not None and budget_new >= 0 and business_new is not None and business_new >= 0:
            default_additional = (
                0 if state == "ENDED" else round_half_up(Fraction(max(Decimal(0), budget_new - business_new)))
            )
        additional = adjustments.get((season, campus, "additional"), default_additional)
        scope_additions = {
            _key(row): _integer(row.get("_scope_additional"))
            for row in budget_rows
            if row.get("_forecast_only")
        }
        scope_total = sum(value for value in scope_additions.values() if value is not None)
        campus_view: dict[str, Any] = {
            "season": season,
            "campus": campus,
            "budget_new": _ratio(budget_new),
            "current_business_new": _ratio(business_new),
            "additional_default": default_additional + scope_total
            if default_additional is not None
            else None,
            "additional": additional + scope_total if additional is not None else None,
            "budgeted_additional": additional,
            "unbudgeted_additional": scope_total,
            "new_withdrawal": None,
            "old_withdrawal": None,
            "new_base": None,
            "old_base": None,
        }
        campuses.append(campus_view)
        missing = [row for row in budget_rows if current[_key(row)] is None]
        if missing:
            for row in missing:
                _issue(
                    issues,
                    "CURRENT_SOURCE_MISSING",
                    "预算明细缺少当季来源；未按零处理",
                    season=season,
                    row=row,
                )
            continue
        invalid = False
        for row in budget_rows:
            actual = current[_key(row)]
            assert actual is not None
            new, old, total = (
                _integer(actual.get(field)) for field in ("new_base", "old_base", "enrollment")
            )
            if (
                actual.get("invalid_current_base")
                or new is None
                or old is None
                or total is None
                or new + old != total
            ):
                _issue(
                    issues,
                    "CLASS_BASE_MISMATCH",
                    "新老退班分类基数须为非负整数且逐明细等于招生人次",
                    season=season,
                    row=row,
                )
                invalid = True
        if invalid:
            continue
        caps_by_kind: dict[str, dict[tuple[str, ...], int]] = {"new": {}, "old": {}}
        for kind in ("new", "old"):
            for key, actual in current.items():
                assert actual is not None
                base = _integer(actual.get(f"{kind}_base"))
                assert base is not None  # Original-grain validation above.
                caps_by_kind[kind][key] = base
        campus_view["new_base"] = sum(caps_by_kind["new"].values())
        campus_view["old_base"] = sum(caps_by_kind["old"].values())
        if additional is None or any(value is None for value in scope_additions.values()):
            _issue(
                issues,
                "BUSINESS_NEW_MISSING",
                "缺少完整业务新生预算或累计值，不能估计后续新增",
                season=season,
                row=budget_rows[0],
            )
            continue
        gaps: dict[tuple[str, ...], Fraction] = {}
        for row in covered_rows:
            actual = current[_key(row)]
            assert actual is not None
            planned, actual_new = _decimal(row.get("new")), _decimal(actual.get("new_business"))
            if planned is None or planned < 0 or actual_new is None or actual_new < 0:
                invalid = True
            else:
                gaps[_key(row)] = Fraction(max(Decimal(0), planned - actual_new))
        if invalid:
            _issue(
                issues,
                "BUSINESS_NEW_INVALID",
                "业务新生预算和累计值缺失或为负数，不能分摊新增",
                season=season,
                row=budget_rows[0],
            )
            continue
        gap_sum = sum(gaps.values(), Fraction(0))
        if additional > 0 and gap_sum == 0:
            _issue(
                issues,
                "ADDITIONAL_ALLOCATION_INFEASIBLE",
                "后续新增没有可用正向预算缺口，需人工指定明细",
                season=season,
                row=budget_rows[0],
            )
            continue
        additions = _allocate(
            {key: value * additional / gap_sum if gap_sum else Fraction(0) for key, value in gaps.items()},
            additional,
        )
        if additions is None or (additional > 0 and gap_sum == 0):
            _issue(
                issues,
                "ADDITIONAL_ALLOCATION_INFEASIBLE",
                "后续新增没有可用正向预算缺口，需人工指定明细",
                season=season,
                row=budget_rows[0],
            )
            continue
        additions.update({key: value for key, value in scope_additions.items() if value is not None})
        for key, value in additions.items():
            result[key]["additional"] = value
        if not verified:
            continue
        withdrawals: dict[str, dict[tuple[str, ...], int]] = {}
        for kind in ("new", "old"):
            manual = adjustments.get((season, campus, f"{kind}_withdrawal"))
            caps = caps_by_kind[kind]
            histories: dict[tuple[str, ...], dict[str, Any] | None] = {}
            for row in budget_rows:
                key = _key(row)
                actual = current[key]
                assert actual is not None
                histories[key] = _history(actual, all_source_rows, kind, minimum) if caps[key] else None
            if manual is not None:
                base_sum = sum(caps.values())
                theory = {
                    key: Fraction(value * manual, base_sum) if base_sum else Fraction(0)
                    for key, value in caps.items()
                }
                allocated = _allocate(theory, manual, caps)
                mode = "MANUAL_CURRENT_BASE_ALLOCATION"
                if allocated is None:
                    _issue(
                        issues,
                        "WITHDRAWAL_ADJUSTMENT_INFEASIBLE",
                        "人工预计退班人数超过当前对应在班基数",
                        season=season,
                        row=budget_rows[0],
                        student_type=kind,
                    )
                    continue
            else:
                unavailable = [key for key in caps if caps[key] > 0 and histories[key] is None]
                if unavailable:
                    _issue(
                        issues,
                        "WITHDRAWAL_HISTORY_MISSING",
                        "逐级回退后仍无可用历史，请填写校区分类退班合计",
                        season=season,
                        row=budget_rows[0],
                        student_type=kind,
                        unavailable_count=len(unavailable),
                    )
                    for key in unavailable:
                        evidence.append(
                            {
                                "season": season,
                                **dict(zip(DIMS, key, strict=True)),
                                "student_type": kind,
                                "level": "NO_HISTORY",
                                "minimum_sample": minimum,
                            }
                        )
                    continue
                theory = {}
                for key in caps:
                    history = histories[key]
                    theory[key] = (
                        min(Fraction(caps[key]), Fraction(caps[key]) * history["remaining_exact"])
                        if history is not None
                        else Fraction(0)
                    )
                target = round_half_up(sum(theory.values(), Fraction(0)))
                allocated = _allocate(theory, target, caps)
                mode = "HISTORICAL_DEVELOPMENT"
                assert allocated is not None
            withdrawals[kind] = allocated
            campus_view[f"{kind}_withdrawal"] = sum(allocated.values())
            for key in sorted(caps):
                history = histories[key]
                trace = {name: value for name, value in (history or {}).items() if name != "remaining_exact"}
                evidence.append(
                    {
                        "season": season,
                        **dict(zip(DIMS, key, strict=True)),
                        "student_type": kind,
                        "mode": mode,
                        "minimum_sample": minimum,
                        "current_base": caps[key],
                        "level": trace.get("level", "ZERO_BASE" if caps[key] == 0 else "NO_HISTORY"),
                        **trace,
                        "unrounded": _ratio(theory[key]),
                        "rounded": allocated[key],
                        "group_target": sum(allocated.values()),
                        "remainder": _ratio(theory[key] - theory[key].numerator // theory[key].denominator),
                    }
                )
        if set(withdrawals) != {"new", "old"}:
            continue
        for key in current:
            new = caps_by_kind["new"][key] + additions[key] - withdrawals["new"][key]
            old = caps_by_kind["old"][key] - withdrawals["old"][key]
            result[key].update(
                new=new,
                old=old,
                total=new + old,
                new_withdrawal=withdrawals["new"][key],
                old_withdrawal=withdrawals["old"][key],
            )
    return result


def _future_counts(
    season: str,
    rows: list[dict[str, Any]],
    previous: dict[tuple[str, ...], dict[str, Any]],
    issues: list[dict[str, Any]],
) -> dict[tuple[str, ...], dict[str, int | None]]:
    theories: dict[tuple[str, ...], tuple[Fraction | None, Fraction | None]] = {}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _key(row)
        groups[row["track"], row["campus"]].append(row)
        prior = previous.get(key, {}).get("forecast_total")
        renewal, share = _decimal(row.get("renewal")), _decimal(row.get("share"))
        old = new = None
        explicit_zero = all(_decimal(row.get(field)) == 0 for field in ("old", "new", "total"))
        if row.get("_scope_kind") == "FINANCIAL_ONLY":
            old = new = Fraction(0)
        elif row.get("_hold_size") and _integer(prior) is not None:
            old, new = Fraction(_integer(prior) or 0), Fraction(0)
        elif prior is None:
            _issue(
                issues,
                "UPSTREAM_HEADCOUNT_UNAVAILABLE",
                "前一季度终值不可用，不能推演未来季度",
                season=season,
                row=row,
            )
        elif prior == 0 and row.get("renewal") == "-" and explicit_zero and row.get("share") == "-":
            # Saved approved '-' plus explicit all-zero scope is genuinely not
            # applicable, not an absent parameter silently manufactured as 0%.
            old = new = Fraction(0)
        elif renewal is None or not 0 <= renewal <= 1:
            _issue(
                issues,
                "RENEWAL_RATE_INVALID",
                "未来季度缺少有效继承续班率，未用零替代",
                season=season,
                row=row,
            )
        elif share is None and row.get("share") == "-" and explicit_zero and renewal == 0:
            old = new = Fraction(0)
        elif share is None or not 0 <= share < 1:
            _issue(
                issues,
                "NEW_STUDENT_SHARE_INVALID",
                "未来季度缺少可反推的继承新生占比，未用零替代",
                season=season,
                row=row,
            )
        else:
            old = Fraction(prior) * Fraction(renewal)
            new = old * Fraction(share) / (1 - Fraction(share))
        theories[key] = old, new
    result: dict[tuple[str, ...], dict[str, int | None]] = {
        key: {"new": None, "old": None, "total": None} for key in theories
    }
    for group in groups.values():
        by_id = {_stable(row): _key(row) for row in group}
        rounded = [
            _largest_remainder(
                {sid: theories[key][index] for sid, key in by_id.items()},
                fixed_ids=set(),
                student_type="OLD" if index == 0 else "NEW",
            )
            for index in range(2)
        ]
        for sid, key in by_id.items():
            old_count, new_count = rounded[0][sid][0], rounded[1][sid][0]
            result[key].update(
                old=old_count,
                new=new_count,
                total=old_count + new_count if old_count is not None and new_count is not None else None,
            )
    return result


def _forecast_revenue(
    row: dict[str, Any],
    total: int | None,
    incoming: Decimal | None,
    issues: list[dict[str, Any]],
    *,
    allow_signed_incoming: bool = False,
) -> tuple[Decimal | None, Decimal | None]:
    if total is None:
        return None, None
    price, discount, recognition = (
        _decimal(row.get(field)) for field in ("price", "discount", "recognition")
    )
    if (
        price is None
        or price < 0
        or discount is None
        or not 0 <= discount <= 1
        or recognition is None
        or not 0 <= recognition <= 1
    ):
        _issue(
            issues,
            "REVENUE_PARAMETER_INVALID",
            "继承的价格、折扣或当季确认比例不可用",
            season=row["season"],
            row=row,
        )
        return None, None
    tuition = (price * discount).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    business = (Decimal(total) * tuition).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    net = (business / TAX_FACTOR).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    current = (net * recognition).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    carryout = (net * (1 - recognition)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if incoming is None:
        _issue(
            issues,
            "ACTUAL_CARRYOUT_REQUIRED",
            "前季实际转出结转缺失，不能使用预算结转替代",
            season=row["season"],
            row=row,
        )
        return None, carryout
    if incoming < 0 and not allow_signed_incoming:
        _issue(issues, "CARRYOVER_NEGATIVE", "收入计算引用的结转为负数", season=row["season"], row=row)
        return None, carryout
    return current + incoming, carryout


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    budget = _sum([_decimal(row.get("budget_revenue")) for row in rows])
    forecast = _sum([_decimal(row.get("forecast_revenue")) for row in rows])
    budget_total = _sum([_decimal(row.get("budget_total")) for row in rows])
    forecast_total = _sum([_decimal(row.get("forecast_total")) for row in rows])
    gap = budget - forecast if budget is not None and forecast is not None else None
    return {
        "budget_revenue": _money(budget),
        "forecast_revenue": _money(forecast),
        "gap": _money(gap),
        "achievement": _ratio(forecast / budget)
        if forecast is not None and budget is not None and budget > 0
        else None,
        "budget_total": _ratio(budget_total),
        "forecast_total": int(forecast_total) if forecast_total is not None else None,
        "status": "BLOCKED"
        if gap is None
        else "NO_SIGNIFICANT_GAP"
        if abs(gap) <= GAP_TOLERANCE
        else "GAP"
        if gap > 0
        else "ABOVE_BUDGET",
    }


def calculate_forecast(
    baseline: dict[str, Any], snapshot: dict[str, Any], assumptions: dict[str, Any]
) -> dict[str, Any]:
    """Compute independent natural forecast with exact data-quality boundaries."""
    with localcontext() as context:
        context.prec = 38
        return _calculate(baseline, snapshot, assumptions)


def _review_empty_budget_actuals(
    indexed: dict[tuple[str, tuple[str, ...]], dict[str, Any]],
    snapshot: dict[str, Any],
    assumptions: dict[str, Any],
    output: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Exempt absent, inactive budget slots from confirmation, never assert actual zero.

    A complete source plus a matching department sum alone cannot prove that a
    particular missing fact is zero. This narrowly reviews original *contribution*
    fields as well, and retains every absent fact and issue as informational
    evidence. Unit prices/rates are not contributions and need not be zero.
    """
    if (
        assumptions.get("trial_mode") is True
        or assumptions.get("fixed_rules_version") != FIXED_RULES_VERSION
        or not _signed_actuals_verified(snapshot)
        or any(
            issue["severity"] == "BLOCKING" and issue["code"] != "FINALIZED_ACTUAL_MISSING"
            for issue in issues
        )
    ):
        return []
    frozen = snapshot.get("frozen_carryins")
    if not isinstance(frozen, list):
        return []
    mappings = {_key(item["source"]): _key(item["target"]) for item in assumptions.get("mappings", [])}
    carry_keys: set[tuple[str, ...]] = set()
    for carry in frozen:
        if not isinstance(carry, dict):
            return []
        dims = carry.get("dims", carry)
        if (
            not isinstance(dims, dict)
            or not all(_key(dims))
            or carry.get("season") not in SEASONS
            or _decimal(carry.get("revenue")) is None
        ):
            return []
        # Any retained carry input for this business, including future arrivals,
        # keeps it outside the empty-slot exemption. No netting positive/negative.
        carry_keys.add(_key(dims))
        carry_keys.add(mappings.get(_key(dims), _key(dims)))
    fields = ("old", "new", "total", "opening", "list_revenue", "business", "current", "revenue", "carryout")
    checks: list[dict[str, Any]] = []
    for season in SEASONS:
        missing = [
            issue
            for issue in issues
            if issue["code"] == "FINALIZED_ACTUAL_MISSING" and issue.get("season") == season
        ]
        if not missing or assumptions.get("states", {}).get(season) != "FINALIZED":
            continue
        empty = True
        for issue in missing:
            dims = issue.get("dims", {})
            key = _key(dims)
            row = indexed.get((season, key))
            prior = indexed.get((SEASONS[SEASONS.index(season) - 1], key)) if season != SEASONS[0] else None
            if (
                row is None
                or row.get("_forecast_only")
                or row.get("_scope_kind", "BUDGETED_BUSINESS") != "BUDGETED_BUSINESS"
                or key in carry_keys
                or not all(_decimal(row.get(field)) == 0 for field in fields)
                or (prior is not None and _decimal(prior.get("carryout")) != 0)
                or any(source.get("season") == season and _key(source) == key for source in snapshot["rows"])
            ):
                empty = False
                break
        if not empty:
            continue
        totals = snapshot.get("actual_totals", {}).get(season, {})
        actual_revenue = _decimal(totals.get("actual_revenue"))
        actual_count = _integer(totals.get("enrollment"))
        source_rows = [
            row for row in snapshot["rows"] if row.get("season") == season and not row.get("financial_only")
        ]
        source_revenue = _sum([_decimal(row.get("actual_revenue")) for row in source_rows])
        source_count = _sum([_integer(row.get("enrollment")) for row in source_rows])
        known = [row for row in output if row["season"] == season and row["method"] != "ACTUAL_UNAVAILABLE"]
        known_revenue = _sum([_decimal(row.get("forecast_revenue")) for row in known])
        known_count = _sum([_integer(row.get("forecast_total")) for row in known])
        if (
            actual_revenue is None
            or actual_count is None
            or source_revenue != actual_revenue
            or source_count != actual_count
            # Compare exact Decimal at the engine's retained money precision;
            # source fractional-cent artefacts are not rounded via binary float.
            or known_revenue != _decimal(_money(actual_revenue))
            or known_count != actual_count
        ):
            continue
        checks.append(
            {
                "season": season,
                "as_of": snapshot["as_of"],
                "source_complete": True,
                "budget_components_zero": True,
                "no_frozen_carryins": True,
                "revenue_matched": True,
                "enrollment_matched": True,
                "excluded_empty_rows": len(missing),
                "known_revenue": _money(known_revenue),
                "actual_revenue": _money(actual_revenue),
                "known_enrollment": actual_count,
                "actual_enrollment": actual_count,
            }
        )
        for issue in missing:
            issue["severity"] = "INFO"
            issue["detail"] = {
                **issue.get("detail", {}),
                "confirmation_exemption": CONFIRMATION_POLICY_VERSION,
                "classification": "EMPTY_BUDGET_SLOT_NO_SOURCE_BUSINESS",
            }
    return checks


def _calculate(
    baseline: dict[str, Any], snapshot: dict[str, Any], assumptions: dict[str, Any]
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for source_issue in snapshot.get("issues", []):
        issues.append(
            {**source_issue, "severity": "BLOCKING"}
            if isinstance(source_issue, dict)
            else {"code": "SOURCE_ISSUE", "message": str(source_issue), "severity": "BLOCKING"}
        )
    states = assumptions.get("states", {})
    trial = assumptions.get("trial_mode") is True
    fixed_version = assumptions.get("fixed_rules_version")
    fixed = fixed_version == FIXED_RULES_VERSION
    defaults = trial or fixed
    if fixed_version is not None and fixed_version != FIXED_RULES_VERSION:
        _issue(issues, "FIXED_RULES_VERSION_INVALID", "未知固定预测规则版本")
    if fixed and trial:
        _issue(issues, "FIXED_RULES_TRIAL_CONFLICT", "固定预测规则不能同时声明为试用模式")
    trial_notes: list[dict[str, Any]] = []
    minimum = _integer(assumptions.get("minimum_sample", 30))
    if minimum is None or minimum < 1:
        _issue(issues, "MINIMUM_SAMPLE_INVALID", "历史样本门槛须为正整数")
        minimum = 30
    indexed: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for original in baseline.get("rows", []):
        row = {**original, **_dims(original)}
        season = str(row.get("season") or "")
        if season not in SEASONS or not all(_key(row)):
            _issue(
                issues, "BASELINE_DIMENSIONS_INVALID", "定版预算明细财季或维度不完整", season=season, row=row
            )
            continue
        budget_slot = season, _key(row)
        if budget_slot in indexed:
            _issue(issues, "BASELINE_KEY_DUPLICATED", "定版预算同财季同完整维度重复", season=season, row=row)
            continue
        indexed[budget_slot] = row
        for field in ("old", "new", "total", "price", "discount", "recognition", "opening"):
            value = _decimal(row.get(field))
            if value is not None and value < 0:
                _issue(
                    issues,
                    "BASELINE_NEGATIVE_VALUE",
                    "定版预算保留了负数输入；预测不自动修正",
                    season=season,
                    row=row,
                    severity="WARNING",
                    field=field,
                    value=str(value),
                )
    baseline_keys = {key for _, key in indexed}
    if not indexed:
        _issue(issues, "BASELINE_EMPTY", "缺少已定版的年度预算基线")
    for key in sorted(baseline_keys):
        if any((season, key) not in indexed for season in SEASONS):
            _issue(
                issues,
                "BASELINE_SEASONS_INCOMPLETE",
                "定版预算明细未完整覆盖四季",
                row=dict(zip(DIMS, key, strict=True)),
            )
    scopes = _scope_rows(
        assumptions.get("forecast_scopes", []), indexed, minimum, defaults, trial_notes, issues
    )
    indexed.update(scopes)
    all_keys = {key for _, key in indexed}
    financial_only_keys = {key for (_, key), row in scopes.items() if row["_scope_kind"] == "FINANCIAL_ONLY"}
    source, unmapped, history_pool = _source_index(
        snapshot, assumptions, all_keys, issues, financial_only_keys
    )
    fiscal_year = _integer(baseline.get("fiscal_year")) or 2027
    carries, unmapped_carries = _carryins_index(snapshot, assumptions, all_keys, fiscal_year, issues)
    has_financial_carryins = isinstance(snapshot.get("frozen_carryins"), list)
    signed_actuals_verified = _signed_actuals_verified(snapshot)
    adjustments = _adjustments(assumptions, issues)
    evidence: list[dict[str, Any]] = []
    campuses: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    previous: dict[tuple[str, ...], dict[str, Any]] = {}
    for season in SEASONS:
        state = states.get(season)
        season_rows = [row for (name, _), row in sorted(indexed.items()) if name == season]
        if state not in STATES:
            _issue(issues, "QUARTER_STATE_REQUIRED", "每个季度必须明确预测状态", season=season)
        counts: dict[tuple[str, ...], dict[str, int | None]] = {}
        if state in {"OPEN", "ENDED"}:
            source_backed_rows = [
                row
                for row in season_rows
                if row.get("_scope_kind") != "FINANCIAL_ONLY"
                and (not defaults or (season, _key(row)) in source)
            ]
            counts = _open_counts(
                season,
                state,
                source_backed_rows,
                source,
                history_pool,
                adjustments,
                minimum,
                snapshot.get("metric_verified") is True,
                issues,
                evidence,
                campuses,
            )
            if defaults:
                for row in season_rows:
                    if row.get("_scope_kind") == "FINANCIAL_ONLY" or (season, _key(row)) in source:
                        continue
                    if row.get("_forecast_only"):
                        prior_total = _integer(previous.get(_key(row), {}).get("forecast_total"))
                        counts[_key(row)] = {
                            "total": prior_total,
                            "new": 0 if prior_total is not None else None,
                            "old": prior_total,
                            "additional": 0,
                            "new_withdrawal": None,
                            "old_withdrawal": None,
                        }
                        _trial_note(
                            trial_notes,
                            "TRIAL_SIZE_HELD",
                            "当季无来源：预算外业务规模暂沿用上季，不新增新生；未预测退班",
                            row,
                        )
                    else:
                        counts[_key(row)] = {
                            "total": _integer(row.get("total")),
                            "new": _integer(row.get("new")),
                            "old": _integer(row.get("old")),
                            "additional": None,
                            "new_withdrawal": None,
                            "old_withdrawal": None,
                        }
                        _trial_note(
                            trial_notes,
                            "BASELINE_ESTIMATE",
                            "当季来源缺失：暂展示原预算估值，不是实际招生或实际收入",
                            row,
                        )
        elif state == "FUTURE":
            counts = _future_counts(season, season_rows, previous, issues)
        current: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in season_rows:
            key = _key(row)
            actual = source.get((season, key))
            item: dict[str, Any] = {
                "season": season,
                **_dims(row),
                "budget_revenue": _money(_decimal(row.get("revenue"))),
                "budget_total": _ratio(_decimal(row.get("total"))),
                "budget_new": _ratio(_decimal(row.get("new"))),
                "budget_old": _ratio(_decimal(row.get("old"))),
                "budget_covered": not row.get("_forecast_only", False),
                "scope_kind": row.get("_scope_kind", "BUDGETED_BUSINESS"),
                "counts_not_applicable": row.get("_scope_kind") == "FINANCIAL_ONLY",
                "data_status": "ACTUAL" if state == "FINALIZED" else "FORECAST",
                "future_method": row.get("_future_method", "BASELINE_PARAMETERS")
                if state == "FUTURE"
                else None,
                "financial_method": row.get("_financial_method", "BASELINE_PARAMETERS"),
                "forecast_revenue": None,
                "forecast_total": None,
                "forecast_new": None,
                "forecast_old": None,
                "forecast_carryout": None,
                "additional": None,
                "new_withdrawal": None,
                "old_withdrawal": None,
                "renewal": _ratio(_decimal(row.get("renewal"))),
                "renewal_applicability": "NOT_APPLICABLE"
                if row.get("renewal") == "-"
                and previous.get(key, {}).get("forecast_total") == 0
                and all(_decimal(row.get(field)) == 0 for field in ("old", "new", "total"))
                else "APPLICABLE",
                "method": "FROZEN_ACTUAL"
                if state == "FINALIZED"
                else "TERMINAL_FORECAST"
                if state in {"OPEN", "ENDED"}
                else "PARENT_PARAMETER_PROJECTION"
                if state == "FUTURE"
                else "BLOCKED",
            }
            if state == "FINALIZED":
                if actual is None:
                    _issue(
                        issues,
                        "FINALIZED_ACTUAL_MISSING",
                        "已定版季度缺少实际，未用预算值或零替代",
                        season=season,
                        row=row,
                    )
                    if defaults:
                        item.update(
                            data_status="ACTUAL_UNAVAILABLE",
                            method="ACTUAL_UNAVAILABLE",
                            included_in_closed_actual_total=False,
                        )
                        _trial_note(
                            trial_notes,
                            "ACTUAL_UNAVAILABLE",
                            "已定版该明细实际未知；未填零或预算值，部门使用完整冻结实际合计",
                            row,
                        )
                else:
                    revenue, carryout = (
                        _decimal(actual.get("actual_revenue")),
                        _decimal(actual.get("actual_carryout")),
                    )
                    item["frozen_actual_revenue"] = _money(revenue)
                    item["frozen_actual_carryout"] = _money(carryout)
                    financial_only = row.get("_scope_kind") == "FINANCIAL_ONLY"
                    total = (
                        None
                        if actual.get("invalid_financial_only_source")
                        else 0
                        if financial_only
                        else _integer(actual.get("enrollment"))
                    )
                    if revenue is None or (revenue < 0 and not signed_actuals_verified) or total is None:
                        _issue(
                            issues,
                            "FINALIZED_ACTUAL_INVALID",
                            "已定版实际收入或人次缺失、为负或无效",
                            season=season,
                            row=row,
                        )
                    else:
                        item.update(
                            forecast_revenue=_money(revenue),
                            forecast_total=total,
                            forecast_new=0 if financial_only else _integer(actual.get("new_base")),
                            forecast_old=0 if financial_only else _integer(actual.get("old_base")),
                        )
                    if carryout is not None and carryout < 0 and not signed_actuals_verified:
                        if not has_financial_carryins:
                            _issue(
                                issues,
                                "ACTUAL_CARRYOUT_INVALID",
                                "已定版负转出结转尚未经完整财务来源勾稽",
                                season=season,
                                row=row,
                            )
                    else:
                        item["forecast_carryout"] = _money(carryout)
                    if (
                        (season, row["campus"], "additional") in adjustments
                        or (season, row["campus"], "new_withdrawal") in adjustments
                        or (season, row["campus"], "old_withdrawal") in adjustments
                    ):
                        _issue(
                            issues,
                            "FINALIZED_ADJUSTMENT_FORBIDDEN",
                            "已定版实际禁止通过预测调整覆盖",
                            season=season,
                            row=row,
                        )
            elif state in {"OPEN", "ENDED", "FUTURE"}:
                values = counts.get(key, {})
                item.update(
                    forecast_total=values.get("total"),
                    forecast_new=values.get("new"),
                    forecast_old=values.get("old"),
                    additional=values.get("additional"),
                    new_withdrawal=values.get("new_withdrawal"),
                    old_withdrawal=values.get("old_withdrawal"),
                )
                if defaults and state in {"OPEN", "ENDED"} and actual is None:
                    item["data_status"] = (
                        "TRIAL_SIZE_HELD" if row.get("_forecast_only") else "BASELINE_ESTIMATE"
                    )
                if state == "FUTURE" and row.get("_hold_size"):
                    item["data_status"] = "TRIAL_SIZE_HELD"
                preceding_state = (
                    states.get(SEASONS[SEASONS.index(season) - 1]) if season != SEASONS[0] else None
                )
                if has_financial_carryins:
                    frozen_incoming = carries.get((season, key), Decimal(0))
                    if season == SEASONS[0] or preceding_state == "FINALIZED":
                        incoming = frozen_incoming
                    else:
                        prior = _decimal(previous.get(key, {}).get("forecast_carryout"))
                        incoming = (
                            prior + frozen_incoming
                            if prior is not None and frozen_incoming is not None
                            else None
                        )
                else:
                    incoming = (
                        _decimal(row.get("opening"))
                        if season == SEASONS[0]
                        else _decimal(previous.get(key, {}).get("forecast_carryout"))
                    )
                if row.get("_scope_kind") == "FINANCIAL_ONLY":
                    revenue, carryout = incoming, Decimal(0)
                    item.update(forecast_total=0, forecast_new=0, forecast_old=0, counts_not_applicable=True)
                    if actual is not None and actual.get("invalid_financial_only_source"):
                        item.update(forecast_total=None, forecast_new=None, forecast_old=None)
                else:
                    revenue, carryout = _forecast_revenue(
                        row,
                        values.get("total"),
                        incoming,
                        issues,
                        allow_signed_incoming=has_financial_carryins
                        or (signed_actuals_verified and preceding_state == "FINALIZED"),
                    )
                item.update(
                    forecast_revenue=_money(revenue),
                    forecast_carryout=_money(carryout),
                    frozen_carryin=_money(carries.get((season, key), Decimal(0)))
                    if has_financial_carryins
                    else None,
                )
            output.append(item)
            current[key] = item
        # Unmapped frozen actuals are included exactly once in department totals,
        # not dropped, forced into a budget row, or divided by 1.06 a second time.
        for actual in unmapped:
            if actual["season"] != season:
                continue
            revenue = _decimal(actual.get("actual_revenue")) if state == "FINALIZED" else None
            total = _integer(actual.get("enrollment")) if state == "FINALIZED" else None
            frozen_revenue = revenue
            if revenue is not None and revenue < 0 and not signed_actuals_verified:
                revenue = None
            output.append(
                {
                    "season": season,
                    **_dims(actual),
                    "budget_revenue": "0.000000",
                    "budget_total": "0",
                    "budget_new": "0",
                    "budget_old": "0",
                    "forecast_revenue": _money(revenue),
                    "frozen_actual_revenue": _money(frozen_revenue),
                    "forecast_total": total,
                    "forecast_new": _integer(actual.get("new_base")) if state == "FINALIZED" else None,
                    "forecast_old": _integer(actual.get("old_base")) if state == "FINALIZED" else None,
                    "forecast_carryout": None,
                    "method": "UNMAPPED_ACTUAL",
                    "unmapped": True,
                    "additional": None,
                    "new_withdrawal": None,
                    "old_withdrawal": None,
                    "renewal": None,
                }
            )
        for actual in unmapped_carries:
            if actual["season"] != season:
                continue
            output.append(
                {
                    "season": season,
                    **_dims(actual),
                    "budget_revenue": "0.000000",
                    "budget_total": "0",
                    "budget_new": "0",
                    "budget_old": "0",
                    "forecast_revenue": _money(actual["revenue"]),
                    "forecast_total": 0,
                    "forecast_new": 0,
                    "forecast_old": 0,
                    "forecast_carryout": None,
                    "method": "UNMAPPED_FROZEN_CARRYIN",
                    "unmapped": True,
                    "counts_not_applicable": True,
                    "origin_season": actual["origin_season"],
                    "additional": None,
                    "new_withdrawal": None,
                    "old_withdrawal": None,
                    "renewal": None,
                }
            )
        previous = current
    quarters: list[dict[str, Any]] = []
    for season in SEASONS:
        quarter_rows = [row for row in output if row["season"] == season]
        summary = _summary(quarter_rows)
        actual_totals = snapshot.get("actual_totals", {}).get(season)
        if states.get(season) == "FINALIZED" and isinstance(actual_totals, dict):
            actual_revenue = _decimal(actual_totals.get("actual_revenue"))
            actual_enrollment = _integer(actual_totals.get("enrollment"))
            summary["frozen_actual_revenue"] = _money(actual_revenue)
            if (
                actual_revenue is None
                or (actual_revenue < 0 and not signed_actuals_verified)
                or actual_enrollment is None
            ):
                _issue(
                    issues,
                    "FINALIZED_TOTAL_INVALID",
                    "已定版完整部门实际金额或人次缺失、为负或无效",
                    season=season,
                )
                summary.update(
                    forecast_revenue=None, forecast_total=None, gap=None, achievement=None, status="BLOCKED"
                )
            else:
                known_actual_rows = [
                    row
                    for row in quarter_rows
                    if row.get("data_status") not in {"BASELINE_ESTIMATE", "ACTUAL_UNAVAILABLE"}
                ]
                known_sum = _sum([_decimal(row.get("forecast_revenue")) for row in known_actual_rows])
                if (
                    len(known_actual_rows) == len(quarter_rows)
                    and known_sum is not None
                    and abs(known_sum - actual_revenue) > Decimal("0.01")
                ):
                    _issue(
                        issues,
                        "FINALIZED_TOTAL_MISMATCH",
                        "已定版完整部门实际与来源明细合计不一致",
                        season=season,
                        actual_total=str(actual_revenue),
                        known_detail_sum=str(known_sum),
                    )
                budget_revenue = _decimal(summary.get("budget_revenue"))
                gap = budget_revenue - actual_revenue if budget_revenue is not None else None
                summary.update(
                    forecast_revenue=_money(actual_revenue),
                    forecast_total=actual_enrollment,
                    gap=_money(gap),
                    achievement=_ratio(actual_revenue / budget_revenue)
                    if budget_revenue is not None and budget_revenue > 0
                    else None,
                    status="BLOCKED"
                    if gap is None
                    else "NO_SIGNIFICANT_GAP"
                    if abs(gap) <= GAP_TOLERANCE
                    else "GAP"
                    if gap > 0
                    else "ABOVE_BUDGET",
                )
        quarters.append({"season": season, "state": states.get(season), **summary})
    confirmation_checks = _review_empty_budget_actuals(indexed, snapshot, assumptions, output, issues)
    unique_issues: list[dict[str, Any]] = []
    for issue in issues:
        if issue not in unique_issues:
            unique_issues.append(issue)
    complete = {issue["code"] for issue in unique_issues if issue["severity"] == "BLOCKING"}.issubset(
        {"FINALIZED_ACTUAL_MISSING"}
    ) and all(
        quarter["forecast_revenue"] is not None and quarter["forecast_total"] is not None
        for quarter in quarters
    )
    return {
        "rule_version": RULE_VERSION,
        "confirmation_policy_version": CONFIRMATION_POLICY_VERSION,
        "confirmation_checks": confirmation_checks,
        "fixed_rules_version": FIXED_RULES_VERSION if fixed else None,
        "forecast_complete": complete,
        "ready": not trial and not any(issue["severity"] == "BLOCKING" for issue in unique_issues),
        "trial_mode": trial,
        "trial_ready": trial and complete,
        "trial_assumptions": trial_notes if trial else [],
        "rule_notes": _fixed_rule_notes(trial_notes, states) if fixed else [],
        "as_of": snapshot.get("as_of"),
        "rows": output,
        "quarters": quarters,
        "annual": _summary(quarters),
        "issues": unique_issues,
        "campuses": campuses,
        "withdrawal_evidence": evidence,
        "student_classification": "WITHDRAWAL_CLASSIFICATION",
        "signed_actuals_verified": signed_actuals_verified,
        "gap_tolerance_yuan": str(GAP_TOLERANCE),
    }
