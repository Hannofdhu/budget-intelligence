from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from itertools import product
from math import prod
from time import monotonic
from typing import TYPE_CHECKING

from packages.class_size.validation import (
    ClassSizeInvariantError,
    HeadcountInput,
    validate_class_sizes,
)
from packages.headcount.calculation import (
    CombinationInput,
    HeadcountInvariantError,
    SeasonHeadcount,
    calculate_headcounts,
)
from packages.optimization.cp_sat_adapter import (
    AllowedAssignmentsSpec,
    CpSatProblem,
    IntegerVariableSpec,
    LinearConstraintSpec,
    LinearExpressionSpec,
    ObjectiveTierSpec,
)
from packages.optimization.cp_sat_rates import compile_entry_and_rates_problem
from packages.optimization.engine import (
    CONTRIBUTION_PROFILES,
    RATE_STEP_PPM,
    EntryDecision,
    RateDecision,
    ScenarioProfile,
    apply_equal_percentage_point_water_level,
)
from packages.revenue.calculation import RevenueInvariantError, RevenueLineInput, calculate_revenue

if TYPE_CHECKING:
    from packages.optimization.evaluator import BudgetEvaluationContext, RevenueLineTemplate

MICRO_YUAN = Decimal(1_000_000)
TARGET_TOLERANCE_MICRO_YUAN = 10_000 * 1_000_000
COMPOSITE_TOLERANCE_PPM = 500
DIRECT_BUSINESS_MODEL_VERSION = "entry-and-rates-direct-business-v1"


class BusinessCompilationError(RuntimeError):
    pass


class BusinessCompilationTimeout(RuntimeError):
    pass


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise BusinessCompilationTimeout("business compilation deadline exceeded")


class BusinessCompilationIncomplete(RuntimeError):
    def __init__(self, *, row_count_lower_bound: int, maximum_group_table_rows: int) -> None:
        super().__init__("group state-table row limit exceeded")
        self.row_count_lower_bound = row_count_lower_bound
        self.maximum_group_table_rows = maximum_group_table_rows


class BusinessCompilationInfeasible(RuntimeError):
    def __init__(self, group_key: tuple[str, str]) -> None:
        super().__init__("a complete rounding group has no kernel-valid state")
        self.group_key = group_key


@dataclass(frozen=True)
class CompiledBusinessProblem:
    problem: CpSatProblem
    compiled_group_row_count: int
    group_outputs: tuple[tuple[tuple[str, str], tuple[tuple[str, str], ...]], ...] = ()


def reprofile_business_problem(
    compiled: CompiledBusinessProblem, profile: ScenarioProfile
) -> CompiledBusinessProblem:
    variables = [
        row for row in compiled.problem.variables if row.stable_id != "contribution-profile-deviation"
    ]
    constraints = [
        row
        for row in compiled.problem.constraints
        if row.stable_id not in {"contribution-positive", "contribution-negative"}
    ]
    groups = [(key, dict(outputs)) for key, outputs in compiled.group_outputs]
    objective = _add_contribution_objective(profile, groups, variables, constraints)
    return replace(
        compiled,
        problem=replace(
            compiled.problem,
            variables=tuple(variables),
            constraints=tuple(constraints),
            objective_tiers=(objective, *compiled.problem.objective_tiers[1:]),
        ),
    )


@dataclass(frozen=True)
class _GroupOutput:
    r00: int
    r10: int
    r01: int
    r11: int
    list_price_by_scenario_period: tuple[tuple[int, ...], ...]
    business_by_scenario_period: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class _KernelRevenue:
    recognized_annual: int
    list_price_by_period: tuple[int, ...]
    business_by_period: tuple[int, ...]
    validation_codes: tuple[str, ...]


KernelLookup = Callable[[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]], _KernelRevenue]


