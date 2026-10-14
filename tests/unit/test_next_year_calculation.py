"""Pure planning-adapter acceptance; no BI, ledger, or annual-budget writes."""

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from packages.forecast.calculation import DIMS, SEASONS
from packages.forecast.store import fingerprint
from packages.headcount.calculation import OPENING_HEADCOUNT_RULE_VERSION
from packages.next_year.calculation import (
    NextYearError,
    calculate_plan,
    forecast_basis,
    prepare_inputs,
    validate_plan_inputs,
)

Json = dict[str, Any]


def _dims(grade: str = "七年级", subject: str = "数学", *, product: str = "普通") -> Json:
    return {
        "track": "智慧空间",
        "product": product,
        "grade": grade,
        "subject": subject,
        "campus": "示例校区",
    }


def _id(dims: Json) -> str:
    return fingerprint(tuple(dims[key] for key in DIMS))[:24]


def _plan_inputs() -> Json:
    combinations = [
        {
            "id": _id(dims),
            **dims,
            "entry": dims["grade"] == "七年级",
            "enabled": True,
            "source_entry_weight": 10 if dims["grade"] == "七年级" else 0,
            "source_new_by_season": {season: 10 for season in SEASONS},
            "opening_carryover_yuan": "0",
        }
        for dims in (_dims(), _dims("八年级"))
    ]
    return {
        "fiscal_year": 2028,
        "source_as_of": "2026-10-05",
        "target_revenue_yuan": "1000000",
        "track_targets": {"智慧空间": "1000000"},
        "entry_total": 10,
        "tax_factor": "1.06",
        "combinations": combinations,
        "rates": [
            {
                "id": "rate-" + combo["id"] + season,
                "season": season,
                **{key: combo[key] for key in ("track", "product", "grade", "campus")},
                "renewal_ppm": 800000,
                "share_ppm": 200000,
            }
            for combo in combinations
            for season in SEASONS
        ],
        "finance": [
            {
                "id": "finance-" + combo["id"] + season,
                "combination_id": combo["id"],
                "season": season,
                "price_yuan": "106",
                "discount_ppm": 1000000,
                "recognition_ppm": 800000,
            }
            for combo in combinations
            for season in SEASONS
        ],
        "class_sizes": [{"track": "智慧空间", "product": "普通", "minimum": 5}],
        "plans": [
            {"combination_id": combo["id"], "season": season, "students": 10}
            for combo in combinations
            for season in SEASONS
        ],
        "opening_sources": [
            {**_dims(), "students": 100, "target_id": combinations[1]["id"], "exclusion": None}
        ],
        "notes": [],
    }


def _forecast_inputs(scopes: list[Json] | None = None) -> tuple[Json, Json]:
    dims = scopes or [_dims(), _dims("八年级")]
    baseline_rows = [
        {
            **scope,
            "season": season,
            "entry": "是" if scope["grade"] == "七年级" else "否",
            "price": "106",
            "discount": "1",
            "recognition": "0.8",
            "renewal": "0.8",
            "share": "0.2",
            "minimum": 5,
            "old": 80,
            "new": 20,
            "total": 100,
            "revenue": "1000",
        }
        for scope in dims
        for season in SEASONS
    ]
    rows = [
        {
            **scope,
            "season": season,
            "forecast_old": 80,
            "forecast_new": 20,
            "forecast_total": 100,
            "forecast_revenue": "1000",
            "forecast_carryout": "0",
            "counts_not_applicable": False,
        }
        for scope in dims
        for season in SEASONS
    ]
    snapshot = {
        "as_of": "2026-10-05",
        "rows": [
            {
                **scope,
                "season": "暑假",
                "enrollment": 100,
                "new_business": 13,
                "new_base": 20,
                "old_base": 80,
                "actual_revenue": "1000",
                "actual_carryout": "0",
            }
            for scope in dims
        ],
    }
    baseline = {"sha256": fingerprint("immutable archive"), "fiscal_year": 2027, "rows": baseline_rows}
    states = {"暑假": "FINALIZED", "秋季": "FUTURE", "寒假": "FUTURE", "春季": "FUTURE"}
    forecast = {
        "id": "confirmed-forecast",
        "sequence": 16,
        "status": "CONFIRMED",
        "as_of": snapshot["as_of"],
        "snapshot": snapshot,
        "snapshot_hash": fingerprint(snapshot),
        "baseline_hash": baseline["sha256"],
        "states": states,
        "assumptions": {"states": states, "trial_mode": False, "mappings": [], "minimum_sample": 30},
        "calculation": {
            "ready": True,
            "issues": [],
            "rows": rows,
            "annual": {"forecast_revenue": str(1000 * len(rows)), "forecast_total": 100 * len(rows)},
        },
    }
    return baseline, forecast


