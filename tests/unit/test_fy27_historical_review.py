import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from packages.source_snapshot import fy27_review as review


@pytest.fixture
def source() -> Any:
    if not review.EXTRACTION.exists():
        pytest.skip("Local FY27 source is not checked into Git")
    return json.loads(review.EXTRACTION.read_text())


def column_number(column: str) -> int:
    result = 0
    for char in column:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def insert_blank_columns(source: dict[str, Any], *columns: str) -> dict[str, Any]:
    shifted = copy.deepcopy(source)
    sheet = shifted["sheets"]["滚动表"]
    for column in columns:
        at = column_number(column)
        for row in sheet["rows"]:
            row["cells"] = {
                column_letter(column_number(col) + (column_number(col) >= at)): cell
                for col, cell in row["cells"].items()
            }
            row["cells"][column] = {
                "value": None if row["row"] == 1 else -987654.321,
                "formula": None if row["row"] == 1 else "=1/0",
            }
        sheet["max_column"] += 1
    return shifted


def with_header_cells(source: dict[str, Any], cells: dict[str, Any]) -> dict[str, Any]:
    return {
        **source,
        "sheets": {
            **source["sheets"],
            "滚动表": {**source["sheets"]["滚动表"], "rows": [{"row": 1, "cells": cells}]},
        },
    }


REQUIRED_LEGACY_COLUMNS = [
    *"A B C D E F G W AE AG AH".split(),
    *(column for group in review.COLUMNS for column in group.split()),
]


def test_legacy_saved_headers_resolve_original_source_coordinates(source: dict[str, Any]) -> None:
    columns = review.resolve_review_columns(source["sheets"]["滚动表"]["rows"])
    assert columns["base"] == dict(zip(review.BASE_HEADERS, "A B C D E F G AE AG AH W".split(), strict=True))
    for season, original in zip(review.SEASONS, review.COLUMNS, strict=True):
        assert columns[season] == dict(zip(review.FIELDS, original.split(), strict=True))
    assert columns["helpers"]["暑假.renewal"] == "U"
    assert [columns["helpers"][f"{season}.share"] for season in review.SEASONS] == list("PQRS")


def test_blank_header_insertions_preserve_values_and_use_shifted_sources(source: dict[str, Any]) -> None:
    original = review.build_review(source)
    shifted = insert_blank_columns(source, "BZ", "CR")
    result = review.build_review(shifted)
    assert {key: value for key, value in result.items() if key != "rows"} == {
        key: value for key, value in original.items() if key != "rows"
    }
    for before, after in zip(original["rows"], result["rows"], strict=True):
        assert {key: value for key, value in before.items() if key != "sources"} == {
            key: value for key, value in after.items() if key != "sources"
        }
        for field, before_cell in before["sources"].items():
            after_cell = after["sources"][field]
            assert {key: value for key, value in before_cell.items() if key != "cell"} == {
                key: value for key, value in after_cell.items() if key != "cell"
            }
            source_column = after_cell["cell"].split("!")[1].removesuffix(str(after["row"]))
            assert source_column not in ("BZ", "CR")
            assert shifted["sheets"]["滚动表"]["rows"][after["row"] - 1]["cells"][source_column] == {
                key: value for key, value in after_cell.items() if key != "cell"
            }
    columns = review.resolve_review_columns(shifted["sheets"]["滚动表"]["rows"])
    assert columns["寒假"]["list_revenue"] == "CA"
    assert columns["寒假"]["revenue"] == "CG"
    assert columns["寒假"]["carryout"] == "CH"
    assert columns["春季"]["list_revenue"] == "CS"
    assert columns["春季"]["revenue"] == "CY"
    assert columns["春季"]["carryout"] == "CZ"


