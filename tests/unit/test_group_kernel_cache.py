from dataclasses import replace
from unittest.mock import patch

from packages.optimization.cp_sat_business import (
    _calculate_group_kernel,
    _evaluate_group,
    _group_kernel_lookup,
)
from tests.unit.test_opening_optimization import context


def test_cached_group_matches_uncached_and_reuses_contribution_scenarios() -> None:
    ctx = context()
    states = [
        {
            **{row.rate_id: row.value_ppm for row in ctx.rates},
            "opening": renewal,
            "20281-NEW_STUDENT_SHARE": share,
        }
        for renewal in (800000, 801000)
        for share in (0, 1000)
    ]
    expected = [
        _evaluate_group(ctx, combinations=ctx.combinations, entry_values={"a": 0}, rate_values=state)
        for state in states
    ]
    with patch(
        "packages.optimization.cp_sat_business._calculate_group_kernel", wraps=_calculate_group_kernel
    ) as kernel:
        lookup = _group_kernel_lookup(ctx, ctx.combinations)
        actual = [
            _evaluate_group(
                ctx,
                combinations=ctx.combinations,
                entry_values={"a": 0},
                rate_values=state,
                kernel_lookup=lookup,
            )
            for state in states
        ]
        assert actual == expected
        assert kernel.call_count == 4  # Four unique decisions, not sixteen contribution evaluations.


def test_new_context_gets_new_cache_and_entry_values_are_part_of_key() -> None:
    ctx = context()
    entries = (("a", 0),)
    rates = tuple(sorted((row.rate_id, row.value_ppm) for row in ctx.rates))
    original = _group_kernel_lookup(ctx, ctx.combinations)(entries, rates)
    changed = replace(
        ctx,
        revenue_line_templates=tuple(
            replace(row, list_price_yuan=row.list_price_yuan * 2) for row in ctx.revenue_line_templates
        ),
    )
    assert (
        _group_kernel_lookup(changed, changed.combinations)(entries, rates).recognized_annual
        == original.recognized_annual * 2
    )
    with patch(
        "packages.optimization.cp_sat_business._calculate_group_kernel", wraps=_calculate_group_kernel
    ) as kernel:
        lookup = _group_kernel_lookup(ctx, ctx.combinations)
        lookup(entries, rates)
        lookup((("a", 1),), rates)
        assert kernel.call_count == 2
