"""Synthetic FY28 source -> immutable opening -> real headcount API."""

import os
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apps.api.main import create_app
from packages.calculation_pipeline import run_full_calculation_job
from packages.contracts.versioning import (
    DiscountParameterReplaceDraftRequest,
    ResourceFinanceReplaceDraftRequest,
    TargetSetReplaceAndLockRequest,
)
from packages.domain.identity.models import Actor, Role
from packages.optimization.engine import DecisionVector
from packages.optimization.evaluator import BudgetCandidateEvaluator
from packages.persistence.database import get_session_factory
from packages.persistence.models import AsyncJob, BudgetRevision, SourceSnapshotComponent
from packages.persistence.optimization import _build_context
from packages.persistence.resource_finance import resource_finance_batch_content_hash
from packages.persistence.revenue import discount_parameter_batch_content_hash
from packages.persistence.target_closure import target_set_batch_content_hash
from tests.integration.test_rebalancing_real_journey import Journey
from tests.integration.test_source_snapshot_builder import _build, _create_rate_budget_chain, _extract, _row

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1", reason="requires isolated PostgreSQL"
)


async def verify_worker_downstream(
    j: Journey, money_scale: int = 1, combinations: list[dict[str, Any]] | None = None
) -> None:
    evidence = {"source_evidence": {"reference": "synthetic-fy28"}, "change_reason": "synthetic"}
    product = {"track": "初中", "product": "智慧空间"}
    grades = ("七年级", "八年级", "九年级")
    large = combinations is not None
    baseline_revenue = 189608000 if large else 402000 * money_scale
    dimensions = (
        combinations
        if combinations is not None
        else [{"grade": grade, "subject": "数学", "campus": "西直门"} for grade in grades]
    )
    seasons = range(20281, 20285)
    resource = {
        "budget_tax_conversion_factor": "1.06",
        "tax_factor_source_evidence": {"reference": "synthetic"},
        "tax_factor_change_reason": "synthetic",
        "class_size_items": [dict(product, system_value=1, final_value=1, **evidence)],
        "revenue_items": [
            dict(
                product,
                fiscal_season=s,
                list_price_yuan=str(1060 * money_scale),
                current_season_recognition_rate_ppm=1000000,
                opening_carryover_revenue_yuan="0",
                **evidence,
            )
            for s in seasons
        ],
        "opening_carryover_items": [
            dict(
                product,
                fiscal_season=20281,
                grade=row["grade"],
                subject=row["subject"],
                campus=row["campus"],
                amount_yuan="0",
                **evidence,
            )
            for row in dimensions
        ],
        "batch_content_hash": "0" * 64,
    }
    resource["batch_content_hash"] = resource_finance_batch_content_hash(
        ResourceFinanceReplaceDraftRequest.model_validate(resource)
    )
    saved = await j.post("/resource-finance:replace-draft", resource)
    await j.post(
        "/resource-finance:confirm",
        {"resource_finance_parameter_version_id": saved["resource_finance_parameter_version_id"]},
    )
    discount = {
        "detail_items": [
            dict(product, fiscal_season=s, grade=grade, discount_rate_ppm=500000, **evidence)
            for s in seasons
            for grade in grades
        ],
        "composite_targets": [
            dict(
                scope_level=scope,
                track=track,
                period_type=period,
                fiscal_season=s,
                target_rate_ppm=500000,
                **evidence,
            )
            for scope, track in (("DEPARTMENT", None), ("TRACK", "初中"))
            for period, s in [("ANNUAL", None), *(("SEASON", s) for s in seasons)]
        ],
        "batch_content_hash": "0" * 64,
    }
    discount["batch_content_hash"] = discount_parameter_batch_content_hash(
        DiscountParameterReplaceDraftRequest.model_validate(discount)
    )
    saved = await j.post("/discount-parameters:replace-draft", discount)
    await j.post(
        "/discount-parameters:confirm",
        {"discount_parameter_version_id": saved["discount_parameter_version_id"]},
    )
    target: dict[str, Any] = {
        "items": [
            dict(scope_level=scope, track=track, target_revenue_yuan=str(baseline_revenue), **evidence)
            for scope, track in (("DEPARTMENT", None), ("TRACK", "初中"))
        ],
        "batch_content_hash": "0" * 64,
    }
    target["batch_content_hash"] = target_set_batch_content_hash(
        TargetSetReplaceAndLockRequest.model_validate(target)
    )
    await j.post("/targets:replace-and-lock", target)
    job = await j.post(":calculate", {})
    result = await j.run_job(job["job_id"])
    assert result["headcount_calculation_rule_version"] == "phase1-headcount-opening-v2"
    assert result["headcount_publishable"] is True
    assert result["class_size_publishable"] is True
    assert result["revenue_publishable"] is True
    async with get_session_factory()() as session:
        stored = await session.get(AsyncJob, uuid.UUID(job["job_id"]))
        assert stored is not None
        payload = stored.payload
    replay = await run_full_calculation_job(uuid.UUID(job["job_id"]), payload)
    assert replay == result
    revenue = await j.get(f"revenue-result-versions/{result['revenue_result_version_id']}")
    assert sum(Decimal(row["quarter_recognized_revenue_yuan"]) for row in revenue["items"]) == Decimal(
        baseline_revenue
    )
    if large:
        assert len(revenue["items"]) == 952

    closure = await j.closure()
    department = next(row for row in closure["items"] if row["scope_level"] == "DEPARTMENT")
    assert Decimal(department["variance_yuan"]) == 0
    readiness = await j.get(f"revisions/{j.revision}/publication-readiness")
    opening_gate = next(row for row in readiness["checks"] if row["code"] == "OPENING_CALCULATION_READY")
    assert opening_gate["passed"] is True
    preview = await j.post("/optimization-runs", {"solver_adapter": "EXACT_ENUMERATION"})
    assert preview["status"] == "OPTIMAL", preview
    assert preview["scenarios"]
    async with get_session_factory()() as session:
        revision = await session.get(BudgetRevision, uuid.UUID(j.revision))
        assert revision is not None
        context, _, _ = await _build_context(session, revision, 2028)
    endpoint = BudgetCandidateEvaluator(context).evaluate(
        DecisionVector(
            entry_values=tuple((row.stable_id, row.current_value) for row in context.entry_decisions),
            rate_values=tuple((row.stable_id, row.upper_bound_ppm) for row in context.rate_decisions),
        )
    )
    assert endpoint.hard_constraints_satisfied
    assert endpoint.revenue_r11_yuan > Decimal(baseline_revenue + 10000)
    for row in target["items"]:
        row["target_revenue_yuan"] = str(endpoint.revenue_r11_yuan)
    target["batch_content_hash"] = target_set_batch_content_hash(
        TargetSetReplaceAndLockRequest.model_validate(target)
    )
    await j.post("/targets:replace-and-lock", target)
    await j.run_job((await j.post(":calculate", {}))["job_id"])
    original = await j.get(f"revisions/{j.revision}")
    solved = await j.post("/optimization-runs", {"solver_adapter": "CP_SAT_ENTRY_AND_RATES"})
    assert solved["status"] == "OPTIMAL", solved
    candidate = solved["scenarios"][0]
    application = await j.post(
        f"/scenarios/{candidate['rebalancing_scenario_id']}:apply", {"reason": "synthetic FY28"}
    )
    await j.run_job(application["calculation_job_id"])
    applied = await j.get(f"revisions/{j.revision}")
    assert (
        applied["component_manifest"]["opening_student_version_id"]
        != original["component_manifest"]["opening_student_version_id"]
    )
    assert (await j.closure())["publishable"] is True
    new_opening = await j.get(
        f"opening-student-versions/{applied['component_manifest']['opening_student_version_id']}"
    )
    assert new_opening["content"]["derivation"] == "SCENARIO_APPLICATION"
    undone = await j.post(
        f"/scenario-applications/{application['scenario_application_id']}:revert",
        {"reason": "synthetic undo"},
    )
    await j.run_job(undone["calculation_job_id"])
    restored = await j.get(f"revisions/{j.revision}")
    assert (
        restored["component_manifest"]["opening_student_version_id"]
        == original["component_manifest"]["opening_student_version_id"]
    )
    assert (await j.closure())["publishable"] is False
    solved = await j.post("/optimization-runs", {"solver_adapter": "CP_SAT_ENTRY_AND_RATES"})
    application = await j.post(
        f"/scenarios/{solved['scenarios'][0]['rebalancing_scenario_id']}:apply",
        {"reason": "synthetic reapply"},
    )
    await j.run_job(application["calculation_job_id"])
    formal = await j.post(":confirm-formal-plan", {"reason": "synthetic FY28 owner confirmation"})
    await j.run_job(formal["job_id"])
    published = await j.post(":publish", {"publication_note": "synthetic FY28 golden verification"})
    assert published["published_revision_id"] == j.revision
    assert published["formal_plan_confirmation_id"]
    budget = await j.get(f"annual-budgets/{j.budget_id}")
    assert budget["current_published_revision_id"] == j.revision
    if large:
        final_revision_response = await j.get(f"revisions/{j.revision}")
        final_revenue = await j.get(
            f"revenue-result-versions/{final_revision_response['component_manifest']['revenue_result_version_id']}"
        )
        assert len(final_revenue["items"]) == 952


