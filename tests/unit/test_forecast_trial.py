from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from packages.forecast.calculation import DIMS, SEASONS
from packages.forecast.rules import FIXED_RULES_VERSION, build_fixed_assumptions
from packages.forecast.trial import build_trial_assumptions

Json = dict[str, Any]


def dims(*, campus: str = "甲", grade: str = "七年级", product: str = "云帆", subject: str = "数学") -> Json:
    return {"track": "智慧空间", "product": product, "grade": grade, "subject": subject, "campus": campus}


def inputs() -> tuple[Json, Json, Json]:
    rows = [
        {
            **dims(campus=campus),
            "season": season,
            "prior_students": base,
            "renewal": rate,
            "new": new,
            "old": old,
            "total": base,
            "price": "106.0",
            "discount": "1",
            "recognition": "0.5",
        }
        for season in SEASONS
        for campus, base, rate, new, old in (("甲", 100, "0.2", 2, 25), ("乙", 900, "0.8", 450, 600))
    ]
    return (
        {"fiscal_year": 2027, "rows": rows},
        {"rows": [], "frozen_carryins": [], "issues": [], "metric_verified": True},
        {
            "states": {"暑假": "FINALIZED", "秋季": "OPEN", "寒假": "FUTURE", "春季": "FUTURE"},
            "minimum_sample": 30,
            "mappings": [],
            "adjustments": [],
        },
    )


def source(*, season: str = "秋季", **dimensions: str) -> Json:
    return {
        **dims(campus="丙"),
        "season": season,
        "enrollment": 10,
        "new_business": 2,
        "new_base": 2,
        "old_base": 8,
        "actual_revenue": "100",
        "actual_carryout": "25",
        **dimensions,
    }


def mapping(source_dims: Json, target_dims: Json) -> Json:
    return {"source": source_dims, "target": target_dims, "reason": "已由业务方确认"}


def test_weighted_rates_use_prior_base_and_aggregate_share_not_rate_averages() -> None:
    baseline, snapshot, prior = inputs()
    snapshot["rows"] = [source()]
    result = build_trial_assumptions(baseline, snapshot, prior)
    scope = result["forecast_scopes"][0]
    assert set(scope["financial"]) == {"秋季", "寒假", "春季"}
    for season in SEASONS[1:]:
        financial = scope["financial"][season]
        assert Decimal(financial["renewal"]) == Decimal("0.74")  # (100*.2 + 900*.8)/1000
        assert Decimal(financial["share"]) == Decimal("0.452")  # (2+450)/(100+900)
        assert financial["method"] == "TRIAL_SAME_GRADE_WEIGHTED"
    assert scope["additional"] == {"秋季": 0}
    assert result["trial_mode"] is True


def test_no_same_grade_ratio_keeps_grade_product_and_explicit_carry_forward() -> None:
    baseline, snapshot, prior = inputs()
    snapshot["rows"] = [source(grade="八年级")]
    result = build_trial_assumptions(baseline, snapshot, prior)
    scope = result["forecast_scopes"][0]
    assert scope["dims"] == dims(campus="丙", grade="八年级")
    assert result["mappings"] == []  # no cloud-to-ordinary or grade change
    assert scope["kind"] == "UNBUDGETED_BUSINESS"
    for financial in scope["financial"].values():
        assert financial["renewal"] is financial["share"] is None
        assert financial["method"] == "TRIAL_CARRY_FORWARD"
        assert Decimal(financial["price"]) == 106  # unique same-product financial template


def test_no_valid_denominator_or_full_new_share_never_invents_ratios() -> None:
    baseline, snapshot, prior = inputs()
    snapshot["rows"] = [source()]
    for row in baseline["rows"]:
        row["prior_students"] = 0
        row["total"] = row["new"] = 0
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert all(
        financial["renewal"] is financial["share"] is None
        for financial in result["forecast_scopes"][0]["financial"].values()
    )
    for row in baseline["rows"]:
        row["prior_students"] = row["total"] = row["new"] = 100
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert all(
        financial["renewal"] is financial["share"] is None
        for financial in result["forecast_scopes"][0]["financial"].values()
    )


@pytest.mark.parametrize("field,value", [("price", "999"), ("discount", None), ("recognition", "1.1")])
def test_conflicting_or_invalid_financial_parameters_are_not_averaged(field: str, value: object) -> None:
    baseline, snapshot, prior = inputs()
    snapshot["rows"] = [source()]
    baseline["rows"][3][field] = value  # one autumn donor only
    scope = build_trial_assumptions(baseline, snapshot, prior)["forecast_scopes"][0]
    assert set(scope["financial"]) == {"寒假", "春季"}


