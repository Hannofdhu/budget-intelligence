from apps.api.routes.workspace import project
from packages.contracts.versioning import ResourceFinanceReplaceDraftRequest


def test_form_projection_keeps_exact_money_and_explicit_zero() -> None:
    schema = ResourceFinanceReplaceDraftRequest.model_json_schema()
    data = {
        "budget_tax_conversion_factor": "1.060000000",
        "unrelated_result": 123,
        "opening_carryover_items": [{"amount_yuan": "0", "grade": "八年级", "content_hash": "not-input"}],
    }
    result = project(data, schema, schema)
    assert result == {
        "budget_tax_conversion_factor": "1.060000000",
        "class_size_items": [],
        "revenue_items": [],
        "opening_carryover_items": [{"amount_yuan": "0", "grade": "八年级"}],
    }
