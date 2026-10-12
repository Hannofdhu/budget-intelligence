from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from itertools import product
from time import monotonic
from typing import TYPE_CHECKING, cast

from packages.class_size.validation import ClassSizeInvariantError, HeadcountInput, validate_class_sizes
from packages.headcount.calculation import CombinationInput, HeadcountInvariantError, calculate_headcounts
from packages.optimization.cp_sat_adapter import (
    AllowedAssignmentsSpec,
    CpSatProblem,
    DeterministicCpSatAdapter,
    DivisionConstraintSpec,
    IntegerVariableSpec,
    LinearConstraintSpec,
    LinearExpressionSpec,
    MultiplicationConstraintSpec,
    ObjectiveTierSpec,
)
from packages.optimization.cp_sat_business import (
    BusinessCompilationError,
    BusinessCompilationIncomplete,
    BusinessCompilationInfeasible,
    BusinessCompilationTimeout,
    _check_deadline,
    _entry_states,
    _rate_ids_for_combinations,
    _rate_states,
    _rate_type,
    _validate_context,
)
from packages.optimization.cp_sat_rates import compile_entry_and_rates_problem
from packages.optimization.engine import (
    CONTRIBUTION_PROFILES,
    DISCOUNT_STEP_PPM,
    CandidateEvaluation,
    DecisionVector,
    DiscountDecision,
    OptimizationStage,
    ScenarioProfile,
    SolveAttempt,
    SolveStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from packages.optimization.evaluator import BudgetEvaluationContext

MICRO_YUAN = Decimal(1_000_000)
NANO_FACTOR = Decimal(1_000_000_000)
TARGET_TOLERANCE_MICRO_YUAN = 10_000 * 1_000_000
COMPOSITE_TOLERANCE_PPM = 500
DISCOUNT_BUSINESS_MODEL_VERSION = "entry-rates-and-discount-direct-business-v1"


class _InvalidLocalState(RuntimeError):
    pass


@dataclass(frozen=True)
class CompiledDiscountProblem:
    problem: CpSatProblem
    compiled_group_row_count: int


@dataclass(frozen=True)
class _LineOutputs:
    headcount_id: str
    business_id: str
    current_id: str
    cross_id: str
    list_price_micro_yuan: int


class CpSatEntryRatesAndDiscountSolver:
    def __init__(
        self,
        *,
        maximum_time_seconds: float = 300,
        maximum_group_table_rows: int = 100_000,
        random_seed: int = 27,
    ) -> None:
        if maximum_time_seconds <= 0:
            raise ValueError("maximum_time_seconds must be positive")
        if maximum_group_table_rows <= 0:
            raise ValueError("maximum_group_table_rows must be positive")
        self.maximum_time_seconds = maximum_time_seconds
        self.maximum_group_table_rows = maximum_group_table_rows
        self.random_seed = random_seed

    def solve(
        self,
        *,
        context: BudgetEvaluationContext,
        track_directions: dict[str, str],
        profile: ScenarioProfile,
        evaluator: Callable[[DecisionVector], CandidateEvaluation],
    ) -> SolveAttempt:
        deadline = monotonic() + self.maximum_time_seconds
        try:
            compiled = compile_direct_discount_problem(
                context,
                track_directions=track_directions,
                profile=profile,
                maximum_group_table_rows=self.maximum_group_table_rows,
                deadline=deadline,
            )
        except BusinessCompilationTimeout:
            return _attempt(
                profile, SolveStatus.INCOMPLETE, proof_code="CP_SAT_BUSINESS_COMPILATION_TIME_LIMIT"
            )
        except BusinessCompilationIncomplete as exc:
            return _attempt(
                profile,
                SolveStatus.INCOMPLETE,
                proof_code="CP_SAT_GROUP_TABLE_LIMIT_EXCEEDED",
                proof_summary={
                    "model_version": DISCOUNT_BUSINESS_MODEL_VERSION,
                    "row_count_lower_bound": exc.row_count_lower_bound,
                    "maximum_group_table_rows": exc.maximum_group_table_rows,
                },
            )
        except BusinessCompilationInfeasible as exc:
            return _attempt(
                profile,
                SolveStatus.INFEASIBLE,
                proof_code="CP_SAT_DISCOUNT_GROUP_KERNEL_SPACE_INFEASIBLE",
                proof_summary={
                    "model_version": DISCOUNT_BUSINESS_MODEL_VERSION,
                    "group_key": list(exc.group_key),
                },
            )
        except BusinessCompilationError as exc:
            return _attempt(
                profile,
                SolveStatus.FAILED,
                proof_code="CP_SAT_DISCOUNT_COMPILATION_FAILED",
                proof_summary={"safe_error": str(exc)},
            )

        remaining = deadline - monotonic()
        if remaining <= 0:
            return _attempt(
                profile, SolveStatus.INCOMPLETE, proof_code="CP_SAT_BUSINESS_COMPILATION_TIME_LIMIT"
            )
        solution = DeterministicCpSatAdapter(
            random_seed=self.random_seed,
            maximum_time_seconds=remaining,
        ).solve(compiled.problem)
        summary: dict[str, object] = {
            "model_version": DISCOUNT_BUSINESS_MODEL_VERSION,
            "compiled_group_row_count": compiled.compiled_group_row_count,
            "solver_status": solution.solver_status,
        }
        if solution.status == SolveStatus.INFEASIBLE:
            return _attempt(
                profile,
                SolveStatus.INFEASIBLE,
                proof_code="CP_SAT_DIRECT_DISCOUNT_MODEL_INFEASIBLE",
                proof_summary=summary,
            )
        if solution.status != SolveStatus.OPTIMAL:
            return _attempt(
                profile,
                solution.status,
                proof_code=solution.reason_code,
                proof_summary={**summary, "model_validation_error": solution.model_validation_error},
            )

        values = dict(solution.values)
        decision = DecisionVector(
            entry_values=tuple(
                (item.stable_id, values[f"entry:{index:06d}"])
                for index, item in enumerate(sorted(context.entry_decisions))
            ),
            rate_values=tuple(
                (item.stable_id, values[f"rate:{index:06d}"])
                for index, item in enumerate(sorted(context.rate_decisions))
            ),
            discount_values=tuple(
                (item.stable_id, values[f"discount:{index:06d}"])
                for index, item in enumerate(sorted(context.discount_decisions))
            ),
        )
        candidate = evaluator(decision)
        if not candidate.targets_closed:
            return _attempt(
                profile,
                SolveStatus.FAILED,
                evaluated_state_count=1,
                proof_code="CP_SAT_DISCOUNT_MODEL_RECOMPUTATION_MISMATCH",
                proof_summary={
                    **summary,
                    "validation_codes": list(candidate.validation_codes),
                    "target_variances": [
                        {"scope": scope, "variance_yuan": format(value, "f")}
                        for scope, value in candidate.target_variances_yuan
                    ],
                },
            )
        return _attempt(
            profile,
            SolveStatus.OPTIMAL,
            candidates=(replace(candidate, scenario_types=(profile,)),),
            evaluated_state_count=1,
            proof_summary={**summary, "solver_objectives": dict(solution.objective_values)},
        )


def compile_direct_discount_problem(
    context: BudgetEvaluationContext,
    *,
    track_directions: dict[str, str],
    profile: ScenarioProfile,
    maximum_group_table_rows: int,
    deadline: float | None = None,
) -> CompiledDiscountProblem:
    _check_deadline(deadline)
    _validate_context(context)
    _validate_discount_context(context)
    ordered_entries = tuple(sorted(context.entry_decisions))
    ordered_rates = tuple(sorted(context.rate_decisions))
    ordered_discounts = tuple(sorted(context.discount_decisions))
    entry_ids = tuple(f"entry:{index:06d}" for index in range(len(ordered_entries)))
    rate_ids = tuple(f"rate:{index:06d}" for index in range(len(ordered_rates)))
    entry_by_stable = dict(zip((item.stable_id for item in ordered_entries), entry_ids, strict=True))
    rate_by_stable = dict(zip((item.stable_id for item in ordered_rates), rate_ids, strict=True))
    base = compile_entry_and_rates_problem(
        ordered_entries,
        ordered_rates,
        fixed_total=context.fixed_entry_total,
        track_directions=track_directions,
        entry_variable_ids=entry_ids,
        rate_variable_ids=rate_ids,
        forbidden_assignments=(),
    )
    variables = list(base.variables)
    constraints = list(base.constraints)
    tables = list(base.allowed_assignments)
    multiplications = list(base.multiplication_constraints)
    divisions = list(base.division_constraints)
    headcount_ids: dict[tuple[int, str, int], str] = {}
    compiled_rows = 0

    grouped: dict[tuple[str, str], list[CombinationInput]] = defaultdict(list)
    for item in context.combinations:
        grouped[(item.track, item.campus)].append(item)
    for group_index, group_key in enumerate(sorted(grouped)):
        combinations = tuple(sorted(grouped[group_key]))
        combination_ids = {item.stable_id for item in combinations}
        local_entries = tuple(item for item in ordered_entries if item.stable_id in combination_ids)
        local_rate_stable_ids = _rate_ids_for_combinations(context, combinations)
        local_rates = tuple(item for item in ordered_rates if item.stable_id in local_rate_stable_ids)
        input_ids = (
            *(entry_by_stable[item.stable_id] for item in local_entries),
            *(rate_by_stable[item.stable_id] for item in local_rates),
        )
        line_keys = tuple(
            (combination.stable_id, context.fiscal_year * 10 + season)
            for combination in combinations
            for season in range(1, 5)
        )
        output_ids = tuple(
            f"headcount:{group_index:06d}:{scenario}:{line_index:06d}"
            for scenario in range(4)
            for line_index in range(len(line_keys))
        )
        raw_rows: set[tuple[int, ...]] = set()
        for entry_state, rate_state in product(
            _entry_states(local_entries, deadline=deadline, maximum_states=maximum_group_table_rows),
            _rate_states(
                all_rates=ordered_rates,
                local_rates=local_rates,
                track_directions=track_directions,
                deadline=deadline,
                maximum_states=maximum_group_table_rows,
            ),
        ):
            _check_deadline(deadline)
            compiled_rows += 1
            if compiled_rows > maximum_group_table_rows:
                raise BusinessCompilationIncomplete(
                    row_count_lower_bound=compiled_rows,
                    maximum_group_table_rows=maximum_group_table_rows,
                )
            outputs = _evaluate_group_headcounts(
                context,
                combinations=combinations,
                entry_values=dict(entry_state),
                rate_values=dict(rate_state),
            )
            if outputs is None:
                continue
            input_values = (
                *(value for _, value in entry_state),
                *(dict(rate_state)[item.stable_id] for item in local_rates),
            )
            raw_rows.add((*input_values, *(value for scenario in outputs for value in scenario)))
        if not raw_rows:
            raise BusinessCompilationInfeasible(group_key)
        rows = tuple(sorted(raw_rows))
        columns = tuple(zip(*(row[len(input_ids) :] for row in rows), strict=True))
        for output_id, column in zip(output_ids, columns, strict=True):
            variables.append(IntegerVariableSpec(output_id, min(column), max(column)))
        tables.append(
            AllowedAssignmentsSpec(
                f"discount-kernel-group:{group_index:06d}",
                (*input_ids, *output_ids),
                rows,
            )
        )
        for scenario in range(4):
            for line_index, (combination_id, season) in enumerate(line_keys):
                headcount_ids[(scenario, combination_id, season)] = output_ids[
                    scenario * len(line_keys) + line_index
                ]

    discount_variable_by_key, discount_objectives = _add_discount_variables(
        ordered_discounts,
        track_directions=track_directions,
        variables=variables,
        constraints=constraints,
    )
    line_outputs = _add_line_revenue_constraints(
        context,
        headcount_ids=headcount_ids,
        discount_variable_by_key=discount_variable_by_key,
        variables=variables,
        multiplications=multiplications,
        divisions=divisions,
    )
    recognized = _recognized_expressions(context, line_outputs)
    _add_target_constraints(context, recognized, constraints)
    _add_composite_constraints(context, line_outputs, constraints)
    contribution = _contribution_objective(profile, recognized, variables, constraints)
    return CompiledDiscountProblem(
        CpSatProblem(
            variables=tuple(variables),
            constraints=tuple(constraints),
            objective_tiers=(
                contribution,
                *base.objective_tiers,
                *discount_objectives,
            ),
            allowed_assignments=tuple(tables),
            multiplication_constraints=tuple(multiplications),
            division_constraints=tuple(divisions),
            stable_tiebreak_variable_ids=(
                *entry_ids,
                *rate_ids,
                *(discount_variable_by_key[item.stable_id] for item in ordered_discounts),
            ),
        ),
        compiled_rows,
    )


def _evaluate_group_headcounts(
    context: BudgetEvaluationContext,
    *,
    combinations: tuple[CombinationInput, ...],
    entry_values: dict[str, int],
    rate_values: dict[str, int],
) -> tuple[tuple[int, ...], ...] | None:
    baseline = {item.rate_id: item.value_ppm for item in context.rates}
    selected = {**baseline, **rate_values}
    renewal = {
        **baseline,
        **{key: value for key, value in rate_values.items() if _rate_type(context, key) == "RENEWAL_RATE"},
    }
    new_share = {
        **baseline,
        **{
            key: value
            for key, value in rate_values.items()
            if _rate_type(context, key) == "NEW_STUDENT_SHARE"
        },
    }
    try:
        return tuple(
            _one_headcount_scenario(
                context,
                combinations=combinations,
                entry_values=entry_values,
                rate_values=values,
                final_scenario=scenario == 3,
            )
            for scenario, values in enumerate((baseline, renewal, new_share, selected))
        )
    except HeadcountInvariantError as exc:
        if exc.code in {"ROUNDING_FIXED_TOTAL_EXCEEDS_TARGET", "ROUNDING_TAIL_INVALID"}:
            return None
        raise BusinessCompilationError(f"headcount kernel rejected the context: {exc.code}") from exc
    except ClassSizeInvariantError as exc:
        raise BusinessCompilationError(f"class-size kernel rejected the context: {exc.code}") from exc
    except _InvalidLocalState:
        return None


def _one_headcount_scenario(
    context: BudgetEvaluationContext,
    *,
    combinations: tuple[CombinationInput, ...],
    entry_values: dict[str, int],
    rate_values: dict[str, int],
    final_scenario: bool = True,
) -> tuple[int, ...]:
    combination_ids = {item.stable_id for item in combinations}
    from packages.optimization.evaluator import attribution_blocking_codes, candidate_opening_values

    opening = candidate_opening_values(context, rate_values)
    rows = calculate_headcounts(
        fiscal_year=context.fiscal_year,
        combinations=[
            replace(item, entry_initial_value=entry_values[item.stable_id]) for item in combinations
        ],
        rates=[
            replace(item, value_ppm=rate_values[item.rate_id])
            for item in context.rates
            if item.rate_id in _rate_ids_for_combinations(context, combinations)
        ],
        plans=[item for item in context.plans if item.combination_stable_id in combination_ids],
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
            for row in rows.rows
        ],
        parameters=list(context.class_size_parameters),
    )
    codes = (
        *tuple(item.code for item in rows.validations),
        *tuple(item.code for item in class_result.validations),
    )
    blocking_codes = codes if final_scenario else attribution_blocking_codes(codes)
    if blocking_codes:
        raise _InvalidLocalState
    by_key = {(row.combination.stable_id, row.fiscal_season): row.integer_total_students for row in rows.rows}
    expected = {
        (combination.stable_id, context.fiscal_year * 10 + season)
        for combination in combinations
        for season in range(1, 5)
    }
    if (
        len(rows.rows) != len(expected)
        or set(by_key) != expected
        or any(value is None for value in by_key.values())
    ):
        raise BusinessCompilationError("headcount kernel returned an unavailable integer result")
    return tuple(
        cast(int, by_key[(combination.stable_id, context.fiscal_year * 10 + season)])
        for combination in combinations
        for season in range(1, 5)
    )