@pytest.mark.parametrize("invalid", ["draft", "trial", "not_ready", "blocking", "baseline", "snapshot"])
def test_preparation_requires_confirmed_source_and_bound_snapshot(invalid: str) -> None:
    baseline, forecast = _forecast_inputs()
    if invalid == "draft":
        forecast["status"] = "DRAFT"
    elif invalid == "trial":
        forecast["assumptions"]["trial_mode"] = True
    elif invalid == "not_ready":
        forecast["calculation"]["ready"] = False
    elif invalid == "blocking":
        forecast["calculation"]["issues"] = [{"code": "BAD", "severity": "BLOCKING"}]
    elif invalid == "baseline":
        forecast["baseline_hash"] = "different"
    else:
        forecast["snapshot"]["rows"][0]["new_business"] = 999
    before = fingerprint((baseline, forecast))
    with pytest.raises(NextYearError, match="确认有效"):
        prepare_inputs(baseline, forecast)
    assert fingerprint((baseline, forecast)) == before


def test_preparation_is_pure_deterministic_and_uses_business_new_not_classification_new() -> None:
    baseline, forecast = _forecast_inputs()
    before = fingerprint((baseline, forecast))
    first = prepare_inputs(baseline, forecast)
    assert first == prepare_inputs(baseline, forecast)
    assert fingerprint((baseline, forecast)) == before
    assert first["entry_total"] == 13  # Frozen 业务新生, not forecast_new=20.
    basis = forecast_basis(forecast)
    assert basis["calculation_hash"] == fingerprint(forecast["calculation"])
    assert basis["snapshot_hash"] == forecast["snapshot_hash"]
    first["opening_sources"][0]["students"] = 99
    assert fingerprint((baseline, forecast)) == before


def test_preparation_leaves_business_targets_unset_instead_of_inheriting_forecast_income() -> None:
    baseline, forecast = _forecast_inputs([_dims(), {**_dims(), "track": "智慧伴学"}])
    prepared = prepare_inputs(baseline, forecast)
    assert forecast["calculation"]["annual"]["forecast_revenue"] == "8000"
    assert prepared["target_revenue_yuan"] is None
    assert prepared["track_targets"] == {"智慧伴学": None, "智慧空间": None}
    result = calculate_plan(prepared)
    assert result["complete"] is True
    assert result["target_status"] == "NOT_SET"
    assert result["annual"]["revenue_yuan"] is not None
    assert result["annual"]["total_students"] > 0


def test_unset_targets_preserve_calculation_and_clear_only_target_comparisons() -> None:
    original = _plan_inputs()
    specified = calculate_plan(original)
    cleared = deepcopy(original)
    cleared["target_revenue_yuan"] = None
    cleared["track_targets"] = {key: None for key in original["track_targets"]}
    before = fingerprint(cleared)
    validate_plan_inputs(cleared, original)  # Existing prepared inputs may contain inherited targets.
    result = calculate_plan(cleared)
    assert result["rows"] == specified["rows"]
    for field in ("quarters", "campuses", "opening", "entry_allocations", "issues"):
        assert result[field] == specified[field]
    for field in ("revenue_yuan", "old_students", "new_students", "total_students"):
        assert result["annual"][field] == specified["annual"][field]
        assert result["tracks"][0][field] == specified["tracks"][0][field]
    for summary in (result["annual"], *result["tracks"]):
        assert summary["target_revenue_yuan"] is None
        assert summary["gap_yuan"] is None
        assert summary["attainment"] is None
    assert result["target_status"] == "NOT_SET"
    assert result["complete"] is True
    assert fingerprint(cleared) == before
    restored = deepcopy(cleared)
    restored["target_revenue_yuan"] = original["target_revenue_yuan"]
    restored["track_targets"] = original["track_targets"]
    validate_plan_inputs(restored, cleared)
    assert calculate_plan(restored) == specified


