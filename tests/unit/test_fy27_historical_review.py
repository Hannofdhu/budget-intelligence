import copy
import json
from decimal import Decimal

import pytest

from packages.source_snapshot import fy27_review as review


@pytest.fixture
def source():
    if not review.EXTRACTION.exists():
        pytest.skip("Local FY27 source is not checked into Git")
    return json.loads(review.EXTRACTION.read_text())


def test_source_totals_and_exact_cell_values(source):
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


def test_preserves_negative_blank_text_and_formula_evidence(source):
    result = review.build_review(source)
    assert next(r for r in result["rows"] if r["row"] == 151)["opening"] == -0.01
    summer = next(r for r in result["rows"] if r["row"] == 14)
    assert (summer["prior_students"], summer["old"]) == (183, 115)
    assert summer["sources"]["old"]["formula"] == "=ROUND(AE14*AI14,0)"
    assert summer["parameters"]["renewal"]["cell"].startswith("参数表!AN")
    assert any(r["parameters"]["share"] and r["parameters"]["share"]["value"] is None for r in result["rows"])
    assert result["rows"][0]["prior_students"] == "-"
    assert "publishable" not in result


def test_rejects_changed_totals(source):
    changed = copy.deepcopy(source)
    changed["sheets"]["滚动表"]["rows"][1]["cells"]["W"]["value"] += 1
    with pytest.raises(ValueError, match="合计不一致"):
        review.build_review(changed)


def test_current_income_and_carryout_reconcile_across_fiscal_years(source):
    result = review.build_review(source)
    rows = result["rows"]

    def total(items, field):
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
            row["opening"] if row["season"] == "暑假" else
            next(item["carryout"] for item in rows if item["row"] == row["row"] and
                 review.SEASONS.index(item["season"]) == review.SEASONS.index(row["season"]) - 1)
        )
        assert abs(Decimal(str(row["revenue"])) - Decimal(str(row["current"])) - Decimal(str(prior))) < Decimal(".00001")


def test_rejects_stale_extraction(source, tmp_path, monkeypatch):
    original = tmp_path / "source.xlsx"
    original.write_bytes(b"different source")
    extraction = tmp_path / "source.json"
    extraction.write_text(json.dumps(source))
    monkeypatch.setattr(review, "SOURCE", original)
    monkeypatch.setattr(review, "EXTRACTION", extraction)
    with pytest.raises(ValueError, match="已变化"):
        review.read_review()


@pytest.mark.asyncio
async def test_history_access_local_only_and_department_scoped(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    from apps.api.routes import historical_review as route
    from packages.domain.identity.models import Actor, Role

    async def session(_):
        return {"departmentId": "test-dept"}

    monkeypatch.setattr(route, "trial_session", session)
    monkeypatch.setattr(route, "read_review", lambda: {"mode": "HISTORICAL_EXCEL_READ_ONLY"})
    actor = Actor(user_id="owner", department_id="test-dept", roles=frozenset({Role.DEPARTMENT_BUDGET_OWNER}))

    def request(address):
        return Request({"type": "http", "headers": [(b"x-budget-client-address", address.encode())]})

    assert (await route.fy27_review(request("127.0.0.1"), actor))["mode"] == "HISTORICAL_EXCEL_READ_ONLY"
    for address in ("", "192.168.1.2", "unknown"):
        with pytest.raises(HTTPException) as exc:
            await route.fy27_review(request(address), actor)
        assert exc.value.status_code == 403
    outsider = Actor(user_id="other", department_id="other-dept", roles=actor.roles)
    with pytest.raises(HTTPException) as exc:
        await route.fy27_review(request("127.0.0.1"), outsider)
    assert exc.value.status_code == 403
