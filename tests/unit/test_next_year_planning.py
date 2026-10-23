"""Draft extensions do not mutate a forecast, annual baseline or source inputs."""

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from packages.forecast.calculation import SEASONS
from packages.forecast.store import fingerprint
from packages.next_year.calculation import NextYearError, calculate_plan, prepare_inputs, validate_plan_inputs
from packages.next_year.planning import BalanceOptions, _context, balance_plan, compare_calculations
from packages.optimization.engine import (
    CandidateEvaluation,
    OptimizationOutcome,
    OptimizationStage,
    SolveStatus,
)
from packages.optimization.evaluator import BudgetEvaluationContext
from tests.unit.test_next_year_calculation import _forecast_inputs, _plan_inputs


def test_multi_grade_entry_is_an_override_not_rewriting_the_historical_attribute() -> None:
    original = _plan_inputs()
    inputs = deepcopy(original)
    first, second = inputs["combinations"]
    first.update(entry_students=6, entry_locked=True)
    second.update(entry_override=True, entry_students=4)
    validate_plan_inputs(inputs, original)
    result = calculate_plan(inputs)
    summer = {row["combination_id"]: row for row in result["rows"] if row["season"] == "暑假"}
    assert summer[first["id"]]["new_students"] == 6
    assert summer[second["id"]]["new_students"] == 4
    assert summer[second["id"]]["entry"] is True
    assert second["entry"] is False
    assert sum(row["students"] for row in result["entry_details"]) == 10
    assert original["combinations"][1].get("entry_override") is None


@pytest.mark.parametrize("value", [11, 100])
def test_manual_entry_cannot_overallocate_a_fixed_total(value: int) -> None:
    inputs = _plan_inputs()
    inputs["combinations"][0]["entry_students"] = value
    with pytest.raises(NextYearError, match="超过"):
        calculate_plan(inputs)


def test_manual_new_students_uses_the_existing_lock_engine_and_retains_reason() -> None:
    original = _plan_inputs()
    before = calculate_plan(original)
    inputs = deepcopy(original)
    combo = inputs["combinations"][1]
    plan = next(
        row for row in inputs["plans"] if row["combination_id"] == combo["id"] and row["season"] == "秋季"
    )
    plan.update(students=123, locked=True, reason="业务手动调整")
    validate_plan_inputs(inputs, original)
    result = calculate_plan(inputs)
    row = next(
        row for row in result["rows"] if row["combination_id"] == combo["id"] and row["season"] == "秋季"
    )
    assert row["new_students"] == 123
    assert row["manual_locked"] is True
    assert (
        row["old_students"]
        == next(
            old for old in before["rows"] if old["combination_id"] == combo["id"] and old["season"] == "秋季"
        )["old_students"]
    )
    plan["reason"] = ""
    with pytest.raises(NextYearError, match="原因"):
        calculate_plan(inputs)