def _add_discount_variables(
    discounts: tuple[DiscountDecision, ...],
    *,
    track_directions: dict[str, str],
    variables: list[IntegerVariableSpec],
    constraints: list[LinearConstraintSpec],
) -> tuple[dict[str, str], tuple[ObjectiveTierSpec, ...]]:
    variable_by_stable: dict[str, str] = {}
    adjustment_terms: list[tuple[str, int]] = []
    max_adjustment = 0
    for index, item in enumerate(discounts):
        direction = track_directions.get(item.track)
        if item.locked:
            lower = upper = item.current_value_ppm
        elif direction == "UP":
            lower, upper = item.current_value_ppm, item.upper_bound_ppm
        elif direction == "DOWN":
            lower, upper = item.lower_bound_ppm, item.current_value_ppm
        elif direction is None:
            lower, upper = item.lower_bound_ppm, item.upper_bound_ppm
        else:
            raise BusinessCompilationError("track discount direction must be UP or DOWN")
        variable_id = f"discount:{index:06d}"
        index_id = f"discount-index:{index:06d}"
        adjustment_id = f"discount-adjustment:{index:06d}"
        maximum_steps = (upper - lower) // DISCOUNT_STEP_PPM
        maximum_adjustment = max(
            abs(lower - item.current_value_ppm),
            abs(upper - item.current_value_ppm),
        )
        variables.extend(
            (
                IntegerVariableSpec(variable_id, lower, upper),
                IntegerVariableSpec(index_id, 0, maximum_steps),
                IntegerVariableSpec(adjustment_id, 0, maximum_adjustment),
            )
        )
        constraints.extend(
            (
                LinearConstraintSpec(
                    f"discount-step-link:{index:06d}",
                    LinearExpressionSpec(((variable_id, 1), (index_id, -DISCOUNT_STEP_PPM))),
                    lower_bound=lower,
                    upper_bound=lower,
                ),
                LinearConstraintSpec(
                    f"discount-adjustment-positive:{index:06d}",
                    LinearExpressionSpec(((adjustment_id, 1), (variable_id, -1))),
                    lower_bound=-item.current_value_ppm,
                ),
                LinearConstraintSpec(
                    f"discount-adjustment-negative:{index:06d}",
                    LinearExpressionSpec(((adjustment_id, 1), (variable_id, 1))),
                    lower_bound=item.current_value_ppm,
                ),
            )
        )
        variable_by_stable[item.stable_id] = variable_id
        adjustment_terms.append((adjustment_id, 1))
        max_adjustment = max(max_adjustment, maximum_adjustment)

    max_id = "maximum-discount-adjustment"
    variables.append(IntegerVariableSpec(max_id, 0, max_adjustment))
    for index, _ in enumerate(discounts):
        constraints.append(
            LinearConstraintSpec(
                f"maximum-discount-adjustment:{index:06d}",
                LinearExpressionSpec(((max_id, 1), (f"discount-adjustment:{index:06d}", -1))),
                lower_bound=0,
            )
        )
    return variable_by_stable, (
        ObjectiveTierSpec("maximum-discount-adjustment", LinearExpressionSpec(((max_id, 1),))),
        ObjectiveTierSpec("total-discount-adjustment", LinearExpressionSpec(tuple(adjustment_terms))),
    )