def test_confirmed_campus_alias_merges_only_equal_complete_dimensions() -> None:
    baseline, snapshot, prior = inputs()
    prior["mappings"] = [mapping(dims(campus="甲旧"), dims(campus="甲"))]
    snapshot["rows"] = [source(campus="甲旧", grade="八年级"), source(campus="甲", grade="八年级")]
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert len(result["forecast_scopes"]) == 1
    assert result["forecast_scopes"][0]["dims"] == dims(campus="甲", grade="八年级")
    assert len(result["mappings"]) == 2
    assert result["mappings"][1]["source"] == dims(campus="甲旧", grade="八年级")
    assert result["mappings"][1]["target"] == dims(campus="甲", grade="八年级")
    assert build_trial_assumptions(baseline, snapshot, result) == result


def test_existing_budget_target_does_not_receive_independent_scope() -> None:
    baseline, snapshot, prior = inputs()
    prior["mappings"] = [mapping(dims(campus="甲旧", subject="英语"), dims(campus="甲", subject="英语"))]
    snapshot["rows"] = [source(campus="甲旧")]
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert result["forecast_scopes"] == []
    assert result["mappings"][-1]["target"] == dims(campus="甲")


def test_conflicting_campus_alias_is_not_generalized() -> None:
    baseline, snapshot, prior = inputs()
    prior["mappings"] = [
        mapping(dims(campus="旧甲"), dims(campus="甲")),
        mapping(dims(campus="旧甲", subject="英语"), dims(campus="乙", subject="英语")),
    ]
    snapshot["rows"] = [source(campus="旧甲", grade="八年级")]
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert len(result["mappings"]) == 2
    assert result["forecast_scopes"][0]["dims"]["campus"] == "旧甲"


def test_explicit_high_end_relation_only_generalizes_ninth_grade_and_same_track() -> None:
    baseline, snapshot, prior = inputs()
    prior["mappings"] = [
        mapping(dims(product="卓越", grade="九年级"), dims(product="高端小班", grade="九年级"))
    ]
    snapshot["rows"] = [
        source(product="卓越", grade="九年级"),
        source(product="卓越", grade="八年级"),
        source(product="卓越", grade="九年级", track="智慧伴学"),
    ]
    result = build_trial_assumptions(baseline, snapshot, prior)
    scope_keys = {tuple(scope["dims"][field] for field in DIMS) for scope in result["forecast_scopes"]}
    assert ("智慧空间", "高端小班", "九年级", "数学", "丙") in scope_keys
    assert ("智慧空间", "卓越", "八年级", "数学", "丙") in scope_keys
    assert ("智慧伴学", "卓越", "九年级", "数学", "丙") in scope_keys
    assert result["mappings"][-1]["target"]["product"] == "高端小班"


def test_unconfirmed_product_mapping_is_not_generalized() -> None:
    baseline, snapshot, prior = inputs()
    prior["mappings"] = [mapping(dims(product="新产品"), dims(product="普通"))]
    snapshot["rows"] = [source(product="新产品")]
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert result["forecast_scopes"][0]["dims"]["product"] == "新产品"
    assert len(result["mappings"]) == 1


def test_financial_only_signed_adjustment_gets_no_future_population_parameters() -> None:
    baseline, snapshot, prior = inputs()
    row = source(campus="网络", actual_revenue="-50")
    row.update({field: 0 for field in ("enrollment", "new_business", "new_base", "old_base")})
    snapshot["rows"] = [row]
    result = build_trial_assumptions(baseline, snapshot, prior)
    scope = result["forecast_scopes"][0]
    assert scope["kind"] == "FINANCIAL_ONLY"
    assert scope["financial"] == scope["additional"] == {}
    assert snapshot["rows"][0]["actual_revenue"] == "-50"


def test_missing_counts_are_not_financial_only_or_zero_filled() -> None:
    baseline, snapshot, prior = inputs()
    row = source(campus="网络", actual_revenue="-50")
    row.update(enrollment=None, new_business=0, new_base=0, old_base=0)
    snapshot["rows"] = [row]
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert result["forecast_scopes"][0]["kind"] == "UNBUDGETED_BUSINESS"
    assert snapshot["rows"][0]["enrollment"] is None


def test_confirmed_cloud_business_identity_survives_explicit_zero_enrollment() -> None:
    baseline, snapshot, prior = inputs()
    row = source(campus="百学汇公主坟校区", actual_revenue="-50")
    row.update({field: 0 for field in ("enrollment", "new_business", "new_base", "old_base")})
    snapshot["rows"] = [row]
    scope = build_trial_assumptions(baseline, snapshot, prior)["forecast_scopes"][0]
    assert scope["kind"] == "UNBUDGETED_BUSINESS"
    assert scope["additional"] == {"秋季": 0}
    assert scope["financial"]


def test_ended_season_zero_additional_is_explicit_and_frozen_quarter_has_no_parameters() -> None:
    baseline, snapshot, prior = inputs()
    prior["states"]["秋季"] = "ENDED"
    snapshot["rows"] = [source()]
    scope = build_trial_assumptions(baseline, snapshot, prior)["forecast_scopes"][0]
    assert scope["additional"] == {"秋季": 0}
    assert "暑假" not in scope["financial"]


