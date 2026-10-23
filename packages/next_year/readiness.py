"""Read-only class diagnostics, never a repair or a global feasibility claim.

Use the authoritative calculation and existing preview rate bounds. A row below
the class minimum does not by itself prove that the whole budget is infeasible.
Closing a scope remains a separate human decision with an audited draft change.
"""

from collections import defaultdict
from fractions import Fraction
from typing import Any

from packages.forecast.calculation import DIMS, SEASONS
from packages.next_year.calculation import allocate_entry_counts, calculate_plan, effective_entry, number
from packages.next_year.planning import BalanceOptions, _rate_bounds

Json = dict[str, Any]
CLASS_CODES = {"BELOW_MINIMUM_CLASS_SIZE", "ENABLED_COMBINATION_ALL_SEASONS_ZERO"}


def _preview_bounds(
    inputs: Json,
    calculated: dict[tuple[str, str], Json],
    rates: dict[tuple[Any, ...], Json],
    seed_cohorts: set[tuple[Any, ...]],
) -> dict[tuple[str, str], tuple[int, int]]:
    """Conservative floor/ceil bounds for the default fixed-entry preview only.

    The exact kernel rounds old/new counts by largest remainder, so each row
    stays between the floor and ceiling of its own theory. Independent interval
    bounds relax group coupling: failure to reach the minimum is a proof within
    this preview, but the opposite is not a promise of a feasible candidate.
    """
    entries, _, _ = allocate_entry_counts(inputs)
    opening: dict[str, int] = defaultdict(int)
    for row in inputs["opening_sources"]:
        if row["target_id"] is not None:
            opening[row["target_id"]] += row["students"]
    result: dict[tuple[str, str], tuple[int, int]] = {}
    step_limit = BalanceOptions().rate_step_limit_ppm

    def floor(value: Fraction) -> int:
        return value.numerator // value.denominator

    def ceiling(value: Fraction) -> int:
        return (value.numerator + value.denominator - 1) // value.denominator

    for index, season in enumerate(SEASONS):
        for combo in inputs["combinations"]:
            if not combo["enabled"]:
                continue
            identifier = combo["id"]
            key = (season, *(combo[field] for field in ("track", "product", "grade", "campus")))
            rate = rates[key]
            renewal = _rate_bounds(rate, "renewal", step_limit)
            share = _rate_bounds(rate, "share", step_limit)
            if rate.get("locked") or key in seed_cohorts:
                renewal = (rate["renewal_ppm"], rate["renewal_ppm"])
            if rate.get("locked"):
                share = (rate["share_ppm"], rate["share_ppm"])
            prior = result[identifier, SEASONS[index - 1]] if index else (opening[identifier],) * 2
            old_lower = Fraction(prior[0] * renewal[0], 1_000_000)
            old_upper = Fraction(prior[1] * renewal[1], 1_000_000)
            row = calculated[identifier, season]
            if index == 0 and effective_entry(combo) and entries[identifier] > 0:
                new_lower = new_upper = Fraction(entries[identifier])
            elif row["planned_new_applied"]:
                new_lower = new_upper = Fraction(row["new_students"])
            else:
                new_lower = old_lower * share[0] / (1_000_000 - share[0])
                new_upper = old_upper * share[1] / (1_000_000 - share[1])
            result[identifier, season] = (
                floor(old_lower) + floor(new_lower),
                ceiling(old_upper) + ceiling(new_upper),
            )
    return result


