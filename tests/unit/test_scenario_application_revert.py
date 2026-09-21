from packages.persistence.scenario_application import _restorable_source_components


def test_revert_restores_only_pre_application_inputs_and_invalidates_results() -> None:
    manifest = {
        "schema_version": "1.0",
        "target_set_version_id": "target-v1",
        "entry_total_version_id": "entry-total-v1",
        "rate_parameter_version_id": "rates-v1",
        "headcount_result_version_id": "headcount-v1",
        "revenue_result_version_id": "revenue-v1",
        "target_closure_result_version_id": "closure-v1",
        "optimization_run_id": "run-v1",
        "discount_unlock_id": "unlock-v1",
        "formal_confirmation_id": "confirmation-v1",
        "scenario_application_id": "application-v1",
        "non_string_diagnostic": {"ignored": True},
    }

    assert _restorable_source_components(manifest) == {
        "target_set_version_id": "target-v1",
        "entry_total_version_id": "entry-total-v1",
        "rate_parameter_version_id": "rates-v1",
    }
