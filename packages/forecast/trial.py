"""Prepare explicit forecast-only trial assumptions, never annual-budget inputs.

Only previously confirmed naming changes are generalized. Unbudgeted business
remains separate from the approved budget; missing actuals remain missing. The
engine owns their visibly labelled trial fallback, source validation and totals.
"""

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from packages.forecast.calculation import DIMS, SEASONS

Json = dict[str, Any]
Key = tuple[str, ...]
COUNT_FIELDS = ("enrollment", "new_business", "new_base", "old_base")
ACTIVE_FIELDS = (*COUNT_FIELDS, "actual_revenue")
FINANCIAL_FIELDS = ("price", "discount", "recognition")
# The owner confirmed these actual cloud-class scopes even where a current
# count is explicitly zero. Zero enrollment must not erase a business identity.
CONFIRMED_CLOUD_SCOPES: frozenset[Key] = frozenset(
    ("智慧空间", "云帆", grade, subject, campus)
    for grade, subject, campus in (
        ("七年级", "数学", "百学汇公主坟校区"),
        ("七年级", "英语", "百学汇公主坟校区"),
        ("七年级", "语文", "百学汇公主坟校区"),
        ("八年级", "数学", "百学汇蒲黄榆校区"),
        ("八年级", "英语", "百学汇蒲黄榆校区"),
        ("八年级", "英语", "百学汇玉泉路校区"),
    )
)


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() in {"", "-"}:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _count(value: object) -> Decimal | None:
    result = _number(value)
    if result is None or result < 0 or result != result.to_integral_value():
        return None
    return result


def _key(row: Json) -> Key:
    return tuple(str(row.get(field) or "").strip() for field in DIMS)


def _dims(key: Key) -> dict[str, str]:
    return dict(zip(DIMS, key, strict=True))


def _confirmed_names(mappings: list[Json]) -> tuple[dict[str, str], bool]:
    campuses: dict[str, set[str]] = defaultdict(set)
    products: set[str] = set()
    for mapping in mappings:
        source, target = mapping.get("source"), mapping.get("target")
        if not isinstance(source, dict) or not isinstance(target, dict):
            continue
        source_key, target_key = _key(source), _key(target)
        if not all(source_key) or not all(target_key) or not str(mapping.get("reason") or "").strip():
            continue
        if source_key[:4] == target_key[:4] and source_key[4] != target_key[4]:
            campuses[source_key[4]].add(target_key[4])
        # This is the one product-family relation explicitly confirmed by the
        # business owner. Other full mappings are not generalized as synonyms.
        if (
            source_key[:3] == ("智慧空间", "卓越", "九年级")
            and source_key[0] == target_key[0]
            and source_key[2:] == target_key[2:]
        ):
            products.add(target_key[1])
    return (
        {source: next(iter(targets)) for source, targets in campuses.items() if len(targets) == 1},
        products == {"高端小班"},
    )


def _canonical(key: Key, campuses: dict[str, str], high_end_confirmed: bool) -> Key:
    track, product, grade, subject, campus = key
    if high_end_confirmed and (track, product, grade) == ("智慧空间", "卓越", "九年级"):
        product = "高端小班"
    return track, product, grade, subject, campuses.get(campus, campus)


def _financial_template(peers: list[Json]) -> tuple[Decimal, Decimal, Decimal] | None:
    templates: set[tuple[Decimal, Decimal, Decimal]] = set()
    for row in peers:
        price, discount, recognition = (_number(row.get(field)) for field in FINANCIAL_FIELDS)
        if (
            price is None
            or price < 0
            or discount is None
            or not 0 <= discount <= 1
            or recognition is None
            or not 0 <= recognition <= 1
        ):
            return None
        templates.add((price, discount, recognition))
    return next(iter(templates)) if len(templates) == 1 else None


def _ratios(
    peers: list[Json], season: str, baseline: dict[tuple[str, Key], Json], minimum: int
) -> tuple[str | None, str | None]:
    renewal_n = renewal_d = share_n = share_d = Decimal(0)
    prior_season = SEASONS[SEASONS.index(season) - 1] if season != SEASONS[0] else None
    for row in peers:
        base = _count(
            row.get("prior_students")
            if prior_season is None
            else baseline.get((prior_season, _key(row)), {}).get("total")
        )
        renewal = _number(row.get("renewal"))
        if base is not None and renewal is not None and 0 <= renewal <= 1:
            renewal_n += base * renewal
            renewal_d += base
        new, total = _count(row.get("new")), _count(row.get("total"))
        if new is not None and total is not None and 0 <= new <= total:
            share_n += new
            share_d += total
    if renewal_d < minimum or share_d < minimum or not 0 <= share_n < share_d:
        return None, None
    with localcontext() as context:
        context.prec = 40
        return str(renewal_n / renewal_d), str(share_n / share_d)


