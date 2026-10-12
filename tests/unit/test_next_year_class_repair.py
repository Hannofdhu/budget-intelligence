"""New recruitment proposals are explicit, bounded, pure and not source actuals."""

from copy import deepcopy
from decimal import Decimal
from itertools import product
from typing import Any

import pytest

from packages.next_year.calculation import calculate_plan, validate_plan_inputs
from packages.next_year.class_repair import _stability_bounds, repair_class_plan
from packages.next_year.planning import _rate_bounds, balance_plan
from tests.unit.test_next_year_calculation import _id, _plan_inputs


def _small_scope() -> dict[str, Any]:
    inputs = _plan_inputs()
    inputs["entry_total"] = 100
    inputs["opening_sources"][0]["students"] = 2
    inputs["class_sizes"][0]["minimum"] = 20
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    inputs["combinations"][1]["opening_carryover_yuan"] = "1234.567890"
    return inputs


def _two_entries() -> dict[str, Any]:
    inputs = _small_scope()
    inputs["opening_sources"][0]["students"] = 0
    first, second = inputs["combinations"]
    first["source_entry_weight"] = 95
    second.update(entry_override=True, source_entry_weight=5)
    return inputs


def test_whole_plan_suggestion_clears_small_scopes_without_modifying_source_or_target() -> None:
    inputs = _small_scope()
    original = deepcopy(inputs)
    result = repair_class_plan(inputs)
    assert result["status"] == "READY"
    assert result["issue_count"]["before"] > 0 and result["issue_count"]["after"] == 0
    assert result["calculation"]["complete"]
    assert all(result["constraints"].values())
    assert result["proposed_inputs"]["target_revenue_yuan"] is None
    assert result["proposed_inputs"]["track_targets"] == inputs["track_targets"]
    assert result["proposed_inputs"]["combinations"] == inputs["combinations"]
    assert result["proposed_inputs"]["finance"] == inputs["finance"]
    assert result["proposed_inputs"]["opening_sources"] == inputs["opening_sources"]
    assert result["proposed_inputs"]["class_sizes"] == inputs["class_sizes"]
    assert inputs == original
    validate_plan_inputs(result["proposed_inputs"], inputs)
    assert calculate_plan(result["proposed_inputs"]) == result["calculation"]
    assert result["before_calculation"] == calculate_plan(inputs)
    assert not result["globally_minimal"] and result["requires_review"]
    actions = [row for row in result["actions"] if row["kind"] == "NEW_STUDENT_TARGET"]
    assert actions and all(row["plan_is_not_actual"] and row["manual_target_priority"] for row in actions)
    delta = Decimal(result["impact"]["annual"]["delta"]["revenue_yuan"])
    assert delta == sum(Decimal(row["delta"]["revenue_yuan"]) for row in result["impact"]["rows"])


def test_fixed_total_entry_reallocation_can_exceed_soft_twenty_percent_preference() -> None:
    inputs = _two_entries()
    result = repair_class_plan(inputs)
    assert result["status"] == "READY"
    allocation = {row["combination_id"]: row["students"] for row in result["calculation"]["entry_details"]}
    first, second = inputs["combinations"]
    assert allocation == {first["id"]: 80, second["id"]: 20}
    assert sum(allocation.values()) == inputs["entry_total"] == 100
    assert result["summary"]["entry_reallocated_students"] == 15
    assert any(row["combination_id"] == second["id"] for row in result["entry_soft_deviations"])
    assert all(row["enabled"] for row in result["proposed_inputs"]["combinations"])


def test_proposed_absolute_targets_explicitly_expand_implied_share_bounds() -> None:
    inputs = _small_scope()
    result = repair_class_plan(inputs)
    proposal = result["proposed_inputs"]
    original_rates = {row["id"]: row for row in inputs["rates"]}
    new_rates = {row["id"]: row for row in proposal["rates"]}
    actions = [row for row in result["actions"] if row["kind"] == "RATE_BOUNDARY"]
    assert any(row["field"] == "share_upper_ppm" and row["after"] > row["before"] for row in actions)
    assert all(row["share_ppm"] == original_rates[key]["share_ppm"] for key, row in new_rates.items())
    for action in actions:
        rate = new_rates[action["rate_id"]]
        assert action["after"] == rate[action["field"]]
        assert action["after"] % 100 == 0
    assert all(_rate_bounds(row, "share", 50_000)[1] <= 999_900 for row in proposal["rates"])