def test_user_can_set_annual_target_before_allocating_track_targets() -> None:
    inputs = _plan_inputs()
    specified = calculate_plan(inputs)
    inputs["track_targets"] = {"智慧空间": None}
    result = calculate_plan(inputs)
    assert result["rows"] == specified["rows"]
    assert result["annual"] == specified["annual"]
    assert result["target_status"] == "TRACK_TARGETS_NOT_SET"
    assert result["tracks"][0]["target_revenue_yuan"] is None
    assert result["tracks"][0]["gap_yuan"] is None
    assert result["tracks"][0]["attainment"] is None


@pytest.mark.parametrize("target,tracks", [(None, {"智慧空间": "0"}), (None, {"智慧空间": "1000000"})])
def test_track_target_requires_a_user_specified_annual_target(target: object, tracks: Json) -> None:
    inputs = _plan_inputs()
    inputs["target_revenue_yuan"] = target
    inputs["track_targets"] = tracks
    with pytest.raises(NextYearError):
        calculate_plan(inputs)


@pytest.mark.parametrize(
    "tracks", [{"智慧空间": "100", "智慧伴学": None}, {"智慧空间": "100", "智慧伴学": "99"}]
)
def test_track_targets_must_be_all_unset_or_complete_and_equal_annual_target(tracks: Json) -> None:
    baseline, forecast = _forecast_inputs([_dims(), {**_dims(), "track": "智慧伴学"}])
    inputs = prepare_inputs(baseline, forecast)
    inputs["target_revenue_yuan"] = "200"
    inputs["track_targets"] = tracks
    with pytest.raises(NextYearError):
        calculate_plan(inputs)


def test_user_specified_zero_target_is_not_treated_as_missing() -> None:
    inputs = _plan_inputs()
    inputs["target_revenue_yuan"] = "0"
    inputs["track_targets"] = {"智慧空间": "0"}
    result = calculate_plan(inputs)
    assert result["annual"]["target_revenue_yuan"] == "0.000000"
    assert result["annual"]["gap_yuan"] == "-41800.000000"
    assert result["annual"]["attainment"] is None  # A rate with zero denominator is undefined.
    assert result["tracks"][0]["target_revenue_yuan"] == "0.000000"
    assert result["tracks"][0]["gap_yuan"] == "-41800.000000"
    assert result["target_status"] != "NOT_SET"


@pytest.mark.parametrize("field", ["source_as_of", "opening_sources", "notes"])
def test_frozen_source_fields_cannot_be_replaced(field: str) -> None:
    original = _plan_inputs()
    changed = deepcopy(original)
    if field == "source_as_of":
        changed[field] = "2026-10-06"
    elif field == "opening_sources":
        changed[field][0]["students"] += 1
    else:
        changed[field].append({"code": "replacement"})
    with pytest.raises(NextYearError, match="来源不可修改"):
        validate_plan_inputs(changed, original)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "replacement"),
        ("subject", "英语"),
        ("entry", False),
        ("source_entry_weight", 100),
        ("source_new_by_season", {season: 100 for season in SEASONS}),
        ("opening_carryover_yuan", "123"),
    ],
)
def test_combination_source_and_dimensions_are_frozen(field: str, value: object) -> None:
    original = _plan_inputs()
    changed = deepcopy(original)
    changed["combinations"][0][field] = value
    with pytest.raises(NextYearError):
        validate_plan_inputs(changed, original)


def test_frozen_sku_source_error_uses_sku_label_without_mutating_inputs() -> None:
    original = _plan_inputs()
    changed = deepcopy(original)
    changed["combinations"][0]["source_entry_weight"] += 1
    before = fingerprint(changed)
    with pytest.raises(NextYearError, match="预算SKU或来源已变化，不能静默替换"):
        validate_plan_inputs(changed, original)
    assert fingerprint(changed) == before
    assert original["combinations"][0]["source_entry_weight"] == 10


@pytest.mark.parametrize("name", ["rates", "finance", "plans", "class_sizes"])
@pytest.mark.parametrize("operation", ["missing", "duplicate"])
def test_parameter_sets_have_unique_complete_business_coverage(name: str, operation: str) -> None:
    inputs = _plan_inputs()
    if operation == "missing":
        inputs[name].pop()
    else:
        inputs[name].append(deepcopy(inputs[name][0]))
    with pytest.raises(NextYearError):
        calculate_plan(inputs)