def class_readiness(inputs: Json) -> Json:
    """Describe current issues without changing inputs, targets or any locks."""
    calculation = calculate_plan(inputs)
    combinations = {row["id"]: row for row in inputs["combinations"]}
    calculated = {(row["combination_id"], row["season"]): row for row in calculation["rows"]}
    rates = {
        (row["season"], row["track"], row["product"], row["grade"], row["campus"]): row
        for row in inputs["rates"]
    }
    plans = {(row["combination_id"], row["season"]): row for row in inputs["plans"]}
    minimums = {(row["track"], row["product"]): row["minimum"] for row in inputs["class_sizes"]}
    seed_cohorts = {
        (row["season"], row["track"], row["product"], row["grade"], row["campus"])
        for row in calculation["rows"]
        if row["planned_new_applied"] and not row["manual_locked"] and row["old_students"] == 0
    }
    bounds = _preview_bounds(inputs, calculated, rates, seed_cohorts) if calculation["complete"] else {}
    grouped: dict[str, list[Json]] = defaultdict(list)
    for issue in calculation["issues"]:
        if issue["code"] in CLASS_CODES:
            grouped[issue["combination_id"]].append(issue)

    result: list[Json] = []
    closure_allowed_ids: set[str] = set()
    carry_protected_ids: set[str] = set()
    step_limit = BalanceOptions().rate_step_limit_ppm
    for identifier, issues in grouped.items():
        combo = combinations[identifier]
        cohort = tuple(combo[key] for key in ("track", "product", "grade", "campus"))
        combo_rates = [rates[(season, *cohort)] for season in SEASONS]
        entry_locked = bool(combo.get("entry_locked"))
        manual_locked = any(plans[identifier, season].get("locked", False) for season in SEASONS)
        rate_locked = any(row.get("locked", False) for row in combo_rates)
        lock_reasons = []
        if entry_locked:
            lock_reasons.append("入口人次已锁定")
        if manual_locked:
            lock_reasons.append("手动新生计划已锁定")
        if rate_locked:
            lock_reasons.append("招生参数已锁定")
        carry = number(combo["opening_carryover_yuan"])
        closure_allowed = carry == 0 and not lock_reasons
        if closure_allowed:
            closure_allowed_ids.add(identifier)
        if carry > 0:
            carry_protected_ids.add(identifier)
        for issue in issues:
            season = None if issue["code"] == "ENABLED_COMBINATION_ALL_SEASONS_ZERO" else issue["season"]
            row = calculated.get((identifier, season))
            selected_seasons = [season] if season is not None else list(SEASONS)
            parameters = []
            for selected_season in selected_seasons:
                key = (selected_season, *cohort)
                rate = rates[key]
                values: Json = {"season": selected_season, "rate_id": rate["id"]}
                for kind in ("renewal", "share"):
                    implicit_lock = kind == "renewal" and key in seed_cohorts
                    user_lock = bool(rate.get("locked"))
                    locked = user_lock or implicit_lock
                    lower, upper = _rate_bounds(rate, kind, step_limit)
                    if locked:
                        lower = upper = rate[kind + "_ppm"]
                    values[kind] = {
                        "id": rate["id"] + kind + "_ppm",
                        "current_ppm": rate[kind + "_ppm"],
                        "lower_bound_ppm": lower,
                        "upper_bound_ppm": upper,
                        "locked": locked,
                        "lock_source": "USER" if user_lock else "SEED_PLAN" if implicit_lock else None,
                        "bound_source": "LOCKED"
                        if locked
                        else "EXPLICIT"
                        if any(kind + suffix in rate for suffix in ("_lower_ppm", "_upper_ppm"))
                        else "DEFAULT_STEP",
                    }
                parameters.append(values)
            seed_plan = any(
                (candidate := calculated.get((identifier, selected_season))) is not None
                and candidate["planned_new_applied"]
                and not candidate["manual_locked"]
                and candidate["old_students"] == 0
                for selected_season in selected_seasons
            )
            actions = []
            if carry > 0:
                actions.append("保留期初结转，不能直接关闭业务。")
            if lock_reasons:
                actions.append("先检查已锁定的业务计划；本次诊断不会解除锁定。")
            if seed_plan:
                actions.append("当前采用绝对新生计划，调平时不会仅因调整比例而改动这项计划。")
            if inputs["target_revenue_yuan"] is None:
                actions.append("自动调平前需填写经营目标；本次检查不会替你设置目标。")
            if issue["code"] == "BELOW_MINIMUM_CLASS_SIZE":
                actions.append("在固定入口总量、经营目标和已确认参数边界内调整，再重新计算。")
            if closure_allowed:
                actions.append("若确认不再开展，可人工关闭该业务，并核对修改影响。")
            proof = None
            if season is not None and (identifier, season) in bounds:
                lower, upper = bounds[identifier, season]
                minimum = minimums[combo["track"], combo["product"]]
                unreachable = 0 < lower <= upper < minimum
                proof = {
                    "scope": "CURRENT_PREVIEW",
                    "entry_adjustment": False,
                    "lower_students": lower,
                    "upper_students": upper,
                    "minimum": minimum,
                    "unreachable": unreachable,
                    "message": "入口固定时，当前比例可调范围到不了班均。"
                    if unreachable
                    else "当前保守范围不能证明不可达，仍需完整预算求解和核对。",
                }
            result.append(
                {
                    "code": issue["code"],
                    "combination_id": identifier,
                    "season": season,
                    **{field: combo[field] for field in DIMS},
                    "current_students": row["total_students"] if row else 0 if season is None else None,
                    "old_students": row["old_students"] if row else 0 if season is None else None,
                    "new_students": row["new_students"] if row else 0 if season is None else None,
                    "minimum": minimums[combo["track"], combo["product"]],
                    "gap": issue["detail"].get("class_size_gap", 0),
                    "carryover_yuan": combo["opening_carryover_yuan"],
                    "closure_allowed": closure_allowed,
                    "plan_locked": bool(plans[identifier, season].get("locked"))
                    if season is not None
                    else manual_locked,
                    "seed_plan": seed_plan,
                    "entry_locked": entry_locked,
                    "rate_locked": rate_locked,
                    "combination_locked": bool(lock_reasons),
                    "lock_reasons": lock_reasons,
                    "rate_parameters": parameters,
                    "bound_proof": proof,
                    "actions": actions,
                }
            )
    return {
        "calculation_complete": calculation["complete"],
        "blocking_issue_count": sum(issue["severity"] == "BLOCKING" for issue in calculation["issues"]),
        "issue_count": len(result),
        "combination_count": len(grouped),
        "closure_allowed_count": len(closure_allowed_ids),
        "carry_protected_count": len(carry_protected_ids),
        "rows": result,
    }