@pytest.mark.parametrize("money_scale,large", [(1, False), (280, False), (4, True)])
async def test_fy28_opening_and_headcount_api(money_scale: int, large: bool) -> None:
    department = f"opening-api-{uuid.uuid4()}"
    extract = _extract(2027)
    roster = [
        {**_row(fiscal_season=season), "招生人次": 100, "目标标识(目标,数据)": "数据"}
        for season in range(20271, 20275)
    ]
    summary = [_row(summary=True, fiscal_season=season) for season in range(20271, 20275)]
    roster += [{**row, "年级": grade} for grade in ("八年级", "九年级") for row in roster[:4]]
    summary += [{**row, "年级": grade} for grade in ("八年级", "九年级") for row in summary[:4]]
    campuses = [f"合成校区-{index:02d}" for index in range(17)] if large else ["西直门"]
    if large:
        subjects = ("数学", "语文", "英语", "物理", "地理")
        roster = [
            {**row, "科目": subject, "教学区": campus}
            for row in roster
            for campus in campuses
            for subject in subjects
            if not (row["年级"] == "九年级" and subject == "地理")
        ]
        summary = [
            {**row, "科目": subject, "教学区": campus}
            for row in summary
            for campus in campuses
            for subject in subjects
            if not (row["年级"] == "九年级" and subject == "地理")
        ]
    snapshot, built = await _build(
        replace(extract, roster_rows=roster, summary_rows=summary), fiscal_year=2028, department_id=department
    )
    assert built["status"] == "FROZEN"
    actor = Actor("real-journey-test", department, frozenset({Role.DATA_MAINTAINER}))
    reference, budget = await _create_rate_budget_chain(snapshot_id=snapshot, fiscal_year=2028, actor=actor)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        j = Journey(client, department, str(budget.new_revision_id), budget.annual_budget_id)
        combos = await j.post(
            "/combinations:generate-draft",
            {"rate_reference_version_id": str(reference.rate_reference_version_id)},
        )
        assert len(combos["items"]) == (238 if large else 3)
        await j.post(
            "/combinations:confirm", {"combination_set_version_id": combos["combination_set_version_id"]}
        )
        await j.post(
            "/entry-total:save-and-lock",
            {"fiscal_year_entry_total": 1, "change_reason": "synthetic"},
        )
        suggestion = await j.post(
            "/campus-entry:generate-suggestions",
            {
                "campuses": [
                    {
                        "campus": campus,
                        "classroom_resource": {"rooms": 10},
                        "sales_resource": {"consultants": 5},
                    }
                    for campus in campuses
                ]
            },
        )
        campus = await j.post(
            "/campus-entry:replace-draft",
            {
                "items": [
                    {
                        "campus": campus,
                        "determined_entry_value": 1 if campus == campuses[0] else 0,
                        "adjustment_reason_category": "PERFORMANCE_JUDGMENT",
                        "adjustment_reason_detail": "合成验收固定单一入口；不代表真实校区目标",
                        "performance_evidence": {"reference": "synthetic"},
                        "classroom_resource": suggestion["items"][0]["classroom_resource"],
                        "sales_resource": suggestion["items"][0]["sales_resource"],
                    }
                    for campus in campuses
                ]
            },
        )
        await j.post(
            "/campus-entry:confirm", {"campus_entry_set_version_id": campus["campus_entry_set_version_id"]}
        )
        exclusions = (
            [row for row in combos["items"] if row["grade"] == "七年级" and row["subject"] != "数学"]
            if large
            else []
        )
        scope = await j.post(
            "/entry-scope:replace-draft",
            {
                "items": [
                    {
                        **{key: row[key] for key in ("track", "product", "grade", "subject", "campus")},
                        "fiscal_year": 2028,
                        "exception_type": "EXCLUDE",
                        "reason": "合成验收固定单一入口，其他明细使用计划新生",
                    }
                    for row in exclusions
                ]
            },
        )
        await j.post("/entry-scope:confirm", {"entry_scope_version_id": scope["entry_scope_version_id"]})
        await j.post("/entry-detail:calculate", {"manual_reservations": []})
        references = await j.get(
            f"rate-reference-versions/{reference.rate_reference_version_id}/references?limit=500"
        )
        rates = await j.post(
            "/rate-parameters:replace-draft",
            {
                "rate_reference_version_id": str(reference.rate_reference_version_id),
                "items": [
                    {
                        "rate_reference_id": row["rate_reference_id"],
                        "current_value_ppm": 800000 if row["rate_type"] == "RENEWAL_RATE" else 200000,
                        "lower_bound_ppm": 800000 if row["rate_type"] == "RENEWAL_RATE" else 200000,
                        "upper_bound_ppm": (
                            (801000 if large else 850000)
                            if row["rate_type"] == "RENEWAL_RATE"
                            and row["target_fiscal_season"] == 20281
                            and row["grade"] in ("七年级", "八年级")
                            and (not large or row["campus"] in campuses[:3])
                            else 800000
                            if row["rate_type"] == "RENEWAL_RATE"
                            else 200000
                        ),
                        "boundary_reason": "synthetic",
                        "manual_reason": "synthetic",
                    }
                    for row in references["items"]
                ],
            },
        )
        renewals = {
            (row["campus"], row["grade"]): row
            for row in rates["items"]
            if row["rate_type"] == "RENEWAL_RATE" and row["target_fiscal_season"] == 20281
        }
        await j.post(
            "/rate-boundaries:confirm",
            {
                "rate_parameter_version_id": rates["rate_parameter_version_id"],
                "rate_parameter_stable_keys": [row["rate_parameter_stable_key"] for row in rates["items"]],
            },
        )
        async with get_session_factory()() as session:
            component_id = await session.scalar(
                select(SourceSnapshotComponent.source_snapshot_component_id).where(
                    SourceSnapshotComponent.source_snapshot_id == snapshot,
                    SourceSnapshotComponent.dataset_id == "roster",
                )
            )
        workspace = await j.get(f"revisions/{j.revision}/opening-students/workspace")
        assert workspace["is_current_draft"] is True
        assert workspace["source_fiscal_season"] == 20274
        assert len(workspace["combinations"]) == len(combos["items"])
        source = next(s for s in workspace["sources"] if s["source_component_id"] == str(component_id))
        assert len(source["rows"]) == len(combos["items"])
        assert all(r["enrollment"] == 100 and r["rate_parameter_id"] for r in source["rows"])
        denied_workspace = await client.get(
            f"/api/v1/revisions/{j.revision}/opening-students/workspace",
            headers={**j.headers, "X-Department-ID": "unrelated-department"},
        )
        assert denied_workspace.status_code == 404
        payload = {
            "source_component_id": str(component_id),
            "source_fiscal_season": 20274,
            "reason": "synthetic",
            "zero_combination_ids": [
                row["combination_id"] for row in combos["items"] if row["grade"] == "七年级"
            ],
            "rows": [
                {
                    "track": "初中",
                    "product": "智慧空间",
                    "grade": row["grade"],
                    "subject": row["subject"],
                    "campus": row["campus"],
                    "enrollment": 100,
                    "rate_parameter_id": renewals[(row["campus"], row["grade"])]["rate_parameter_id"],
                }
                for row in combos["items"]
            ],
        }
        before = j.revision
        headers = {**j.headers, "If-Match": f'"{before}"', "Idempotency-Key": str(uuid.uuid4())}
        saved = await client.post(
            f"/api/v1/revisions/{before}/opening-students", json=payload, headers=headers
        )
        assert saved.status_code == 201, saved.text
        replay = await client.post(
            f"/api/v1/revisions/{before}/opening-students", json=payload, headers=headers
        )
        assert replay.json() == saved.json()
        j.revision = saved.json()["revision"]["new_revision_id"]
        version = await j.get(f"opening-student-versions/{saved.json()['opening_student_version_id']}")
        assert version["content"]["source_verification"] == "FROZEN_AGGREGATES_MATCHED"
        if large:
            await j.post(
                "/planned-new-students:change-and-lock",
                {
                    "items": [
                        {
                            **{key: row[key] for key in ("track", "product", "grade", "subject", "campus")},
                            "fiscal_season": 20281,
                            "entry_attribute": "ENTRY" if row["subject"] == "数学" else "NON_ENTRY",
                            "planned_new_students": 100,
                            "reason": "合成验收显式计划新生，不作为自动调平杠杆",
                        }
                        for row in combos["items"]
                        if row["grade"] == "七年级"
                        and (row["campus"], row["subject"]) != (campuses[0], "数学")
                    ]
                },
            )
        result = await j.post(
            "/headcounts:calculate", {"calculation_rule_version": "phase1-headcount-opening-v2"}
        )
        assert result["calculation_rule_version"] == "phase1-headcount-opening-v2"
        first = next(
            row for row in result["items"] if row["grade"] == "八年级" and row["fiscal_season"] == 20281
        )
        assert first["integer_old_students"] == 80
        assert first["integer_new_students"] == 20
        autumn = next(
            row for row in result["items"] if row["grade"] == "八年级" and row["fiscal_season"] == 20282
        )
        assert autumn["integer_old_students"] == 80
        assert result["publishable"] is True, result.get("validations")
        await verify_worker_downstream(j, money_scale, combos["items"] if large else None)
        denied = await client.get(
            f"/api/v1/opening-student-versions/{saved.json()['opening_student_version_id']}",
            headers={**j.headers, "X-Department-ID": f"other-{uuid.uuid4()}"},
        )
        assert denied.status_code == 404  # Do not disclose another department's version.
        # Even identical values in a new parameter version invalidate the old binding.
        await j.post(
            "/rate-parameters:replace-draft",
            {
                "rate_reference_version_id": str(reference.rate_reference_version_id),
                "items": [
                    {
                        "rate_reference_id": row["rate_reference_id"],
                        "current_value_ppm": 800000 if row["rate_type"] == "RENEWAL_RATE" else 200000,
                        "lower_bound_ppm": 0,
                        "upper_bound_ppm": 900000,
                        "boundary_reason": "synthetic",
                        "manual_reason": "synthetic",
                    }
                    for row in references["items"]
                ],
            },
        )
        stale = await client.post(
            f"/api/v1/revisions/{j.revision}/headcounts:calculate",
            json={"calculation_rule_version": "phase1-headcount-opening-v2"},
            headers={**j.headers, "If-Match": f'"{j.revision}"', "Idempotency-Key": str(uuid.uuid4())},
        )
        assert stale.status_code == 409, stale.text
        assert "OPENING_BASIS_STALE" in stale.text