def test_incomplete_sku_parameters_keep_quarter_coverage_validation() -> None:
    inputs = _plan_inputs()
    inputs["finance"].pop()
    before = fingerprint(inputs)
    with pytest.raises(NextYearError, match="预算参数必须无重复且完整覆盖四季度SKU"):
        calculate_plan(inputs)
    assert fingerprint(inputs) == before


def test_zero_entry_allocation_cannot_resurrect_source_or_plan_seed() -> None:
    inputs = _plan_inputs()
    inputs["entry_total"] = 0
    entry = inputs["combinations"][0]
    result = calculate_plan(inputs)
    row = next(r for r in result["rows"] if r["combination_id"] == entry["id"] and r["season"] == "暑假")
    assert row["new_students"] == row["total_students"] == 0
    assert row["planned_new_applied"] is True
    assert sum(r["students"] for r in result["entry_allocations"]) == 0
    assert entry["source_new_by_season"]["暑假"] == 10


def test_absolute_new_share_is_not_clipped_to_near_one_and_reverse_inferred() -> None:
    baseline, forecast = _forecast_inputs()
    for row in baseline["rows"]:
        row["share"] = "1"
    prepared = prepare_inputs(baseline, forecast)
    assert {r["share_ppm"] for r in prepared["rates"]} == {0}
    assert any(n["code"] == "NO_APPLICABLE_PARAMETER" and n["field"] == "share" for n in prepared["notes"])
    result = calculate_plan(prepared)
    assert result["complete"] is True
    assert max(r["total_students"] for r in result["rows"]) <= 100
    assert all(r["share_ppm"] != 999900 for r in prepared["rates"])


@pytest.mark.parametrize("share", ["0.99990001", "0.99995", "0.99999"])
def test_inherited_near_one_share_is_rejected_before_clipping_or_giant_reverse_inference(share: str) -> None:
    baseline, forecast = _forecast_inputs()
    baseline["rows"][0]["share"] = share
    before = fingerprint((baseline, forecast))
    with pytest.raises(NextYearError, match="超过99.99%.*不能截断"):
        prepare_inputs(baseline, forecast)
    assert fingerprint((baseline, forecast)) == before


@pytest.mark.parametrize("field", ["renewal", "share"])
@pytest.mark.parametrize("value", ["-0.01", "1.0001", "2"])
def test_out_of_range_inherited_ratio_cannot_be_omitted_from_valid_peer_pool(field: str, value: str) -> None:
    baseline, forecast = _forecast_inputs([_dims("八年级"), _dims("八年级", "英语")])
    baseline["rows"][0][field] = value
    before = fingerprint((baseline, forecast))
    with pytest.raises(NextYearError, match="继承比例超出"):
        prepare_inputs(baseline, forecast)
    assert fingerprint((baseline, forecast)) == before


def test_supported_inherited_share_boundary_is_preserved_exactly() -> None:
    baseline, forecast = _forecast_inputs()
    for row in baseline["rows"]:
        row["share"] = "0.9999"
    prepared = prepare_inputs(baseline, forecast)
    assert {row["share_ppm"] for row in prepared["rates"]} == {999900}


@pytest.mark.parametrize("bad_share", [1000000, True, -100, 123])
def test_illegal_share_input_does_not_reach_reverse_calculation(bad_share: object) -> None:
    inputs = _plan_inputs()
    inputs["rates"][0]["share_ppm"] = bad_share
    with pytest.raises(NextYearError):
        calculate_plan(inputs)


