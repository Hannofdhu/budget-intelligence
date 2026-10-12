from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from packages.config.settings import Settings
from packages.forecast.source import (
    CURRENT_FIELDS,
    DIMENSIONS,
    FINANCE_DATASET_ID,
    HISTORY_FIELDS,
    SUMMARY_FIELDS,
    SUMMARY_REVENUE_FIELD,
    _expected_roster_formula,
    aggregate_forecast_rows,
    extract_forecast_source,
    verify_metric_formulas,
)
from packages.source_snapshot.adapter import DatasetMetadata, SourceAdapterError


def summary(season: int = 20271, revenue: object = "10", **changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "dtdate": "2026-10-03",
        "季节财年": season,
        "财季": f"Q{season % 10}",
        **dict(zip(DIMENSIONS.values(), ("初中", "智慧空间", "七年级", "数学", "花园桥"), strict=True)),
        **dict.fromkeys(SUMMARY_FIELDS.values(), 0),
        "招生人次": 100,
        "新生人次": 20,
        "新生退班基数(在班基数)": 20,
        "老生退班基数(在班基数)": 80,
        "退班基数(在班基数)": 100,
        SUMMARY_REVENUE_FIELD: revenue,
    }
    value.update(changes)
    return value


def finance(
    amount: object = "10",
    year: int = 2027,
    quarter: str = "Q1",
    origin_year: int = 2026,
    origin_quarter: str = "春季",
    **changes: Any,
) -> dict[str, Any]:
    value = {
        "财年": year,
        "财季": quarter,
        "业务归属年": origin_year,
        "业务归属季度": origin_quarter,
        **dict(zip(DIMENSIONS.values(), ("初中", "智慧空间", "七年级", "数学", "花园桥"), strict=True)),
        "预计结转收入": amount,
    }
    value.update(changes)
    return value


def etls() -> tuple[dict[str, Any], dict[str, Any]]:
    aggregate = [f"SUM(`{upstream}`) `{upstream}`" for _, upstream in HISTORY_FIELDS.values()]
    projection = [f"ifnull(t3.`{upstream}`,0) `{physical}`" for physical, upstream in HISTORY_FIELDS.values()]
    summary_sql = (
        "SELECT "
        + ",".join(aggregate + projection)
        + (
            " FROM t1 JOIN t3 ON round((t1.`业务归属年`-1)*10+t1.`业务归属季度`,0)="
            "round(t3.`业务归属年`*10+t3.`业务归属季度`,0)"
        )
        + " AND "
        + " AND ".join(
            f"t1.`{field}`=t3.`{field}`" for field in (*DIMENSIONS.values(), "是否白金标识(白金,普通)")
        )
    )
    roster_sql = "SELECT\n" + ",\n".join(
        f"{_expected_roster_formula(key)} `{upstream}`" for key, (_, upstream) in HISTORY_FIELDS.items()
    )
    return (
        {"data": {"utime": "2026-08-30", "actions": [{"id": "summary-node", "sql": summary_sql}]}},
        {
            "data": {
                "utime": "2026-09-20",
                "dataFlowId": "roster-etl",
                "actions": [{"id": "roster-node", "sql": roster_sql}],
            }
        },
    )


def codes(issues: list[dict[str, Any]]) -> set[str]:
    return {issue["code"] for issue in issues}


def test_verified_mapping_requires_all_eight_formulas_not_numeric_coincidence() -> None:
    wide, roster = etls()
    verified, receipt = verify_metric_formulas(wide, roster)
    assert verified
    assert len(receipt["history_fields"]) == 8
    assert all(item["formula_verified"] for item in receipt["history_fields"].values())
    assert len(receipt["formula_hash"]) == 64
    roster["data"]["actions"][0]["sql"] = roster["data"]["actions"][0]["sql"].replace("'老生'", "'新生'", 1)
    assert not verify_metric_formulas(wide, roster)[0]


def test_same_cohort_requires_all_business_dimensions_and_platinum_marker() -> None:
    wide, roster = etls()
    wide["data"]["actions"][0]["sql"] = wide["data"]["actions"][0]["sql"].replace(
        "t1.`教学区`=t3.`教学区`", "t1.`教学区`=t3.`科目`"
    )
    assert not verify_metric_formulas(wide, roster)[0]


def test_sums_numerators_and_denominators_before_rates_and_keeps_flat_dimensions() -> None:
    first = summary(
        revenue="3",
        **{"新生同期退班人次-过程值": 2, "同期退班人次-过程值": 2, "新生同期退班基数(退班率分母)-过程值": 10},
    )
    second = summary(
        revenue="7",
        **{"新生同期退班人次-过程值": 8, "同期退班人次-过程值": 8, "新生同期退班基数(退班率分母)-过程值": 90},
    )
    rows, frozen, issues, _ = aggregate_forecast_rows([first, second], [finance()])
    assert not issues
    assert rows[0]["history_new_process_n"] == 10
    assert rows[0]["history_new_process_d"] == 100
    assert rows[0]["actual_revenue"] == Decimal(10)
    assert rows[0]["track"] == "初中" and "dims" not in rows[0]
    assert frozen[0]["campus"] == "花园桥" and frozen[0]["origin_season"] == 20264


