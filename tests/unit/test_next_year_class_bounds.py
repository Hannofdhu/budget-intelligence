"""Conservative bounds are checked against finite original-kernel decisions."""

from copy import deepcopy
from itertools import product

import pytest

from packages.next_year.calculation import calculate_plan
from packages.next_year.planning import BalanceOptions, _context
from packages.optimization.evaluator import BudgetCandidateEvaluator
from scripts.diagnose_next_year_class_bounds import diagnose
from tests.unit.test_next_year_calculation import _plan_inputs


def _draft(*, manual: bool = False, split_entry: bool = False) -> dict:
    inputs = _plan_inputs()
    if manual:
        inputs["opening_sources"][0]["students"] = 0
        for row in inputs["plans"]:
            if row["combination_id"] == inputs["combinations"][1]["id"]:
                row.update(students=0 if row["season"] == "秋季" else 7, locked=True, reason="已确认计划")
    if split_entry:
        source = inputs["combinations"][0]
        sibling = {**deepcopy(source), "id": "entry-sibling", "subject": "英语", "source_entry_weight": 3}
        inputs["combinations"].append(sibling)
        for collection in ("finance", "plans"):
            inputs[collection].extend(
                {
                    **deepcopy(row),
                    "combination_id": sibling["id"],
                    **({"id": row["id"] + "-sibling"} if "id" in row else {}),
                }
                for row in tuple(inputs[collection])
                if row["combination_id"] == source["id"]
            )
    for row in inputs["rates"]:
        row.update(
            renewal_lower_ppm=799_900,
            renewal_upper_ppm=800_100,
            share_lower_ppm=199_900,
            share_upper_ppm=200_100,
        )
    return {"inputs": inputs, "calculation": calculate_plan(inputs)}


@pytest.mark.parametrize("mode", ["fixed", "soft20", "unrestricted"])
@pytest.mark.parametrize("manual", [False, True])
def test_class_bounds_cover_finite_original_kernel_values_including_zero_seeds_and_locks(
    mode: str, manual: bool
) -> None:
    draft = _draft(manual=manual)
    original = deepcopy(draft)
    bounds = {(row["id"], row["season"]): row for row in diagnose(draft, mode)["all_bounds"]}
    context = _context(draft["inputs"], draft["calculation"], BalanceOptions(adjust_entry=mode != "fixed"))
    evaluator = BudgetCandidateEvaluator(context)
    baseline_rates = {row.stable_id: row.current_value_ppm for row in context.rate_decisions}
    variable = [row for row in context.rate_decisions if not row.locked][:2]
    entry = {row.stable_id: row.current_value for row in context.entry_decisions}
    evaluated = 0
    for choices in product(
        *[(row.lower_bound_ppm, row.current_value_ppm, row.upper_bound_ppm) for row in variable]
    ):
        rates = {**baseline_rates, **dict(zip((row.stable_id for row in variable), choices, strict=True))}
        result = evaluator._calculate(entry, rates, {})
        assert "KERNEL_OUTPUT_UNAVAILABLE" not in result.validation_codes
        for key, count in result.headcount_signature:
            identifier, season = key.rsplit(":", 1)
            interval = bounds[identifier, int(season)]
            assert interval["lower"] <= count <= interval["upper"]
        evaluated += 1
    assert evaluated == 9
    assert draft == original


def test_unrestricted_entry_bounds_cover_fixed_total_reallocation_and_largest_remainder() -> None:
    draft = _draft(split_entry=True)
    bounds = {(row["id"], row["season"]): row for row in diagnose(draft, "unrestricted")["all_bounds"]}
    context = _context(draft["inputs"], draft["calculation"], BalanceOptions(adjust_entry=True))
    evaluator = BudgetCandidateEvaluator(context)
    rates = {row.stable_id: row.current_value_ppm for row in context.rate_decisions}
    keys = [row.stable_id for row in context.entry_decisions]
    assert len(keys) == 2
    for first in (1, 5, 9):
        result = evaluator._calculate({keys[0]: first, keys[1]: 10 - first}, rates, {})
        assert "KERNEL_OUTPUT_UNAVAILABLE" not in result.validation_codes
        for key, count in result.headcount_signature:
            identifier, season = key.rsplit(":", 1)
            interval = bounds[identifier, int(season)]
            assert interval["lower"] <= count <= interval["upper"]