def test_locked_rate_is_not_bypassed_by_a_new_absolute_recruitment_plan() -> None:
    inputs = _small_scope()
    small = inputs["combinations"][1]
    for rate in inputs["rates"]:
        if rate["grade"] == small["grade"]:
            rate["locked"] = True
    result = repair_class_plan(inputs)
    assert result["status"] == "PARTIAL" and result["after_issues"]
    assert result["proposed_inputs"]["rates"] == inputs["rates"]
    assert all(
        row
        == next(
            old
            for old in inputs["plans"]
            if (old["combination_id"], old["season"]) == (row["combination_id"], row["season"])
        )
        for row in result["proposed_inputs"]["plans"]
        if row["combination_id"] == small["id"]
    )
    assert any(row["code"] == "RATE_LOCKED" for row in result["lock_conflicts"])
    assert all(result["constraints"].values())


def test_locked_small_entry_is_not_changed_or_replaced_by_manual_new_students() -> None:
    inputs = _two_entries()
    small = inputs["combinations"][1]
    small.update(entry_students=5, entry_locked=True)
    result = repair_class_plan(inputs)
    assert result["status"] == "PARTIAL"
    assert next(row for row in result["proposed_inputs"]["combinations"] if row["id"] == small["id"]) == small
    assert any(row["code"] == "ENTRY_LOCKED" for row in result["lock_conflicts"])
    assert result["proposed_inputs"]["entry_total"] == 100


def test_zero_old_seed_can_be_proposed_as_a_labelled_future_recruitment_target() -> None:
    inputs = _small_scope()
    inputs["opening_sources"][0]["students"] = 0
    for plan in inputs["plans"]:
        if plan["combination_id"] == inputs["combinations"][1]["id"]:
            plan["students"] = 0
    result = repair_class_plan(inputs)
    assert result["status"] == "READY"
    summer = next(
        row for row in result["calculation"]["rows"] if row["season"] == "暑假" and not row["entry"]
    )
    assert summer["old_students"] == 0 and summer["new_students"] == 20
    action = next(
        row for row in result["actions"] if row["kind"] == "NEW_STUDENT_TARGET" and row["season"] == "暑假"
    )
    assert action["plan_is_not_actual"] and "建议" in action["reason"]


def test_locked_new_target_is_preserved_and_minimum_necessary_retention_is_proposed() -> None:
    inputs = _small_scope()
    inputs["opening_sources"][0]["students"] = 25
    small = inputs["combinations"][1]
    for rate in inputs["rates"]:
        if rate["grade"] == small["grade"] and rate["season"] == "秋季":
            rate.update(renewal_ppm=500_000, renewal_lower_ppm=490_000, renewal_upper_ppm=510_000)
    locked = next(
        row for row in inputs["plans"] if row["combination_id"] == small["id"] and row["season"] == "秋季"
    )
    locked.update(students=0, locked=True, reason="业务已确认新生目标")
    result = repair_class_plan(inputs)
    assert result["status"] == "READY"
    assert (
        next(
            row
            for row in result["proposed_inputs"]["plans"]
            if (row["combination_id"], row["season"]) == (small["id"], "秋季")
        )
        == locked
    )
    assert any(
        row["kind"] == "RENEWAL_ADJUSTMENT" and row["after"] > row["before"] for row in result["actions"]
    )
    assert any(
        row["kind"] == "RATE_BOUNDARY" and row["field"] == "renewal_upper_ppm" for row in result["actions"]
    )
    assert all(result["constraints"].values())


def test_currently_passing_rows_receive_explicit_reserve_for_the_allowed_rate_range() -> None:
    inputs = _small_scope()
    inputs["opening_sources"][0]["students"] = 25
    inputs["class_sizes"][0]["minimum"] = 25
    assert not calculate_plan(inputs)["issues"]
    result = repair_class_plan(inputs)
    assert result["status"] == "READY" and result["adjustable_range_class_safe"]
    assert result["summary"]["stability_reserve_students"] > 0
    actions = [row for row in result["actions"] if row["kind"] == "NEW_STUDENT_TARGET"]
    assert any(row["before_total_students"] >= row["minimum"] for row in actions)
    assert any(row["stability_reserve_students"] > 0 for row in actions)
    proposal = result["proposed_inputs"]
    bounds, _ = _stability_bounds(proposal, result["calculation"])
    # Vary every quarter's rates together at all corners, including the exact
    # formal kernel's integer/last-remainder changes. The sufficient reserve
    # must not depend on the current snapshot's favourable rounding bonus.
    for renewal_side, share_side in product((0, 1), repeat=2):
        candidate = deepcopy(proposal)
        for rate in candidate["rates"]:
            rate["renewal_ppm"] = _rate_bounds(rate, "renewal", 50_000)[renewal_side]
            rate["share_ppm"] = _rate_bounds(rate, "share", 50_000)[share_side]
        calculation = calculate_plan(candidate)
        assert not calculation["issues"]
        for row in calculation["rows"]:
            lower, upper = bounds[row["combination_id"], row["season"]]
            assert lower <= row["total_students"] <= upper