def test_raw_complement_and_current_base_failures_cannot_cancel_each_other() -> None:
    first = summary(revenue="5", **{"同期退班人次-过程值": 1, "老生退班基数(在班基数)": 81})
    second = summary(revenue="5", **{"老生同期退班人次-过程值": 1, "老生退班基数(在班基数)": 79})
    _, _, issues, proof = aggregate_forecast_rows([first, second], [finance()])
    assert {"WITHDRAWAL_COMPLEMENT_MISMATCH", "CURRENT_BASE_MISMATCH"} <= codes(issues)
    assert proof["complement_failure_count"] == 2 and proof["current_base_failure_count"] == 2


@pytest.mark.parametrize("count", [None, "", "-", "NaN", "Infinity", True, -1, "0.5"])
def test_invalid_counts_are_not_coalesced_to_zero(count: object) -> None:
    changes: dict[str, Any] = {"新生人次": count}
    rows, _, issues, _ = aggregate_forecast_rows([summary(**changes)], [finance()])
    assert rows[0]["new_business"] is None
    assert "SOURCE_COUNT_INVALID" in codes(issues)


def test_only_explicit_all_zero_incomplete_dimensions_may_be_excluded() -> None:
    empty = {**summary(revenue="0"), **dict.fromkeys(SUMMARY_FIELDS.values(), 0), "教学区": ""}
    rows, _, issues, proof = aggregate_forecast_rows([empty], [])
    assert rows == [] and not issues and proof["excluded_zero_incomplete_rows"] == 1
    empty["新生人次"] = None
    assert "SOURCE_DIMENSIONS_INCOMPLETE" in codes(aggregate_forecast_rows([empty], [])[2])


def test_signed_financial_corrections_and_old_origin_future_carryins_are_preserved() -> None:
    rows, carries, issues, proof = aggregate_forecast_rows(
        [summary(revenue="8"), summary(20272, revenue="5.25")],
        [finance("10"), finance("-2"), finance("5.25", quarter="Q2")],
    )
    assert not issues and proof["signed_financial_adjustment_rows"] == 1
    assert rows[0]["actual_revenue"] == 8
    assert {(item["season"], item["origin_season"], item["revenue"]) for item in carries} == {
        ("暑假", 20264, Decimal(8)),
        ("秋季", 20264, Decimal("5.25")),
    }


def test_financial_carryout_uses_actual_not_budget_and_spring_is_next_financial_year() -> None:
    rows, carries, issues, _ = aggregate_forecast_rows(
        [summary(revenue="0"), summary(20272, revenue="102"), summary(20274, revenue="0")],
        [
            finance("102", quarter="Q2", origin_year=2027, origin_quarter="暑假"),
            finance("99", year=2028, origin_year=2027, origin_quarter="春季"),
        ],
    )
    assert not issues
    by_season = {row["season"]: row for row in rows}
    assert by_season["暑假"]["actual_carryout"] == 102
    assert by_season["春季"]["actual_carryout"] == 99
    assert sum(row["actual_revenue"] for row in rows) == 102
    assert all(item["revenue"] != 99 for item in carries)


def test_latest_partition_actual_is_canonical_and_raw_finance_must_reconcile() -> None:
    rows, _, issues, _ = aggregate_forecast_rows([summary(revenue="10")], [finance("11")])
    assert rows[0]["actual_revenue"] == 10
    assert {"FINANCIAL_PARTITION_MISMATCH", "FINANCIAL_DEPARTMENT_MISMATCH"} <= codes(issues)
    assert not aggregate_forecast_rows([summary(revenue="10")], [finance("10.009")])[2]


def test_missing_financial_amount_remains_missing_and_nonzero_blank_dimensions_block() -> None:
    rows, carries, issues, _ = aggregate_forecast_rows([summary()], [finance(None)])
    assert rows[0]["actual_revenue"] == 10
    assert carries[0]["revenue"] is None
    assert "ACTUAL_REVENUE_SOURCE_INVALID" in codes(issues)
    changes: dict[str, Any] = {"教学区": ""}
    assert "FINANCIAL_DIMENSIONS_INCOMPLETE" in codes(
        aggregate_forecast_rows([summary()], [finance(**changes)])[2]
    )


