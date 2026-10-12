"""Read-only optimistic class-feasibility bounds; not a proposed recruitment plan.

Run with ``.venv/bin/python -m scripts.diagnose_next_year_class_bounds``.
No snapshot, source, target, ledger or database is written. Entry relaxations
deliberately ignore the joint fixed-total constraint: impossibility even in that
larger space is a valid lower bound on unavoidable class-size violations.
"""

import argparse
import json
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any

from packages.next_year.planning import BalanceOptions, _context


def _floor(value: Fraction) -> int:
    return value.numerator // value.denominator


def _ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def diagnose(draft: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode not in {"fixed", "soft20", "unrestricted"}:
        raise ValueError("mode must be fixed, soft20 or unrestricted")
    inputs = deepcopy(draft["inputs"])
    calculation = draft["calculation"]
    # The context builder needs a revenue target. This diagnostic-only value is
    # never saved and is not a FY28 operating target or a candidate constraint.
    inputs["target_revenue_yuan"] = calculation["annual"]["revenue_yuan"]
    context = _context(inputs, calculation, BalanceOptions(adjust_entry=mode != "fixed"))
    rules = {row.stable_id: row for row in context.rate_decisions}
    rates = {
        (row.fiscal_season, row.track, row.product, row.grade, row.campus, row.rate_type): rules[row.rate_id]
        for row in context.rates
    }
    openings = {row.combination_stable_id: row for row in context.opening_inputs or ()}
    plans = {(row.combination_stable_id, row.fiscal_season): row.value for row in context.plans}
    entries = {row.stable_id: row for row in context.entry_decisions}
    minimums = {(row.track, row.product): row.minimum_class_size for row in context.class_size_parameters}
    by_id = {row["id"]: row for row in inputs["combinations"]}
    impossible = []
    all_bounds = []
    optimistic_all_below = []
    for combination in context.combinations:
        entry = entries.get(combination.stable_id)
        if entry is None:
            entry_lower = entry_upper = 0
        elif mode == "unrestricted" and not by_id[combination.stable_id].get("entry_locked", False):
            entry_lower, entry_upper = 0, context.fixed_entry_total
        elif mode == "soft20" and not by_id[combination.stable_id].get("entry_locked", False):
            # Reproduce the old draft preview cap, not the accepted soft rule.
            entry_lower = entry.current_value * 80 // 100
            entry_upper = min(context.fixed_entry_total, (entry.current_value * 120 + 99) // 100)
        else:
            entry_lower = entry_upper = entry.current_value
        minimum = minimums[combination.track, combination.product]
        previous_lower = previous_upper = 0
        row_bounds = []
        for season_index in range(1, 5):
            season = context.fiscal_year * 10 + season_index
            cohort = season, combination.track, combination.product, combination.grade, combination.campus
            renewal, share = rates[*cohort, "RENEWAL_RATE"], rates[*cohort, "NEW_STUDENT_SHARE"]
            if season_index == 1:
                source = openings[combination.stable_id].source_enrollment
                old_lower = Fraction(source * renewal.lower_bound_ppm, 1_000_000)
                old_upper = Fraction(source * renewal.upper_bound_ppm, 1_000_000)
            else:
                old_lower = Fraction(previous_lower * renewal.lower_bound_ppm, 1_000_000)
                old_upper = Fraction(previous_upper * renewal.upper_bound_ppm, 1_000_000)
            plan = plans.get((combination.stable_id, season))
            if season_index == 1 and entry is not None and entry_upper > 0:
                new_lower, new_upper = Fraction(entry_lower), Fraction(entry_upper)
                if entry_lower == 0:
                    fallback = (
                        Fraction(plan)
                        if plan is not None
                        else old_upper * share.upper_bound_ppm / (1_000_000 - share.upper_bound_ppm)
                    )
                    new_upper = max(new_upper, fallback)
            elif plan is not None:
                # Includes explicit zero and zero-old absolute seeds; never turn
                # a frozen plan into a guessed share when retention changes.
                new_lower = new_upper = Fraction(plan)
            else:
                new_lower = old_lower * share.lower_bound_ppm / (1_000_000 - share.lower_bound_ppm)
                new_upper = old_upper * share.upper_bound_ppm / (1_000_000 - share.upper_bound_ppm)
            # Largest remainder can choose either floor or ceiling separately
            # for old/new in each track-campus season. This interval deliberately
            # permits both independently, making the upper bound optimistic.
            previous_lower = _floor(old_lower) + _floor(new_lower)
            previous_upper = _ceil(old_upper) + _ceil(new_upper)
            row_bounds.append((previous_lower, previous_upper))
            all_bounds.append(
                {
                    "id": combination.stable_id,
                    "season": season,
                    "lower": previous_lower,
                    "upper": previous_upper,
                    "minimum": minimum,
                }
            )
            if 0 < previous_lower <= previous_upper < minimum:
                impossible.append(
                    {
                        "id": combination.stable_id,
                        "season": season,
                        "lower": previous_lower,
                        "upper": previous_upper,
                        "minimum": minimum,
                    }
                )
        if all(upper < minimum for _, upper in row_bounds):
            optimistic_all_below.append(combination.stable_id)
    ids = {row["id"] for row in impossible}
    return {
        "mode": mode,
        "rate_step_limit_ppm": 50_000,
        "unavoidably_active_below_minimum_rows": len(impossible),
        "affected_combinations": len(ids),
        "positive_carry_combinations": sum(Fraction(by_id[key]["opening_carryover_yuan"]) > 0 for key in ids),
        "all_four_seasons_optimistic_upper_below_minimum": len(optimistic_all_below),
        "all_four_seasons_below_positive_carry_combinations": sum(
            Fraction(by_id[key]["opening_carryover_yuan"]) > 0 for key in optimistic_all_below
        ),
        "rows": impossible,
        "all_bounds": all_bounds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path("output/next-year-ledger"))
    parser.add_argument("--sequence", type=int, default=8)
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--summary", dest="details", action="store_false", help="Only output statistics (default)"
    )
    output.add_argument(
        "--details", dest="details", action="store_true", help="Include per-season bounds and impossible rows"
    )
    parser.set_defaults(details=False)
    args = parser.parse_args()
    matches = [json.loads(path.read_text())["event"]["draft"] for path in args.ledger.glob("event-*.json")]
    draft = next(row for row in matches if row["sequence"] == args.sequence)
    bounds = [diagnose(draft, mode) for mode in ("fixed", "soft20", "unrestricted")]
    if not args.details:
        for result in bounds:
            result.pop("rows")
            result.pop("all_bounds")
    print(
        json.dumps(
            {
                "draft_id": draft["id"],
                "sequence": draft["sequence"],
                "bounds": bounds,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
