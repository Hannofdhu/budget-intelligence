"""Pure draft planning tools backed by the existing exact budget kernels.

These functions never save, confirm or publish a budget. Rebalancing retains the
fixed entry total, locked inputs, price/discount and all existing class/target
constraints. A proposed plan must be accepted through the normal draft command.
"""

from copy import deepcopy
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from time import monotonic
from typing import Any

from pydantic import Field, StrictBool, StrictInt, ValidationError

from packages.class_size.validation import ClassSizeInput
from packages.forecast.calculation import DIMS, SEASONS
from packages.headcount.calculation import CombinationInput, ManualPlanInput, RateInput, RateType
from packages.next_year.calculation import (
    NextYearError,
    StrictModel,
    _id,
    _money,
    allocate_entry_counts,
    calculate_plan,
    effective_entry,
    number,
    validate_plan_inputs,
)
from packages.optimization.engine import (
    CandidateEvaluation,
    DecisionVector,
    EntryDecision,
    OptimizationSolverAdapter,
    RateDecision,
    ScenarioProfile,
    apply_equal_percentage_point_water_level,
    candidate_rank,
)
from packages.optimization.evaluator import (
    BudgetCandidateEvaluator,
    BudgetEvaluationContext,
    OpeningDecisionInput,
    RevenueLineTemplate,
    RevenueTarget,
    optimize_budget,
)

Json = dict[str, Any]
METRICS = ("revenue_yuan", "old_students", "new_students", "total_students", "class_count")


class BalanceOptions(StrictModel):
    adjust_entry: StrictBool = False
    rate_step_limit_ppm: StrictInt = Field(default=50_000, ge=0, le=1_000_000, multiple_of=100)
    maximum_seconds: StrictInt = Field(default=10, ge=1, le=60)


class _CachedEvaluator(BudgetCandidateEvaluator):
    """Reuse exact kernel results across symmetric contribution evaluations."""

    def __init__(self, context: BudgetEvaluationContext) -> None:
        super().__init__(context)
        self._kernel_cache: dict[tuple[Any, ...], Any] = {}

    def _calculate(
        self, entry_values: dict[str, int], rate_values: dict[str, int], discount_values: dict[str, int]
    ) -> Any:
        key = (
            tuple(sorted(entry_values.items())),
            tuple(sorted(rate_values.items())),
            tuple(sorted(discount_values.items())),
        )
        if key not in self._kernel_cache:
            self._kernel_cache[key] = super()._calculate(entry_values, rate_values, discount_values)
        return self._kernel_cache[key]