def _add_line_revenue_constraints(
    context: BudgetEvaluationContext,
    *,
    headcount_ids: dict[tuple[int, str, int], str],
    discount_variable_by_key: dict[str, str],
    variables: list[IntegerVariableSpec],
    multiplications: list[MultiplicationConstraintSpec],
    divisions: list[DivisionConstraintSpec],
) -> dict[tuple[int, str, int], _LineOutputs]:
    variable_bounds = {item.stable_id: (item.lower_bound, item.upper_bound) for item in variables}
    discounts_by_key = {
        (item.fiscal_season, item.track, item.product, item.grade): item
        for item in context.discount_decisions
    }
    tax_numerator_factor, tax_denominator = _tax_fraction(context.budget_tax_conversion_factor)
    outputs: dict[tuple[int, str, int], _LineOutputs] = {}
    for line_index, template in enumerate(
        sorted(
            context.revenue_line_templates,
            key=lambda item: (item.combination_stable_key, item.fiscal_season),
        )
    ):
        discount = discounts_by_key[
            (template.fiscal_season, template.track, template.product, template.grade)
        ]
        discount_id = discount_variable_by_key[discount.stable_id]
        discount_bounds = variable_bounds[discount_id]
        list_micro = _micro(template.list_price_yuan)
        recognition = template.current_season_recognition_rate_ppm
        for scenario in range(4):
            prefix = f"line:{line_index:06d}:{scenario}"
            headcount_id = headcount_ids[(scenario, template.combination_stable_key, template.fiscal_season)]
            headcount_upper = variable_bounds[headcount_id][1]
            unit_lower = _round_div(list_micro * discount_bounds[0], 1_000_000)
            unit_upper = _round_div(list_micro * discount_bounds[1], 1_000_000)
            unit_id = f"{prefix}:discounted-unit"
            business_id = f"{prefix}:business"
            net_id = f"{prefix}:net"
            current_id = f"{prefix}:current"
            cross_id = f"{prefix}:cross"
            business_upper = headcount_upper * unit_upper
            net_upper = _round_div(business_upper * tax_numerator_factor, tax_denominator)
            current_upper = _round_div(net_upper * recognition, 1_000_000)
            cross_upper = _round_div(net_upper * (1_000_000 - recognition), 1_000_000)
            variables.extend(
                (
                    IntegerVariableSpec(unit_id, unit_lower, unit_upper),
                    IntegerVariableSpec(business_id, 0, business_upper),
                    IntegerVariableSpec(net_id, 0, net_upper),
                    IntegerVariableSpec(current_id, 0, current_upper),
                    IntegerVariableSpec(cross_id, 0, cross_upper),
                )
            )
            divisions.extend(
                (
                    DivisionConstraintSpec(
                        f"{prefix}:discounted-unit",
                        unit_id,
                        LinearExpressionSpec(((discount_id, list_micro),), 500_000),
                        1_000_000,
                    ),
                    DivisionConstraintSpec(
                        f"{prefix}:net",
                        net_id,
                        LinearExpressionSpec(
                            ((business_id, tax_numerator_factor),),
                            tax_denominator // 2,
                        ),
                        tax_denominator,
                    ),
                    DivisionConstraintSpec(
                        f"{prefix}:current",
                        current_id,
                        LinearExpressionSpec(((net_id, recognition),), 500_000),
                        1_000_000,
                    ),
                    DivisionConstraintSpec(
                        f"{prefix}:cross",
                        cross_id,
                        LinearExpressionSpec(((net_id, 1_000_000 - recognition),), 500_000),
                        1_000_000,
                    ),
                )
            )
            multiplications.append(
                MultiplicationConstraintSpec(f"{prefix}:business", business_id, headcount_id, unit_id)
            )
            outputs[(scenario, template.combination_stable_key, template.fiscal_season)] = _LineOutputs(
                headcount_id,
                business_id,
                current_id,
                cross_id,
                list_micro,
            )
    return outputs