def test_promotion_graduation_and_geography_biology_endings_reuse_shared_opening_rules() -> None:
    scopes = [
        _dims(),
        _dims("八年级"),
        _dims("八年级", "地理"),
        _dims("八年级", "生物"),
        _dims("九年级", "英语"),
    ]
    baseline, forecast = _forecast_inputs(scopes)
    prepared = prepare_inputs(baseline, forecast)
    sources = {(s["grade"], s["subject"]): s for s in prepared["opening_sources"]}
    assert sources["七年级", "数学"]["target_id"] == _id(_dims("八年级"))
    assert sources["八年级", "数学"]["target_id"] == _id(_dims("九年级"))
    for subject in ("地理", "生物"):
        assert sources["八年级", subject]["target_id"] is None
        assert sources["八年级", subject]["exclusion"] == "SUBJECT_ENDED"
    assert sources["九年级", "英语"]["target_id"] is None
    assert sources["九年级", "英语"]["exclusion"] == "GRADUATED"
    result = calculate_plan(prepared)
    assert result["opening"]["spring_students"] == 500
    assert result["opening"]["eligible_students"] == 200
    assert result["opening"]["graduated_students"] == 100
    assert result["opening"]["subject_ended_students"] == 200
    assert result["opening"]["summer_old_students"] == 160
    assert result["rule_versions"]["opening"] == "forecast-opening-students-v1"


def test_missing_spring_count_is_not_replaced_with_zero() -> None:
    baseline, forecast = _forecast_inputs()
    next(r for r in forecast["calculation"]["rows"] if r["season"] == "春季")["forecast_total"] = None
    with pytest.raises(NextYearError, match="春季预计人次未知"):
        prepare_inputs(baseline, forecast)


def test_weighted_cohort_rates_use_original_bases_not_a_mean_of_percentages() -> None:
    baseline, forecast = _forecast_inputs([_dims("八年级"), _dims("八年级", "英语")])
    for row in baseline["rows"]:
        math = row["subject"] == "数学"
        row["renewal"] = "0.5" if math else "0.9"
        row["share"] = "0.2" if math else "0.8"
        row["total"] = (20 if math else 80) if row["season"] == "春季" else (100 if math else 300)
    prepared = prepare_inputs(baseline, forecast)
    summer = next(r for r in prepared["rates"] if r["season"] == "暑假" and r["grade"] == "八年级")
    assert summer["renewal_ppm"] == 820000  # (20*.5 + 80*.9) / 100.
    assert summer["share_ppm"] == 650000  # (100*.2 + 300*.8) / 400.
    assert summer["renewal_ppm"] != 700000
    assert summer["share_ppm"] != 500000
    assert any(n["code"] == "COHORT_WEIGHTED_PARAMETER" for n in prepared["notes"])


def test_missing_renewal_without_sample_never_becomes_automatic_hundred_percent() -> None:
    baseline, forecast = _forecast_inputs()
    for row in baseline["rows"]:
        row["renewal"] = None
    prepared = prepare_inputs(baseline, forecast)
    assert {r["renewal_ppm"] for r in prepared["rates"]} == {0}
    notes = [
        n for n in prepared["notes"] if n["code"] == "NO_APPLICABLE_PARAMETER" and n["field"] == "renewal"
    ]
    assert notes and all(n["value_ppm"] == 0 for n in notes)
    assert calculate_plan(prepared)["opening"]["summer_old_students"] == 0


def test_missing_renewal_uses_same_track_grade_prior_base_sample_with_explicit_note() -> None:
    baseline, forecast = _forecast_inputs(
        [_dims("八年级"), _dims("八年级", product="白金"), _dims("八年级", "英语", product="白金")]
    )
    for row in baseline["rows"]:
        if row["product"] == "普通":
            row["renewal"] = None
        else:
            math = row["subject"] == "数学"
            row["renewal"] = "0.5" if math else "0.9"
            row["total"] = 20 if math else 80
    prepared = prepare_inputs(baseline, forecast)
    rate = next(
        r
        for r in prepared["rates"]
        if r["season"] == "暑假" and r["grade"] == "八年级" and r["product"] == "普通"
    )
    assert rate["renewal_ppm"] == 820000
    note = next(
        n
        for n in prepared["notes"]
        if n["code"] == "NO_APPLICABLE_PARAMETER"
        and n["field"] == "renewal"
        and n["season"] == "暑假"
        and n["dims"]["grade"] == "八年级"
        and n["dims"]["product"] == "普通"
    )
    assert note["value_ppm"] == 820000
    assert "同赛道同年级" in note["message"]