def test_dimensions_and_annual_opening_columns_follow_exact_headers(source: dict[str, Any]) -> None:
    shifted = copy.deepcopy(source)
    swaps = {"A": "G", "G": "A", "W": "AF", "AF": "W", "AE": "X", "X": "AE"}
    swaps.update({"AG": "Y", "Y": "AG", "AH": "Z", "Z": "AH"})
    for row in shifted["sheets"]["滚动表"]["rows"]:
        row["cells"] = {swaps.get(col, col): cell for col, cell in row["cells"].items()}
    original, result = review.build_review(source), review.build_review(shifted)
    assert result["annual_revenue"] == original["annual_revenue"]
    assert result["scope_count"] == original["scope_count"]
    for before, after in zip(original["rows"], result["rows"], strict=True):
        assert {key: value for key, value in before.items() if key != "sources"} == {
            key: value for key, value in after.items() if key != "sources"
        }
        for field, col in ("annual", "AF"), ("prior_students", "X"), ("opening", "Y"), ("minimum", "Z"):
            assert after["sources"][field]["cell"] == f"滚动表!{col}{after['row']}"


@pytest.mark.parametrize("column", REQUIRED_LEGACY_COLUMNS)
def test_rejects_missing_required_header(source: dict[str, Any], column: str) -> None:
    cells = copy.deepcopy(source["sheets"]["滚动表"]["rows"][0]["cells"])
    cells[column]["value"] = None
    with pytest.raises(ValueError, match="缺失必要表头"):
        review.build_review(with_header_cells(source, cells))


@pytest.mark.parametrize("column", REQUIRED_LEGACY_COLUMNS)
def test_rejects_duplicate_required_header(source: dict[str, Any], column: str) -> None:
    cells = copy.deepcopy(source["sheets"]["滚动表"]["rows"][0]["cells"])
    cells["ZZ"] = copy.deepcopy(cells[column])
    with pytest.raises(ValueError, match="必要表头重复"):
        review.build_review(with_header_cells(source, cells))


@pytest.mark.parametrize("column", review.FIXED_HELPER_HEADERS)
def test_rejects_shifted_or_missing_fixed_helper_header(source: dict[str, Any], column: str) -> None:
    cells = copy.deepcopy(source["sheets"]["滚动表"]["rows"][0]["cells"])
    cells[column]["value"] = None
    with pytest.raises(ValueError, match="辅助列表头不匹配"):
        review.build_review(with_header_cells(source, cells))


@pytest.mark.parametrize(
    ("column", "alternate"),
    [("BM", "FY27秋-结转节奏"), ("CF", "FY27-Q3总结转(税后)"), ("CW", "FY27-Q4总结转(税后)")],
)
def test_accepts_explicit_f27_fy27_header_alternates(
    source: dict[str, Any], column: str, alternate: str
) -> None:
    shifted = copy.deepcopy(source)
    shifted["sheets"]["滚动表"]["rows"][0]["cells"][column]["value"] = alternate
    assert review.build_review(shifted) == review.build_review(source)


@pytest.mark.parametrize(
    ("column", "alternate"),
    [("BM", "FY27秋-结转节奏"), ("CF", "FY27-Q3总结转(税后)"), ("CW", "FY27-Q4总结转(税后)")],
)
def test_rejects_both_alternate_headers_present(source: dict[str, Any], column: str, alternate: str) -> None:
    cells = copy.deepcopy(source["sheets"]["滚动表"]["rows"][0]["cells"])
    cells["ZZ"] = {**cells[column], "value": alternate}
    with pytest.raises(ValueError, match="必要表头重复"):
        review.build_review(with_header_cells(source, cells))


def test_source_totals_and_exact_cell_values(source: dict[str, Any]) -> None:
    result = review.build_review(source)
    assert result["scope_count"] == 238
    assert result["season_count"] == 952
    assert Decimal(result["annual_revenue"]).quantize(Decimal(".01")) == Decimal("113004618.59")
    assert result["entry_total"] == "8007"
    assert result["prior_students"] == "5951"
    assert result["opening_old"] == "3876"
    for row in result["rows"]:
        for field, cell in row["sources"].items():
            assert row[field] == cell["value"]
    assert (
        sum(
            r["new"]
            for r in result["rows"]
            if r["season"] == "暑假" and r["entry"] == "是" and r["grade"] == "七年级"
        )
        == 7700
    )