def test_locked_target_retention_changes_have_explicit_bounds_not_a_moving_default_window() -> None:
    inputs = _small_scope()
    inputs["combinations"][1]["enabled"] = False
    inputs["combinations"][1]["opening_carryover_yuan"] = "0"
    inputs["class_sizes"][0]["minimum"] = 81
    entry = inputs["combinations"][0]
    locked = next(
        row for row in inputs["plans"] if row["combination_id"] == entry["id"] and row["season"] == "秋季"
    )
    locked.update(students=0, locked=True, reason="已确认的新生目标")
    result = repair_class_plan(inputs)
    assert result["status"] == "READY" and result["adjustable_range_class_safe"]
    rate = next(
        row
        for row in result["proposed_inputs"]["rates"]
        if row["grade"] == entry["grade"] and row["season"] == "秋季"
    )
    assert rate["renewal_ppm"] == 810_000
    # Raising the permitted floor is intentional and visible; the old upper
    # bound remains 85%, not a silently shifted 86% implicit window.
    assert _rate_bounds(rate, "renewal", 50_000) == (810_000, 850_000)
    action = next(
        row
        for row in result["actions"]
        if row.get("rate_id") == rate["id"] and row["field"] == "renewal_lower_ppm"
    )
    assert (action["before"], action["after"]) == (750_000, 810_000)
    assert (
        next(
            row
            for row in result["proposed_inputs"]["plans"]
            if row["combination_id"] == entry["id"] and row["season"] == "秋季"
        )
        == locked
    )


@pytest.mark.parametrize("difference", [20_000, -20_000])
def test_repaired_plan_still_allows_real_nontrivial_up_and_down_revenue_balancing(difference: int) -> None:
    inputs = _small_scope()
    inputs["opening_sources"][0]["students"] = 25
    inputs["class_sizes"][0]["minimum"] = 25
    for row in inputs["finance"]:
        row["price_yuan"] = "10600"
    repaired = repair_class_plan(inputs)
    proposal = repaired["proposed_inputs"]
    # This manual operating target exists only in a test copy. The class
    # proposal must neither set nor derive the business's revenue target.
    assert proposal["target_revenue_yuan"] is None
    draft = deepcopy(proposal)
    draft["target_revenue_yuan"] = str(
        Decimal(repaired["calculation"]["annual"]["revenue_yuan"]) + difference
    )
    result = balance_plan(draft, {"maximum_seconds": 2})
    assert result["status"] == "FEASIBLE" and result["proposed_inputs"]
    assert abs(Decimal(result["calculation"]["annual"]["gap_yuan"])) <= 10_000
    assert not result["calculation"]["issues"]
    assert all(
        row
        == next(
            after
            for after in result["proposed_inputs"]["plans"]
            if (after["combination_id"], after["season"]) == (row["combination_id"], row["season"])
        )
        for row in proposal["plans"]
        if row.get("locked")
    )


def test_group_rounding_can_resolve_an_earlier_lock_conflict_without_leaving_stale_blockers() -> None:
    inputs = _small_scope()
    inputs["entry_total"] = 0
    first, second = inputs["combinations"]
    old_first, old_second = first["id"], second["id"]
    first.update(entry=False, grade="八年级", product="B", source_entry_weight=0)
    second.update(product="A", opening_carryover_yuan="0")
    first["id"], second["id"] = _id(first), _id(second)
    old_ids = {old_first: first["id"], old_second: second["id"]}
    assert first["id"] < second["id"]
    for row in inputs["finance"] + inputs["plans"]:
        row["combination_id"] = old_ids[row["combination_id"]]
    for row in inputs["rates"]:
        is_first = row["grade"] == "七年级"
        row.update(
            grade="八年级",
            product="B" if is_first else "A",
            share_ppm=0,
            renewal_ppm=400_000 if is_first else 600_000,
        )
        if row["season"] == "暑假" and is_first:
            row["locked"] = True
    inputs["opening_sources"] = [
        {
            **{field: combo[field] for field in ("track", "product", "grade", "subject", "campus")},
            "grade": "七年级",
            "students": count,
            "target_id": combo["id"],
            "exclusion": None,
        }
        for combo, count in ((first, 4), (second, 3))
    ]
    inputs["class_sizes"] = [
        {"track": first["track"], "product": "B", "minimum": 2},
        {"track": first["track"], "product": "A", "minimum": 3},
    ]
    locked = next(
        row for row in inputs["plans"] if row["combination_id"] == second["id"] and row["season"] == "暑假"
    )
    locked.update(students=0, locked=True, reason="已确认的新生目标")
    result = repair_class_plan(inputs)
    assert result["status"] == "READY" and not result["after_issues"]
    assert not result["lock_conflicts"]
    # Current legality is not a proof of all allowed rates when the user lock
    # prevents an independent rounding-floor guard. Report that limit honestly.
    assert not result["adjustable_range_class_safe"]
    assert any(row["code"] == "RATE_LOCKED" for row in result["stability_unresolved"])