def _bounded_preview(
    context: BudgetEvaluationContext, *, deadline: float
) -> tuple[CandidateEvaluation | None, int]:
    """Coarse-to-fine draft search, not a proof of global optimality/infeasibility.

    Existing water-level construction, balanced revenue-contribution ranking and
    the authoritative full evaluator own every candidate. Formal solver rules
    stay unchanged. This path offers a usable *verified feasible* suggestion when
    the exact solver's local kernel tables are too large for an interactive call.
    """
    entries = tuple((row.stable_id, row.current_value) for row in context.entry_decisions)
    rates = {row.stable_id: row.current_value_ppm for row in context.rate_decisions}
    original = BudgetCandidateEvaluator(context).evaluate(
        DecisionVector(entries, tuple(sorted(rates.items())))
    )
    if not original.target_variances_yuan:
        return None, 0
    evaluated = 0
    targets = {
        str(row.track): row
        for row in context.revenue_targets
        if row.scope_level == "TRACK" and row.track is not None
    }
    ordered_targets = sorted(targets.items())
    # Each track closing within the full department tolerance is insufficient:
    # two same-direction 9,000-yuan gaps would still miss the department gate.
    # Share that unchanged tolerance across tracks during the sampled search.
    track_tolerance = Decimal(10000) / max(1, len(ordered_targets))
    for target_index, (track, target) in enumerate(ordered_targets):
        if monotonic() >= deadline:
            break
        # An early track must not consume the whole interactive preview budget.
        track_deadline = monotonic() + max(0, deadline - monotonic()) / (len(ordered_targets) - target_index)
        current = BudgetCandidateEvaluator(context).evaluate(
            DecisionVector(entries, tuple(sorted(rates.items())))
        )
        track_variance = dict(current.target_variances_yuan).get("TRACK:" + track)
        if track_variance is None:
            return None, evaluated
        other_revenue = current.revenue_r11_yuan - target.target_revenue_yuan - track_variance
        scoped = replace(
            context,
            revenue_targets=(
                RevenueTarget(
                    "preview-department", "DEPARTMENT", None, other_revenue + target.target_revenue_yuan
                ),
                target,
            ),
        )
        evaluator = _CachedEvaluator(scoped)
        initial = evaluator.evaluate(DecisionVector(entries, tuple(sorted(rates.items()))))
        if not initial.target_variances_yuan:
            return None, evaluated
        variance = initial.target_variances_yuan[0][1]
        if initial.hard_constraints_satisfied and abs(variance) <= track_tolerance:
            continue
        direction = "UP" if variance < 0 else "DOWN"
        maximum = []
        for kind in ("RENEWAL_RATE", "NEW_STUDENT_SHARE"):
            maximum.append(
                max(
                    (
                        (
                            row.upper_bound_ppm - row.current_value_ppm
                            if direction == "UP"
                            else row.current_value_ppm - row.lower_bound_ppm
                        )
                        // 100
                        for row in context.rate_decisions
                        if row.track == track and row.rate_type == kind and not row.locked
                    ),
                    default=0,
                )
            )
        best = initial
        best_levels = (0, 0)
        visited: set[tuple[int, int]] = set()

        def track_closed(candidate: CandidateEvaluation) -> bool:
            return (
                candidate.hard_constraints_satisfied
                and bool(candidate.target_variances_yuan)
                and abs(candidate.target_variances_yuan[0][1]) <= track_tolerance
            )

        def rank(candidate: CandidateEvaluation) -> tuple[Any, ...]:
            if track_closed(candidate):
                return (0, *candidate_rank(candidate, ScenarioProfile.BALANCED))
            return (
                1,
                not candidate.hard_constraints_satisfied,
                abs(candidate.target_variances_yuan[0][1])
                if candidate.target_variances_yuan
                else Decimal("Infinity"),
                sum(abs(value) for value in candidate.rate_adjustments_ppm),
            )

        def evaluate_levels(
            levels: tuple[int, int],
            *,
            initial_rates: dict[str, int] = rates,
            selected_track: str = track,
            selected_direction: str = direction,
            track_evaluator: _CachedEvaluator = evaluator,
            seen: set[tuple[int, int]] = visited,
        ) -> CandidateEvaluation:
            nonlocal evaluated, best, best_levels
            chosen = dict(initial_rates)
            for kind, level in zip(("RENEWAL_RATE", "NEW_STUDENT_SHARE"), levels, strict=True):
                chosen.update(
                    apply_equal_percentage_point_water_level(
                        context.rate_decisions,
                        track=selected_track,
                        rate_type=kind,
                        direction=selected_direction,
                        water_level_steps=level,
                    )
                )
            candidate = track_evaluator.evaluate(DecisionVector(entries, tuple(sorted(chosen.items()))))
            seen.add(levels)
            evaluated += 1
            if rank(candidate) < rank(best):
                best, best_levels = candidate, levels
            return candidate

        # First try a common water level with a bounded binary warm start. A
        # full 9x9 grid can spend the request budget before reaching the small
        # 0.01pp changes that close a modest business target. This is a sampled
        # search, never a monotonicity or infeasibility proof: every accepted
        # point still goes through the authoritative complete evaluator.
        low, high = 0, max(maximum, default=0)
        while low <= high and monotonic() < track_deadline:
            level = (low + high) // 2
            levels = (min(level, maximum[0]), min(level, maximum[1]))
            candidate = evaluate_levels(levels)
            if track_closed(candidate):
                break
            if not candidate.target_variances_yuan:
                break
            signed_variance = candidate.target_variances_yuan[0][1]
            needs_more = signed_variance < 0 if direction == "UP" else signed_variance > 0
            if needs_more:
                low = level + 1
            else:
                high = level - 1
        if track_closed(best):
            rates = dict(best.decision.rate_values)
            continue

        # A joint step can jump over the target after integer headcounts are
        # allocated. Search each family separately before paying for a broad
        # Cartesian grid, retaining the best complete-kernel-verified point.
        for family in (0, 1):
            low, high = 0, maximum[family]
            anchor = best_levels
            while low <= high and monotonic() < track_deadline:
                level = (low + high) // 2
                levels = (level, anchor[1]) if family == 0 else (anchor[0], level)
                candidate = evaluate_levels(levels)
                if track_closed(candidate) or not candidate.target_variances_yuan:
                    break
                signed_variance = candidate.target_variances_yuan[0][1]
                needs_more = signed_variance < 0 if direction == "UP" else signed_variance > 0
                if needs_more:
                    low = level + 1
                else:
                    high = level - 1
            if track_closed(best):
                break
        if track_closed(best):
            rates = dict(best.decision.rate_values)
            continue

        radius = max(maximum, default=0)
        rounds = 0
        while rounds < 5 and monotonic() < track_deadline:
            if rounds == 0:
                grids = [sorted({limit * step // 8 for step in range(9)}) for limit in maximum]
            else:
                grids = [
                    sorted({max(0, min(limit, center + radius * step // 4)) for step in range(-4, 5)})
                    for limit, center in zip(maximum, best_levels, strict=True)
                ]
            for renewal in grids[0]:
                for share in grids[1]:
                    levels = renewal, share
                    if levels in visited:
                        continue
                    if monotonic() >= track_deadline:
                        break
                    evaluate_levels(levels)
            if radius <= 1:
                break
            radius = max(1, radius // 4)
            rounds += 1
        if best.hard_constraints_satisfied:
            rates = dict(best.decision.rate_values)
    verified = BudgetCandidateEvaluator(context).evaluate(
        DecisionVector(entries, tuple(sorted(rates.items())))
    )
    if not verified.target_variances_yuan:
        return None, evaluated
    improved = sum(abs(value) for _, value in verified.target_variances_yuan) < sum(
        abs(value) for _, value in original.target_variances_yuan
    )
    return (
        verified if verified.targets_closed or (verified.hard_constraints_satisfied and improved) else None
    ), evaluated


def _delta(before: Json, after: Json) -> Json:
    result: Json = {}
    for field in METRICS:
        if field == "class_count" and ((before and field not in before) or (after and field not in after)):
            result[field] = None
            continue
        left, right = before.get(field, 0), after.get(field, 0)
        if left is None or right is None:
            result[field] = None
        elif field == "revenue_yuan":
            result[field] = _money(Decimal(str(right)) - Decimal(str(left)))
        else:
            result[field] = right - left
    return result


def compare_calculations(before: Json, after: Json) -> Json:
    """Exact additive before/after differences; missing rows are additions/removals."""
    result: Json = {
        "annual": {
            "before": deepcopy(before["annual"]),
            "after": deepcopy(after["annual"]),
            "delta": _delta(before["annual"], after["annual"]),
        },
    }
    for collection, key in (("quarters", "season"), ("tracks", "track"), ("campuses", "campus")):
        left = {row[key]: row for row in before.get(collection, [])}
        right = {row[key]: row for row in after.get(collection, [])}
        order = SEASONS if key == "season" else sorted(left.keys() | right.keys())
        result[collection] = [
            {
                key: identifier,
                "before": deepcopy(left.get(identifier, {})),
                "after": deepcopy(right.get(identifier, {})),
                "delta": _delta(left.get(identifier, {}), right.get(identifier, {})),
            }
            for identifier in order
            if identifier in left or identifier in right
        ]
    left_rows = {(row["combination_id"], row["season"]): row for row in before.get("rows", [])}
    right_rows = {(row["combination_id"], row["season"]): row for row in after.get("rows", [])}
    changed = []
    for row_key in sorted(left_rows.keys() | right_rows.keys()):
        previous, current = left_rows.get(row_key, {}), right_rows.get(row_key, {})
        if previous == current:
            continue
        row = current or previous
        changed.append(
            {
                "combination_id": row_key[0],
                "season": row_key[1],
                **{field: row[field] for field in DIMS},
                "before": deepcopy(previous),
                "after": deepcopy(current),
                "delta": _delta(previous, current),
            }
        )
    result["rows"] = changed
    result["changed_row_count"] = len(changed)
    return result


def _track_targets(inputs: Json, calculation: Json) -> dict[str, Decimal]:
    targets = inputs["track_targets"]
    if all(value is not None for value in targets.values()):
        return {key: number(value) for key, value in targets.items()}
    target = number(inputs["target_revenue_yuan"])
    amounts = {row["track"]: number(row["revenue_yuan"]) for row in calculation["tracks"]}
    total = sum(amounts.values(), Decimal(0))
    result: dict[str, Decimal] = {}
    ordered = sorted(amounts)
    for key in ordered[:-1]:
        share = amounts[key] / total if total else Decimal(1) / len(ordered)
        result[key] = (target * share).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    result[ordered[-1]] = target - sum(result.values(), Decimal(0))
    return result


def _rate_bounds(row: Json, kind: str, step_limit_ppm: int) -> tuple[int, int]:
    """Explicit per-field bounds are authoritative, default limits are not hidden caps."""
    value = row[kind + "_ppm"]
    maximum = 1_000_000 if kind == "renewal" else 999_900
    return (
        row.get(kind + "_lower_ppm", max(0, value - step_limit_ppm)),
        row.get(kind + "_upper_ppm", min(maximum, value + step_limit_ppm)),
    )


def _class_diagnostics(calculation: Json) -> list[Json]:
    """Make unchanged hard constraints actionable without dumping hundreds of rows."""
    rows = {(row["combination_id"], row["season"]): row for row in calculation["rows"]}
    definitions = {
        "BELOW_MINIMUM_CLASS_SIZE": (
            "部分季度人次不足最低成班人数",
            "调整对应季度的新生人次或招生参数；不开展的业务可关闭范围，但不能丢弃期初结转。",
            ["plans.students", "rates.renewal_ppm", "rates.share_ppm", "combinations.enabled"],
        ),
        "ENABLED_COMBINATION_ALL_SEASONS_ZERO": (
            "部分已启用业务四季人次均为零",
            "关闭不开展的SKU；需要开展时，先分配入口人次或填写季度新生人次。",
            ["combinations.enabled", "combinations.entry_students", "plans.students"],
        ),
    }
    result = []
    for code, (message, action, fields) in definitions.items():
        issues = [row for row in calculation["issues"] if row["code"] == code]
        if not issues:
            continue
        examples = []
        for issue in issues[:3]:
            calculated = rows.get((issue["combination_id"], issue["season"]), {})
            examples.append(
                {
                    "combination_id": issue["combination_id"],
                    "season": issue["season"],
                    **{field: calculated.get(field) for field in DIMS},
                    "total_students": calculated.get("total_students"),
                    **issue["detail"],
                }
            )
        result.append(
            {
                "code": code,
                "count": len(issues),
                "message": message,
                "action": action,
                "editable_fields": fields,
                "examples": examples,
            }
        )
    return result


def _context(inputs: Json, calculation: Json, options: BalanceOptions) -> BudgetEvaluationContext:
    year = inputs["fiscal_year"]
    active = [row for row in inputs["combinations"] if row["enabled"]]
    by_id = {row["id"]: row for row in active}
    entry_counts, _, _ = allocate_entry_counts(inputs)
    engine_combinations = tuple(
        CombinationInput(
            row["id"],
            row["track"],
            row["product"],
            "ENTRY" if effective_entry(row) else "NON_ENTRY",
            row["grade"],
            row["subject"],
            row["campus"],
            entry_counts.get(row["id"], 0),
        )
        for row in active
    )
    entry_decisions = []
    for row in active:
        if not effective_entry(row):
            continue
        value = entry_counts[row["id"]]
        locked = row.get("entry_locked", False) or not options.adjust_entry
        # These are explicit preview limits, not a mutation of formal resource
        # hard bounds. Zero-history scope first needs a manually assigned plan.
        lower, upper = (
            (value, value)
            if locked
            else (value * 80 // 100, min(inputs["entry_total"], (value * 120 + 99) // 100))
        )
        entry_decisions.append(EntryDecision(row["id"], value, lower, upper, not locked))
    rate_rows = {
        (row["season"], row["track"], row["product"], row["grade"], row["campus"]): row
        for row in inputs["rates"]
    }
    applied = [row for row in calculation["rows"] if row["planned_new_applied"]]
    # The formal optimizer accepts immutable absolute-new inputs. A zero-old
    # seed remains zero-old during this preview, so that its frozen absolute
    # plan cannot silently become an inferred share when retention is raised.
    seed_cohorts = {
        (row["season"], row["track"], row["product"], row["grade"], row["campus"])
        for row in applied
        if not row.get("manual_locked") and row["old_students"] == 0
    }
    engine_rates: list[RateInput] = []
    rate_decisions: list[RateDecision] = []
    active_cohorts = {(row["track"], row["product"], row["grade"], row["campus"]) for row in active}
    for key, row in rate_rows.items():
        if key[1:] not in active_cohorts:
            continue
        rate_fields: tuple[tuple[RateType, str], ...] = (
            ("RENEWAL_RATE", "renewal"),
            ("NEW_STUDENT_SHARE", "share"),
        )
        for rate_type, kind in rate_fields:
            field = kind + "_ppm"
            identifier = row["id"] + field
            value = row[field]
            locked = row.get("locked", False) or (kind == "renewal" and key in seed_cohorts)
            lower, upper = _rate_bounds(row, kind, options.rate_step_limit_ppm)
            if locked:
                lower = upper = value
            engine_rates.append(
                RateInput(
                    identifier,
                    rate_type,
                    year * 10 + SEASONS.index(row["season"]) + 1,
                    row["track"],
                    row["product"],
                    row["grade"],
                    row["campus"],
                    value,
                )
            )
            rate_decisions.append(
                RateDecision(identifier, row["track"], rate_type, value, lower, upper, locked)
            )
    opening_totals = {row["id"]: 0 for row in active}
    for row in inputs["opening_sources"]:
        if row["target_id"] in opening_totals:
            opening_totals[row["target_id"]] += row["students"]
    opening = tuple(
        OpeningDecisionInput(
            row["id"],
            opening_totals[row["id"]],
            rate_rows["暑假", row["track"], row["product"], row["grade"], row["campus"]]["id"]
            + "renewal_ppm",
        )
        for row in active
    )
    targets = _track_targets(inputs, calculation)
    return BudgetEvaluationContext(
        fiscal_year=year,
        budget_tax_conversion_factor=number(inputs["tax_factor"]),
        fixed_entry_total=inputs["entry_total"],
        combinations=engine_combinations,
        rates=tuple(engine_rates),
        plans=tuple(
            ManualPlanInput(
                "planning-" + row["combination_id"] + row["season"],
                row["combination_id"],
                year * 10 + SEASONS.index(row["season"]) + 1,
                row["new_students"],
                True,
            )
            for row in applied
        ),
        entry_decisions=tuple(entry_decisions),
        rate_decisions=tuple(rate_decisions),
        class_size_parameters=tuple(
            ClassSizeInput(_id((row["track"], row["product"])), row["track"], row["product"], row["minimum"])
            for row in inputs["class_sizes"]
        ),
        revenue_line_templates=tuple(
            RevenueLineTemplate(
                row["combination_id"],
                year * 10 + SEASONS.index(row["season"]) + 1,
                by_id[row["combination_id"]]["track"],
                by_id[row["combination_id"]]["product"],
                "ENTRY" if effective_entry(by_id[row["combination_id"]]) else "NON_ENTRY",
                by_id[row["combination_id"]]["grade"],
                by_id[row["combination_id"]]["subject"],
                by_id[row["combination_id"]]["campus"],
                number(row["price_yuan"]),
                row["discount_ppm"],
                row["recognition_ppm"],
                number(by_id[row["combination_id"]]["opening_carryover_yuan"]),
            )
            for row in inputs["finance"]
            if row["combination_id"] in by_id
        ),
        composite_discount_targets=(),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, number(inputs["target_revenue_yuan"])),
            *(RevenueTarget(track, "TRACK", track, target) for track, target in targets.items()),
        ),
        opening_inputs=opening,
    )


def balance_plan(inputs: Json, options: Json | None = None) -> Json:
    """Preview a real existing-solver candidate; never force or silently save it."""
    started = monotonic()
    try:
        settings = BalanceOptions.model_validate(options or {})
    except ValidationError as exc:
        raise NextYearError("调平范围无效，请检查入口开关、比例步长及求解时限") from exc
    calculation = calculate_plan(inputs)
    if inputs["target_revenue_yuan"] is None:
        raise NextYearError("请先手动填写经营目标，再生成调平建议")
    if not calculation["complete"]:
        raise NextYearError("请先补齐预算参数，再生成调平建议")
    context = _context(inputs, calculation, settings)
    rate_by_id = {row["id"]: row for row in inputs["rates"]}
    constraints: Json = {
        "entry_total": inputs["entry_total"],
        "entry_total_fixed": True,
        "adjust_entry": settings.adjust_entry,
        "entry_preview_range_percent": [80, 120],
        "rate_step_limit_ppm": settings.rate_step_limit_ppm,
        "rate_step_limit_applies_to": "UNSPECIFIED_BOUNDS_ONLY",
        "rate_bounds": [
            {
                "rate_id": decision.stable_id.removesuffix(kind + "_ppm"),
                "rate_type": decision.rate_type,
                "lower_ppm": decision.lower_bound_ppm,
                "upper_ppm": decision.upper_bound_ppm,
                "locked": decision.locked,
                "source": "LOCKED"
                if decision.locked
                else "EXPLICIT"
                if any(
                    kind + suffix in rate_by_id[decision.stable_id.removesuffix(kind + "_ppm")]
                    for suffix in ("_lower_ppm", "_upper_ppm")
                )
                else "DEFAULT_STEP",
            }
            for decision in context.rate_decisions
            for kind in ("renewal" if decision.rate_type == "RENEWAL_RATE" else "share",)
        ],
        "price_and_discount_fixed": True,
        "manual_locks_preserved": True,
        "class_size_constraints_preserved": True,
        "track_targets": {key: _money(value) for key, value in _track_targets(inputs, calculation).items()},
        "track_targets_derived": all(value is None for value in inputs["track_targets"].values()),
        "tolerance_yuan": "10000.000000",
        "class_issues": [
            row
            for row in calculation["issues"]
            if row["code"] in {"BELOW_MINIMUM_CLASS_SIZE", "ENABLED_COMBINATION_ALL_SEASONS_ZERO"}
        ],
        "class_diagnostics": _class_diagnostics(calculation),
    }
    try:
        outcome = optimize_budget(
            context,
            solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
            # The staged solver may run several profiles. Reserve time for the
            # full-kernel coarse-to-fine fallback instead of giving every stage
            # the complete request budget and leaving no fallback time.
            maximum_solver_seconds=min(1.5, settings.maximum_seconds / 6),
            maximum_candidate_validations=100,
            maximum_group_table_rows=30_000,
            discount_unlocked=False,
        )
    except (ValueError, ArithmeticError, RuntimeError) as exc:
        raise NextYearError("现有调平引擎未能完成本次预览，请缩小调整范围后重试") from exc
    result: Json = {
        "status": str(outcome.status),
        "proposed_inputs": None,
        "calculation": calculation,
        "impact": compare_calculations(calculation, calculation),
        "constraints": constraints,
        "discount_unlock_required": outcome.discount_unlock_required,
        "attempts": [
            {
                "stage": str(attempt.stage),
                "profile": str(attempt.profile),
                "status": str(attempt.status),
                "proof_code": attempt.proof_code,
                "evaluated_state_count": attempt.evaluated_state_count,
            }
            for attempt in outcome.attempts
        ],
        "message": (
            "当前边界内没有可应用的调平方案；预算未被修改。"
            if str(outcome.status) == "INFEASIBLE"
            else "本次求解未完成，尚不能判断是否有解；预算未被修改。"
        ),
    }
    candidate = outcome.candidates[0] if outcome.candidates else None
    if candidate is None and str(outcome.status) == "INCOMPLETE":
        candidate, sampled_count = _bounded_preview(context, deadline=started + settings.maximum_seconds)
        result["attempts"].append(
            {
                "stage": "DRAFT_COARSE_TO_FINE",
                "profile": "BALANCED",
                "status": ("FEASIBLE" if candidate.targets_closed else "PARTIAL")
                if candidate
                else "INCOMPLETE",
                "proof_code": "SAMPLED_CANDIDATES_FULL_KERNEL_VERIFIED",
                "evaluated_state_count": sampled_count,
            }
        )
        constraints["search_complete"] = False
        constraints["sampled_entry_structure_fixed"] = True
        if candidate:
            result["status"] = "FEASIBLE" if candidate.targets_closed else "PARTIAL"
    if candidate is None:
        if constraints["class_issues"]:
            result["message"] = (
                "预算仍有未满足最低成班人数或四季均为零的业务，请先调整人次或关闭范围；原预算未修改。"
            )
        return result
    proposed = deepcopy(inputs)
    entries = dict(candidate.decision.entry_values)
    rates = dict(candidate.decision.rate_values)
    for row in proposed["combinations"]:
        if row["id"] in entries:
            row["entry_students"] = entries[row["id"]]
    for row in proposed["rates"]:
        for kind in ("renewal", "share"):
            field = kind + "_ppm"
            if row["id"] + field in rates:
                row[field] = rates[row["id"] + field]
    if constraints["track_targets_derived"]:
        proposed["track_targets"] = constraints["track_targets"]
    validate_plan_inputs(proposed, inputs)
    verified = calculate_plan(proposed)
    signatures = tuple(
        sorted(
            (
                f"{row['combination_id']}:{inputs['fiscal_year'] * 10 + SEASONS.index(row['season']) + 1}",
                row["total_students"] or 0,
            )
            for row in verified["rows"]
        )
    )
    if number(verified["annual"]["revenue_yuan"]) != candidate.revenue_r11_yuan or signatures != tuple(
        sorted(candidate.headcount_signature)
    ):
        result["status"] = "FAILED"
        result["message"] = "候选复算与预算结果不一致，已拒绝应用；原预算保持不变。"
        return result
    evaluator = BudgetCandidateEvaluator(context)
    rechecked = evaluator.evaluate(candidate.decision)
    if not rechecked.hard_constraints_satisfied or (
        result["status"] != "PARTIAL" and not rechecked.targets_closed
    ):
        result["status"] = "FAILED"
        result["message"] = "候选未通过原有预算约束，已拒绝应用。"
        return result
    result.update(
        {
            "proposed_inputs": proposed,
            "calculation": verified,
            "impact": compare_calculations(calculation, verified),
            "message": (
                "已生成边界内的改善建议，尚未达到经营目标；剩余差额见预览，原预算未修改。"
                if result["status"] == "PARTIAL"
                else "已找到通过完整校验的可用方案；有限搜索未证明最优，确认修改影响后再应用。"
                if result["status"] == "FEASIBLE"
                else "调平建议已生成，确认修改影响后再应用。"
            ),
            "candidate_hash": candidate.result_hash,
            "contributions": {
                "renewal_yuan": _money(
                    (
                        (candidate.revenue_r10_yuan - candidate.revenue_r00_yuan)
                        + (candidate.revenue_r11_yuan - candidate.revenue_r01_yuan)
                    )
                    / 2
                ),
                "new_share_yuan": _money(
                    (
                        (candidate.revenue_r01_yuan - candidate.revenue_r00_yuan)
                        + (candidate.revenue_r11_yuan - candidate.revenue_r10_yuan)
                    )
                    / 2
                ),
            },
        }
    )
    return result