class FakeAdapter:
    def __init__(
        self,
        *,
        change_metadata: bool = False,
        change_partition: bool = False,
        mixed_partition: bool = False,
        formulas: bool = True,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.formula_calls: list[tuple[str, ...]] = []
        self.metadata_calls = 0
        self.preview_calls = 0
        self.change_metadata = change_metadata
        self.change_partition = change_partition
        self.mixed_partition = mixed_partition
        self.formulas = formulas

    async def _metadata(self, dataset: str) -> DatasetMetadata:
        self.metadata_calls += 1
        version = 2 if self.change_metadata and self.metadata_calls > 3 else 1
        return DatasetMetadata(
            dataset, "safe", version, 4, datetime(2026, 10, 4, tzinfo=UTC), datetime(2026, 10, 4, tzinfo=UTC)
        )

    async def _preview(self, dataset: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.preview_calls += 1
        partition = "2026-10-04" if self.change_partition and self.preview_calls > 1 else "2026-10-03"
        return [{"dtdate": partition}]

    async def _complete_partition(self, dataset: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append((dataset, kwargs))
        if dataset == FINANCE_DATASET_ID:
            return [finance("0")] if "财年 EQ 2027" in kwargs["filters"] else []
        season = int(kwargs["filters"][0].split()[-1])
        return [summary(season, revenue="0", dtdate="mixed" if self.mixed_partition else "2026-10-03")]

    async def _run_json(self, *args: str) -> dict[str, Any]:
        self.formula_calls.append(args)
        wide, roster = etls()
        return (wide if args[3].startswith("h991") else roster) if self.formulas else {"data": {}}


async def test_read_only_extraction_is_count_proved_partition_bound_and_has_formula_receipt() -> None:
    adapter = FakeAdapter()
    snapshot = await extract_forecast_source(Settings(), adapter=adapter)
    assert snapshot["metric_verified"] and not snapshot["issues"]
    assert snapshot["as_of"] == "2026-10-03" and len(snapshot["rows"]) == 4
    assert snapshot["actual_totals"]["暑假"] == {"actual_revenue": Decimal(0), "enrollment": Decimal(100)}
    assert len(snapshot["provenance"]["datasets"]) == 3
    assert len(snapshot["provenance"]["metric_mapping"]["history_fields"]) == 8
    assert adapter.metadata_calls == 6 and len(adapter.calls) == 6
    assert len(adapter.formula_calls) == 2
    assert all(
        call[:3] == ("--raw", "etl", "get") and call[-1] == "--brief" for call in adapter.formula_calls
    )
    for dataset, request in adapter.calls:
        assert "学员姓名" not in request["columns"]
        if dataset == FINANCE_DATASET_ID:
            assert not any("季节财年" in item for item in request["filters"])
            assert "目标标识(目标,数据) EQ 数据" in request["filters"]
        else:
            assert SUMMARY_REVENUE_FIELD in request["columns"]
            assert "新生人次" in request["columns"] and "当季新招新生" not in request["columns"]


async def test_saved_source_rows_omit_unrequested_identity_fields() -> None:
    class ExtraFieldsAdapter(FakeAdapter):
        async def _complete_partition(self, dataset: str, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {**row, "学员姓名": "must-not-store", "学员ID": "private-id"}
                for row in await super()._complete_partition(dataset, **kwargs)
            ]

    adapter = ExtraFieldsAdapter()
    snapshot = await extract_forecast_source(Settings(), adapter=adapter)
    for kind in ("summary_rows", "financial_rows"):
        assert snapshot["source_data"][kind]
        for row in snapshot["source_data"][kind]:
            assert "学员姓名" not in row and "学员ID" not in row
    assert snapshot["source_data"]["summary_rows"][0]["dtdate"] == "2026-10-03"
    assert snapshot["source_data"]["summary_rows"][0][SUMMARY_REVENUE_FIELD] == "0"


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"change_metadata": True}, "FORECAST_SOURCE_CHANGED_DURING_EXTRACTION"),
        ({"change_partition": True}, "FORECAST_SOURCE_CHANGED_DURING_EXTRACTION"),
        ({"mixed_partition": True}, "FORECAST_SOURCE_PARTITION_MIXED"),
    ],
)
async def test_changing_source_rejects_snapshot(kwargs: dict[str, bool], code: str) -> None:
    with pytest.raises(SourceAdapterError) as error:
        await extract_forecast_source(Settings(), adapter=FakeAdapter(**kwargs))
    assert error.value.code == code
    assert error.value.retryable == (code == "FORECAST_SOURCE_CHANGED_DURING_EXTRACTION")


async def test_missing_sql_proof_does_not_auto_verify_from_matching_counts() -> None:
    snapshot = await extract_forecast_source(Settings(), adapter=FakeAdapter(formulas=False))
    assert not snapshot["metric_verified"]
    assert "WITHDRAWAL_MAPPING_UNVERIFIED" in codes(snapshot["issues"])


def test_current_business_new_uses_business_label_not_classification() -> None:
    assert CURRENT_FIELDS["new_business"] == "新生人次"