def test_class_average_is_the_single_basis_and_legacy_plan_size_is_ignored() -> None:
    original = _plan_inputs()
    before = calculate_plan(original)
    inputs = deepcopy(original)
    inputs["class_sizes"][0]["planned_class_size"] = 12
    result = calculate_plan(inputs)
    assert result["annual"]["revenue_yuan"] == before["annual"]["revenue_yuan"]
    assert inputs["class_sizes"][0]["minimum"] == 5
    assert result["issues"] == before["issues"]
    assert all(row["class_average"] == 5 for row in result["rows"])
    assert all(row["class_count"] == (row["total_students"] + 4) // 5 for row in result["rows"])
    assert result["annual"]["class_count"] == sum(row["class_count"] for row in result["rows"])
    assert result == before
    assert result["rule_versions"]["class_count"] == "class-average-ceiling-v1"


@pytest.mark.parametrize(
    ("students", "expected_classes", "expected_status"),
    [
        (0, 0, "NO_PLANNED_CLASS"),
        (23, 1, "BELOW_MINIMUM_CLASS_SIZE"),
        (24, 1, "PASS"),
        (25, 2, "PASS"),
        (48, 2, "PASS"),
        (333, 14, "PASS"),
    ],
)
def test_one_class_average_drives_ceiling_count_and_gate_without_filling_students(
    students: int, expected_classes: int, expected_status: str
) -> None:
    inputs = _plan_inputs()
    inputs["entry_total"] = students
    inputs["class_sizes"][0].update(minimum=24, planned_class_size=30)
    before = fingerprint(inputs)
    calculation = calculate_plan(inputs)
    row = next(
        row
        for row in calculation["rows"]
        if row["combination_id"] == inputs["combinations"][0]["id"] and row["season"] == "暑假"
    )
    assert row["total_students"] == students
    assert row["class_average"] == 24
    assert row["class_count"] == expected_classes
    assert row["class_size_status"] == expected_status
    assert "planned_class_size" not in row
    for season in SEASONS:
        quarter = next(q for q in calculation["quarters"] if q["season"] == season)
        assert quarter["class_count"] == sum(
            r["class_count"] for r in calculation["rows"] if r["season"] == season
        )
    assert calculation["annual"]["class_count"] == sum(r["class_count"] for r in calculation["rows"])
    assert fingerprint(inputs) == before


def test_class_average_changes_only_class_calculation_not_people_or_revenue() -> None:
    inputs = _plan_inputs()
    before = calculate_plan(inputs)
    inputs["class_sizes"][0]["minimum"] = 24
    after = calculate_plan(inputs)
    for field in ("revenue_yuan", "old_students", "new_students", "total_students"):
        assert before["annual"][field] == after["annual"][field]
    assert before["annual"]["class_count"] != after["annual"]["class_count"]


def test_class_rule_upgrade_keeps_frozen_results_and_suppresses_cross_rule_class_delta() -> None:
    after = calculate_plan(_plan_inputs())
    before = deepcopy(after)
    before["rule_versions"].pop("class_count")
    before["annual"]["class_count"] = 12
    frozen = fingerprint(before)
    comparison = compare_calculations(before, after)
    assert comparison["annual"]["delta"]["class_count"] is None
    assert comparison["annual"]["delta"]["revenue_yuan"] == "0.000000"
    assert all(
        item["delta"]["class_count"] is None
        for name in ("quarters", "tracks", "campuses", "rows")
        for item in comparison[name]
    )
    assert fingerprint(before) == frozen


def _add_scope(inputs: dict[str, Any]) -> str:
    template = inputs["combinations"][0]
    identifier = "new-campus-new-product"
    combo = {
        **deepcopy(template),
        "id": identifier,
        "product": "新产品",
        "campus": "新校区",
        "entry": False,
        "entry_override": True,
        "entry_students": 3,
        "source_entry_weight": 0,
        "source_new_by_season": {season: 0 for season in SEASONS},
        "opening_carryover_yuan": "0",
        "added": True,
        "reason": "新校区新产品计划",
    }
    inputs["combinations"].append(combo)
    inputs["class_sizes"].append(
        {"track": combo["track"], "product": combo["product"], "minimum": 2, "planned_class_size": 12}
    )
    for season in SEASONS:
        inputs["rates"].append(
            {
                "id": "new-rate-" + season,
                "season": season,
                **{field: combo[field] for field in ("track", "product", "grade", "campus")},
                "renewal_ppm": 800000,
                "share_ppm": 200000,
            }
        )
        finance = next(
            row
            for row in inputs["finance"]
            if row["combination_id"] == template["id"] and row["season"] == season
        )
        inputs["finance"].append({**finance, "id": "new-finance-" + season, "combination_id": identifier})
        inputs["plans"].append({"combination_id": identifier, "season": season, "students": 0})
    return identifier


def test_new_business_is_additive_complete_and_has_no_fake_history_or_carryover() -> None:
    original = _plan_inputs()
    inputs = deepcopy(original)
    identifier = _add_scope(inputs)
    validate_plan_inputs(inputs, original)
    result = calculate_plan(inputs)
    added = [row for row in result["rows"] if row["combination_id"] == identifier]
    assert len(added) == 4
    assert added[0]["new_students"] == 3
    assert added[0]["old_students"] == 0
    assert result["complete"] is True
    inputs["combinations"][-1]["source_entry_weight"] = 1
    with pytest.raises(NextYearError, match="伪造历史"):
        validate_plan_inputs(inputs, original)


def test_existing_business_cannot_be_deleted_or_have_a_source_field_replaced() -> None:
    original = _plan_inputs()
    changed = deepcopy(original)
    changed["combinations"][0]["source_entry_weight"] += 1
    with pytest.raises(NextYearError):
        validate_plan_inputs(changed, original)


def test_target_year_only_advances_an_existing_true_source_year() -> None:
    baseline, forecast = _forecast_inputs()
    baseline["fiscal_year"] = 2028
    result = prepare_inputs(baseline, forecast, 2029)
    assert result["fiscal_year"] == 2029
    calculated = calculate_plan(result)
    assert calculated["fiscal_year"] == 2029
    assert len(calculated["rows"]) > 0
    with pytest.raises(NextYearError, match="下一财年"):
        prepare_inputs(baseline, forecast, 2028)


def test_impact_delta_reconciles_to_each_aggregate_and_changed_rows() -> None:
    inputs = _plan_inputs()
    before = calculate_plan(inputs)
    changed = deepcopy(inputs)
    changed["entry_total"] = 30
    changed["class_sizes"][0]["minimum"] = 12
    after = calculate_plan(changed)
    immutable = fingerprint((before, after))
    diff = compare_calculations(before, after)
    total = Decimal(diff["annual"]["delta"]["revenue_yuan"])
    assert total == Decimal(after["annual"]["revenue_yuan"]) - Decimal(before["annual"]["revenue_yuan"])
    assert total == sum(Decimal(row["delta"]["revenue_yuan"]) for row in diff["quarters"])
    assert total == sum(Decimal(row["delta"]["revenue_yuan"]) for row in diff["rows"])
    assert (
        diff["annual"]["delta"]["class_count"]
        == after["annual"]["class_count"] - before["annual"]["class_count"]
    )
    assert fingerprint((before, after)) == immutable


def test_balance_requires_a_manually_specified_target() -> None:
    inputs = _plan_inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    with pytest.raises(NextYearError, match="手动填写经营目标"):
        balance_plan(inputs)


def test_legacy_uncalculated_class_count_is_not_zero_in_an_impact_comparison() -> None:
    inputs = _plan_inputs()
    before = calculate_plan(inputs)
    before["annual"].pop("class_count")
    inputs["class_sizes"][0]["planned_class_size"] = 12
    after = calculate_plan(inputs)
    assert compare_calculations(before, after)["annual"]["delta"]["class_count"] is None


def test_real_optimizer_returns_a_pure_preview_and_preserves_manual_locks() -> None:
    inputs = _plan_inputs()
    baseline = calculate_plan(inputs)
    inputs["target_revenue_yuan"] = baseline["annual"]["revenue_yuan"]
    inputs["track_targets"] = {"智慧空间": inputs["target_revenue_yuan"]}
    inputs["combinations"][0].update(entry_students=10, entry_locked=True)
    inputs["rates"][0]["locked"] = True
    before = fingerprint(inputs)
    result = balance_plan(inputs, {"maximum_seconds": 2})
    assert result["status"] == "OPTIMAL"
    proposed = result["proposed_inputs"]
    assert proposed is not None
    assert proposed["entry_total"] == inputs["entry_total"]
    assert proposed["combinations"][0]["entry_students"] == 10
    assert proposed["rates"][0] == inputs["rates"][0]
    assert result["calculation"]["annual"]["revenue_yuan"] == baseline["annual"]["revenue_yuan"]
    assert fingerprint(inputs) == before


@pytest.mark.parametrize("seconds", [1, 12])
def test_interactive_solver_reserves_time_for_the_full_kernel_fallback(
    monkeypatch: pytest.MonkeyPatch, seconds: int
) -> None:
    from packages.next_year import planning

    inputs = _plan_inputs()
    inputs["target_revenue_yuan"] = calculate_plan(inputs)["annual"]["revenue_yuan"]
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    observed: dict[str, float] = {}

    def exact(
        context: BudgetEvaluationContext, *, maximum_solver_seconds: float, **kwargs: object
    ) -> OptimizationOutcome:
        observed["exact_budget"] = maximum_solver_seconds
        return OptimizationOutcome(
            status=SolveStatus.INCOMPLETE,
            terminal_stage=OptimizationStage.ENTRY_ONLY,
            candidates=(),
            discount_unlock_required=False,
            attempts=(),
        )

    def bounded(
        context: BudgetEvaluationContext, *, deadline: float
    ) -> tuple[CandidateEvaluation | None, int]:
        observed["fallback_deadline"] = deadline
        return None, 0

    monkeypatch.setattr(planning, "monotonic", lambda: 0.0)
    monkeypatch.setattr(planning, "optimize_budget", exact)
    monkeypatch.setattr(planning, "_bounded_preview", bounded)
    result = planning.balance_plan(inputs, {"maximum_seconds": seconds})
    assert observed["exact_budget"] == min(1.5, seconds / 6)
    assert observed["fallback_deadline"] == seconds
    assert result["proposed_inputs"] is None
    assert result["constraints"]["search_complete"] is False


def test_auto_balance_can_change_rates_within_bounds_and_reconcile_full_output() -> None:
    inputs = _plan_inputs()
    target = Decimal(calculate_plan(inputs)["annual"]["revenue_yuan"]) + 11000
    inputs["target_revenue_yuan"] = str(target)
    inputs["track_targets"] = {"智慧空间": str(target)}
    before = fingerprint(inputs)
    result = balance_plan(inputs, {"maximum_seconds": 5})
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    assert result["proposed_inputs"] is not None
    assert abs(Decimal(result["calculation"]["annual"]["gap_yuan"])) <= 10000
    assert any(
        old["renewal_ppm"] != new["renewal_ppm"] or old["share_ppm"] != new["share_ppm"]
        for old, new in zip(inputs["rates"], result["proposed_inputs"]["rates"], strict=True)
    )
    assert fingerprint(inputs) == before


@pytest.mark.parametrize("direction", [-1, 1])
def test_sampled_warm_start_closes_a_changed_target_without_weakening_gates(
    monkeypatch: pytest.MonkeyPatch, direction: int
) -> None:
    from packages.next_year import planning

    inputs = _plan_inputs()
    target = Decimal(calculate_plan(inputs)["annual"]["revenue_yuan"]) + direction * 11000
    inputs["target_revenue_yuan"] = str(target)
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    immutable = fingerprint(inputs)
    monkeypatch.setattr(
        planning,
        "optimize_budget",
        lambda *args, **kwargs: OptimizationOutcome(
            status=SolveStatus.INCOMPLETE,
            terminal_stage=OptimizationStage.ENTRY_ONLY,
            candidates=(),
            discount_unlock_required=False,
            attempts=(),
        ),
    )
    result = balance_plan(inputs, {"maximum_seconds": 3})
    assert result["status"] == "FEASIBLE"
    assert result["proposed_inputs"] is not None
    assert abs(Decimal(result["calculation"]["annual"]["gap_yuan"])) <= 10000
    assert not result["calculation"]["issues"]
    assert result["proposed_inputs"]["rates"] != inputs["rates"]
    assert result["proposed_inputs"]["finance"] == inputs["finance"]
    assert result["constraints"]["search_complete"] is False
    assert fingerprint(inputs) == immutable


def test_locked_unachievable_target_does_not_force_fill_or_change_price() -> None:
    inputs = _plan_inputs()
    for row in inputs["rates"]:
        row["locked"] = True
    before = fingerprint(inputs)
    result = balance_plan(inputs, {"maximum_seconds": 2})
    assert result["proposed_inputs"] is None
    assert result["status"] in {"INFEASIBLE", "INCOMPLETE"}
    assert result["constraints"]["price_and_discount_fixed"] is True
    assert fingerprint(inputs) == before


def test_explicit_rate_bounds_replace_the_default_five_percentage_point_window() -> None:
    inputs = _plan_inputs()
    row = inputs["rates"][1]
    row.update(renewal_lower_ppm=600000, renewal_upper_ppm=1000000, share_upper_ppm=800000)
    context = _context(inputs, calculate_plan(inputs), BalanceOptions())
    decisions = {decision.stable_id: decision for decision in context.rate_decisions}
    renewal, share = decisions[row["id"] + "renewal_ppm"], decisions[row["id"] + "share_ppm"]
    assert (renewal.lower_bound_ppm, renewal.upper_bound_ppm) == (600000, 1000000)
    assert (share.lower_bound_ppm, share.upper_bound_ppm) == (150000, 800000)
    unchanged = inputs["rates"][2]
    default = decisions[unchanged["id"] + "renewal_ppm"]
    assert (default.lower_bound_ppm, default.upper_bound_ppm) == (750000, 850000)


def test_class_constraints_return_actionable_groups_without_relaxation_or_source_writes() -> None:
    inputs = _plan_inputs()
    inputs["class_sizes"][0]["minimum"] = 1000
    inputs["opening_sources"][0]["students"] = 0
    second = inputs["combinations"][1]["id"]
    for row in inputs["plans"]:
        if row["combination_id"] == second:
            row["students"] = 0
    for row in inputs["rates"]:
        row["locked"] = True
    before = fingerprint(inputs)
    result = balance_plan(inputs, {"maximum_seconds": 1})
    assert result["proposed_inputs"] is None
    diagnostics = result["constraints"]["class_diagnostics"]
    assert {group["code"] for group in diagnostics} == {
        "BELOW_MINIMUM_CLASS_SIZE",
        "ENABLED_COMBINATION_ALL_SEASONS_ZERO",
    }
    assert sum(group["count"] for group in diagnostics) == len(result["constraints"]["class_issues"])
    assert all(0 < len(group["examples"]) <= 3 for group in diagnostics)
    assert all(group["action"] and group["editable_fields"] for group in diagnostics)
    assert (
        next(
            group["action"]
            for group in diagnostics
            if group["code"] == "ENABLED_COMBINATION_ALL_SEASONS_ZERO"
        )
        == "关闭不开展的SKU；需要开展时，先分配入口人次或填写季度新生人次。"
    )
    assert all(bound["source"] == "LOCKED" for bound in result["constraints"]["rate_bounds"])
    assert result["constraints"]["rate_step_limit_applies_to"] == "UNSPECIFIED_BOUNDS_ONLY"
    assert fingerprint(inputs) == before