def test_preserves_negative_blank_text_and_formula_evidence(source: dict[str, Any]) -> None:
    result = review.build_review(source)
    assert next(r for r in result["rows"] if r["row"] == 151)["opening"] == -0.01
    summer = next(r for r in result["rows"] if r["row"] == 14)
    assert (summer["prior_students"], summer["old"]) == (183, 115)
    assert summer["sources"]["old"]["formula"] == "=ROUND(AE14*AI14,0)"
    assert summer["parameters"]["renewal"]["cell"].startswith("参数表!AN")
    assert any(r["parameters"]["share"] and r["parameters"]["share"]["value"] is None for r in result["rows"])
    assert result["rows"][0]["prior_students"] == "-"
    assert "publishable" not in result


def test_rejects_changed_totals(source: dict[str, Any]) -> None:
    changed = copy.deepcopy(source)
    changed["sheets"]["滚动表"]["rows"][1]["cells"]["W"]["value"] += 1
    with pytest.raises(ValueError, match="合计不一致"):
        review.build_review(changed)


def test_current_income_and_carryout_reconcile_across_fiscal_years(source: dict[str, Any]) -> None:
    result = review.build_review(source)
    rows = result["rows"]

    def total(items: list[dict[str, Any]], field: str) -> Decimal:
        return sum((Decimal(str(row[field])) for row in items), Decimal(0))

    generated = total(rows, "current") + total(rows, "carryout")
    opening = total([row for row in rows if row["season"] == "暑假"], "opening")
    next_year = total([row for row in rows if row["season"] == "春季"], "carryout")
    assert generated.quantize(Decimal(".01")) == Decimal("112480004.34")
    assert opening == Decimal("7847731.60")
    assert next_year.quantize(Decimal(".01")) == Decimal("7323117.35")
    assert abs(generated + opening - next_year - Decimal(result["annual_revenue"])) < Decimal(".00001")
    for row in rows:
        prior = (
            row["opening"]
            if row["season"] == "暑假"
            else next(
                item["carryout"]
                for item in rows
                if item["row"] == row["row"]
                and review.SEASONS.index(item["season"]) == review.SEASONS.index(row["season"]) - 1
            )
        )
        assert abs(
            Decimal(str(row["revenue"])) - Decimal(str(row["current"])) - Decimal(str(prior))
        ) < Decimal(".00001")


def test_rejects_stale_extraction(
    source: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "source.xlsx"
    original.write_bytes(b"different source")
    extraction = tmp_path / "source.json"
    extraction.write_text(json.dumps(source))
    monkeypatch.setattr(review, "SOURCE", original)
    monkeypatch.setattr(review, "EXTRACTION", extraction)
    with pytest.raises(ValueError, match="已变化"):
        review.read_review()


@pytest.mark.asyncio
async def test_history_access_local_only_and_department_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from starlette.requests import Request

    from apps.api.routes import historical_review as route
    from packages.domain.identity.models import Actor, Role

    async def session(_: Request) -> dict[str, str]:
        return {"departmentId": "test-dept"}

    baseline = {"mode": "HISTORICAL_EXCEL_READ_ONLY", "sha256": "immutable-baseline"}
    reads: list[str] = []

    async def database_review(department_id: str) -> dict[str, Any]:
        reads.append(department_id)
        return copy.deepcopy(baseline)

    monkeypatch.setattr(route, "trial_session", session)
    monkeypatch.setattr(route, "read_database_review", database_review)
    actor = Actor(user_id="owner", department_id="test-dept", roles=frozenset({Role.DEPARTMENT_BUDGET_OWNER}))

    def request(address: str) -> Request:
        return Request({"type": "http", "headers": [(b"x-budget-client-address", address.encode())]})

    result = await route.fy27_review(request("127.0.0.1"), actor)
    assert result == baseline
    assert result["sha256"] == "immutable-baseline"
    for address in ("", "192.168.1.2", "unknown"):
        with pytest.raises(HTTPException) as exc:
            await route.fy27_review(request(address), actor)
        assert exc.value.status_code == 403
    outsider = Actor(user_id="other", department_id="other-dept", roles=actor.roles)
    with pytest.raises(HTTPException) as exc:
        await route.fy27_review(request("127.0.0.1"), outsider)
    assert exc.value.status_code == 403
    assert reads == ["test-dept"]
