"""Explicit FY-next recruitment-target proposals; never source actuals or saved data.

The user authorized a whole-plan suggestion, not silent minimum-class top-ups.
This module preserves all business scopes and financial/source rules, shows every
input change, and returns a draft proposal for review before the normal save.
"""

from copy import deepcopy
from fractions import Fraction
from typing import Any

from packages.forecast.calculation import DIMS, SEASONS
from packages.next_year.calculation import (
    allocate_entry_counts,
    calculate_plan,
    effective_entry,
    validate_plan_inputs,
)
from packages.next_year.planning import _rate_bounds, compare_calculations
from packages.next_year.readiness import _preview_bounds

Json = dict[str, Any]
CLASS_CODES = {"BELOW_MINIMUM_CLASS_SIZE", "ENABLED_COMBINATION_ALL_SEASONS_ZERO"}


def _row_index(calculation: Json) -> dict[tuple[str, str], Json]:
    return {(row["combination_id"], row["season"]): row for row in calculation["rows"]}


def _rate_key(combo: Json, season: str) -> tuple[str, ...]:
    return season, combo["track"], combo["product"], combo["grade"], combo["campus"]


def _ceil_step(value: Fraction) -> int:
    return -(-value.numerator // (value.denominator * 100)) * 100


def _dims(combo: Json) -> Json:
    return {"combination_id": combo["id"], **{field: combo[field] for field in DIMS}}


def _opening_theory(inputs: Json, combo: Json, season: str, calculation: Json, rate: Json) -> Fraction:
    if season == "暑假":
        students = sum(
            row["students"] for row in inputs["opening_sources"] if row["target_id"] == combo["id"]
        )
    else:
        previous = SEASONS[SEASONS.index(season) - 1]
        students = _row_index(calculation)[combo["id"], previous]["total_students"]
    return Fraction((students or 0) * rate["renewal_ppm"], 1_000_000)


def _widen(rate: Json, kind: str, required_ppm: int, *, freeze_window: bool = False) -> None:
    lower, upper = _rate_bounds(rate, kind, 50_000)
    if freeze_window or required_ppm < lower or required_ppm > upper:
        # Freeze the old default side as well: changing the current renewal
        # value must not silently move its implicit +/-5pp window.
        rate[kind + "_lower_ppm"] = min(lower, required_ppm)
        rate[kind + "_upper_ppm"] = max(upper, required_ppm)


def _stability_bounds(
    inputs: Json, calculation: Json
) -> tuple[dict[tuple[str, str], tuple[int, int]], dict[tuple[str, str], int]]:
    """Conservative integer bounds, including largest-remainder tail changes.

    A proposal must remain class-feasible when the *allowed* rates change, not
    rely on receiving a current rounding remainder. This is a sufficient local
    guard, not a global minimum or promise that a revenue target is reachable.
    """
    indexed = _row_index(calculation)
    rates = {_rate_key(row, row["season"]): row for row in inputs["rates"]}
    seeds = {
        _rate_key(row, row["season"])
        for row in calculation["rows"]
        if row["planned_new_applied"] and not row["manual_locked"] and row["old_students"] == 0
    }
    totals = _preview_bounds(inputs, indexed, rates, seeds)
    old_floors = {}
    for combo in inputs["combinations"]:
        if not combo["enabled"]:
            continue
        opening = sum(row["students"] for row in inputs["opening_sources"] if row["target_id"] == combo["id"])
        for index, season in enumerate(SEASONS):
            key = _rate_key(combo, season)
            rate = rates[key]
            renewal = _rate_bounds(rate, "renewal", 50_000)[0]
            if rate.get("locked") or key in seeds:
                renewal = rate["renewal_ppm"]
            previous = opening if index == 0 else totals[combo["id"], SEASONS[index - 1]][0]
            old_floors[combo["id"], season] = previous * renewal // 1_000_000
    return totals, old_floors


def _preserved(before: Json, after: Json) -> Json:
    before_combos = {row["id"]: row for row in before["combinations"]}
    after_combos = {row["id"]: row for row in after["combinations"]}
    rates = {row["id"]: row for row in after["rates"]}
    plans = {(row["combination_id"], row["season"]): row for row in after["plans"]}
    return {
        "enabled_scopes_preserved": before_combos.keys() == after_combos.keys()
        and all(row["enabled"] == after_combos[key]["enabled"] for key, row in before_combos.items()),
        "source_preserved": before["opening_sources"] == after["opening_sources"]
        and all(
            all(
                row.get(field) == after_combos[key].get(field)
                for field in (*DIMS, "entry", "source_entry_weight", "source_new_by_season")
            )
            for key, row in before_combos.items()
        ),
        "opening_carryover_preserved": all(
            row["opening_carryover_yuan"] == after_combos[key]["opening_carryover_yuan"]
            for key, row in before_combos.items()
        ),
        "minimum_preserved": before["class_sizes"] == after["class_sizes"],
        "entry_total_preserved": before["entry_total"] == after["entry_total"],
        "price_and_discount_fixed": before["finance"] == after["finance"]
        and before["tax_factor"] == after["tax_factor"],
        "user_locks_preserved": all(
            not row.get("entry_locked") or row == after_combos[key] for key, row in before_combos.items()
        )
        and all(not row.get("locked") or row == rates[row["id"]] for row in before["rates"])
        and all(
            not row.get("locked") or row == plans[row["combination_id"], row["season"]]
            for row in before["plans"]
        ),
        "operating_target_unchanged": before["target_revenue_yuan"] == after["target_revenue_yuan"]
        and before["track_targets"] == after["track_targets"],
    }


def repair_class_plan(inputs: Json) -> Json:
    """Return a reviewable recruitment plan, with no save/confirm/ledger operations.

    New absolute plans are proposed tasks, not invented historical enrollment.
    The deterministic strategy proposes sufficient future recruitment targets
    for the allowed rate ranges after fixed-total entry redistribution. A small
    explicit stability reserve avoids relying on a largest-remainder bonus; it
    does not claim a global minimum across cross-season strategies.
    """
    original, proposed = deepcopy(inputs), deepcopy(inputs)
    before = calculate_plan(original)
    current = before
    combos = {row["id"]: row for row in proposed["combinations"] if row["enabled"]}
    original_combos = {row["id"]: row for row in original["combinations"]}
    rates = {_rate_key(row, row["season"]): row for row in proposed["rates"]}
    original_rates = {row["id"]: row for row in original["rates"]}
    plans = {(row["combination_id"], row["season"]): row for row in proposed["plans"]}
    original_plans = {(row["combination_id"], row["season"]): row for row in original["plans"]}
    minimums = {(row["track"], row["product"]): row["minimum"] for row in proposed["class_sizes"]}
    conflicts: dict[tuple[str, str, str], Json] = {}
    target_guards: dict[tuple[str, str], Json] = {}
    stability_conflicts: dict[tuple[str, str], Json] = {}

    def conflict(combo: Json, season: str, code: str, message: str) -> None:
        conflicts[combo["id"], season, code] = {
            **_dims(combo),
            "season": season,
            "code": code,
            "message": message,
        }

    entry_before, _, _ = allocate_entry_counts(original)
    entry_after = dict(entry_before)
    summer = _row_index(before)
    initial_bounds, initial_old_floors = _stability_bounds(original, before)
    zero_combos = {
        row["combination_id"]
        for row in before["issues"]
        if row["code"] == "ENABLED_COMBINATION_ALL_SEASONS_ZERO"
    }
    floors = {}
    deficits = {}
    for key, value in entry_before.items():
        combo, row = combos[key], summer[key, "暑假"]
        minimum = minimums[combo["track"], combo["product"]]
        floor = (
            max(0, minimum - initial_old_floors[key, "暑假"])
            if initial_bounds[key, "暑假"][1] > 0 or key in zero_combos
            else 0
        )
        floors[key] = floor
        if value < floor:
            if combo.get("entry_locked"):
                conflict(combo, "暑假", "ENTRY_LOCKED", "入口人次已锁定，不能在本次建议中重新分配")
            else:
                deficits[key] = floor - value
    donors = {
        key: max(0, value - floors[key])
        for key, value in entry_before.items()
        if not combos[key].get("entry_locked")
    }
    for key, deficit in sorted(deficits.items(), key=lambda item: (item[1], item[0])):
        if sum(donors.values()) < deficit:
            conflict(
                combos[key],
                "暑假",
                "FIXED_ENTRY_TOTAL_INSUFFICIENT",
                "固定入口总量内没有足够的未锁定人次可调剂，未增加入口总量",
            )
            continue
        remaining = deficit
        for donor in sorted(donors, key=lambda identifier: (-donors[identifier], identifier)):
            transferred = min(remaining, donors[donor])
            entry_after[donor] -= transferred
            donors[donor] -= transferred
            entry_after[key] += transferred
            remaining -= transferred
            if remaining == 0:
                break
    if entry_after != entry_before:
        for key, value in entry_after.items():
            if not combos[key].get("entry_locked"):
                combos[key]["entry_students"] = value
        current = calculate_plan(proposed)
        allocated, _, _ = allocate_entry_counts(proposed)
        if any(
            combo.get("entry_locked") and allocated[key] != entry_before[key]
            for key, combo in combos.items()
            if key in entry_before
        ):
            # An implicit locked allocation must stay fixed as well as its
            # literal input. Do not let another row's explicit value move it.
            proposed["combinations"] = deepcopy(original["combinations"])
            combos = {row["id"]: row for row in proposed["combinations"] if row["enabled"]}
            entry_after = dict(entry_before)
            current = before
            for key in deficits:
                conflict(combos[key], "暑假", "ENTRY_LOCKED", "入口重新分配会影响已锁定人次，未应用该调剂")

    # A later quarter uses the prior quarter's revised integer counts. Re-run
    # the full formal kernels after each season, preserving largest remainder.
    for _ in range(3):
        changed = False
        for season in SEASONS:
            indexed = _row_index(current)
            bounds, old_floors = _stability_bounds(proposed, current)
            zeros = {
                row["combination_id"]
                for row in current["issues"]
                if row["code"] == "ENABLED_COMBINATION_ALL_SEASONS_ZERO"
            }
            for key, combo in sorted(combos.items()):
                row = indexed[key, season]
                minimum = minimums[combo["track"], combo["product"]]
                total = row["total_students"]
                lower, upper = bounds[key, season]
                if total is None or not (upper > 0 and lower < minimum or key in zeros and season == "暑假"):
                    continue
                if season == "暑假" and effective_entry(combo):
                    continue
                rate, plan = rates[_rate_key(combo, season)], plans[key, season]
                original_plan, original_rate = original_plans[key, season], original_rates[rate["id"]]
                if original_rate.get("locked"):
                    conflict(combo, season, "RATE_LOCKED", "招生参数已锁定，不能通过绝对新生目标绕过锁定参数")
                    stability_conflicts[key, season] = {
                        **_dims(combo),
                        "season": season,
                        "code": "RATE_LOCKED",
                        "message": "锁定参数或分组取整使本行无法保证整个可调范围内成班；未解除锁定",
                    }
                    continue
                if original_plan.get("locked"):
                    needed_old = minimum - (row["new_students"] or 0)
                    source = (
                        sum(row["students"] for row in proposed["opening_sources"] if row["target_id"] == key)
                        if season == "暑假"
                        else bounds[key, SEASONS[SEASONS.index(season) - 1]][0]
                    )
                    required = _ceil_step(Fraction(needed_old * 1_000_000, source)) if source else 1_000_100
                    if required > 1_000_000:
                        conflict(
                            combo,
                            season,
                            "PLAN_LOCKED",
                            "新生目标已锁定，且仅调整续班率仍不足成班，需另行调整已锁定计划",
                        )
                        stability_conflicts[key, season] = conflicts[key, season, "PLAN_LOCKED"]
                        continue
                    _widen(rate, "renewal", required, freeze_window=True)
                    # This explicit boundary recommendation is visible in
                    # actions. It preserves the user's absolute-new lock and
                    # prevents subsequent balancing from undoing class safety.
                    rate["renewal_lower_ppm"] = max(rate["renewal_lower_ppm"], required)
                    rate["renewal_ppm"] = max(rate["renewal_ppm"], required)
                    changed = True
                    current = calculate_plan(proposed)
                    indexed = _row_index(current)
                    bounds, old_floors = _stability_bounds(proposed, current)
                    continue
                desired = max(row["new_students"] or 0, minimum - old_floors[key, season])
                old_theory = _opening_theory(proposed, combo, season, current, rate)
                if old_theory > 0:
                    implied = _ceil_step(Fraction(desired * 1_000_000, old_theory + desired))
                    if implied > 999_900:
                        conflict(
                            combo,
                            season,
                            "SHARE_DOMAIN_EXCEEDED",
                            "建议目标需要的新生占比超出人数反推允许范围，未自动扩大硬边界",
                        )
                        continue
                    _widen(rate, "share", implied)
                plan.update(
                    students=desired,
                    locked=True,
                    reason=f"{proposed['fiscal_year']}财年招生目标建议：保留业务，按允许续班下界预留成班人次，避免取整及调平时跌破原最低人数；仅为计划，需整批审核后保存",
                )
                target_guards[key, season] = {
                    "stable_old_floor": old_floors[key, season],
                    "stability_reserve_students": max(
                        0, desired - max(row["new_students"] or 0, minimum - (row["old_students"] or 0))
                    ),
                }
                changed = True
            if changed:
                current = calculate_plan(proposed)
        if not changed:
            break

    validate_plan_inputs(proposed, original)
    after = calculate_plan(proposed)
    constraints = _preserved(original, proposed)
    if not all(constraints.values()):
        raise ValueError("class repair changed an immutable source, rule, target or user lock")
    old_rows, new_rows = _row_index(before), _row_index(after)
    actions = []
    for key, value in entry_after.items():
        combo = combos[key]
        if original_combos[key].get("entry_students") != combo.get("entry_students"):
            actions.append(
                {
                    **_dims(combo),
                    "season": "暑假",
                    "kind": "ENTRY_REALLOCATION",
                    "field": "entry_students",
                    "before": entry_before[key],
                    "after": value,
                    "before_input": original_combos[key].get("entry_students"),
                    "stable_old_floor": initial_old_floors[key, "暑假"],
                    "minimum": minimums[combo["track"], combo["product"]],
                    "reason": "在固定入口总量内重新分配，保留全部业务及原最低成班人数"
                    if value != entry_before[key]
                    else "保留本次其余入口分配，防止系统再次按历史权重重分配",
                }
            )
    for plan_key, plan in plans.items():
        if plan == original_plans[plan_key]:
            continue
        combo, season = combos[plan_key[0]], plan_key[1]
        previous, following = old_rows[plan_key], new_rows[plan_key]
        actions.append(
            {
                **_dims(combo),
                "season": season,
                "kind": "NEW_STUDENT_TARGET",
                "field": "plans.students",
                "before": previous["new_students"],
                "after": plan["students"],
                "before_input": original_plans[plan_key]["students"],
                "before_total_students": previous["total_students"],
                "after_total_students": following["total_students"],
                "old_students": following["old_students"],
                "minimum": minimums[combo["track"], combo["product"]],
                "additional_students": plan["students"] - (previous["new_students"] or 0),
                "plan_is_not_actual": True,
                "manual_target_priority": True,
                **target_guards.get(plan_key, {}),
                "reason": plan["reason"],
            }
        )
    for rate in proposed["rates"]:
        previous = original_rates[rate["id"]]
        scoped = [
            combo
            for combo in combos.values()
            if _rate_key(combo, rate["season"]) == _rate_key(rate, rate["season"])
        ]
        for field in (
            "renewal_ppm",
            "renewal_lower_ppm",
            "renewal_upper_ppm",
            "share_lower_ppm",
            "share_upper_ppm",
        ):
            if previous.get(field) == rate.get(field):
                continue
            old_value = previous.get(field)
            if old_value is None:
                kind, bound = field.split("_")[:2]
                old_value = _rate_bounds(previous, kind, 50_000)[0 if bound == "lower" else 1]
            actions.append(
                {
                    "season": rate["season"],
                    **{dim: rate.get(dim, "全部") for dim in DIMS},
                    "rate_id": rate["id"],
                    "combination_ids": [combo["id"] for combo in scoped],
                    "kind": "RENEWAL_ADJUSTMENT" if field == "renewal_ppm" else "RATE_BOUNDARY",
                    "field": field,
                    "before": old_value,
                    "after": rate[field],
                    "reason": "保留已锁定新生目标，建议最低必要续班率"
                    if field == "renewal_ppm"
                    else "显式设定续班可调范围，保留锁定新生目标并防止调平后再次低于成班人数"
                    if field.startswith("renewal_")
                    else (
                        "显式扩展招生参数允许范围，以覆盖建议绝对新生目标；"
                        "绝对目标优先生效，未修改新生占比当前值"
                    ),
                }
            )
    deviations = []
    for key, value in entry_after.items():
        lower, upper = entry_before[key] * 80 // 100, (entry_before[key] * 120 + 99) // 100
        if value < lower or value > upper:
            deviations.append(
                {
                    **_dims(combos[key]),
                    "before": entry_before[key],
                    "after": value,
                    "soft_lower": lower,
                    "soft_upper": upper,
                    "deviation_students": lower - value if value < lower else value - upper,
                    "reason": "超过历史入口分配的20%软偏好，固定总量与用户锁定均未改变",
                }
            )
    unresolved = [
        {
            **_dims(combos[row["combination_id"]]),
            **deepcopy(row),
            "message": "现有锁定或允许参数范围内仍无法满足成班要求，未强制修改",
        }
        for row in after["issues"]
    ]
    unresolved_keys = {(row["combination_id"], row["season"]) for row in after["issues"]}
    unresolved_combos = {
        row["combination_id"]
        for row in after["issues"]
        if row["code"] == "ENABLED_COMBINATION_ALL_SEASONS_ZERO"
    }
    final_bounds, _ = _stability_bounds(proposed, after)
    unsafe_keys = {
        key
        for key, (lower, upper) in final_bounds.items()
        if upper > 0 and lower < minimums[combos[key[0]]["track"], combos[key[0]]["product"]]
    }
    ready = after["complete"] and not after["issues"]
    return {
        "status": "READY" if ready else "PARTIAL",
        "message": "招生目标建议已生成，确认后可保存为新草稿"
        if ready
        else "已生成可调整部分，仍有锁定冲突或未解决的成班问题",
        "proposed_inputs": proposed,
        "before_calculation": before,
        "calculation": after,
        "impact": compare_calculations(before, after),
        "actions": actions,
        "before_issues": before["issues"],
        "after_issues": after["issues"],
        "issue_count": {"before": len(before["issues"]), "after": len(after["issues"])},
        "unresolved": unresolved,
        "lock_conflicts": [
            row
            for (identifier, season, _), row in conflicts.items()
            if (identifier, season) in unresolved_keys or identifier in unresolved_combos
        ],
        "stability_unresolved": [row for key, row in stability_conflicts.items() if key in unsafe_keys],
        "adjustable_range_class_safe": ready and not unsafe_keys,
        "stability_scope": {
            "entry_allocation_fixed": True,
            "rate_bounds": "EXPLICIT_OR_DEFAULT_PLUS_MINUS_5PP",
            "user_and_zero_old_seed_rate_locks_preserved": True,
            "integer_rounding_guard": "SEPARATE_OLD_NEW_FLOOR_AND_CEILING",
            "not_a_revenue_target_reachability_proof": True,
        },
        "entry_soft_deviations": deviations,
        "constraints": constraints,
        "summary": {
            "entry_reallocated_students": sum(
                max(0, entry_after[key] - value) for key, value in entry_before.items()
            ),
            "new_target_count": sum(row["kind"] == "NEW_STUDENT_TARGET" for row in actions),
            "additional_new_students": after["annual"]["new_students"] - before["annual"]["new_students"],
            "changed_rate_bound_count": sum(row["kind"] == "RATE_BOUNDARY" for row in actions),
            "stability_reserve_students": sum(row.get("stability_reserve_students", 0) for row in actions),
        },
        "strategy": "SEASON_BY_SEASON_RECRUITMENT_WITH_EXPLICIT_STABILITY_RESERVE",
        "globally_minimal": False,
        "requires_review": True,
    }
