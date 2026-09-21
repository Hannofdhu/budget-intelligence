"""Synthetic source only: real PostgreSQL, commands, kernels, CP-SAT and worker.

No inserted solver outcomes, no repaired pointers, and no mocked business calculation.
"""

import asyncio
import os
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from apps.api.main import create_app
from apps.worker.main import handle_one
from packages.contracts.versioning import (
    DiscountParameterReplaceDraftRequest,
    ResourceFinanceReplaceDraftRequest,
    TargetSetReplaceAndLockRequest,
)
from packages.domain.identity.models import Actor, Role
from packages.persistence.database import get_session_factory
from packages.persistence.jobs import JobRepository
from packages.persistence.models import AnnualBudget
from packages.persistence.resource_finance import resource_finance_batch_content_hash
from packages.persistence.revenue import discount_parameter_batch_content_hash
from packages.persistence.target_closure import target_set_batch_content_hash
from tests.integration.test_source_snapshot_builder import _build, _create_rate_budget_chain, _extract
from tests.unit.test_optimization_process import _uncooperative_cpu_work

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1", reason="requires migrated PostgreSQL"
)


class Journey:
    def __init__(self, client: AsyncClient, department: str, revision: str, budget_id: uuid.UUID) -> None:
        self.client = client
        self.revision = revision
        self.budget_id = budget_id
        self.headers = {
            "X-User-ID": "real-journey-test",
            "X-Department-ID": department,
            "X-Roles": "DATA_MAINTAINER,DEPARTMENT_BUDGET_OWNER",
        }

    async def get(self, path: str) -> dict[str, Any]:
        response = await self.client.get(f"/api/v1/{path}", headers=self.headers)
        assert response.status_code == 200, response.text
        return dict(response.json())

    async def post(self, suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/revisions/{self.revision}{suffix}",
            json=payload,
            headers={**self.headers, "If-Match": f'"{self.revision}"', "Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code in (200, 201, 202), response.text
        body: dict[str, Any] = response.json()
        self.revision = body.get("revision", {}).get("new_revision_id", self.revision)
        return body

    async def run_job(self, job_id: str) -> dict[str, Any]:
        assert await handle_one(JobRepository("real-journey-worker"), job_id=uuid.UUID(job_id))
        job = await self.get(f"jobs/{job_id}")
        assert job["status"] == "SUCCEEDED", job
        result: dict[str, Any] = job["result"]
        self.revision = result["final_revision_id"]
        return result

    async def closure(self) -> dict[str, Any]:
        revision = await self.get(f"revisions/{self.revision}")
        key = revision["component_manifest"]["target_closure_result_version_id"]
        return await self.get(f"target-closure-result-versions/{key}")

    async def optimize(self) -> dict[str, Any]:
        return await self.post(
            "/optimization-runs",
            {
                "solver_adapter": "CP_SAT_ENTRY_AND_RATES",
                "maximum_solver_seconds": 30,
            },
        )

    async def unlock_and_solve(self) -> dict[str, Any]:
        run = await self.optimize()
        assert run["status"] == "INFEASIBLE", run
        assert run["discount_unlock_required"] is True
        assert run["scenarios"] == []
        proof = next(item for item in run["infeasibility_proofs"] if item["stage"] == "ENTRY_AND_RATES")
        unlock = await self.post(
            "/discount-unlocks:request",
            {
                "optimization_run_id": run["optimization_run_id"],
                "infeasibility_proof_id": proof["infeasibility_proof_id"],
                "reason": "真实穷尽后启用次级杠杆",
            },
        )
        await self.post(
            "/discount-unlocks:confirm",
            {
                "discount_unlock_request_id": unlock["discount_unlock_request_id"],
            },
        )
        solved = await self.optimize()
        assert solved["status"] == "OPTIMAL", solved
        assert solved["terminal_stage"] == "ENTRY_RATES_AND_DISCOUNT"
        return solved


async def prepare(client: AsyncClient, target_ppm: int) -> Journey:
    department = f"real-journey-{uuid.uuid4()}"
    extract = _extract()
    extract = replace(
        extract,
        roster_rows=[r for r in extract.roster_rows if r["教学区"] == "西直门"],
        summary_rows=[r for r in extract.summary_rows if r["教学区"] == "西直门"],
    )
    snapshot, built = await _build(extract, department_id=department)
    assert built["status"] == "FROZEN"
    actor = Actor("real-journey-test", department, frozenset({Role.DATA_MAINTAINER}))
    reference, budget = await _create_rate_budget_chain(snapshot_id=snapshot, fiscal_year=2027, actor=actor)
    j = Journey(client, department, str(budget.new_revision_id), budget.annual_budget_id)
    combos = await j.post(
        "/combinations:generate-draft",
        {
            "rate_reference_version_id": str(reference.rate_reference_version_id),
        },
    )
    await j.post(
        "/combinations:confirm", {"combination_set_version_id": combos["combination_set_version_id"]}
    )
    target = {
        "items": [
            {
                "scope_level": scope,
                "track": track,
                "target_revenue_yuan": str(4 * target_ppm),
                "source_evidence": {"reference": "synthetic-golden-v1"},
                "change_reason": "验收目标",
            }
            for scope, track in (("DEPARTMENT", None), ("TRACK", "初中"))
        ],
        "batch_content_hash": "0" * 64,
    }
    target["batch_content_hash"] = target_set_batch_content_hash(
        TargetSetReplaceAndLockRequest.model_validate(target)
    )
    await j.post("/targets:replace-and-lock", target)
    await j.post(
        "/entry-total:save-and-lock", {"fiscal_year_entry_total": 10, "change_reason": "固定十人入口"}
    )
    suggestion = await j.post(
        "/campus-entry:generate-suggestions",
        {
            "campuses": [
                {
                    "campus": "西直门",
                    "classroom_resource": {"rooms": 10},
                    "sales_resource": {"consultants": 5},
                }
            ]
        },
    )
    campus = await j.post(
        "/campus-entry:replace-draft",
        {
            "items": [
                {
                    "campus": row["campus"],
                    "determined_entry_value": row["suggested_entry_value"],
                    "performance_evidence": {"reference": "synthetic-history-v1"},
                    "classroom_resource": row["classroom_resource"],
                    "sales_resource": row["sales_resource"],
                }
                for row in suggestion["items"]
            ]
        },
    )
    await j.post(
        "/campus-entry:confirm", {"campus_entry_set_version_id": campus["campus_entry_set_version_id"]}
    )
    scope = await j.post("/entry-scope:replace-draft", {"items": []})
    await j.post("/entry-scope:confirm", {"entry_scope_version_id": scope["entry_scope_version_id"]})
    await j.post("/entry-detail:calculate", {"manual_reservations": []})
    references = await j.get(
        f"rate-reference-versions/{reference.rate_reference_version_id}/references?limit=500"
    )
    rate_items = []
    for row in references["items"]:
        value = 1_000_000 if row["rate_type"] == "RENEWAL_RATE" else 0
        rate_items.append(
            {
                "rate_reference_id": row["rate_reference_id"],
                "current_value_ppm": value,
                "lower_bound_ppm": value,
                "upper_bound_ppm": value,
                "boundary_reason": "验收固定边界",
                "manual_reason": "全续班无后续新生的简单黄金样例",
            }
        )
    rates = await j.post(
        "/rate-parameters:replace-draft",
        {
            "rate_reference_version_id": str(reference.rate_reference_version_id),
            "items": rate_items,
        },
    )
    await j.post(
        "/rate-boundaries:confirm",
        {
            "rate_parameter_version_id": rates["rate_parameter_version_id"],
            "rate_parameter_stable_keys": [r["rate_parameter_stable_key"] for r in rates["items"]],
        },
    )
    evidence = {"source_evidence": {"reference": "synthetic-golden-v1"}, "change_reason": "真实核算验收"}
    product = {"track": "初中", "product": "智慧空间"}
    resource = {
        "budget_tax_conversion_factor": "1.06",
        "tax_factor_source_evidence": {"reference": "synthetic"},
        "tax_factor_change_reason": "验收",
        "class_size_items": [dict(product, system_value=10, final_value=10, **evidence)],
        "revenue_items": [
            dict(
                product,
                fiscal_season=s,
                list_price_yuan="106000",
                current_season_recognition_rate_ppm=1_000_000,
                opening_carryover_revenue_yuan="0",
                **evidence,
            )
            for s in range(20271, 20275)
        ],
        "opening_carryover_items": [
            dict(
                product,
                fiscal_season=20271,
                grade="七年级",
                subject="数学",
                campus="西直门",
                amount_yuan="0",
                **evidence,
            )
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
            dict(product, fiscal_season=s, grade="七年级", discount_rate_ppm=500_000, **evidence)
            for s in range(20271, 20275)
        ],
        "composite_targets": [
            dict(
                scope_level=scope,
                track=track,
                period_type=period,
                fiscal_season=s,
                target_rate_ppm=target_ppm,
                **evidence,
            )
            for scope, track in (("DEPARTMENT", None), ("TRACK", "初中"))
            for period, s in [("ANNUAL", None), *(("SEASON", s) for s in range(20271, 20275))]
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
    job = await j.post(":calculate", {})
    await j.run_job(job["job_id"])
    return j


@pytest.mark.parametrize("target_ppm", [400_000, 600_000])
async def test_real_solver_apply_recalculate_undo_reconfirm_publish(target_ppm: int) -> None:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        j = await prepare(client, target_ppm)
        before = await j.get(f"revisions/{j.revision}")
        initial = await j.closure()
        assert initial["publishable"] is False
        assert all(Decimal(r["calculated_revenue_yuan"]) == 2_000_000 for r in initial["items"])
        solved = await j.unlock_and_solve()
        repeated = await j.optimize()
        assert [s["result_hash"] for s in repeated["scenarios"]] == [
            s["result_hash"] for s in solved["scenarios"]
        ]
        assert await j.get(f"revisions/{j.revision}") == before
        candidate = solved["scenarios"][0]
        application = await j.post(
            f"/scenarios/{candidate['rebalancing_scenario_id']}:apply", {"reason": "采用真实候选"}
        )
        applied_revision = j.revision
        result = await j.run_job(application["calculation_job_id"])
        assert result["publishable"] is True, result
        assert all(
            Decimal(r["calculated_revenue_yuan"]) == 4 * target_ppm for r in (await j.closure())["items"]
        )
        status = await j.get(f"revisions/{applied_revision}/scenario-application/current")
        assert status["current_revision_id"] == j.revision != applied_revision
        assert status["can_revert"] is True, status
        application_id = application["scenario_application_id"]
        undone = await j.post(
            f"/scenario-applications/{application_id}:revert", {"reason": "验证自动复算后撤销"}
        )
        restored = await j.get(f"revisions/{j.revision}")
        assert (
            restored["component_manifest"]["discount_parameter_version_id"]
            == before["component_manifest"]["discount_parameter_version_id"]
        )
        assert "formal_confirmation_id" not in restored["component_manifest"]
        await j.run_job(undone["calculation_job_id"])
        assert (await j.closure())["items"] != []
        assert all(Decimal(r["calculated_revenue_yuan"]) == 2_000_000 for r in (await j.closure())["items"])
        assert (await j.get(f"revisions/{applied_revision}/scenario-application/current"))[
            "can_revert"
        ] is False
        solved_again = await j.unlock_and_solve()
        candidate_again = solved_again["scenarios"][0]
        application_again = await j.post(
            f"/scenarios/{candidate_again['rebalancing_scenario_id']}:apply", {"reason": "确认采用调平结果"}
        )
        await j.run_job(application_again["calculation_job_id"])
        async with get_session_factory()() as session:
            budget = await session.get(AnnualBudget, j.budget_id)
            assert budget is not None and budget.current_published_revision_id is None
        formal = await j.post(":confirm-formal-plan", {"reason": "负责人重新确认"})
        await j.run_job(formal["job_id"])
        status = await j.get(f"revisions/{j.revision}/scenario-application/current")
        assert status["can_revert"] is False
        blocked = await client.post(
            f"/api/v1/revisions/{j.revision}/scenario-applications/{application_again['scenario_application_id']}:revert",
            json={"reason": "正式确认不得撤销"},
            headers={**j.headers, "If-Match": f'"{j.revision}"', "Idempotency-Key": str(uuid.uuid4())},
        )
        assert blocked.status_code == 409, blocked.text
        await j.post(":publish", {"reason": "通过真实黄金验收后发布独立测试预算"})
        async with get_session_factory()() as session:
            budget = await session.get(AnnualBudget, j.budget_id)
            assert budget is not None and str(budget.current_published_revision_id) == j.revision


@pytest.mark.parametrize("phase", ["queued", "running", "manual-change"])
async def test_revert_handles_pending_worker_but_preserves_manual_work(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import packages.calculation_pipeline as pipeline

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        j = await prepare(client, 600_000)
        solved = await j.unlock_and_solve()
        applied = await j.post(
            f"/scenarios/{solved['scenarios'][0]['rebalancing_scenario_id']}:apply",
            {"reason": "撤销并发验收"},
        )
        applied_id = j.revision
        worker_task = None
        release = asyncio.Event()
        reached = asyncio.Event()
        original = pipeline._validate_class_size

        async def paused_class_size(*args: Any, **kwargs: Any) -> Any:
            reached.set()
            await release.wait()
            return await original(*args, **kwargs)

        if phase == "running":
            monkeypatch.setattr(pipeline, "_validate_class_size", paused_class_size)
            worker_task = asyncio.create_task(
                handle_one(
                    JobRepository("undo-racing-worker"), job_id=uuid.UUID(applied["calculation_job_id"])
                )
            )
            await asyncio.wait_for(reached.wait(), timeout=10)
            async with get_session_factory()() as session:
                budget = await session.get(AnnualBudget, j.budget_id)
                assert budget is not None
                j.revision = str(budget.current_draft_revision_id)
            assert j.revision != applied_id
        elif phase == "manual-change":
            await j.post(
                "/entry-total:save-and-lock",
                {"fiscal_year_entry_total": 10, "change_reason": "人工重新保存入口"},
            )

        status = await j.get(f"revisions/{applied_id}/scenario-application/current")
        assert status["can_revert"] is (phase != "manual-change")
        path = (
            f"/api/v1/revisions/{j.revision}"
            f"/scenario-applications/{applied['scenario_application_id']}:revert"
        )
        key = str(uuid.uuid4())
        headers = {**j.headers, "If-Match": f'"{j.revision}"', "Idempotency-Key": key}
        payload = {"reason": "撤销本次测试候选"}
        try:
            if phase == "queued":
                hidden = await client.post(
                    path, json=payload, headers={**headers, "X-Department-ID": "unrelated"}
                )
                assert hidden.status_code == 404
                forbidden = await client.post(
                    path, json=payload, headers={**headers, "X-Roles": "DEPARTMENT_BUDGET_OWNER"}
                )
                assert forbidden.status_code == 403
                responses = await asyncio.gather(
                    client.post(path, json=payload, headers=headers),
                    client.post(
                        path, json=payload, headers={**headers, "Idempotency-Key": str(uuid.uuid4())}
                    ),
                )
                assert sorted(r.status_code for r in responses) == [202, 409]
                index = next(i for i, r in enumerate(responses) if r.status_code == 202)
                response = responses[index]
                # Replay the winning request with its exact idempotency identity.
                replay = await client.send(response.request)
                assert replay.status_code == 202 and replay.json() == response.json()
            else:
                response = await client.post(path, json=payload, headers=headers)
            if phase == "manual-change":
                assert response.status_code == 409, response.text
                assert response.json()["code"] == "SCENARIO_APPLICATION_REVERT_INPUT_CHANGED"
                async with get_session_factory()() as session:
                    budget = await session.get(AnnualBudget, j.budget_id)
                    assert budget is not None and str(budget.current_draft_revision_id) == j.revision
                return
            assert response.status_code == 202, response.text
            reverted = response.json()
        finally:
            release.set()
            if worker_task is not None:
                await asyncio.wait_for(worker_task, timeout=10)
        old_job = await j.get(f"jobs/{applied['calculation_job_id']}")
        assert old_job["status"] == "CANCELLED", old_job
        assert not await handle_one(
            JobRepository("cannot-reclaim-cancelled"), job_id=uuid.UUID(applied["calculation_job_id"])
        )
        async with get_session_factory()() as session:
            budget = await session.get(AnnualBudget, j.budget_id)
            assert (
                budget is not None
                and str(budget.current_draft_revision_id) == reverted["reverted_revision_id"]
            )
        with pytest.raises(DBAPIError, match="immutable"):
            async with get_session_factory()() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE scenario_application_revert SET reason = 'overwrite' "
                        "WHERE scenario_application_revert_id = :key"
                    ),
                    {"key": uuid.UUID(reverted["scenario_application_revert_id"])},
                )
        await j.run_job(reverted["calculation_job_id"])
        assert all(Decimal(r["calculated_revenue_yuan"]) == 2_000_000 for r in (await j.closure())["items"])


async def test_hard_timeout_persists_incomplete_without_candidates_or_unlock_proofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import packages.optimization.process_runner as runner

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        j = await prepare(client, 600_000)
        before = await j.get(f"revisions/{j.revision}")
        # Only inject a hung workload, not a result, clock, adapter or repository.
        monkeypatch.setattr(runner, "optimize_budget", _uncooperative_cpu_work)
        result = await j.post(
            "/optimization-runs", {"solver_adapter": "CP_SAT_ENTRY_AND_RATES", "maximum_solver_seconds": 1}
        )
        assert result["status"] == "INCOMPLETE"
        assert result["reason_code"] == "HARD_TIME_LIMIT"
        assert result["scenarios"] == result["infeasibility_proofs"] == []
        assert result["discount_unlock_required"] is False
        persisted = await j.get(f"optimization-runs/{result['optimization_run_id']}")
        assert persisted == result
        assert await j.get(f"revisions/{j.revision}") == before