def _group_kernel_lookup(
    context: BudgetEvaluationContext, combinations: tuple[CombinationInput, ...]
) -> KernelLookup:
    # The closure owns exactly one context and rounding group. No cross-version cache.
    @lru_cache(maxsize=2048)
    def calculate(entries: tuple[tuple[str, int], ...], rates: tuple[tuple[str, int], ...]) -> _KernelRevenue:
        return _calculate_group_kernel(
            context, combinations=combinations, entry_values=dict(entries), rate_values=dict(rates)
        )

    return calculate


def compile_direct_business_problem(
    context: BudgetEvaluationContext,
    *,
    track_directions: dict[str, str],
    profile: ScenarioProfile,
    maximum_group_table_rows: int,
    deadline: float | None = None,
) -> CompiledBusinessProblem:
    _check_deadline(deadline)
    _validate_context(context)
    ordered_entries = tuple(sorted(context.entry_decisions))
    ordered_rates = tuple(sorted(context.rate_decisions))
    entry_variable_ids = tuple(f"entry:{index:06d}" for index in range(len(ordered_entries)))
    rate_variable_ids = tuple(f"rate:{index:06d}" for index in range(len(ordered_rates)))
    entry_variable_by_stable = {
        item.stable_id: variable_id
        for item, variable_id in zip(ordered_entries, entry_variable_ids, strict=True)
    }
    rate_variable_by_stable = {
        item.stable_id: variable_id
        for item, variable_id in zip(ordered_rates, rate_variable_ids, strict=True)
    }
    base = compile_entry_and_rates_problem(
        ordered_entries,
        ordered_rates,
        fixed_total=context.fixed_entry_total,
        track_directions=track_directions,
        entry_variable_ids=entry_variable_ids,
        rate_variable_ids=rate_variable_ids,
        forbidden_assignments=(),
    )
    variables = list(base.variables)
    constraints = list(base.constraints)
    tables = list(base.allowed_assignments)
    group_outputs: list[tuple[tuple[str, str], dict[str, str]]] = []
    compiled_rows = 0

    grouped: dict[tuple[str, str], list[CombinationInput]] = defaultdict(list)
    for item in context.combinations:
        grouped[(item.track, item.campus)].append(item)
    if not grouped:
        raise BusinessCompilationError("at least one rounding group is required")

    for group_index, group_key in enumerate(sorted(grouped)):
        combinations = tuple(sorted(grouped[group_key]))
        local_rate_ids = _rate_ids_for_combinations(context, combinations)
        local_entry_rules = tuple(
            item for item in ordered_entries if item.stable_id in {row.stable_id for row in combinations}
        )
        local_rate_rules = tuple(item for item in ordered_rates if item.stable_id in local_rate_ids)
        entry_states = _entry_states(
            local_entry_rules, deadline=deadline, maximum_states=maximum_group_table_rows
        )
        rate_states = _rate_states(
            all_rates=ordered_rates,
            local_rates=local_rate_rules,
            track_directions=track_directions,
            deadline=deadline,
            maximum_states=maximum_group_table_rows,
        )
        input_ids = (
            *(entry_variable_by_stable[item.stable_id] for item in local_entry_rules),
            *(rate_variable_by_stable[item.stable_id] for item in local_rate_rules),
        )
        output_ids = _group_output_ids(group_index)
        raw_rows: set[tuple[int, ...]] = set()
        output_by_values: dict[tuple[int, ...], _GroupOutput] = {}
        kernel_lookup = _group_kernel_lookup(context, combinations)
        for entry_state, rate_state in product(entry_states, rate_states):
            _check_deadline(deadline)
            compiled_rows += 1
            if compiled_rows > maximum_group_table_rows:
                raise BusinessCompilationIncomplete(
                    row_count_lower_bound=compiled_rows,
                    maximum_group_table_rows=maximum_group_table_rows,
                )
            output = _evaluate_group(
                context,
                combinations=combinations,
                entry_values=dict(entry_state),
                rate_values=dict(rate_state),
                kernel_lookup=kernel_lookup,
            )
            if output is None:
                continue
            input_values = (
                *(value for _, value in entry_state),
                *(dict(rate_state)[item.stable_id] for item in local_rate_rules),
            )
            prior = output_by_values.get(input_values)
            if prior is not None and prior != output:
                raise BusinessCompilationError("one local decision maps to multiple kernel outputs")
            output_by_values[input_values] = output
            raw_rows.add((*input_values, *_output_values(output)))
        if not raw_rows:
            raise BusinessCompilationInfeasible(group_key)

        ordered_rows = tuple(sorted(raw_rows))
        output_columns = tuple(zip(*(row[len(input_ids) :] for row in ordered_rows), strict=True))
        for output_id, column in zip(output_ids.values(), output_columns, strict=True):
            variables.append(IntegerVariableSpec(output_id, min(column), max(column)))
        tables.append(
            AllowedAssignmentsSpec(
                stable_id=f"kernel-group:{group_index:06d}",
                variable_ids=(*input_ids, *output_ids.values()),
                rows=ordered_rows,
            )
        )
        group_outputs.append((group_key, output_ids))

    _add_revenue_target_constraints(context, group_outputs, constraints)
    _add_composite_discount_constraints(context, group_outputs, constraints)
    contribution_objective = _add_contribution_objective(profile, group_outputs, variables, constraints)
    _check_deadline(deadline)
    return CompiledBusinessProblem(
        problem=CpSatProblem(
            variables=tuple(variables),
            constraints=tuple(constraints),
            objective_tiers=(contribution_objective, *base.objective_tiers),
            allowed_assignments=tuple(tables),
            stable_tiebreak_variable_ids=base.stable_tiebreak_variable_ids,
        ),
        compiled_group_row_count=compiled_rows,
        group_outputs=tuple((key, tuple(outputs.items())) for key, outputs in group_outputs),
    )