def _scope(key: Key, kind: str, baseline: dict[tuple[str, Key], Json], states: Json, minimum: int) -> Json:
    financial: Json = {}
    if kind != "FINANCIAL_ONLY":
        for season in SEASONS:
            if states.get(season) == "FINALIZED":
                # Frozen actual quarters neither need nor permit new trial
                # price/ratio inputs. Their facts remain in the source snapshot.
                continue
            # The owner authorized the uniform same-product financial template
            # for cloud classes without a same-grade budget. Never average a
            # conflicting price, discount or recognition coefficient.
            peers = [
                row
                for (quarter, _), row in baseline.items()
                if quarter == season and _key(row)[:2] == key[:2]
            ]
            template = _financial_template(peers)
            if template is None:
                # Omit an unresolved quarter instead of supplying fake zeros or
                # nullable financial fields that the API contract forbids.
                continue
            renewal, share = _ratios(
                [row for row in peers if _key(row)[2] == key[2]], season, baseline, minimum
            )
            financial[season] = {
                **{field: str(value) for field, value in zip(FINANCIAL_FIELDS, template, strict=True)},
                "renewal": renewal,
                "share": share,
                "method": "TRIAL_SAME_GRADE_WEIGHTED" if renewal is not None else "TRIAL_CARRY_FORWARD",
            }
    return {
        "dims": _dims(key),
        "kind": kind,
        "reason": (
            "试用预设：财务调整单列，不生成未来招生，不改定版预算"
            if kind == "FINANCIAL_ONLY"
            else "试用预设：预算未单列的实际业务独立预测，不跨产品或年级，不改定版预算"
        ),
        "financial": financial,
        "additional": {season: 0 for season in SEASONS if states.get(season) in {"OPEN", "ENDED"}}
        if kind != "FINANCIAL_ONLY"
        else {},
    }


def build_trial_assumptions(baseline: Json, snapshot: Json, prior_assumptions: Json) -> Json:
    """Return deterministic, JSON-compatible trial presets without mutating inputs.

    Missing budget scopes are deliberately not filled with actual zero. The
    engine sees ``trial_mode`` and marks its budget fallback as an assumption.
    Existing explicit adjustments, mappings and forecast scopes are retained.
    """
    result = deepcopy(prior_assumptions)
    result["trial_mode"] = True
    states = result.get("states", {})
    mappings: list[Json] = result.setdefault("mappings", [])
    scopes: list[Json] = result.setdefault("forecast_scopes", [])
    minimum = int(result.get("minimum_sample", 30))
    rows: dict[tuple[str, Key], Json] = {}
    for row in baseline.get("rows", []):
        index = str(row.get("season") or ""), _key(row)
        if index in rows:
            raise ValueError("定版预算完整维度存在重复，不能自动选择试用参数")
        rows[index] = row
    budget_keys = {key for _, key in rows}
    existing_scopes = {_key(scope["dims"]) for scope in scopes}
    explicit: dict[Key, Key] = {}
    conflicting: set[Key] = set()
    for mapping in mappings:
        if isinstance(mapping.get("source"), dict) and isinstance(mapping.get("target"), dict):
            source_key, target_key = _key(mapping["source"]), _key(mapping["target"])
            if source_key in explicit and explicit[source_key] != target_key:
                conflicting.add(source_key)
            explicit.setdefault(source_key, target_key)
    campuses, high_end_confirmed = _confirmed_names(mappings)
    active: dict[Key, list[Json]] = defaultdict(list)
    required: dict[Key, Key] = {}

    def target_for(row: Json) -> tuple[Key, Key] | None:
        source_key = _key(row)
        if not all(source_key) or source_key in conflicting:
            # Leave malformed/conflicting originals for the engine to report.
            return None
        target_key = explicit.get(source_key, _canonical(source_key, campuses, high_end_confirmed))
        return source_key, target_key

    for row in snapshot.get("rows", []):
        if states.get(row.get("season")) not in {"FINALIZED", "OPEN", "ENDED"}:
            continue
        keys = target_for(row)
        if keys is None or all(_number(row.get(field)) == 0 for field in ACTIVE_FIELDS):
            continue
        source_key, target_key = keys
        required[source_key] = target_key
        if target_key not in budget_keys:
            active[target_key].append(row)
    fiscal_year = int(baseline.get("fiscal_year", 2027))
    for carry in snapshot.get("frozen_carryins", []):
        origin = _count(carry.get("origin_season"))
        season = carry.get("season")
        if origin is None or int(origin) % 10 not in {1, 2, 3, 4} or season not in SEASONS:
            continue
        origin_year, origin_index = divmod(int(origin), 10)
        eligible = origin_year < fiscal_year or (
            origin_year == fiscal_year and states.get(SEASONS[origin_index - 1]) == "FINALIZED"
        )
        if not eligible or states.get(season) == "FINALIZED" or _number(carry.get("revenue")) == 0:
            continue
        keys = target_for(carry.get("dims", carry))
        if keys is not None:
            source_key, target_key = keys
            required[source_key] = target_key
            if target_key not in budget_keys:
                active.setdefault(target_key, [])
    for key, members in sorted(active.items()):
        if key in existing_scopes:
            continue
        kind = (
            "FINANCIAL_ONLY"
            if key not in CONFIRMED_CLOUD_SCOPES
            and all(all(_number(row.get(field)) == 0 for field in COUNT_FIELDS) for row in members)
            else "UNBUDGETED_BUSINESS"
        )
        scopes.append(_scope(key, kind, rows, states, minimum))
    for source_key, target_key in sorted(required.items()):
        if source_key not in explicit and source_key != target_key:
            mappings.append(
                {
                    "source": _dims(source_key),
                    "target": _dims(target_key),
                    "reason": "试用预设：沿用已确认校区或产品对应，其余维度保持原值；未预算业务单列",
                }
            )
    return result