def test_only_current_activity_not_future_or_dormant_history_creates_scope() -> None:
    baseline, snapshot, prior = inputs()
    dormant = source(campus="历史")
    dormant.update(
        {field: 0 for field in ("enrollment", "new_business", "new_base", "old_base", "actual_revenue")}
    )
    dormant["history_new_final_d"] = 999
    snapshot["rows"] = [dormant, source(season="春季", campus="未来")]
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert result["forecast_scopes"] == result["mappings"] == []


def test_eligible_carry_only_scope_preserves_origin_and_does_not_create_students() -> None:
    baseline, snapshot, prior = inputs()
    snapshot["frozen_carryins"] = [
        {**dims(campus="结转"), "season": "春季", "origin_season": 20264, "revenue": "-12.34"},
        {**dims(campus="未定版"), "season": "春季", "origin_season": 20272, "revenue": "30"},
        {**dims(campus="已含实际"), "season": "暑假", "origin_season": 20264, "revenue": "20"},
        {**dims(campus="零结转"), "season": "春季", "origin_season": 20264, "revenue": "0"},
    ]
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert len(result["forecast_scopes"]) == 1
    scope = result["forecast_scopes"][0]
    assert scope["dims"]["campus"] == "结转"
    assert scope["kind"] == "FINANCIAL_ONLY"
    assert scope["financial"] == {}


def test_inputs_and_manual_assumptions_are_preserved_missing_budget_never_actual_zero() -> None:
    baseline, snapshot, prior = inputs()
    prior["adjustments"] = [
        {"season": "秋季", "campus": "甲", "kind": "additional", "value": 3, "reason": "人工"}
    ]
    before = deepcopy((baseline, snapshot, prior))
    result = build_trial_assumptions(baseline, snapshot, prior)
    assert (baseline, snapshot, prior) == before
    assert result["forecast_scopes"] == []  # no fake actual rows for missing budget scopes
    assert result["adjustments"] == prior["adjustments"]
    assert result["states"] == prior["states"]
    assert result["minimum_sample"] == prior["minimum_sample"]
    assert "trial_mode" not in prior


def test_duplicate_baseline_cannot_choose_hidden_parameter_winner() -> None:
    baseline, snapshot, prior = inputs()
    baseline["rows"].append(deepcopy(baseline["rows"][0]))
    with pytest.raises(ValueError, match="完整维度存在重复"):
        build_trial_assumptions(baseline, snapshot, prior)


def test_deterministic_order_and_json_compatible_decimal_boundaries() -> None:
    baseline, snapshot, prior = inputs()
    snapshot["rows"] = [source(campus="丁"), source(campus="丙")]
    first = build_trial_assumptions(baseline, snapshot, prior)
    snapshot["rows"].reverse()
    baseline["rows"].reverse()
    assert build_trial_assumptions(baseline, snapshot, prior) == first
    assert first["forecast_scopes"][0]["dims"]["campus"] == "丁"


def test_fixed_policy_inherits_every_saved_override_and_does_not_mutate_inputs() -> None:
    baseline, snapshot, prior = inputs()
    snapshot["rows"] = [source()]
    prior = build_trial_assumptions(baseline, snapshot, prior)
    prior["adjustments"] = [
        {"season": "秋季", "campus": "甲", "kind": "new_withdrawal", "value": 3, "reason": "人工"}
    ]
    prior["forecast_scopes"][0]["financial"]["寒假"]["price"] = "106.00123456789"
    prior["forecast_scopes"][0]["additional"]["秋季"] = 7
    prior["minimum_sample"] = 31
    before = deepcopy((baseline, snapshot, prior))
    result = build_fixed_assumptions(baseline, snapshot, prior)
    assert result["trial_mode"] is False
    assert result["fixed_rules_version"] == FIXED_RULES_VERSION
    for field in ("forecast_scopes", "mappings", "adjustments", "states", "minimum_sample"):
        assert result[field] == prior[field]
    assert build_fixed_assumptions(baseline, snapshot, result) == result
    assert (baseline, snapshot, prior) == before


def test_fixed_policy_adds_missing_scope_without_changing_product_grade_or_frozen_parameters() -> None:
    baseline, snapshot, prior = inputs()
    snapshot["rows"] = [source(grade="八年级")]
    result = build_fixed_assumptions(baseline, snapshot, prior)
    scope = result["forecast_scopes"][0]
    assert scope["dims"] == dims(campus="丙", grade="八年级")
    assert "暑假" not in scope["financial"]
    assert scope["financial"]["春季"]["renewal"] is None
    assert scope["additional"] == {"秋季": 0}
    assert result["mappings"] == []


def test_fixed_policy_rejects_unknown_versions_instead_of_replacing_them() -> None:
    baseline, snapshot, prior = inputs()
    prior["fixed_rules_version"] = "unknown"
    with pytest.raises(ValueError, match="未知固定预测规则"):
        build_fixed_assumptions(baseline, snapshot, prior)