def _recognized_expressions(
    context: BudgetEvaluationContext,
    outputs: dict[tuple[int, str, int], _LineOutputs],
) -> dict[tuple[int, str, str | None], LinearExpressionSpec]:
    templates = tuple(context.revenue_line_templates)
    result: dict[tuple[int, str, str | None], LinearExpressionSpec] = {}
    for scenario in range(4):
        for scope, track in (
            ("DEPARTMENT", None),
            *(("TRACK", item) for item in sorted({row.track for row in templates})),
        ):
            selected = [row for row in templates if track is None or row.track == track]
            terms: list[tuple[str, int]] = []
            constant = 0
            for template in selected:
                line = outputs[(scenario, template.combination_stable_key, template.fiscal_season)]
                terms.append((line.current_id, 1))
                if template.fiscal_season % 10 == 1:
                    constant += _micro(template.opening_carryover_revenue_yuan)
                else:
                    prior = outputs[
                        (
                            scenario,
                            template.combination_stable_key,
                            template.fiscal_season - 1,
                        )
                    ]
                    terms.append((prior.cross_id, 1))
            result[(scenario, scope, track)] = LinearExpressionSpec(tuple(terms), constant)
    return result


def _add_target_constraints(
    context: BudgetEvaluationContext,
    recognized: dict[tuple[int, str, str | None], LinearExpressionSpec],
    constraints: list[LinearConstraintSpec],
) -> None:
    for index, target in enumerate(sorted(context.revenue_targets, key=lambda item: item.target_id)):
        target_micro = _micro(target.target_revenue_yuan)
        constraints.append(
            LinearConstraintSpec(
                f"discount-revenue-target:{index:06d}",
                recognized[(3, target.scope_level, target.track)],
                target_micro - TARGET_TOLERANCE_MICRO_YUAN,
                target_micro + TARGET_TOLERANCE_MICRO_YUAN,
            )
        )