def test_only_explicit_forecast_hold_size_rule_is_inherited_as_hundred_percent() -> None:
    baseline, forecast = _forecast_inputs([_dims(), _dims("八年级", "英语")])
    scope_dims = _dims("八年级", "英语")
    baseline["rows"] = [r for r in baseline["rows"] if r["subject"] != "英语"]
    forecast["assumptions"]["forecast_scopes"] = [
        {
            "dims": scope_dims,
            "kind": "UNBUDGETED_BUSINESS",
            "reason": "保留已确认的独立规模保持规则",
            "financial": {
                season: {"price": "106", "discount": "1", "recognition": "0.8", "method": "EXPLICIT"}
                for season in SEASONS
            },
            "additional": {},
        }
    ]
    prepared = prepare_inputs(baseline, forecast)
    rate = next(r for r in prepared["rates"] if r["season"] == "暑假" and r["grade"] == "八年级")
    assert rate["renewal_ppm"] == 1000000
    assert any(
        n["code"] == "NO_APPLICABLE_PARAMETER"
        and n["field"] == "renewal"
        and n["season"] == "暑假"
        and "规模保持规则" in n["message"]
        for n in prepared["notes"]
    )


def test_existing_engines_keep_counts_and_unbalanced_income_gap_visible() -> None:
    inputs = _plan_inputs()
    before = fingerprint(inputs)
    result = calculate_plan(inputs)
    by_grade = {grade: [r for r in result["rows"] if r["grade"] == grade] for grade in ("七年级", "八年级")}
    assert [r["total_students"] for r in by_grade["七年级"]] == [10, 10, 10, 10]
    assert [r["total_students"] for r in by_grade["八年级"]] == [100, 100, 100, 100]
    assert result["annual"]["old_students"] == 344
    assert result["annual"]["new_students"] == 96
    assert result["annual"]["total_students"] == 440
    assert result["annual"]["revenue_yuan"] == "41800.000000"
    assert result["annual"]["gap_yuan"] == "958200.000000"
    assert result["target_status"] == "GAP_PENDING_REBALANCE"
    assert result["complete"] is True
    assert result["publication_available"] is False
    assert result["rule_versions"]["headcount"] == OPENING_HEADCOUNT_RULE_VERSION
    assert fingerprint(inputs) == before
    changed = deepcopy(inputs)
    changed["target_revenue_yuan"] = changed["track_targets"]["智慧空间"] = "2000000"
    second = calculate_plan(changed)
    assert second["rows"] == result["rows"]
    assert Decimal(second["annual"]["gap_yuan"]) - Decimal(result["annual"]["gap_yuan"]) == 1000000


def test_opening_carryover_is_added_once_without_dividing_by_tax_again() -> None:
    inputs = _plan_inputs()
    original = calculate_plan(inputs)
    inputs["combinations"][0]["opening_carryover_yuan"] = "123.45"
    result = calculate_plan(inputs)
    assert result["opening"]["carryover_yuan"] == "123.450000"
    assert Decimal(result["annual"]["revenue_yuan"]) - Decimal(original["annual"]["revenue_yuan"]) == Decimal(
        "123.45"
    )
    for index, (before, after) in enumerate(zip(original["quarters"], result["quarters"], strict=True)):
        assert Decimal(after["revenue_yuan"]) - Decimal(before["revenue_yuan"]) == (
            Decimal("123.45") if index == 0 else 0
        )


def test_a_range_with_carryover_cannot_be_disabled_and_silently_drop_money() -> None:
    inputs = _plan_inputs()
    inputs["combinations"][0]["opening_carryover_yuan"] = "123"
    original = deepcopy(inputs)
    inputs["combinations"][0]["enabled"] = False
    with pytest.raises(NextYearError, match="结转"):
        validate_plan_inputs(inputs, original)


def test_financial_only_cross_year_carryover_is_blocked_instead_of_discarded() -> None:
    baseline, forecast = _forecast_inputs()
    finance_dims = _dims("九年级", "物理", product="纯财务")
    forecast["calculation"]["rows"].extend(
        {
            **finance_dims,
            "season": season,
            "forecast_total": None,
            "forecast_old": None,
            "forecast_new": None,
            "forecast_revenue": "10",
            "forecast_carryout": "20" if season == "春季" else "0",
            "counts_not_applicable": True,
        }
        for season in SEASONS
    )
    before = fingerprint((baseline, forecast))
    with pytest.raises(NextYearError, match="纯财务业务有跨年结转"):
        prepare_inputs(baseline, forecast)
    assert fingerprint((baseline, forecast)) == before
