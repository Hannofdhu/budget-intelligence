import uuid

import pytest
from pydantic import ValidationError

from packages.contracts.versioning import (
    BudgetCalculationEnqueueRequest,
    ClassSizeValidateRequest,
    DiscountUnlockCreateRequest,
    FormalPlanConfirmRequest,
    HeadcountCalculateRequest,
    OptimizationRunCreateRequest,
    ParameterVersionCreate,
    RateReferenceGenerateRequest,
    ResourceFinanceReplaceDraftRequest,
    ScenarioApplicationRevertRequest,
    ScenarioApplyRequest,
)
from packages.domain.versioning.hashing import content_hash


def test_content_hash_is_deterministic_and_rejects_binary_float() -> None:
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})
    with pytest.raises(TypeError):
        content_hash({"ratio": 0.1})


def test_parameter_version_requires_exactly_four_component_types() -> None:
    component = {
        "component_version_id": str(uuid.uuid4()),
        "content_hash": "a" * 64,
        "schema_version": "1.0",
        "record_count": 1,
    }
    with pytest.raises(ValidationError):
        ParameterVersionCreate.model_validate(
            {
                "source_snapshot_id": str(uuid.uuid4()),
                "components": [
                    {"component_type": "RATE_PARAMETERS", **component},
                    {"component_type": "RATE_PARAMETERS", **component},
                    {"component_type": "RESOURCE_FINANCE", **component},
                    {"component_type": "DISCOUNTS", **component},
                ],
            }
        )


def test_rate_reference_generation_requires_four_prior_year_mature_seasons() -> None:
    valid = RateReferenceGenerateRequest.model_validate(
        {
            "target_fiscal_year": 2027,
            "mature_source_fiscal_seasons": [20264, 20261, 20263, 20262],
            "maturity_evidence_reference": "全部班级结课并完成后续 ETL 刷新",
        }
    )
    assert valid.minimum_sample_size == 30
    with pytest.raises(ValidationError):
        RateReferenceGenerateRequest.model_validate(
            {
                "target_fiscal_year": 2027,
                "mature_source_fiscal_seasons": [20261, 20262, 20263, 20263],
                "maturity_evidence_reference": "无效重复季节",
            }
        )


def test_headcount_calculation_rule_is_fixed_and_versioned() -> None:
    assert HeadcountCalculateRequest.model_validate({}).calculation_rule_version == "phase1-headcount-v1"
    with pytest.raises(ValidationError):
        HeadcountCalculateRequest.model_validate({"calculation_rule_version": "latest"})


def test_class_size_validation_rule_is_fixed_and_versioned() -> None:
    assert ClassSizeValidateRequest.model_validate({}).validation_rule_version == "phase1-class-size-v1"
    with pytest.raises(ValidationError):
        ClassSizeValidateRequest.model_validate({"validation_rule_version": "latest"})


def test_optimization_run_contract_fixes_model_version_and_state_limit() -> None:
    request = OptimizationRunCreateRequest.model_validate({})
    assert request.solver_adapter == "EXACT_ENUMERATION"
    assert request.maximum_enumerated_states == 100_000
    assert request.maximum_solver_seconds == 300
    assert request.maximum_candidate_validations == 10_000
    assert request.maximum_group_table_rows == 100_000
    assert request.optimization_model_version == "phase1-deterministic-rebalancing-v3"
    with pytest.raises(ValidationError):
        OptimizationRunCreateRequest.model_validate({"maximum_enumerated_states": 0})
    with pytest.raises(ValidationError):
        OptimizationRunCreateRequest.model_validate({"maximum_group_table_rows": 0})
    with pytest.raises(ValidationError):
        OptimizationRunCreateRequest.model_validate({"solver_adapter": "UNSAFE_HEURISTIC"})
    assert (
        OptimizationRunCreateRequest.model_validate(
            {"solver_adapter": "CP_SAT_ENTRY_AND_RATES"}
        ).solver_adapter
        == "CP_SAT_ENTRY_AND_RATES"
    )
    with pytest.raises(ValidationError):
        OptimizationRunCreateRequest.model_validate({"optimization_model_version": "latest"})