def _add_composite_constraints(
    context: BudgetEvaluationContext,
    outputs: dict[tuple[int, str, int], _LineOutputs],
    constraints: list[LinearConstraintSpec],
) -> None:
    for index, target in enumerate(
        sorted(context.composite_discount_targets, key=lambda item: item.target_id)
    ):
        selected_templates = [
            item
            for item in context.revenue_line_templates
            if (target.track is None or item.track == target.track)
            and (target.fiscal_season is None or item.fiscal_season == target.fiscal_season)
        ]
        # ADR-0147: the other scenarios only explain parallel-rate contributions.
        # Composite-discount closure is a hard requirement of final r11 alone.
        for scenario in (3,):
            business_terms = tuple(
                (
                    outputs[(scenario, item.combination_stable_key, item.fiscal_season)].business_id,
                    1_000_000,
                )
                for item in selected_templates
            )
            list_terms = tuple(
                (
                    outputs[(scenario, item.combination_stable_key, item.fiscal_season)].headcount_id,
                    outputs[
                        (scenario, item.combination_stable_key, item.fiscal_season)
                    ].list_price_micro_yuan,
                )
                for item in selected_templates
            )
            constraints.extend(
                (
                    LinearConstraintSpec(
                        f"discount-composite-denominator:{index:06d}:{scenario}",
                        LinearExpressionSpec(list_terms),
                        lower_bound=1,
                    ),
                    LinearConstraintSpec(
                        f"discount-composite-lower:{index:06d}:{scenario}",
                        LinearExpressionSpec(
                            (
                                *business_terms,
                                *(
                                    (key, -coefficient * (target.target_rate_ppm - COMPOSITE_TOLERANCE_PPM))
                                    for key, coefficient in list_terms
                                ),
                            )
                        ),
                        lower_bound=0,
                    ),
                    LinearConstraintSpec(
                        f"discount-composite-upper:{index:06d}:{scenario}",
                        LinearExpressionSpec(
                            (
                                *business_terms,
                                *(
                                    (key, -coefficient * (target.target_rate_ppm + COMPOSITE_TOLERANCE_PPM))
                                    for key, coefficient in list_terms
                                ),
                            )
                        ),
                        upper_bound=0,
                    ),
                )
            )


