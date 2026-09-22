from apps.api.routes.workspace import entry_detail_form_value, project, resource_finance_form_seed
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


def test_entry_detail_form_restores_only_manual_reservations() -> None:
    result = entry_detail_form_value(
        {
            "items": [
                {
                    "track": "初中",
                    "product": "智慧伴学",
                    "grade": "七年级",
                    "subject": "语文",
                    "campus": "中关村南",
                    "manual_reservation_ratio_ppm": 1_000_000,
                    "manual_reservation_reason": "新产品人工分配",
                },
                {
                    "track": "初中",
                    "product": "智慧空间",
                    "grade": "七年级",
                    "subject": "数学",
                    "campus": "西直门",
                    "manual_reservation_ratio_ppm": None,
                    "manual_reservation_reason": None,
                },
            ]
        }
    )
    assert result == {
        "manual_reservations": [
            {
                "track": "初中",
                "product": "智慧伴学",
                "grade": "七年级",
                "subject": "语文",
                "campus": "中关村南",
                "reservation_ratio_ppm": 1_000_000,
                "reason": "新产品人工分配",
            }
        ]
    }


def test_resource_finance_seed_covers_confirmed_business_dimensions() -> None:
    result = resource_finance_form_seed(
        [
            {
                "enabled": True,
                "track": "初中",
                "product": "智慧空间",
                "grade": "七年级",
                "subject": "数学",
                "campus": "西直门",
            },
            {
                "enabled": True,
                "track": "初中",
                "product": "智慧空间",
                "grade": "八年级",
                "subject": "数学",
                "campus": "西直门",
            },
            {
                "enabled": True,
                "track": "初中",
                "product": "智慧伴学",
                "grade": "七年级",
                "subject": "语文",
                "campus": "中关村南",
            },
            {
                "enabled": False,
                "track": "初中",
                "product": "不参与",
                "grade": "七年级",
                "subject": "数学",
                "campus": "西直门",
            },
        ],
        2028,
    )
    assert len(result["class_size_items"]) == 2
    assert len(result["revenue_items"]) == 8
    assert len(result["opening_carryover_items"]) == 3
    assert {row["fiscal_season"] for row in result["revenue_items"]} == {20281, 20282, 20283, 20284}
    assert {row["fiscal_season"] for row in result["opening_carryover_items"]} == {20281}