def _entry_states(
    entries: tuple[EntryDecision, ...],
    *,
    deadline: float | None = None,
    maximum_states: int | None = None,
) -> tuple[tuple[tuple[str, int], ...], ...]:
    ranges = [
        range(item.hard_lower_bound, item.hard_upper_bound + 1)
        if item.candidate_eligible
        else range(item.current_value, item.current_value + 1)
        for item in entries
    ]
    state_count = prod(len(values) for values in ranges)
    if maximum_states is not None and state_count > maximum_states:
        raise BusinessCompilationIncomplete(
            row_count_lower_bound=state_count, maximum_group_table_rows=maximum_states
        )
    states = []
    for values in product(*ranges):
        _check_deadline(deadline)
        states.append(tuple((item.stable_id, value) for item, value in zip(entries, values, strict=True)))
    return tuple(states)


def _rate_states(
    *,
    all_rates: tuple[RateDecision, ...],
    local_rates: tuple[RateDecision, ...],
    track_directions: dict[str, str],
    deadline: float | None = None,
    maximum_states: int | None = None,
) -> tuple[tuple[tuple[str, int], ...], ...]:
    groups = tuple(
        sorted(
            {
                (item.track, item.rate_type)
                for item in local_rates
                if _rate_is_adjustable(item, track_directions)
            }
        )
    )
    family_states: list[tuple[tuple[tuple[str, int], ...], ...]] = []
    for track, rate_type in groups:
        direction = track_directions[track]
        matching = [
            item
            for item in all_rates
            if (item.track, item.rate_type) == (track, rate_type)
            and _rate_is_adjustable(item, track_directions)
        ]
        maximum_delta = max(
            (
                item.upper_bound_ppm - item.current_value_ppm
                if direction == "UP"
                else item.current_value_ppm - item.lower_bound_ppm
            )
            for item in matching
        )
        local_ids = {
            item.stable_id for item in local_rates if (item.track, item.rate_type) == (track, rate_type)
        }
        projected: set[tuple[tuple[str, int], ...]] = set()
        for level in range(maximum_delta // RATE_STEP_PPM + 1):
            _check_deadline(deadline)
            water_values = apply_equal_percentage_point_water_level(
                all_rates,
                track=track,
                rate_type=rate_type,
                direction=direction,
                water_level_steps=level,
            )
            projected.add(tuple(sorted((key, value) for key, value in water_values if key in local_ids)))
            if maximum_states is not None and len(projected) > maximum_states:
                raise BusinessCompilationIncomplete(
                    row_count_lower_bound=len(projected), maximum_group_table_rows=maximum_states
                )
        family_states.append(tuple(sorted(projected)))

    # Families own disjoint rate IDs, so the product is the exact unique-state count.
    state_count = prod(len(family) for family in family_states)
    if maximum_states is not None and state_count > maximum_states:
        raise BusinessCompilationIncomplete(
            row_count_lower_bound=state_count, maximum_group_table_rows=maximum_states
        )

    states: set[tuple[tuple[str, int], ...]] = set()
    for family_values in product(*family_states):
        _check_deadline(deadline)
        values = {item.stable_id: item.current_value_ppm for item in local_rates}
        for family in family_values:
            values.update(family)
        states.add(tuple(sorted((item.stable_id, values[item.stable_id]) for item in local_rates)))
        if maximum_states is not None and len(states) > maximum_states:
            raise BusinessCompilationIncomplete(
                row_count_lower_bound=len(states), maximum_group_table_rows=maximum_states
            )
    return tuple(sorted(states))


def _evaluate_group(
    context: BudgetEvaluationContext,
    *,
    combinations: tuple[CombinationInput, ...],
    entry_values: dict[str, int],
    rate_values: dict[str, int],
    kernel_lookup: KernelLookup | None = None,
) -> _GroupOutput | None:
    baseline = {item.rate_id: item.value_ppm for item in context.rates}
    selected = {**baseline, **rate_values}
    renewal_only = {
        **baseline,
        **{key: value for key, value in rate_values.items() if _rate_type(context, key) == "RENEWAL_RATE"},
    }
    new_share_only = {
        **baseline,
        **{
            key: value
            for key, value in rate_values.items()
            if _rate_type(context, key) == "NEW_STUDENT_SHARE"
        },
    }
    try:
        results = tuple(
            kernel_lookup(tuple(sorted(entry_values.items())), tuple(sorted(values.items())))
            if kernel_lookup is not None
            else _calculate_group_kernel(
                context,
                combinations=combinations,
                entry_values=entry_values,
                rate_values=values,
            )
            for values in (baseline, renewal_only, new_share_only, selected)
        )
    except HeadcountInvariantError as exc:
        if exc.code in {"ROUNDING_FIXED_TOTAL_EXCEEDS_TARGET", "ROUNDING_TAIL_INVALID"}:
            return None
        raise BusinessCompilationError(f"headcount kernel rejected the context: {exc.code}") from exc
    except (ClassSizeInvariantError, RevenueInvariantError, KeyError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise BusinessCompilationError(f"group kernel rejected the context: {code}") from exc
    if any(item.validation_codes for item in results):
        return None
    r00, r10, r01, r11 = results
    return _GroupOutput(
        r00.recognized_annual,
        r10.recognized_annual,
        r01.recognized_annual,
        r11.recognized_annual,
        tuple(item.list_price_by_period for item in results),
        tuple(item.business_by_period for item in results),
    )


def _calculate_group_kernel(
    context: BudgetEvaluationContext,
    *,
    combinations: tuple[CombinationInput, ...],
    entry_values: dict[str, int],
    rate_values: dict[str, int],
) -> _KernelRevenue:
    combination_ids = {item.stable_id for item in combinations}
    local_combinations = [
        replace(item, entry_initial_value=entry_values.get(item.stable_id, 0)) for item in combinations
    ]
    local_rate_ids = _rate_ids_for_combinations(context, combinations)
    local_rates = [
        replace(item, value_ppm=rate_values[item.rate_id])
        for item in context.rates
        if item.rate_id in local_rate_ids
    ]
    local_plans = [item for item in context.plans if item.combination_stable_id in combination_ids]
    from packages.optimization.evaluator import candidate_opening_values

    opening = candidate_opening_values(context, rate_values)
    headcounts = calculate_headcounts(
        fiscal_year=context.fiscal_year,
        combinations=local_combinations,
        rates=local_rates,
        plans=local_plans,
        opening_old_students=(
            {key: value for key, value in opening.items() if key in combination_ids}
            if opening is not None
            else None
        ),
    )
    class_result = validate_class_sizes(
        headcounts=[
            HeadcountInput(
                result_id=f"{row.combination.stable_id}:{row.fiscal_season}",
                combination_stable_key=row.combination.stable_id,
                fiscal_season=row.fiscal_season,
                track=row.combination.track,
                product=row.combination.product,
                entry_attribute=row.combination.entry_attribute,
                grade=row.combination.grade,
                subject=row.combination.subject,
                campus=row.combination.campus,
                calculation_status=("CALCULATED" if row.integer_total_students is not None else "BLOCKED"),
                integer_total_students=row.integer_total_students,
            )
            for row in headcounts.rows
        ],
        parameters=list(context.class_size_parameters),
    )
    templates = {
        (item.combination_stable_key, item.fiscal_season): item
        for item in context.revenue_line_templates
        if item.combination_stable_key in combination_ids
    }
    revenue_lines = [
        _revenue_line(row, templates[(row.combination.stable_id, row.fiscal_season)])
        for row in headcounts.rows
    ]
    revenue = calculate_revenue(
        fiscal_year=context.fiscal_year,
        budget_tax_conversion_factor=context.budget_tax_conversion_factor,
        lines=revenue_lines,
        composite_targets=[],
    )
    validation_codes = tuple(
        sorted(
            {
                *(item.code for item in headcounts.validations),
                *(item.code for item in class_result.validations),
                *(item.code for item in revenue.validations),
            }
        )
    )
    aggregates = {
        (item.period_type, item.fiscal_season): item
        for item in revenue.aggregates
        if item.scope_level == "DEPARTMENT"
    }
    annual = aggregates[("ANNUAL", None)]
    seasons = tuple(context.fiscal_year * 10 + index for index in range(1, 5))
    return _KernelRevenue(
        recognized_annual=_micro(annual.recognized_revenue_yuan),
        list_price_by_period=(
            _micro(annual.list_price_revenue_yuan),
            *(_micro(aggregates[("SEASON", season)].list_price_revenue_yuan) for season in seasons),
        ),
        business_by_period=(
            _micro(annual.business_revenue_yuan),
            *(_micro(aggregates[("SEASON", season)].business_revenue_yuan) for season in seasons),
        ),
        validation_codes=validation_codes,
    )


def _revenue_line(row: SeasonHeadcount, template: RevenueLineTemplate) -> RevenueLineInput:
    combination = row.combination
    return RevenueLineInput(
        result_id=f"{combination.stable_id}:{row.fiscal_season}",
        combination_stable_key=combination.stable_id,
        fiscal_season=row.fiscal_season,
        track=combination.track,
        product=combination.product,
        entry_attribute=combination.entry_attribute,
        grade=combination.grade,
        subject=combination.subject,
        campus=combination.campus,
        integer_total_students=row.integer_total_students,
        list_price_yuan=template.list_price_yuan,
        detail_discount_rate_ppm=template.detail_discount_rate_ppm,
        current_season_recognition_rate_ppm=template.current_season_recognition_rate_ppm,
        opening_carryover_revenue_yuan=template.opening_carryover_revenue_yuan,
    )


def _group_output_ids(group_index: int) -> dict[str, str]:
    prefix = f"kernel:{group_index:06d}"
    result = {
        "r00": f"{prefix}:r00",
        "r10": f"{prefix}:r10",
        "r01": f"{prefix}:r01",
        "r11": f"{prefix}:r11",
    }
    for scenario_index in range(4):
        for period_index in range(5):
            result[f"list:{scenario_index}:{period_index}"] = f"{prefix}:list:{scenario_index}:{period_index}"
            result[f"business:{scenario_index}:{period_index}"] = (
                f"{prefix}:business:{scenario_index}:{period_index}"
            )
    return result


def _output_values(output: _GroupOutput) -> tuple[int, ...]:
    return (
        output.r00,
        output.r10,
        output.r01,
        output.r11,
        *(
            value
            for scenario_lists, scenario_business in zip(
                output.list_price_by_scenario_period,
                output.business_by_scenario_period,
                strict=True,
            )
            for pair in zip(scenario_lists, scenario_business, strict=True)
            for value in pair
        ),
    )


def _add_revenue_target_constraints(
    context: BudgetEvaluationContext,
    groups: list[tuple[tuple[str, str], dict[str, str]]],
    constraints: list[LinearConstraintSpec],
) -> None:
    for index, target in enumerate(sorted(context.revenue_targets, key=lambda item: item.target_id)):
        selected = [
            outputs
            for (track, _), outputs in groups
            if target.scope_level == "DEPARTMENT" or target.track == track
        ]
        target_micro = _micro(target.target_revenue_yuan)
        constraints.append(
            LinearConstraintSpec(
                f"revenue-target:{index:06d}",
                LinearExpressionSpec(tuple((item["r11"], 1) for item in selected)),
                lower_bound=target_micro - TARGET_TOLERANCE_MICRO_YUAN,
                upper_bound=target_micro + TARGET_TOLERANCE_MICRO_YUAN,
            )
        )


def _add_composite_discount_constraints(
    context: BudgetEvaluationContext,
    groups: list[tuple[tuple[str, str], dict[str, str]]],
    constraints: list[LinearConstraintSpec],
) -> None:
    for index, target in enumerate(
        sorted(context.composite_discount_targets, key=lambda item: item.target_id)
    ):
        if target.period_type == "ANNUAL":
            period_index = 0
        elif target.fiscal_season is not None:
            period_index = target.fiscal_season % 10
        else:
            raise BusinessCompilationError("season composite target is missing its fiscal season")
        selected = [
            outputs
            for (track, _), outputs in groups
            if target.scope_level == "DEPARTMENT" or target.track == track
        ]
        for scenario_index in range(4):
            list_terms = tuple((item[f"list:{scenario_index}:{period_index}"], 1) for item in selected)
            business_terms = tuple(
                (item[f"business:{scenario_index}:{period_index}"], 1) for item in selected
            )
            constraints.extend(
                (
                    LinearConstraintSpec(
                        f"composite-denominator:{index:06d}:{scenario_index}",
                        LinearExpressionSpec(list_terms),
                        lower_bound=1,
                    ),
                    LinearConstraintSpec(
                        f"composite-lower:{index:06d}:{scenario_index}",
                        LinearExpressionSpec(
                            (
                                *((key, 1_000_000) for key, _ in business_terms),
                                *(
                                    (
                                        key,
                                        -(target.target_rate_ppm - COMPOSITE_TOLERANCE_PPM),
                                    )
                                    for key, _ in list_terms
                                ),
                            )
                        ),
                        lower_bound=0,
                    ),
                    LinearConstraintSpec(
                        f"composite-upper:{index:06d}:{scenario_index}",
                        LinearExpressionSpec(
                            (
                                *((key, 1_000_000) for key, _ in business_terms),
                                *(
                                    (
                                        key,
                                        -(target.target_rate_ppm + COMPOSITE_TOLERANCE_PPM),
                                    )
                                    for key, _ in list_terms
                                ),
                            )
                        ),
                        upper_bound=0,
                    ),
                )
            )


def _add_contribution_objective(
    profile: ScenarioProfile,
    groups: list[tuple[tuple[str, str], dict[str, str]]],
    variables: list[IntegerVariableSpec],
    constraints: list[LinearConstraintSpec],
) -> ObjectiveTierSpec:
    retention_weight, new_weight = CONTRIBUTION_PROFILES[profile]
    terms: list[tuple[str, int]] = []
    for _, output in groups:
        terms.extend(
            (
                (output["r10"], new_weight + retention_weight),
                (output["r00"], -new_weight + retention_weight),
                (output["r11"], new_weight - retention_weight),
                (output["r01"], -new_weight - retention_weight),
            )
        )
    bounds = {item.stable_id: (item.lower_bound, item.upper_bound) for item in variables}
    maximum = sum(
        max(abs(bounds[key][0] * coefficient), abs(bounds[key][1] * coefficient))
        for key, coefficient in terms
    )
    deviation_id = "contribution-profile-deviation"
    variables.append(IntegerVariableSpec(deviation_id, 0, maximum))
    constraints.extend(
        (
            LinearConstraintSpec(
                "contribution-positive",
                LinearExpressionSpec(((deviation_id, 1), *((key, -value) for key, value in terms))),
                lower_bound=0,
            ),
            LinearConstraintSpec(
                "contribution-negative",
                LinearExpressionSpec(((deviation_id, 1), *terms)),
                lower_bound=0,
            ),
        )
    )
    return ObjectiveTierSpec(
        "contribution-profile-deviation",
        LinearExpressionSpec(((deviation_id, 1),)),
    )


def _rate_type(context: BudgetEvaluationContext, rate_id: str) -> str:
    return next(item.rate_type for item in context.rates if item.rate_id == rate_id)


def _rate_is_adjustable(item: RateDecision, track_directions: dict[str, str]) -> bool:
    return (
        item.track in track_directions
        and not item.locked
        and item.boundary_confirmed
        and not item.quantitative_new_student
    )


def _micro(value: Decimal) -> int:
    return int(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) * MICRO_YUAN)


def _rate_ids_for_combinations(
    context: BudgetEvaluationContext,
    combinations: tuple[CombinationInput, ...],
) -> set[str]:
    keys = {(item.track, item.product, item.grade, item.campus) for item in combinations}
    result = {
        item.rate_id for item in context.rates if (item.track, item.product, item.grade, item.campus) in keys
    }
    if context.opening_inputs is not None:
        combination_ids = {item.stable_id for item in combinations}
        result.update(
            item.renewal_rate_id
            for item in context.opening_inputs
            if item.combination_stable_id in combination_ids and item.renewal_rate_id is not None
        )
    return result


def _validate_context(context: BudgetEvaluationContext) -> None:
    combination_ids = [item.stable_id for item in context.combinations]
    rate_input_ids = [item.rate_id for item in context.rates]
    rate_decision_ids = [item.stable_id for item in context.rate_decisions]
    if len(combination_ids) != len(set(combination_ids)):
        raise BusinessCompilationError("combination stable IDs must be unique")
    if len(rate_input_ids) != len(set(rate_input_ids)):
        raise BusinessCompilationError("rate input IDs must be unique")
    if set(rate_input_ids) != set(rate_decision_ids):
        raise BusinessCompilationError("rate inputs and decisions must have the same IDs")
    expected_template_keys = {
        (combination_id, context.fiscal_year * 10 + season)
        for combination_id in combination_ids
        for season in range(1, 5)
    }
    template_keys = [
        (item.combination_stable_key, item.fiscal_season) for item in context.revenue_line_templates
    ]
    if len(template_keys) != len(set(template_keys)) or set(template_keys) != expected_template_keys:
        raise BusinessCompilationError("revenue templates must uniquely cover four seasons per combination")