def _contribution_objective(
    profile: ScenarioProfile,
    recognized: dict[tuple[int, str, str | None], LinearExpressionSpec],
    variables: list[IntegerVariableSpec],
    constraints: list[LinearConstraintSpec],
) -> ObjectiveTierSpec:
    retention_weight, new_weight = CONTRIBUTION_PROFILES[profile]
    scenario_coefficients = (
        -new_weight + retention_weight,
        new_weight + retention_weight,
        -new_weight - retention_weight,
        new_weight - retention_weight,
    )
    expression = _combine_expressions(
        tuple(
            (recognized[(scenario, "DEPARTMENT", None)], coefficient)
            for scenario, coefficient in enumerate(scenario_coefficients)
        )
    )
    bounds = {item.stable_id: (item.lower_bound, item.upper_bound) for item in variables}
    lower, upper = _expression_bounds(expression, bounds)
    deviation_id = "discount-contribution-profile-deviation"
    variables.append(IntegerVariableSpec(deviation_id, 0, max(abs(lower), abs(upper))))
    constraints.extend(
        (
            LinearConstraintSpec(
                "discount-contribution-positive",
                _combine_expressions(((LinearExpressionSpec(((deviation_id, 1),)), 1), (expression, -1))),
                lower_bound=0,
            ),
            LinearConstraintSpec(
                "discount-contribution-negative",
                _combine_expressions(((LinearExpressionSpec(((deviation_id, 1),)), 1), (expression, 1))),
                lower_bound=0,
            ),
        )
    )
    return ObjectiveTierSpec(
        "discount-contribution-profile-deviation",
        LinearExpressionSpec(((deviation_id, 1),)),
    )