def test_discount_unlock_requires_an_exact_run_proof_and_non_blank_reason() -> None:
    payload = DiscountUnlockCreateRequest.model_validate(
        {
            "optimization_run_id": str(uuid.uuid4()),
            "infeasibility_proof_id": str(uuid.uuid4()),
            "reason": "比例边界已穷尽，申请启用最后折扣杠杆",
        }
    )
    assert payload.reason.startswith("比例边界")
    with pytest.raises(ValidationError):
        DiscountUnlockCreateRequest.model_validate(
            {
                "optimization_run_id": str(uuid.uuid4()),
                "infeasibility_proof_id": str(uuid.uuid4()),
                "reason": "   ",
            }
        )


def test_scenario_application_requires_a_non_blank_audit_reason() -> None:
    assert ScenarioApplyRequest(reason="采用平衡候选方案").reason == "采用平衡候选方案"
    with pytest.raises(ValidationError):
        ScenarioApplyRequest(reason="   ")


def test_scenario_application_revert_requires_a_non_blank_audit_reason() -> None:
    request = ScenarioApplicationRevertRequest(reason="恢复应用前预算输入")
    assert request.reason == "恢复应用前预算输入"
    with pytest.raises(ValidationError):
        ScenarioApplicationRevertRequest(reason="   ")


def test_full_calculation_pipeline_version_is_fixed() -> None:
    request = BudgetCalculationEnqueueRequest.model_validate({})
    assert request.calculation_pipeline_version == "phase1-full-calculation-v1"
    with pytest.raises(ValidationError):
        BudgetCalculationEnqueueRequest.model_validate({"calculation_pipeline_version": "latest"})


def test_formal_confirmation_requires_reason_and_a_fixed_recalculation_pipeline() -> None:
    request = FormalPlanConfirmRequest(reason="确认 FY27 完整预算方案")
    assert request.calculation_pipeline_version == "phase1-full-calculation-v1"
    with pytest.raises(ValidationError):
        FormalPlanConfirmRequest(reason="   ")
    with pytest.raises(ValidationError):
        FormalPlanConfirmRequest(reason="确认", calculation_pipeline_version="latest")


def test_resource_finance_contract_rejects_duplicate_parameter_keys() -> None:
    class_item = {
        "track": "智慧空间",
        "product": "THINK",
        "system_value": 12,
        "final_value": 12,
        "source_evidence": {"reference": "FY27 班均表"},
        "change_reason": "初始值导入",
    }
    with pytest.raises(ValidationError):
        ResourceFinanceReplaceDraftRequest.model_validate(
            {
                "budget_tax_conversion_factor": "1.06",
                "tax_factor_source_evidence": {"reference": "FY27 学校版"},
                "tax_factor_change_reason": "初始值导入",
                "class_size_items": [class_item, class_item],
                "revenue_items": [
                    {
                        "fiscal_season": 20271,
                        "track": "智慧空间",
                        "product": "THINK",
                        "list_price_yuan": "1000",
                        "current_season_recognition_rate_ppm": 750_000,
                        "opening_carryover_revenue_yuan": "0",
                        "source_evidence": {"reference": "FY27 结转表"},
                        "change_reason": "初始值导入",
                    }
                ],
                "opening_carryover_items": [
                    {
                        "fiscal_season": 20271,
                        "track": "智慧空间",
                        "product": "THINK",
                        "grade": "七年级",
                        "subject": "数学",
                        "campus": "中关村",
                        "amount_yuan": "0",
                        "source_evidence": {"reference": "FY26 春季结转"},
                        "change_reason": "初始值导入",
                    }
                ],
                "batch_content_hash": "a" * 64,
            }
        )