def _combine_expressions(
    items: tuple[tuple[LinearExpressionSpec, int], ...],
) -> LinearExpressionSpec:
    terms: dict[str, int] = defaultdict(int)
    constant = 0
    for expression, multiplier in items:
        constant += expression.constant * multiplier
        for key, coefficient in expression.terms:
            terms[key] += coefficient * multiplier
    return LinearExpressionSpec(
        tuple((key, value) for key, value in sorted(terms.items()) if value), constant
    )


def _expression_bounds(
    expression: LinearExpressionSpec,
    bounds: dict[str, tuple[int, int]],
) -> tuple[int, int]:
    lower = upper = expression.constant
    for key, coefficient in expression.terms:
        variable_lower, variable_upper = bounds[key]
        products = (variable_lower * coefficient, variable_upper * coefficient)
        lower += min(products)
        upper += max(products)
    return lower, upper


def _validate_discount_context(context: BudgetEvaluationContext) -> None:
    if not context.discount_decisions:
        raise BusinessCompilationError("discount decisions are required after unlock")
    stable_ids = [item.stable_id for item in context.discount_decisions]
    keys = [(item.fiscal_season, item.track, item.product, item.grade) for item in context.discount_decisions]
    if len(stable_ids) != len(set(stable_ids)) or len(keys) != len(set(keys)):
        raise BusinessCompilationError("discount decisions must have unique stable IDs and keys")
    if any(
        item.current_value_ppm % DISCOUNT_STEP_PPM
        or item.lower_bound_ppm % DISCOUNT_STEP_PPM
        or item.upper_bound_ppm % DISCOUNT_STEP_PPM
        or not 0 <= item.lower_bound_ppm <= item.current_value_ppm <= item.upper_bound_ppm <= 1_000_000
        for item in context.discount_decisions
    ):
        raise BusinessCompilationError("discount values and bounds must use the 1000 ppm step")
    template_keys = {
        (item.fiscal_season, item.track, item.product, item.grade) for item in context.revenue_line_templates
    }
    if set(keys) != template_keys:
        raise BusinessCompilationError("discount decisions must exactly cover revenue template keys")
    decision_by_key = dict(zip(keys, context.discount_decisions, strict=True))
    if any(
        item.list_price_yuan < 0
        or item.opening_carryover_revenue_yuan < 0
        or not 0 <= item.current_season_recognition_rate_ppm <= 1_000_000
        or decision_by_key[(item.fiscal_season, item.track, item.product, item.grade)].current_value_ppm
        != item.detail_discount_rate_ppm
        for item in context.revenue_line_templates
    ):
        raise BusinessCompilationError(
            "revenue templates and discount decisions must share valid current inputs"
        )


def _micro(value: Decimal) -> int:
    return int(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) * MICRO_YUAN)


def _tax_fraction(value: Decimal) -> tuple[int, int]:
    from math import gcd

    scaled = value * NANO_FACTOR
    if scaled != scaled.to_integral_value():
        raise BusinessCompilationError("budget tax factor exceeds nine decimal places")
    denominator = int(scaled)
    if denominator <= 0:
        raise BusinessCompilationError("budget tax factor must be positive")
    common = gcd(1_000_000_000, denominator)
    return 1_000_000_000 // common, denominator // common


def _round_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def _attempt(
    profile: ScenarioProfile,
    status: SolveStatus,
    *,
    candidates: tuple[CandidateEvaluation, ...] = (),
    evaluated_state_count: int = 0,
    proof_code: str | None = None,
    proof_summary: dict[str, object] | None = None,
) -> SolveAttempt:
    return SolveAttempt(
        OptimizationStage.ENTRY_RATES_AND_DISCOUNT,
        profile,
        status,
        candidates,
        evaluated_state_count,
        proof_code,
        proof_summary,
    )
