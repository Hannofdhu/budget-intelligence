import hashlib
import io
import json
import os
import uuid
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from apps.api.main import create_app
from apps.worker.main import handle_one
from packages.calculation_pipeline import run_full_calculation_job
from packages.config.settings import Settings
from packages.contracts.versioning import (
    AnnualBudgetCreate,
    DiscountParameterReplaceDraftRequest,
    ParameterComponentInput,
    ParameterComponentType,
    ParameterVersionCreate,
    RateReferenceGenerateRequest,
    RateReferenceVersionResponse,
    ResourceFinanceReplaceDraftRequest,
    RevisionCommandResponse,
    TargetSetReplaceAndLockRequest,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.hashing import content_hash
from packages.persistence.database import get_engine, get_session_factory
from packages.persistence.jobs import JobRepository
from packages.persistence.models import (
    AsyncJob,
    BudgetRevision,
    DetailDiscountParameter,
    EntryDetailAllocation,
    EntryHistoryAggregate,
    InfeasibilityProof,
    OptimizationRun,
    RateParameter,
    RateReference,
    RateReferenceVersion,
    RebalancingScenario,
    ScenarioChange,
    SourceMappingVerification,
    SourceMetricAggregate,
    SourceReconciliation,
    SourceSnapshot,
)
from packages.persistence.resource_finance import resource_finance_batch_content_hash
from packages.persistence.revenue import discount_parameter_batch_content_hash
from packages.persistence.target_closure import target_set_batch_content_hash
from packages.persistence.versioning import (
    create_annual_budget,
    create_parameter_version,
    create_rate_reference_version,
)
from packages.source_snapshot.adapter import DatasetMetadata, SourceExtract
from packages.source_snapshot.service import build_source_snapshot

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1",
    reason="set BUDGET_RUN_DB_TESTS=1 with a migrated PostgreSQL database",
)


class FakeAdapter:
    def __init__(self, extract: SourceExtract, *, fiscal_year: int = 2027) -> None:
        self.value = extract
        self.fiscal_year = fiscal_year

    async def extract(self, fiscal_year: int) -> SourceExtract:
        assert fiscal_year == self.fiscal_year
        return self.value


def _row(*, summary: bool = False, campus: str = "西直门", fiscal_season: int = 20261) -> dict[str, object]:
    result: dict[str, object] = {
        "季节财年": fiscal_season,
        "季度": {1: "暑假", 2: "秋季", 3: "寒假", 4: "春季"}[fiscal_season % 10],
        "赛道": "初中",
        "产品": "智慧空间",
        "年级": "七年级",
        "科目": "数学",
        "教学区": campus,
        "招生人次(三分之二出勤口径)": 20,
        "招生人次": 20,
        "目标标识(目标,数据)": "数据",
        "原班人次": 100,
        "续班人次": 90,
        "专项班续班人次": 5,
    }
    result["新生人次" if summary else "业务新生人次"] = 10
    return result


def _extract(source_fiscal_year: int = 2026) -> SourceExtract:
    now = datetime.now(UTC)
    roster = DatasetMetadata("roster", "智慧学习部学员花名册", 461, 100, now, now)
    summary = DatasetMetadata("summary", "智慧学习部指标汇总宽表", 484, 100, now, now + timedelta(minutes=10))
    return SourceExtract(
        roster=roster,
        summary=summary,
        latest_summary_partition="2026-08-31",
        fiscal_season_start=source_fiscal_year * 10 + 1,
        fiscal_season_end=source_fiscal_year * 10 + 4,
        roster_rows=[
            _row(fiscal_season=season, campus=campus)
            for season in range(source_fiscal_year * 10 + 1, source_fiscal_year * 10 + 5)
            for campus in ("西直门", "中关村")
        ],
        summary_rows=[
            _row(summary=True, fiscal_season=season, campus=campus)
            for season in range(source_fiscal_year * 10 + 1, source_fiscal_year * 10 + 5)
            for campus in ("西直门", "中关村")
        ],
    )


async def _build(
    extract: SourceExtract,
    *,
    fiscal_year: int = 2027,
    department_id: str | None = None,
) -> tuple[uuid.UUID, dict[str, object]]:
    department_id = department_id or f"source-builder-{uuid.uuid4()}"
    actor = Actor("builder-test", department_id, frozenset({Role.DATA_MAINTAINER}))
    job_id = uuid.uuid4()
    settings = Settings(
        roster_dataset_id="roster",
        summary_dataset_id="summary",
        roster_etl_id="etl-roster",
    )
    async with get_session_factory()() as session:
        result = await build_source_snapshot(
            session,
            adapter=FakeAdapter(extract, fiscal_year=fiscal_year),
            settings=settings,
            actor=actor,
            fiscal_year=fiscal_year,
            build_job_id=job_id,
            request_id=str(uuid.uuid4()),
        )
    return uuid.UUID(str(result["source_snapshot_id"])), result


async def _create_rate_budget_chain(
    *, snapshot_id: uuid.UUID, fiscal_year: int, actor: Actor
) -> tuple[RateReferenceVersionResponse, RevisionCommandResponse]:
    mature_seasons = [(fiscal_year - 1) * 10 + quarter for quarter in range(1, 5)]
    async with get_session_factory()() as session:
        rate_version = await create_rate_reference_version(
            session,
            actor=actor,
            source_snapshot_id=snapshot_id,
            payload=RateReferenceGenerateRequest(
                target_fiscal_year=fiscal_year,
                mature_source_fiscal_seasons=mature_seasons,
                maturity_evidence_reference=f"FY{fiscal_year} 比例参数测试证据",
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
    async with get_session_factory()() as session:
        parameter_version = await create_parameter_version(
            session,
            actor=actor,
            payload=ParameterVersionCreate(
                source_snapshot_id=snapshot_id,
                components=[
                    ParameterComponentInput(
                        component_type=component_type,
                        component_version_id=(
                            rate_version.rate_reference_version_id
                            if component_type == ParameterComponentType.RATE_PARAMETERS
                            else uuid.uuid4()
                        ),
                        content_hash=f"{index + 1}" * 64,
                        schema_version="1.0",
                        record_count=1,
                    )
                    for index, component_type in enumerate(ParameterComponentType)
                ],
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
    async with get_session_factory()() as session:
        budget = await create_annual_budget(
            session,
            actor=actor,
            payload=AnnualBudgetCreate(
                fiscal_year=fiscal_year,
                parameter_version_id=parameter_version.parameter_version_id,
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
    return rate_version, budget


async def test_matching_source_maps_are_frozen_with_immutable_aggregates() -> None:
    snapshot_id, result = await _build(_extract())
    assert result["status"] == "FROZEN"
    async with get_session_factory()() as session:
        snapshot = await session.get(SourceSnapshot, snapshot_id)
        assert snapshot is not None and snapshot.status == "FROZEN"
        metric_count = await session.scalar(
            select(func.count())
            .select_from(SourceMetricAggregate)
            .where(SourceMetricAggregate.source_snapshot_id == snapshot_id)
        )
        entry_count = await session.scalar(
            select(func.count())
            .select_from(EntryHistoryAggregate)
            .where(EntryHistoryAggregate.source_snapshot_id == snapshot_id)
        )
        mapping_count = await session.scalar(
            select(func.count())
            .select_from(SourceMappingVerification)
            .where(SourceMappingVerification.source_snapshot_id == snapshot_id)
        )
        checks = list(
            await session.scalars(
                select(SourceReconciliation).where(SourceReconciliation.source_snapshot_id == snapshot_id)
            )
        )
        assert metric_count == 16
        assert entry_count == 2
        assert mapping_count == 4
        assert len(checks) == 2 and all(item.passed for item in checks)


async def test_exact_key_mismatch_keeps_snapshot_draft() -> None:
    extract = _extract()
    changed = dict(extract.summary_rows[0])
    changed["新生人次"] = 9
    snapshot_id, result = await _build(replace(extract, summary_rows=[changed, extract.summary_rows[1]]))
    assert result["status"] == "DRAFT"
    assert result["reconciliation_passed"] is False
    async with get_session_factory()() as session:
        snapshot = await session.get(SourceSnapshot, snapshot_id)
        assert snapshot is not None and snapshot.status == "DRAFT"


async def test_stale_summary_chain_waits_instead_of_freezing() -> None:
    extract = _extract()
    stale_summary = replace(
        extract.summary,
        last_execution_at=extract.roster.last_execution_at - timedelta(minutes=1),
    )
    snapshot_id, result = await _build(replace(extract, summary=stale_summary))
    assert result["status"] == "WAITING_FOR_SUMMARY"
    async with get_session_factory()() as session:
        aggregate_count = await session.scalar(
            select(func.count())
            .select_from(SourceMetricAggregate)
            .where(SourceMetricAggregate.source_snapshot_id == snapshot_id)
        )
        assert aggregate_count == 0


async def test_frozen_snapshot_generates_immutable_rate_reference_version() -> None:
    snapshot_id, _ = await _build(_extract())
    async with get_session_factory()() as session:
        snapshot = await session.get(SourceSnapshot, snapshot_id)
        assert snapshot is not None
        department_id = snapshot.department_id
    actor = Actor("builder-test", department_id, frozenset({Role.DATA_MAINTAINER}))
    async with get_session_factory()() as session:
        response = await create_rate_reference_version(
            session,
            actor=actor,
            source_snapshot_id=snapshot_id,
            payload=RateReferenceGenerateRequest(
                target_fiscal_year=2027,
                mature_source_fiscal_seasons=[20261, 20262, 20263, 20264],
                maturity_evidence_reference="集成测试成熟季节证据",
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
        version_id = response.rate_reference_version_id
        assert response.reference_count == 16
        assert response.exact_count == 8
        assert response.fallback_count == 8
        assert response.manual_required_count == 0

    async with get_session_factory()() as session:
        version = await session.get(RateReferenceVersion, version_id)
        assert version is not None and version.source_snapshot_id == snapshot_id
        references = list(
            await session.scalars(
                select(RateReference).where(RateReference.rate_reference_version_id == version_id)
            )
        )
        assert len(references) == 16
        assert {item.target_fiscal_season for item in references} == {
            20271,
            20272,
            20273,
            20274,
        }
        assert all(item.system_value_ppm is not None for item in references)


async def test_rate_reference_api_is_idempotent_paginated_and_department_scoped() -> None:
    snapshot_id, _ = await _build(_extract())
    async with get_session_factory()() as session:
        snapshot = await session.get(SourceSnapshot, snapshot_id)
        assert snapshot is not None
        department_id = snapshot.department_id
    idempotency_key = uuid.uuid4()
    headers = {
        "X-User-ID": "builder-test",
        "X-Department-ID": department_id,
        "X-Roles": "DATA_MAINTAINER",
        "X-Request-ID": str(uuid.uuid4()),
        "Idempotency-Key": str(idempotency_key),
    }
    payload = {
        "target_fiscal_year": 2027,
        "mature_source_fiscal_seasons": [20261, 20262, 20263, 20264],
        "maturity_evidence_reference": "API 集成测试成熟季节证据",
    }
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            f"/api/v1/source-snapshots/{snapshot_id}/rate-reference-versions",
            headers=headers,
            json=payload,
        )
        assert created.status_code == 201, created.text
        version_id = created.json()["rate_reference_version_id"]
        replay = await client.post(
            f"/api/v1/source-snapshots/{snapshot_id}/rate-reference-versions",
            headers=headers,
            json=payload,
        )
        assert replay.status_code == 201
        assert replay.json()["rate_reference_version_id"] == version_id

        version = await client.get(f"/api/v1/rate-reference-versions/{version_id}", headers=headers)
        assert version.status_code == 200
        assert version.headers["etag"] == f'"{version.json()["content_hash"]}"'
        first_page = await client.get(
            f"/api/v1/rate-reference-versions/{version_id}/references?limit=3",
            headers=headers,
        )
        assert first_page.status_code == 200
        assert len(first_page.json()["items"]) == 3
        cursor = first_page.json()["next_cursor"]
        assert cursor is not None
        second_page = await client.get(
            f"/api/v1/rate-reference-versions/{version_id}/references?limit=3&cursor={cursor}",
            headers=headers,
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["items"]) == 3
        assert {item["rate_reference_id"] for item in first_page.json()["items"]}.isdisjoint(
            {item["rate_reference_id"] for item in second_page.json()["items"]}
        )

        other_headers = {**headers, "X-Department-ID": "other-department"}
        forbidden = await client.get(f"/api/v1/rate-reference-versions/{version_id}", headers=other_headers)
        assert forbidden.status_code == 404


async def test_rate_parameter_governance_is_confirmed_atomic_and_inherited() -> None:
    snapshot_id, _ = await _build(_extract())
    async with get_session_factory()() as session:
        snapshot = await session.get(SourceSnapshot, snapshot_id)
        assert snapshot is not None
        department_id = snapshot.department_id
    maintainer = Actor("rate-maintainer", department_id, frozenset({Role.DATA_MAINTAINER}))
    async with get_session_factory()() as session:
        rate_version = await create_rate_reference_version(
            session,
            actor=maintainer,
            source_snapshot_id=snapshot_id,
            payload=RateReferenceGenerateRequest(
                target_fiscal_year=2027,
                mature_source_fiscal_seasons=[20261, 20262, 20263, 20264],
                maturity_evidence_reference="比例参数治理集成测试",
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
    async with get_session_factory()() as session:
        parameter_version = await create_parameter_version(
            session,
            actor=maintainer,
            payload=ParameterVersionCreate(
                source_snapshot_id=snapshot_id,
                components=[
                    ParameterComponentInput(
                        component_type=component_type,
                        component_version_id=(
                            rate_version.rate_reference_version_id
                            if component_type == ParameterComponentType.RATE_PARAMETERS
                            else uuid.uuid4()
                        ),
                        content_hash=f"{index + 1}" * 64,
                        schema_version="1.0",
                        record_count=1,
                    )
                    for index, component_type in enumerate(ParameterComponentType)
                ],
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
    async with get_session_factory()() as session:
        budget = await create_annual_budget(
            session,
            actor=maintainer,
            payload=AnnualBudgetCreate(
                fiscal_year=2027,
                parameter_version_id=parameter_version.parameter_version_id,
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )

    maintainer_headers = {
        "X-User-ID": "rate-maintainer",
        "X-Department-ID": department_id,
        "X-Roles": "DATA_MAINTAINER",
        "X-Request-ID": str(uuid.uuid4()),
    }
    owner_headers = {
        **maintainer_headers,
        "X-User-ID": "rate-owner",
        "X-Roles": "DEPARTMENT_BUDGET_OWNER",
    }
    revision_id = budget.new_revision_id
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reference_page = await client.get(
            f"/api/v1/rate-reference-versions/{rate_version.rate_reference_version_id}/references?limit=100",
            headers=maintainer_headers,
        )
        assert reference_page.status_code == 200, reference_page.text
        references = reference_page.json()["items"]
        draft_items = []
        for reference in references:
            current = reference["system_value_ppm"]
            assert current is not None
            maximum = 999_900 if reference["rate_type"] == "NEW_STUDENT_SHARE" else 1_000_000
            draft_items.append(
                {
                    "rate_reference_id": reference["rate_reference_id"],
                    "current_value_ppm": current,
                    "lower_bound_ppm": max(0, current - 100),
                    "upper_bound_ppm": min(maximum, current + 100),
                    "boundary_reason": "按历史参考值上下 0.01 个百分点建立边界",
                    "manual_reason": "下限低于历史参考值的业务依据",
                }
            )

        invalid_items = [dict(item) for item in draft_items]
        invalid_items[0]["lower_bound_ppm"] = invalid_items[0]["current_value_ppm"] + 100
        invalid_draft = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-parameters:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "rate_reference_version_id": str(rate_version.rate_reference_version_id),
                "items": invalid_items,
            },
        )
        assert invalid_draft.status_code == 422
        assert invalid_draft.json()["code"] == "RATE_PARAMETER_BATCH_INVALID"
        assert invalid_draft.json()["errors"][0]["code"] == "RATE_BOUNDARY_INVALID"

        draft = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-parameters:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "rate_reference_version_id": str(rate_version.rate_reference_version_id),
                "items": draft_items,
            },
        )
        assert draft.status_code == 202, draft.text
        draft_body = draft.json()
        assert draft_body["record_count"] == len(references)
        assert draft_body["confirmed_boundary_count"] == 0
        assert all(not item["auto_balance_eligible"] for item in draft_body["items"])
        revision_id = draft_body["revision"]["new_revision_id"]
        stable_keys = [item["rate_parameter_stable_key"] for item in draft_body["items"]]

        unconfirmed_lock = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-parameters:lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": [{"rate_parameter_stable_key": stable_keys[0], "reason": "锁定"}]},
        )
        assert unconfirmed_lock.status_code == 422
        assert unconfirmed_lock.json()["errors"][0]["code"] == "RATE_BOUNDARY_NOT_CONFIRMED"

        confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-boundaries:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "rate_parameter_version_id": draft_body["rate_parameter_version_id"],
                "rate_parameter_stable_keys": stable_keys,
            },
        )
        assert confirmation.status_code == 200, confirmation.text
        assert confirmation.json()["confirmed_count"] == len(stable_keys)

        first, second = draft_body["items"][:2]
        invalid_batch = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-parameters:change-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "items": [
                    {
                        "rate_parameter_stable_key": first["rate_parameter_stable_key"],
                        "new_value_ppm": first["upper_bound_ppm"],
                        "reason": "达到确认上限并锁定",
                    },
                    {
                        "rate_parameter_stable_key": second["rate_parameter_stable_key"],
                        "new_value_ppm": second["upper_bound_ppm"] + 100,
                        "reason": "制造整批失败",
                    },
                ]
            },
        )
        assert invalid_batch.status_code == 422
        assert invalid_batch.json()["code"] == "RATE_PARAMETER_BATCH_INVALID"

        locked = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-parameters:change-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "items": [
                    {
                        "rate_parameter_stable_key": first["rate_parameter_stable_key"],
                        "new_value_ppm": first["upper_bound_ppm"],
                        "reason": "确认范围内修改并锁定",
                    },
                    {
                        "rate_parameter_stable_key": second["rate_parameter_stable_key"],
                        "new_value_ppm": second["current_value_ppm"],
                        "reason": "保持当前值并锁定",
                    },
                ]
            },
        )
        assert locked.status_code == 202, locked.text
        locked_body = locked.json()
        assert locked_body["locked_count"] == 2
        assert locked_body["confirmed_boundary_count"] == len(stable_keys)
        locked_by_key = {item["rate_parameter_stable_key"]: item for item in locked_body["items"]}
        assert (
            locked_by_key[first["rate_parameter_stable_key"]]["current_value_ppm"] == first["upper_bound_ppm"]
        )
        assert locked_by_key[first["rate_parameter_stable_key"]]["auto_balance_eligible"] is False
        locked_version_id = locked_body["rate_parameter_version_id"]
        revision_id = locked_body["revision"]["new_revision_id"]

        derived = await client.post(
            f"/api/v1/revisions/{revision_id}:derive",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "同财年派生验证锁定精确继承"},
        )
        assert derived.status_code == 202, derived.text
        revision_id = derived.json()["new_revision_id"]
        derived_revision = await client.get(f"/api/v1/revisions/{revision_id}", headers=maintainer_headers)
        assert derived_revision.status_code == 200
        assert derived_revision.json()["component_manifest"]["rate_parameter_version_id"] == locked_version_id

        unlocked = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-parameters:unlock",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "items": [
                    {
                        "rate_parameter_stable_key": first["rate_parameter_stable_key"],
                        "reason": "部门预算负责人解锁",
                    }
                ]
            },
        )
        assert unlocked.status_code == 202, unlocked.text
        unlocked_body = unlocked.json()
        assert unlocked_body["locked_count"] == 1
        assert unlocked_body["confirmed_boundary_count"] == len(stable_keys)
        unlocked_version_id = unlocked_body["rate_parameter_version_id"]

        readback = await client.get(
            f"/api/v1/rate-parameter-versions/{unlocked_version_id}",
            headers=maintainer_headers,
        )
        assert readback.status_code == 200, readback.text
        assert readback.json()["content_hash"] == unlocked_body["content_hash"]
        assert readback.json()["items"] == unlocked_body["items"]

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE rate_parameter_version SET locked_count = locked_count + 1 "
                    "WHERE rate_parameter_version_id = :version_id"
                ),
                {"version_id": unlocked_version_id},
            )


async def test_cross_year_rate_lock_copy_reports_unmatched_keys_without_fuzzy_matching() -> None:
    department_id = f"cross-year-rates-{uuid.uuid4()}"
    source_snapshot_id, _ = await _build(_extract(2026), fiscal_year=2027, department_id=department_id)
    target_extract = _extract(2027)

    def change_west_product(row: dict[str, object]) -> dict[str, object]:
        changed = dict(row)
        if changed["教学区"] == "西直门":
            changed["产品"] = "新财年替代产品"
        return changed

    target_snapshot_id, _ = await _build(
        replace(
            target_extract,
            roster_rows=[change_west_product(row) for row in target_extract.roster_rows],
            summary_rows=[change_west_product(row) for row in target_extract.summary_rows],
        ),
        fiscal_year=2028,
        department_id=department_id,
    )
    actor = Actor("cross-year-maintainer", department_id, frozenset({Role.DATA_MAINTAINER}))
    source_rate, source_budget = await _create_rate_budget_chain(
        snapshot_id=source_snapshot_id,
        fiscal_year=2027,
        actor=actor,
    )
    target_rate, target_budget = await _create_rate_budget_chain(
        snapshot_id=target_snapshot_id,
        fiscal_year=2028,
        actor=actor,
    )
    maintainer_headers = {
        "X-User-ID": "cross-year-maintainer",
        "X-Department-ID": department_id,
        "X-Roles": "DATA_MAINTAINER",
        "X-Request-ID": str(uuid.uuid4()),
    }
    owner_headers = {
        **maintainer_headers,
        "X-User-ID": "cross-year-owner",
        "X-Roles": "DEPARTMENT_BUDGET_OWNER",
    }
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        async def initialize_and_confirm(
            rate_version_id: uuid.UUID, revision_id: uuid.UUID
        ) -> tuple[dict[str, Any], uuid.UUID]:
            page = await client.get(
                f"/api/v1/rate-reference-versions/{rate_version_id}/references?limit=100",
                headers=maintainer_headers,
            )
            assert page.status_code == 200, page.text
            items = []
            for reference in page.json()["items"]:
                current = reference["system_value_ppm"]
                if current is None:
                    current = 500_000
                assert isinstance(current, int)
                maximum = 999_900 if reference["rate_type"] == "NEW_STUDENT_SHARE" else 1_000_000
                items.append(
                    {
                        "rate_reference_id": reference["rate_reference_id"],
                        "current_value_ppm": current,
                        "lower_bound_ppm": max(0, current - 100),
                        "upper_bound_ppm": min(maximum, current + 100),
                        "boundary_reason": "跨财年复制测试边界",
                        "manual_reason": "允许低于历史参考 100 ppm",
                    }
                )
            draft = await client.post(
                f"/api/v1/revisions/{revision_id}/rate-parameters:replace-draft",
                headers={
                    **maintainer_headers,
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": f'"{revision_id}"',
                },
                json={"rate_reference_version_id": str(rate_version_id), "items": items},
            )
            assert draft.status_code == 202, draft.text
            body = draft.json()
            new_revision_id = uuid.UUID(body["revision"]["new_revision_id"])
            confirmation = await client.post(
                f"/api/v1/revisions/{new_revision_id}/rate-boundaries:confirm",
                headers={
                    **owner_headers,
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": f'"{new_revision_id}"',
                },
                json={
                    "rate_parameter_version_id": body["rate_parameter_version_id"],
                    "rate_parameter_stable_keys": [
                        item["rate_parameter_stable_key"] for item in body["items"]
                    ],
                },
            )
            assert confirmation.status_code == 200, confirmation.text
            return body, new_revision_id

        source_draft, source_revision_id = await initialize_and_confirm(
            source_rate.rate_reference_version_id,
            source_budget.new_revision_id,
        )
        source_candidates = [
            item
            for item in source_draft["items"]
            if item["rate_type"] == "RENEWAL_RATE" and item["target_fiscal_season"] == 20271
        ]
        assert {item["campus"] for item in source_candidates} == {"西直门", "中关村"}
        source_lock = await client.post(
            f"/api/v1/revisions/{source_revision_id}/rate-parameters:lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{source_revision_id}"',
            },
            json={
                "items": [
                    {
                        "rate_parameter_stable_key": item["rate_parameter_stable_key"],
                        "reason": "跨财年精确复制来源",
                    }
                    for item in source_candidates
                ]
            },
        )
        assert source_lock.status_code == 202, source_lock.text
        source_locked_version_id = source_lock.json()["rate_parameter_version_id"]

        target_draft, target_revision_id = await initialize_and_confirm(
            target_rate.rate_reference_version_id,
            target_budget.new_revision_id,
        )
        copied = await client.post(
            f"/api/v1/revisions/{target_revision_id}/rate-parameters:copy-cross-year-locks",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{target_revision_id}"',
            },
            json={
                "source_rate_parameter_version_id": source_locked_version_id,
                "reason": "FY27 锁定显式复制到 FY28",
            },
        )
        assert copied.status_code == 202, copied.text
        copied_body = copied.json()
        assert copied_body["locked_count"] == 1
        assert copied_body["copy_exception_count"] == 1
        assert copied_body["is_publishable"] is False
        assert copied_body["copy_exceptions"][0]["error_code"] == "CROSS_YEAR_KEY_NOT_MATCHED"
        assert copied_body["copy_exceptions"][0]["detail"]["source_fiscal_year"] == 2027
        matched = [item for item in copied_body["items"] if item["locked"] and item["campus"] == "中关村"]
        assert len(matched) == 1
        assert matched[0]["lock_origin"]["action"] == "CROSS_YEAR_COPY"
        assert matched[0]["fiscal_year"] == 2028
        assert target_draft["copy_exception_count"] == 0


async def test_entry_planning_vertical_slice_requires_confirmation_and_balances_total() -> None:
    snapshot_id, _ = await _build(_extract())
    async with get_session_factory()() as session:
        snapshot = await session.get(SourceSnapshot, snapshot_id)
        assert snapshot is not None
        department_id = snapshot.department_id
    maintainer = Actor("builder-test", department_id, frozenset({Role.DATA_MAINTAINER}))
    async with get_session_factory()() as session:
        rate_version = await create_rate_reference_version(
            session,
            actor=maintainer,
            source_snapshot_id=snapshot_id,
            payload=RateReferenceGenerateRequest(
                target_fiscal_year=2027,
                mature_source_fiscal_seasons=[20261, 20262, 20263, 20264],
                maturity_evidence_reference="入口纵切集成测试成熟证据",
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
    component_types = list(ParameterComponentType)
    async with get_session_factory()() as session:
        parameter_version = await create_parameter_version(
            session,
            actor=maintainer,
            payload=ParameterVersionCreate(
                source_snapshot_id=snapshot_id,
                components=[
                    ParameterComponentInput(
                        component_type=component_type,
                        component_version_id=(
                            rate_version.rate_reference_version_id
                            if component_type == ParameterComponentType.RATE_PARAMETERS
                            else uuid.uuid4()
                        ),
                        content_hash=f"{index + 1}" * 64,
                        schema_version="1.0",
                        record_count=1,
                    )
                    for index, component_type in enumerate(component_types)
                ],
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )
    async with get_session_factory()() as session:
        budget_result = await create_annual_budget(
            session,
            actor=maintainer,
            payload=AnnualBudgetCreate(
                fiscal_year=2027,
                parameter_version_id=parameter_version.parameter_version_id,
            ),
            idempotency_key=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )

    transport = ASGITransport(app=create_app())
    maintainer_headers = {
        "X-User-ID": "builder-test",
        "X-Department-ID": department_id,
        "X-Roles": "DATA_MAINTAINER",
        "X-Request-ID": str(uuid.uuid4()),
    }
    owner_headers = {
        **maintainer_headers,
        "X-User-ID": "budget-owner-test",
        "X-Roles": "DEPARTMENT_BUDGET_OWNER",
    }
    revision_id = budget_result.new_revision_id
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        combination_headers = {
            **maintainer_headers,
            "Idempotency-Key": str(uuid.uuid4()),
            "If-Match": f'"{revision_id}"',
        }
        combination = await client.post(
            f"/api/v1/revisions/{revision_id}/combinations:generate-draft",
            headers=combination_headers,
            json={"rate_reference_version_id": str(rate_version.rate_reference_version_id)},
        )
        assert combination.status_code == 202, combination.text
        combination_id = combination.json()["combination_set_version_id"]
        assert combination.json()["record_count"] == len(combination.json()["items"])
        assert all(
            item["entry_attribute"] == ("ENTRY" if item["grade"] == "七年级" else "NON_ENTRY")
            for item in combination.json()["items"]
        )
        revision_id = combination.json()["revision"]["new_revision_id"]

        combination_read = await client.get(
            f"/api/v1/combination-set-versions/{combination_id}", headers=maintainer_headers
        )
        assert combination_read.status_code == 200, combination_read.text
        assert combination_read.json()["content_hash"] == combination.json()["content_hash"]

        revenue_without_targets = await client.post(
            f"/api/v1/revisions/{revision_id}/revenues:calculate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert revenue_without_targets.status_code == 409
        assert revenue_without_targets.json()["code"] == "TARGET_SET_REQUIRED"

        target_tracks = sorted({item["track"] for item in combination.json()["items"]})
        target_payload: dict[str, Any] = {
            "items": [
                {
                    "scope_level": "DEPARTMENT",
                    "track": None,
                    "target_revenue_yuan": "0.000000",
                    "source_evidence": {"reference": "FY27 学校版预算"},
                    "change_reason": "集成测试学校版部门目标",
                },
                *[
                    {
                        "scope_level": "TRACK",
                        "track": track,
                        "target_revenue_yuan": "0.000000",
                        "source_evidence": {"reference": "FY27 学校版预算"},
                        "change_reason": "集成测试学校版赛道目标",
                    }
                    for track in target_tracks
                ],
            ],
            "batch_content_hash": "0" * 64,
        }
        target_contract = TargetSetReplaceAndLockRequest.model_validate(target_payload)
        target_payload["batch_content_hash"] = target_set_batch_content_hash(target_contract)
        target_set = await client.post(
            f"/api/v1/revisions/{revision_id}/targets:replace-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=target_payload,
        )
        assert target_set.status_code == 202, target_set.text
        target_set_body = target_set.json()
        assert target_set_body["lock_status"] == "LOCKED"
        assert target_set_body["record_count"] == len(target_tracks) + 1
        assert all(item["tolerance_yuan"] == "10000.000000" for item in target_set_body["items"])
        target_set_version_id = target_set_body["target_set_version_id"]
        budget_target_id = target_set_body["items"][0]["budget_target_id"]
        revision_id = target_set_body["revision"]["new_revision_id"]
        target_set_read = await client.get(
            f"/api/v1/target-set-versions/{target_set_version_id}", headers=maintainer_headers
        )
        assert target_set_read.status_code == 200, target_set_read.text
        assert target_set_read.headers["ETag"] == f'"{target_set_body["content_hash"]}"'

        entry_headers = {
            **maintainer_headers,
            "Idempotency-Key": str(uuid.uuid4()),
            "If-Match": f'"{revision_id}"',
        }
        entry_total = await client.post(
            f"/api/v1/revisions/{revision_id}/entry-total:save-and-lock",
            headers=entry_headers,
            json={"fiscal_year_entry_total": 101, "change_reason": "FY27 固定入口总值"},
        )
        assert entry_total.status_code == 202, entry_total.text
        entry_total_id = entry_total.json()["entry_total_version_id"]
        revision_id = entry_total.json()["revision"]["new_revision_id"]

        entry_total_read = await client.get(
            f"/api/v1/entry-total-versions/{entry_total_id}", headers=maintainer_headers
        )
        assert entry_total_read.status_code == 200, entry_total_read.text
        assert entry_total_read.json()["locked_value"] == 101

        campus_payload = {
            "campuses": [
                {
                    "campus": campus,
                    "classroom_resource": {"rooms": 10},
                    "sales_resource": {"consultants": 5},
                }
                for campus in ("西直门", "中关村")
            ]
        }
        blocked = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:generate-suggestions",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=campus_payload,
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "COMBINATION_SET_NOT_CONFIRMED"

        confirmed = await client.post(
            f"/api/v1/revisions/{revision_id}/combinations:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"combination_set_version_id": combination_id},
        )
        assert confirmed.status_code == 200, confirmed.text

        suggestions = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:generate-suggestions",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=campus_payload,
        )
        assert suggestions.status_code == 202, suggestions.text
        body = suggestions.json()
        assert body["suggestion_total"] == 101
        assert sum(item["suggested_entry_value"] for item in body["items"]) == 101
        assert sorted(item["suggested_entry_value"] for item in body["items"]) == [50, 51]
        suggestions_read = await client.get(
            f"/api/v1/campus-entry-suggestion-versions/{body['campus_entry_suggestion_version_id']}",
            headers=maintainer_headers,
        )
        assert suggestions_read.status_code == 200, suggestions_read.text
        assert suggestions_read.json()["content_hash"] == body["content_hash"]
        assert suggestions_read.json()["items"] == body["items"]

        revision_id = body["revision"]["new_revision_id"]
        campus_draft_items = [
            {
                "campus": item["campus"],
                "determined_entry_value": item["suggested_entry_value"] + (10 if index == 0 else 0),
                "performance_evidence": {
                    "historical_business_new_students": item["historical_business_new_students"]
                },
                "classroom_resource": item["classroom_resource"],
                "sales_resource": item["sales_resource"],
            }
            for index, item in enumerate(body["items"])
        ]
        unbalanced = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": campus_draft_items},
        )
        assert unbalanced.status_code == 202, unbalanced.text
        assert unbalanced.json()["total_difference"] == -10
        assert unbalanced.json()["is_confirmable"] is False
        assert "CAMPUS_TOTAL_NOT_BALANCED" in unbalanced.json()["blocking_reasons"]
        revision_id = unbalanced.json()["revision"]["new_revision_id"]
        blocked_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"campus_entry_set_version_id": unbalanced.json()["campus_entry_set_version_id"]},
        )
        assert blocked_confirmation.status_code == 422
        assert blocked_confirmation.json()["code"] == "CAMPUS_ENTRY_SET_NOT_CONFIRMABLE"

        adjusted_items = [
            {
                "campus": item["campus"],
                "determined_entry_value": item["suggested_entry_value"] + (1 if index == 0 else -1),
                "performance_evidence": {
                    "historical_business_new_students": item["historical_business_new_students"]
                },
                "classroom_resource": item["classroom_resource"],
                "sales_resource": item["sales_resource"],
            }
            for index, item in enumerate(body["items"])
        ]
        missing_reason = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": adjusted_items},
        )
        assert missing_reason.status_code == 202, missing_reason.text
        assert missing_reason.json()["total_difference"] == 0
        assert "CAMPUS_ADJUSTMENT_REASON_REQUIRED" in missing_reason.json()["blocking_reasons"]
        revision_id = missing_reason.json()["revision"]["new_revision_id"]
        missing_reason_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"campus_entry_set_version_id": missing_reason.json()["campus_entry_set_version_id"]},
        )
        assert missing_reason_confirmation.status_code == 422

        for item in adjusted_items:
            item["adjustment_reason_category"] = "PERFORMANCE_JUDGMENT"
            item["adjustment_reason_detail"] = "结合校区上一财年入口表现调整"
        balanced = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": adjusted_items},
        )
        assert balanced.status_code == 202, balanced.text
        assert balanced.json()["is_confirmable"] is True
        assert balanced.json()["determined_total"] == 101
        campus_entry_set_id = balanced.json()["campus_entry_set_version_id"]
        revision_id = balanced.json()["revision"]["new_revision_id"]
        campus_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"campus_entry_set_version_id": campus_entry_set_id},
        )
        assert campus_confirmation.status_code == 200, campus_confirmation.text
        campus_entry_read = await client.get(
            f"/api/v1/campus-entry-set-versions/{campus_entry_set_id}",
            headers=maintainer_headers,
        )
        assert campus_entry_read.status_code == 200, campus_entry_read.text
        assert campus_entry_read.json()["content_hash"] == balanced.json()["content_hash"]
        hidden_campus_entry = await client.get(
            f"/api/v1/campus-entry-set-versions/{campus_entry_set_id}",
            headers={**maintainer_headers, "X-Department-ID": "other-department"},
        )
        assert hidden_campus_entry.status_code == 404

        forbidden_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/campus-entry:confirm",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"campus_entry_set_version_id": campus_entry_set_id},
        )
        assert forbidden_confirmation.status_code == 403

        combination_draft_items = [
            {
                "track": item["track"],
                "product": item["product"],
                "grade": item["grade"],
                "subject": item["subject"],
                "campus": item["campus"],
                "entry_attribute": item["entry_attribute"],
                "enabled": item["enabled"],
                "change_reason": item["change_reason"],
                "class_size_parameter_reference": item["class_size_parameter_reference"],
            }
            for item in combination.json()["items"]
        ]
        closing_without_reason = [dict(item) for item in combination_draft_items]
        closing_without_reason[0]["enabled"] = False
        invalid_close = await client.post(
            f"/api/v1/revisions/{revision_id}/combinations:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": closing_without_reason},
        )
        assert invalid_close.status_code == 422
        assert invalid_close.json()["code"] == "COMBINATION_CHANGE_REASON_REQUIRED"

        combination_draft_items.append(
            {
                "track": combination_draft_items[0]["track"],
                "product": "创新产品",
                "grade": "七年级",
                "subject": "数学",
                "campus": combination_draft_items[0]["campus"],
                "entry_attribute": "ENTRY",
                "enabled": True,
                "change_reason": "FY27 新产品人工计划",
                "class_size_parameter_reference": "class-size:innovation:v1",
            }
        )
        combination_draft_items.append(
            {
                "track": combination_draft_items[0]["track"],
                "product": "人工计划产品",
                "grade": "八年级",
                "subject": "数学",
                "campus": combination_draft_items[0]["campus"],
                "entry_attribute": "NON_ENTRY",
                "enabled": True,
                "change_reason": "零入口明细用于人工计划新生",
                "class_size_parameter_reference": "class-size:planned:v1",
            }
        )
        internal_change = await client.post(
            f"/api/v1/revisions/{revision_id}/combinations:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": combination_draft_items},
        )
        assert internal_change.status_code == 202, internal_change.text
        revision_id = internal_change.json()["revision"]["new_revision_id"]
        internal_revision = await client.get(f"/api/v1/revisions/{revision_id}", headers=maintainer_headers)
        assert internal_revision.status_code == 200
        assert (
            internal_revision.json()["component_manifest"]["campus_entry_set_version_id"]
            == campus_entry_set_id
        )
        assert "campus_entry_suggestion_version_id" not in internal_revision.json()["component_manifest"]

        internal_combination_id = internal_change.json()["combination_set_version_id"]
        internal_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/combinations:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"combination_set_version_id": internal_combination_id},
        )
        assert internal_confirmation.status_code == 200, internal_confirmation.text

        entry_scope = await client.post(
            f"/api/v1/revisions/{revision_id}/entry-scope:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": []},
        )
        assert entry_scope.status_code == 202, entry_scope.text
        assert entry_scope.json()["record_count"] == 0
        assert len(entry_scope.json()["evaluations"]) == len(internal_change.json()["items"])
        assert all(
            evaluation["effective_entry_attribute"]
            == ("ENTRY" if evaluation["default_match"] else "NON_ENTRY")
            for evaluation in entry_scope.json()["evaluations"]
        )
        entry_scope_id = entry_scope.json()["entry_scope_version_id"]
        revision_id = entry_scope.json()["revision"]["new_revision_id"]
        innovation = next(item for item in internal_change.json()["items"] if item["product"] == "创新产品")
        planned_combination = next(
            item for item in internal_change.json()["items"] if item["product"] == "人工计划产品"
        )
        manual_reservation = {
            "track": innovation["track"],
            "product": innovation["product"],
            "grade": innovation["grade"],
            "subject": innovation["subject"],
            "campus": innovation["campus"],
            "reservation_ratio_ppm": 200_000,
            "reason": "新产品人工预留校区入口确定值的 20%",
        }
        unconfirmed_scope_detail = await client.post(
            f"/api/v1/revisions/{revision_id}/entry-detail:calculate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"manual_reservations": [manual_reservation]},
        )
        assert unconfirmed_scope_detail.status_code == 409
        assert unconfirmed_scope_detail.json()["code"] == "ENTRY_SCOPE_NOT_CONFIRMED"

        scope_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/entry-scope:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"entry_scope_version_id": entry_scope_id},
        )
        assert scope_confirmation.status_code == 200, scope_confirmation.text
        entry_detail = await client.post(
            f"/api/v1/revisions/{revision_id}/entry-detail:calculate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"manual_reservations": [manual_reservation]},
        )
        assert entry_detail.status_code == 202, entry_detail.text
        detail_body = entry_detail.json()
        assert detail_body["campus_entry_total"] == 101
        assert detail_body["allocated_entry_total"] == 101
        assert sum(item["entry_initial_value"] for item in detail_body["items"]) == 101
        expected_campus_totals = {
            item["campus"]: item["determined_entry_value"] for item in balanced.json()["items"]
        }
        actual_campus_totals: dict[str, int] = {}
        for item in detail_body["items"]:
            actual_campus_totals[item["campus"]] = (
                actual_campus_totals.get(item["campus"], 0) + item["entry_initial_value"]
            )
            if item["entry_initial_value"] == 0:
                assert item["candidate_eligible"] is False
                assert item["soft_lower_bound"] is None
                assert item["soft_upper_bound"] is None
            else:
                assert item["candidate_eligible"] is True
                assert item["soft_lower_bound"] == (4 * item["entry_initial_value"] + 4) // 5
                assert item["soft_upper_bound"] == (6 * item["entry_initial_value"]) // 5
        assert actual_campus_totals == expected_campus_totals
        innovation_detail = next(item for item in detail_body["items"] if item["product"] == "创新产品")
        expected_innovation_value = (
            expected_campus_totals[innovation["campus"]] * 200_000 + 500_000
        ) // 1_000_000
        assert innovation_detail["entry_initial_value"] == expected_innovation_value
        assert innovation_detail["allocation_source_type"] == "MANUAL_RESERVATION"
        assert innovation_detail["manual_reservation_ratio_ppm"] == 200_000

        entry_detail_id = detail_body["entry_detail_set_version_id"]
        entry_detail_read = await client.get(
            f"/api/v1/entry-detail-set-versions/{entry_detail_id}",
            headers=maintainer_headers,
        )
        assert entry_detail_read.status_code == 200, entry_detail_read.text
        assert entry_detail_read.json()["content_hash"] == detail_body["content_hash"]
        assert entry_detail_read.json()["items"] == detail_body["items"]
        revision_id = detail_body["revision"]["new_revision_id"]

        zero_detail = {
            "track": planned_combination["track"],
            "product": planned_combination["product"],
            "grade": planned_combination["grade"],
            "subject": planned_combination["subject"],
            "campus": planned_combination["campus"],
            "effective_entry_attribute": "NON_ENTRY",
        }
        positive_detail = next(
            item
            for item in detail_body["items"]
            if item["entry_initial_value"] > 0
            and item["origin_type"] == "HISTORICAL"
            # Keep this plan's rounding group separate from the deliberately
            # incomplete new-product group; stable-key order is not a fixture.
            and item["campus"] != planned_combination["campus"]
        )

        def planned_item(detail: dict[str, object], value: object, reason: str) -> dict[str, object]:
            return {
                "fiscal_season": 20271,
                "track": detail["track"],
                "product": detail["product"],
                "entry_attribute": detail["effective_entry_attribute"],
                "grade": detail["grade"],
                "subject": detail["subject"],
                "campus": detail["campus"],
                "planned_new_students": value,
                "reason": reason,
            }

        fractional = await client.post(
            f"/api/v1/revisions/{revision_id}/planned-new-students:change-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": [planned_item(zero_detail, 12.5, "不得静默取整")]},
        )
        assert fractional.status_code == 422

        invalid_batch = await client.post(
            f"/api/v1/revisions/{revision_id}/planned-new-students:change-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "items": [
                    planned_item(zero_detail, 0, "明确不招生"),
                    planned_item(positive_detail, 9, "已有入口绝对量，制造整批失败"),
                ]
            },
        )
        assert invalid_batch.status_code == 422
        assert invalid_batch.json()["code"] == "PLANNED_NEW_STUDENT_BATCH_INVALID"
        unchanged = await client.get(f"/api/v1/revisions/{revision_id}", headers=maintainer_headers)
        assert unchanged.status_code == 200
        assert "planned_new_student_version_id" not in unchanged.json()["component_manifest"]

        locked_zero = await client.post(
            f"/api/v1/revisions/{revision_id}/planned-new-students:change-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": [planned_item(zero_detail, 0, "业务明确本季不招生")]},
        )
        assert locked_zero.status_code == 202, locked_zero.text
        zero_body = locked_zero.json()
        assert zero_body["all_items_locked"] is True
        assert zero_body["items"][0]["handling_status"] == "LOCKED_ZERO"
        assert zero_body["items"][0]["locked_value"] == 0
        assert zero_body["items"][0]["entry_detail_allocation_id"] is None
        assert zero_body["items"][0]["headcount_eligibility_status"] == "PENDING_HEADCOUNT_EVALUATION"
        assert zero_body["items"][0]["calculated_new_student_share_ppm"] is None
        assert zero_body["items"][0]["auto_balance_eligible"] is False
        assert zero_body["items"][0]["enters_largest_remainder"] is False
        revision_id = zero_body["revision"]["new_revision_id"]

        locked_positive = await client.post(
            f"/api/v1/revisions/{revision_id}/planned-new-students:change-and-lock",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": [planned_item(zero_detail, 30, "负责人改为计划招生 30 人")]},
        )
        assert locked_positive.status_code == 202, locked_positive.text
        positive_body = locked_positive.json()
        assert positive_body["items"][0]["handling_status"] == "LOCKED_POSITIVE"
        assert positive_body["items"][0]["calculated_new_student_share_ppm"] == 1_000_000
        planned_version_id = positive_body["planned_new_student_version_id"]
        revision_id = positive_body["revision"]["new_revision_id"]

        later_plan_item = planned_item(positive_detail, 7, "后续季是否适用由人数推演判断")
        later_plan_item["fiscal_season"] = 20272
        later_plan = await client.post(
            f"/api/v1/revisions/{revision_id}/planned-new-students:change-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": [later_plan_item]},
        )
        assert later_plan.status_code == 202, later_plan.text
        planned_version_id = later_plan.json()["planned_new_student_version_id"]
        revision_id = later_plan.json()["revision"]["new_revision_id"]

        reference_page = await client.get(
            f"/api/v1/rate-reference-versions/{rate_version.rate_reference_version_id}/references?limit=500",
            headers=maintainer_headers,
        )
        assert reference_page.status_code == 200, reference_page.text
        rate_items = []
        for reference in reference_page.json()["items"]:
            current = reference["system_value_ppm"]
            if current is None:
                current = 500_000
            maximum = 999_900 if reference["rate_type"] == "NEW_STUDENT_SHARE" else 1_000_000
            rate_items.append(
                {
                    "rate_reference_id": reference["rate_reference_id"],
                    "current_value_ppm": current,
                    "lower_bound_ppm": max(0, current - 100),
                    "upper_bound_ppm": min(maximum, current + 100),
                    "boundary_reason": "人数纵切集成测试边界",
                    "manual_reason": "允许低于历史参考 100 ppm",
                }
            )
        rate_draft = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-parameters:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "rate_reference_version_id": str(rate_version.rate_reference_version_id),
                "items": rate_items,
            },
        )
        assert rate_draft.status_code == 202, rate_draft.text
        rate_draft_body = rate_draft.json()
        revision_id = rate_draft.json()["revision"]["new_revision_id"]
        rate_boundary_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/rate-boundaries:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "rate_parameter_version_id": rate_draft_body["rate_parameter_version_id"],
                "rate_parameter_stable_keys": [
                    item["rate_parameter_stable_key"] for item in rate_draft_body["items"]
                ],
            },
        )
        assert rate_boundary_confirmation.status_code == 200, rate_boundary_confirmation.text

        headcount = await client.post(
            f"/api/v1/revisions/{revision_id}/headcounts:calculate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert headcount.status_code == 202, headcount.text
        headcount_body = headcount.json()
        assert headcount_body["status"] == "SUCCEEDED"
        assert headcount_body["publishable"] is False
        assert headcount_body["validation_count"] > 0
        assert headcount_body["record_count"] == len(internal_change.json()["items"]) * 4
        planned_result = next(
            item
            for item in headcount_body["items"]
            if item["fiscal_season"] == 20271 and item["product"] == "人工计划产品"
        )
        assert planned_result["theoretical_new_fraction"] == "30/1"
        assert planned_result["new_student_source"] == "LOCKED_MANUAL_PLAN"
        assert planned_result["planned_eligibility_status"] == "APPLICABLE_LOCKED"
        applied_later_plan = next(
            item
            for item in headcount_body["items"]
            if item["fiscal_season"] == 20272
            and item["combination_stable_key"] == positive_detail["combination_stable_key"]
        )
        # An explicitly locked later-season plan takes priority over rate
        # inference even when the combination already has returning students.
        assert applied_later_plan["new_student_source"] == "LOCKED_MANUAL_PLAN"
        assert applied_later_plan["planned_eligibility_status"] == "APPLICABLE_LOCKED"
        assert applied_later_plan["theoretical_new_fraction"] == "7/1"
        assert applied_later_plan["integer_new_students"] == 7
        assert applied_later_plan["new_student_share_input_ppm"] is None
        assert applied_later_plan["new_rounding_trace"]["fixed_input"] is True
        headcount_version_id = headcount_body["headcount_result_version_id"]
        revision_id = headcount_body["revision"]["new_revision_id"]
        headcount_read = await client.get(
            f"/api/v1/headcount-result-versions/{headcount_version_id}",
            headers=maintainer_headers,
        )
        assert headcount_read.status_code == 200, headcount_read.text
        assert headcount_read.json()["content_hash"] == headcount_body["content_hash"]
        assert headcount_read.json()["items"] == headcount_body["items"]
        hidden_headcount = await client.get(
            f"/api/v1/headcount-result-versions/{headcount_version_id}",
            headers={**maintainer_headers, "X-Department-ID": "other-department"},
        )
        assert hidden_headcount.status_code == 404

        resource_products = sorted({(item["track"], item["product"]) for item in headcount_body["items"]})
        resource_payload: dict[str, Any] = {
            "budget_tax_conversion_factor": "1.060000000",
            "tax_factor_source_evidence": {"reference": "FY27 学校版预算"},
            "tax_factor_change_reason": "建立 FY27 含税转换参数基线",
            "class_size_items": [
                {
                    "track": track,
                    "product": product,
                    "system_value": 12,
                    "final_value": 12,
                    "source_evidence": {"reference": "FY27 班均表"},
                    "change_reason": "集成测试完整班均参数",
                }
                for track, product in resource_products
            ],
            "revenue_items": [
                {
                    "fiscal_season": season,
                    "track": track,
                    "product": product,
                    "list_price_yuan": "1000.000000",
                    "current_season_recognition_rate_ppm": 750_000,
                    "opening_carryover_revenue_yuan": "0.000000",
                    "source_evidence": {"reference": "FY27 学费结转表"},
                    "change_reason": "集成测试完整收入参数",
                }
                for season in (20271, 20272, 20273, 20274)
                for track, product in resource_products
            ],
            "opening_carryover_items": [
                {
                    "fiscal_season": 20271,
                    "track": item["track"],
                    "product": item["product"],
                    "grade": item["grade"],
                    "subject": item["subject"],
                    "campus": item["campus"],
                    "amount_yuan": "0.000000",
                    "source_evidence": {"reference": "FY26 春季结转"},
                    "change_reason": "集成测试完整期初结转参数",
                }
                for item in headcount_body["items"]
                if item["fiscal_season"] == 20271
            ],
            "batch_content_hash": "0" * 64,
        }
        resource_contract = ResourceFinanceReplaceDraftRequest.model_validate(resource_payload)
        resource_payload["batch_content_hash"] = resource_finance_batch_content_hash(resource_contract)
        incomplete_resource_payload = {
            **resource_payload,
            "class_size_items": resource_payload["class_size_items"][:-1],
            "batch_content_hash": "0" * 64,
        }
        incomplete_contract = ResourceFinanceReplaceDraftRequest.model_validate(incomplete_resource_payload)
        incomplete_resource_payload["batch_content_hash"] = resource_finance_batch_content_hash(
            incomplete_contract
        )
        incomplete_resource = await client.post(
            f"/api/v1/revisions/{revision_id}/resource-finance:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=incomplete_resource_payload,
        )
        assert incomplete_resource.status_code == 422
        assert incomplete_resource.json()["code"] == "RESOURCE_FINANCE_BATCH_INVALID"
        resource_draft = await client.post(
            f"/api/v1/revisions/{revision_id}/resource-finance:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=resource_payload,
        )
        assert resource_draft.status_code == 202, resource_draft.text
        resource_body = resource_draft.json()
        assert resource_body["confirmed"] is False
        assert resource_body["class_size_record_count"] == len(resource_products)
        assert resource_body["revenue_record_count"] == len(resource_products) * 4
        assert resource_body["opening_carryover_record_count"] == len(
            resource_payload["opening_carryover_items"]
        )
        assert all(
            item["cross_season_recognition_rate_ppm"] == 250_000 for item in resource_body["revenue_items"]
        )
        resource_version_id = resource_body["resource_finance_parameter_version_id"]
        revision_id = resource_body["revision"]["new_revision_id"]

        forbidden_resource_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/resource-finance:confirm",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"resource_finance_parameter_version_id": resource_version_id},
        )
        assert forbidden_resource_confirmation.status_code == 403
        unconfirmed_class_size = await client.post(
            f"/api/v1/revisions/{revision_id}/class-sizes:validate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert unconfirmed_class_size.status_code == 409
        assert unconfirmed_class_size.json()["code"] == "RESOURCE_FINANCE_CONFIRMATION_REQUIRED"
        resource_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/resource-finance:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"resource_finance_parameter_version_id": resource_version_id},
        )
        assert resource_confirmation.status_code == 200, resource_confirmation.text
        resource_read = await client.get(
            f"/api/v1/resource-finance-parameter-versions/{resource_version_id}",
            headers=maintainer_headers,
        )
        assert resource_read.status_code == 200, resource_read.text
        assert resource_read.json()["confirmed"] is True
        assert resource_read.json()["approval_status"] == "CONFIRMED"
        assert resource_read.headers["ETag"] == f'"{resource_read.json()["content_hash"]}"'

        class_size = await client.post(
            f"/api/v1/revisions/{revision_id}/class-sizes:validate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert class_size.status_code == 202, class_size.text
        class_size_body = class_size.json()
        assert class_size_body["record_count"] == headcount_body["record_count"]
        assert class_size_body["publishable"] is False
        assert any(item["class_size_status"] == "HEADCOUNT_UNAVAILABLE" for item in class_size_body["items"])
        class_size_version_id = class_size_body["class_size_result_version_id"]
        revision_id = class_size_body["revision"]["new_revision_id"]
        class_size_read = await client.get(
            f"/api/v1/class-size-result-versions/{class_size_version_id}",
            headers=maintainer_headers,
        )
        assert class_size_read.status_code == 200, class_size_read.text
        assert class_size_read.json()["items"] == class_size_body["items"]
        assert class_size_read.headers["ETag"] == f'"{class_size_body["content_hash"]}"'

        discount_keys = sorted(
            {
                (item["fiscal_season"], item["track"], item["product"], item["grade"])
                for item in headcount_body["items"]
            }
        )
        tracks = sorted({item["track"] for item in headcount_body["items"]})
        discount_payload: dict[str, Any] = {
            "detail_items": [
                {
                    "fiscal_season": season,
                    "track": track,
                    "product": product,
                    "grade": grade,
                    "discount_rate_ppm": 900_000,
                    "source_evidence": {"reference": "FY27 折扣表"},
                    "change_reason": "集成测试完整明细折扣",
                }
                for season, track, product, grade in discount_keys
            ],
            "composite_targets": [
                {
                    "scope_level": scope,
                    "track": track,
                    "period_type": period,
                    "fiscal_season": season,
                    "target_rate_ppm": 900_000,
                    "source_evidence": {"reference": "FY27 学校版综合折扣"},
                    "change_reason": "集成测试完整综合折扣目标",
                }
                for scope, track in [("DEPARTMENT", None), *(("TRACK", value) for value in tracks)]
                for period, season in [
                    ("ANNUAL", None),
                    *(("SEASON", value) for value in (20271, 20272, 20273, 20274)),
                ]
            ],
            "batch_content_hash": "0" * 64,
        }
        discount_contract = DiscountParameterReplaceDraftRequest.model_validate(discount_payload)
        discount_payload["batch_content_hash"] = discount_parameter_batch_content_hash(discount_contract)
        discount_draft = await client.post(
            f"/api/v1/revisions/{revision_id}/discount-parameters:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=discount_payload,
        )
        assert discount_draft.status_code == 202, discount_draft.text
        discount_body = discount_draft.json()
        assert discount_body["detail_record_count"] == len(discount_keys)
        assert discount_body["target_record_count"] == (len(tracks) + 1) * 5
        assert discount_body["confirmed"] is False
        assert discount_body["approval_status"] == "DRAFT"
        discount_version_id = discount_body["discount_parameter_version_id"]
        revision_id = discount_body["revision"]["new_revision_id"]
        discount_read = await client.get(
            f"/api/v1/discount-parameter-versions/{discount_version_id}",
            headers=maintainer_headers,
        )
        assert discount_read.status_code == 200, discount_read.text
        assert discount_read.headers["ETag"] == f'"{discount_body["content_hash"]}"'

        unconfirmed_revenue = await client.post(
            f"/api/v1/revisions/{revision_id}/revenues:calculate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert unconfirmed_revenue.status_code == 409
        assert unconfirmed_revenue.json()["code"] == "DISCOUNT_PARAMETER_CONFIRMATION_REQUIRED"
        forbidden_discount_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/discount-parameters:confirm",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"discount_parameter_version_id": discount_version_id},
        )
        assert forbidden_discount_confirmation.status_code == 403
        discount_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/discount-parameters:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"discount_parameter_version_id": discount_version_id},
        )
        assert discount_confirmation.status_code == 200, discount_confirmation.text
        discount_confirmation_id = discount_confirmation.json()["discount_parameter_confirmation_id"]
        confirmed_discount_read = await client.get(
            f"/api/v1/discount-parameter-versions/{discount_version_id}",
            headers=maintainer_headers,
        )
        assert confirmed_discount_read.status_code == 200, confirmed_discount_read.text
        assert confirmed_discount_read.json()["confirmed"] is True
        assert confirmed_discount_read.json()["approval_status"] == "CONFIRMED"

        revenue = await client.post(
            f"/api/v1/revisions/{revision_id}/revenues:calculate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert revenue.status_code == 202, revenue.text
        revenue_body = revenue.json()
        assert revenue_body["record_count"] == headcount_body["record_count"]
        assert revenue_body["discount_parameter_confirmation_id"] == discount_confirmation_id
        assert revenue_body["publishable"] is False
        assert revenue_body["aggregate_count"] == (len(tracks) + 1) * 5
        assert any(
            item["code"] == "UPSTREAM_CLASS_SIZE_NOT_PUBLISHABLE" for item in revenue_body["validations"]
        )
        revenue_version_id = revenue_body["revenue_result_version_id"]
        revision_id = revenue_body["revision"]["new_revision_id"]
        revenue_read = await client.get(
            f"/api/v1/revenue-result-versions/{revenue_version_id}",
            headers=maintainer_headers,
        )
        assert revenue_read.status_code == 200, revenue_read.text
        assert revenue_read.headers["ETag"] == f'"{revenue_body["content_hash"]}"'

        target_closure = await client.post(
            f"/api/v1/revisions/{revision_id}/target-closures:validate",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert target_closure.status_code == 202, target_closure.text
        target_closure_body = target_closure.json()
        assert target_closure_body["record_count"] == len(target_tracks) + 1
        assert target_closure_body["closure_status"] == "OVER_TARGET_PENDING_REBALANCE"
        assert target_closure_body["publishable"] is False
        assert all(item["check_status"] == "OVER_TARGET" for item in target_closure_body["items"])
        target_closure_version_id = target_closure_body["target_closure_result_version_id"]
        target_closure_result_id = target_closure_body["items"][0]["target_closure_result_id"]
        revision_id = target_closure_body["revision"]["new_revision_id"]
        target_closure_read = await client.get(
            f"/api/v1/target-closure-result-versions/{target_closure_version_id}",
            headers=maintainer_headers,
        )
        assert target_closure_read.status_code == 200, target_closure_read.text
        assert target_closure_read.headers["ETag"] == f'"{target_closure_body["content_hash"]}"'

        optimization_key = uuid.uuid4()
        optimization = await client.post(
            f"/api/v1/revisions/{revision_id}/optimization-runs",
            headers={**maintainer_headers, "Idempotency-Key": str(optimization_key)},
            json={"maximum_enumerated_states": 1},
        )
        assert optimization.status_code == 201, optimization.text
        optimization_body = optimization.json()
        assert optimization_body["status"] == "INCOMPLETE"
        assert optimization_body["terminal_stage"] == "ENTRY_ONLY"
        assert optimization_body["reason_code"] == "SEARCH_SPACE_LIMIT_EXCEEDED"
        assert optimization_body["solver_adapter"] == "EXACT_ENUMERATION"
        assert optimization_body["maximum_solver_seconds"] == 300
        assert optimization_body["maximum_candidate_validations"] == 10_000
        assert optimization_body["maximum_group_table_rows"] == 100_000
        assert optimization_body["discount_unlock_required"] is False
        assert optimization_body["discount_unlock_confirmation_id"] is None
        assert optimization_body["scenarios"] == []
        optimization_run_id = optimization_body["optimization_run_id"]
        optimization_read = await client.get(
            f"/api/v1/optimization-runs/{optimization_run_id}", headers=maintainer_headers
        )
        assert optimization_read.status_code == 200, optimization_read.text
        assert optimization_read.headers["ETag"] == f'"{optimization_body["content_hash"]}"'
        latest_optimization = await client.get(
            f"/api/v1/revisions/{revision_id}/optimization-runs/latest",
            headers=maintainer_headers,
        )
        assert latest_optimization.status_code == 200, latest_optimization.text
        assert latest_optimization.json()["optimization_run_id"] == optimization_run_id
        optimization_replay = await client.post(
            f"/api/v1/revisions/{revision_id}/optimization-runs",
            headers={**maintainer_headers, "Idempotency-Key": str(optimization_key)},
            json={"maximum_enumerated_states": 1},
        )
        assert optimization_replay.status_code == 201
        assert optimization_replay.json()["optimization_run_id"] == optimization_run_id

        cp_sat_optimization = await client.post(
            f"/api/v1/revisions/{revision_id}/optimization-runs",
            headers={**maintainer_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "solver_adapter": "CP_SAT_ENTRY_ONLY",
                # This checks the candidate limit, not process startup speed.
                "maximum_solver_seconds": 60,
                "maximum_candidate_validations": 1,
            },
        )
        assert cp_sat_optimization.status_code == 201, cp_sat_optimization.text
        cp_sat_body = cp_sat_optimization.json()
        assert cp_sat_body["solver_adapter"] == "CP_SAT_ENTRY_ONLY"
        assert cp_sat_body["status"] == "INCOMPLETE"
        assert cp_sat_body["reason_code"] == "CP_SAT_CANDIDATE_VALIDATION_LIMIT_EXCEEDED"
        assert cp_sat_body["attempts"][0]["proof_summary"]["adapter_version"].startswith("ortools-cp-sat-")

        cp_sat_rates_optimization = await client.post(
            f"/api/v1/revisions/{revision_id}/optimization-runs",
            headers={**maintainer_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "solver_adapter": "CP_SAT_ENTRY_AND_RATES",
                "maximum_solver_seconds": 60,
                "maximum_candidate_validations": 1,
            },
        )
        assert cp_sat_rates_optimization.status_code == 201, cp_sat_rates_optimization.text
        cp_sat_rates_body = cp_sat_rates_optimization.json()
        assert cp_sat_rates_body["solver_adapter"] == "CP_SAT_ENTRY_AND_RATES"
        assert cp_sat_rates_body["status"] == "INCOMPLETE"

        eligible_run_id = uuid.uuid4()
        eligible_proof_id = uuid.uuid4()
        async with get_session_factory()() as session:
            async with session.begin():
                session.add(
                    OptimizationRun(
                        optimization_run_id=eligible_run_id,
                        budget_revision_id=uuid.UUID(revision_id),
                        target_closure_result_version_id=uuid.UUID(target_closure_version_id),
                        optimization_model_version="phase1-deterministic-rebalancing-v3",
                        terminal_stage="ENTRY_AND_RATES",
                        status="INFEASIBLE",
                        discount_unlock_required=True,
                        discount_unlock_confirmation_id=None,
                        reason_code="DISCOUNT_UNLOCK_REQUIRED",
                        solver_adapter="CP_SAT_ENTRY_AND_RATES",
                        maximum_enumerated_states=100_000,
                        maximum_solver_seconds=300,
                        maximum_candidate_validations=10_000,
                        maximum_group_table_rows=100_000,
                        evaluated_state_count=0,
                        attempts=[],
                        input_fingerprint=cp_sat_rates_body["input_fingerprint"],
                        content_hash="d" * 64,
                        request_hash="e" * 64,
                        created_by="integration-proof-fixture",
                        created_at=datetime.now(UTC),
                    )
                )
                await session.flush()
                session.add(
                    InfeasibilityProof(
                        infeasibility_proof_id=eligible_proof_id,
                        optimization_run_id=eligible_run_id,
                        stage="ENTRY_AND_RATES",
                        profile="BALANCED",
                        proof_code="CP_SAT_DIRECT_BUSINESS_MODEL_INFEASIBLE",
                        model_hash="f" * 64,
                        hard_constraint_groups=["TARGET_CLOSURE", "COMPOSITE_DISCOUNT"],
                        proof_summary={"fixture": True},
                        content_hash="a" * 64,
                    )
                )
        unlock_request = await client.post(
            f"/api/v1/revisions/{revision_id}/discount-unlocks:request",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={
                "optimization_run_id": str(eligible_run_id),
                "infeasibility_proof_id": str(eligible_proof_id),
                "reason": "入口和比例边界已精确穷尽",
            },
        )
        assert unlock_request.status_code == 201, unlock_request.text
        unlock_request_body = unlock_request.json()
        assert unlock_request_body["status"] == "REQUESTED"
        assert unlock_request_body["discount_unlock_confirmation_id"] is None
        current_unlock = await client.get(
            f"/api/v1/revisions/{revision_id}/discount-unlocks/current",
            params={"input_fingerprint": cp_sat_rates_body["input_fingerprint"]},
            headers=maintainer_headers,
        )
        assert current_unlock.status_code == 200, current_unlock.text
        assert (
            current_unlock.json()["discount_unlock_request_id"]
            == unlock_request_body["discount_unlock_request_id"]
        )
        unlock_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}/discount-unlocks:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"discount_unlock_request_id": unlock_request_body["discount_unlock_request_id"]},
        )
        assert unlock_confirmation.status_code == 200, unlock_confirmation.text
        assert unlock_confirmation.json()["status"] == "ACTIVE"
        assert unlock_confirmation.json()["discount_unlock_confirmation_id"] is not None
        active_unlock = await client.get(
            f"/api/v1/revisions/{revision_id}/discount-unlocks/current",
            params={"input_fingerprint": cp_sat_rates_body["input_fingerprint"]},
            headers=owner_headers,
        )
        assert active_unlock.status_code == 200, active_unlock.text
        assert active_unlock.json()["status"] == "ACTIVE"
        unchanged_revision = await client.get(f"/api/v1/revisions/{revision_id}", headers=maintainer_headers)
        assert unchanged_revision.status_code == 200
        assert (
            unchanged_revision.json()["component_manifest"]["target_closure_result_version_id"]
            == target_closure_version_id
        )
        assert "optimization_run_id" not in unchanged_revision.json()["component_manifest"]

        application_run_id = uuid.uuid4()
        application_scenario_id = uuid.uuid4()
        async with get_session_factory()() as session:
            async with session.begin():
                current_revision = await session.get(BudgetRevision, uuid.UUID(revision_id))
                assert current_revision is not None
                detail_version_id = uuid.UUID(
                    str(current_revision.component_manifest["entry_detail_set_version_id"])
                )
                entry_rows = list(
                    await session.scalars(
                        select(EntryDetailAllocation)
                        .where(
                            EntryDetailAllocation.entry_detail_set_version_id == detail_version_id,
                            EntryDetailAllocation.entry_initial_value > 0,
                        )
                        .order_by(
                            EntryDetailAllocation.campus,
                            EntryDetailAllocation.combination_stable_key,
                        )
                    )
                )
                cross_campus_pair = next(
                    (left, right)
                    for left in entry_rows
                    for right in entry_rows
                    if left.campus != right.campus
                    and left.combination_stable_key != right.combination_stable_key
                )
                rate_parameter_version_id = uuid.UUID(
                    str(current_revision.component_manifest["rate_parameter_version_id"])
                )
                rate_row = await session.scalar(
                    select(RateParameter)
                    .where(RateParameter.rate_parameter_version_id == rate_parameter_version_id)
                    .order_by(RateParameter.rate_parameter_stable_key)
                    .limit(1)
                )
                assert rate_row is not None
                suggested_rate = (
                    rate_row.upper_bound_ppm
                    if rate_row.upper_bound_ppm != rate_row.current_value_ppm
                    else rate_row.lower_bound_ppm
                )
                discount_parameter_version_id = uuid.UUID(
                    str(current_revision.component_manifest["discount_parameter_version_id"])
                )
                discount_row = await session.scalar(
                    select(DetailDiscountParameter)
                    .where(
                        DetailDiscountParameter.discount_parameter_version_id == discount_parameter_version_id
                    )
                    .order_by(DetailDiscountParameter.detail_discount_stable_key)
                    .limit(1)
                )
                assert discount_row is not None
                suggested_discount = (
                    discount_row.discount_rate_ppm + 1_000
                    if discount_row.discount_rate_ppm < 1_000_000
                    else discount_row.discount_rate_ppm - 1_000
                )
                session.add(
                    OptimizationRun(
                        optimization_run_id=application_run_id,
                        budget_revision_id=uuid.UUID(revision_id),
                        target_closure_result_version_id=uuid.UUID(target_closure_version_id),
                        optimization_model_version="phase1-deterministic-rebalancing-v3",
                        terminal_stage="ENTRY_RATES_AND_DISCOUNT",
                        status="OPTIMAL",
                        discount_unlock_required=True,
                        discount_unlock_confirmation_id=uuid.UUID(
                            unlock_confirmation.json()["discount_unlock_confirmation_id"]
                        ),
                        reason_code=None,
                        solver_adapter="CP_SAT_ENTRY_AND_RATES",
                        maximum_enumerated_states=100_000,
                        maximum_solver_seconds=300,
                        maximum_candidate_validations=10_000,
                        maximum_group_table_rows=100_000,
                        evaluated_state_count=1,
                        attempts=[],
                        input_fingerprint=cp_sat_rates_body["input_fingerprint"],
                        content_hash="7" * 64,
                        request_hash="8" * 64,
                        created_by="integration-application-fixture",
                        created_at=datetime.now(UTC),
                    )
                )
                session.add(
                    RebalancingScenario(
                        rebalancing_scenario_id=application_scenario_id,
                        optimization_run_id=application_run_id,
                        result_hash="9" * 64,
                        scenario_types=["ENTRY_STRUCTURE", "BALANCED", "DISCOUNT"],
                        target_variances=[],
                        retention_contribution_yuan=0,
                        new_share_contribution_yuan=0,
                        headcount_signature=[],
                        objective_values={},
                        content_hash="a" * 64,
                    )
                )
                for row, suggested in (
                    (cross_campus_pair[0], cross_campus_pair[0].entry_initial_value + 1),
                    (cross_campus_pair[1], cross_campus_pair[1].entry_initial_value - 1),
                ):
                    change_content = {
                        "object_type": "ENTRY",
                        "object_stable_key": row.combination_stable_key,
                        "original_value": row.entry_initial_value,
                        "suggested_value": suggested,
                        "adjustment": suggested - row.entry_initial_value,
                    }
                    session.add(
                        ScenarioChange(
                            scenario_change_id=uuid.uuid4(),
                            rebalancing_scenario_id=application_scenario_id,
                            **change_content,
                            content_hash=content_hash(change_content),
                        )
                    )
                for object_type, stable_key, original, suggested in (
                    (
                        "RATE",
                        str(rate_row.rate_parameter_id),
                        rate_row.current_value_ppm,
                        suggested_rate,
                    ),
                    (
                        "DISCOUNT",
                        discount_row.detail_discount_stable_key,
                        discount_row.discount_rate_ppm,
                        suggested_discount,
                    ),
                ):
                    change_content = {
                        "object_type": object_type,
                        "object_stable_key": stable_key,
                        "original_value": original,
                        "suggested_value": suggested,
                        "adjustment": suggested - original,
                    }
                    session.add(
                        ScenarioChange(
                            scenario_change_id=uuid.uuid4(),
                            rebalancing_scenario_id=application_scenario_id,
                            **change_content,
                            content_hash=content_hash(change_content),
                        )
                    )
        forbidden_application = await client.post(
            f"/api/v1/revisions/{revision_id}/scenarios/{application_scenario_id}:apply",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "负责人不能代替数据维护人员应用候选"},
        )
        assert forbidden_application.status_code == 403
        application_key = uuid.uuid4()
        application = await client.post(
            f"/api/v1/revisions/{revision_id}/scenarios/{application_scenario_id}:apply",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(application_key),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "采用入口结构平衡候选方案"},
        )
        assert application.status_code == 202, application.text
        application_body = application.json()
        scenario_application_id = application_body["scenario_application_id"]
        applied_revision_id = application_body["applied_revision_id"]
        assert application_body["change_count"] == 4
        assert application_body["entry_change_count"] == 2
        assert application_body["rate_change_count"] == 1
        assert application_body["discount_change_count"] == 1
        assert application_body["campus_confirmation_required"] is True
        assert application_body["campus_entry_set_version_id"] is not None
        assert application_body["entry_detail_set_version_id"] is not None
        assert application_body["planned_new_student_version_id"] is not None
        planned_version_id = application_body["planned_new_student_version_id"]
        assert application_body["rate_parameter_version_id"] is not None
        assert application_body["discount_parameter_version_id"] is not None
        assert application_body["inherited_discount_confirmation_id"] is not None
        assert application_body["calculation_job_status"] == "QUEUED"
        calculation_job_id = application_body["calculation_job_id"]
        assert application_body["links"]["calculation_job"] == f"/api/v1/jobs/{calculation_job_id}"
        discount_version_id = application_body["discount_parameter_version_id"]
        applied_revision = await client.get(
            f"/api/v1/revisions/{applied_revision_id}", headers=maintainer_headers
        )
        assert applied_revision.status_code == 200, applied_revision.text
        applied_manifest = applied_revision.json()["component_manifest"]
        assert applied_manifest["scenario_application_id"] == scenario_application_id
        assert (
            applied_manifest["entry_detail_set_version_id"] == application_body["entry_detail_set_version_id"]
        )
        assert (
            applied_manifest["planned_new_student_version_id"]
            == application_body["planned_new_student_version_id"]
        )
        assert "headcount_result_version_id" not in applied_manifest
        assert "revenue_result_version_id" not in applied_manifest
        assert "target_closure_result_version_id" not in applied_manifest

        rate_detail_diff = await client.get(
            f"/api/v1/revisions/{applied_revision_id}/detail-diff",
            params={
                "against": revision_id,
                "component_key": "rate_parameter_version_id",
            },
            headers=maintainer_headers,
        )
        assert rate_detail_diff.status_code == 200, rate_detail_diff.text
        rate_detail_body = rate_detail_diff.json()
        assert rate_detail_body["changed_count"] == 1
        assert len(rate_detail_body["items"]) == 1
        assert rate_detail_body["items"][0]["detail_type"] == "RENEWAL_RATE"
        assert (
            rate_detail_body["items"][0]["against"]["final_value"]
            != rate_detail_body["items"][0]["revision"]["final_value"]
        )

        discount_detail_diff = await client.get(
            f"/api/v1/revisions/{applied_revision_id}/detail-diff",
            params={
                "against": revision_id,
                "component_key": "discount_parameter_version_id",
            },
            headers=maintainer_headers,
        )
        assert discount_detail_diff.status_code == 200, discount_detail_diff.text
        discount_detail_body = discount_detail_diff.json()
        assert discount_detail_body["changed_count"] == 1
        assert len(discount_detail_body["items"]) == 1
        assert discount_detail_body["items"][0]["detail_type"] == "DETAIL_DISCOUNT"

        unsupported_detail_diff = await client.get(
            f"/api/v1/revisions/{applied_revision_id}/detail-diff",
            params={
                "against": revision_id,
                "component_key": "entry_detail_set_version_id",
            },
            headers=maintainer_headers,
        )
        assert unsupported_detail_diff.status_code == 422
        assert unsupported_detail_diff.json()["code"] == "REVISION_DETAIL_COMPONENT_UNSUPPORTED"

        application_replay = await client.post(
            f"/api/v1/revisions/{revision_id}/scenarios/{application_scenario_id}:apply",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(application_key),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "采用入口结构平衡候选方案"},
        )
        assert application_replay.status_code == 202
        assert application_replay.json()["scenario_application_id"] == scenario_application_id
        duplicate_application = await client.post(
            f"/api/v1/revisions/{applied_revision_id}/scenarios/{application_scenario_id}:apply",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{applied_revision_id}"',
            },
            json={"reason": "不得用新幂等键重复应用同一候选"},
        )
        assert duplicate_application.status_code == 409
        assert duplicate_application.json()["code"] == "REBALANCING_SCENARIO_ALREADY_APPLIED"
        queued_job = await client.get(f"/api/v1/jobs/{calculation_job_id}", headers=maintainer_headers)
        assert queued_job.status_code == 200
        assert queued_job.json()["status"] == "QUEUED"
        calculation_job_uuid = uuid.UUID(calculation_job_id)
        assert (
            await handle_one(
                JobRepository(worker_id="integration-calculation-worker"),
                job_id=calculation_job_uuid,
            )
            is True
        )
        incomplete_job = await client.get(f"/api/v1/jobs/{calculation_job_id}", headers=maintainer_headers)
        assert incomplete_job.status_code == 200
        assert incomplete_job.json()["status"] == "INCOMPLETE"
        assert incomplete_job.json()["last_error_code"] == "CAMPUS_ENTRY_CONFIRMATION_REQUIRED"
        campus_reconfirmation = await client.post(
            f"/api/v1/revisions/{applied_revision_id}/campus-entry:confirm",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{applied_revision_id}"',
            },
            json={"campus_entry_set_version_id": application_body["campus_entry_set_version_id"]},
        )
        assert campus_reconfirmation.status_code == 200, campus_reconfirmation.text
        requeued_job = await client.get(f"/api/v1/jobs/{calculation_job_id}", headers=maintainer_headers)
        assert requeued_job.status_code == 200
        assert requeued_job.json()["status"] == "QUEUED"
        assert (
            await handle_one(
                JobRepository(worker_id="integration-calculation-worker"),
                job_id=calculation_job_uuid,
            )
            is True
        )
        completed_job = await client.get(f"/api/v1/jobs/{calculation_job_id}", headers=maintainer_headers)
        assert completed_job.status_code == 200
        completed_job_body = completed_job.json()
        assert completed_job_body["status"] == "SUCCEEDED"
        assert completed_job_body["attempt_no"] == 2
        calculation_result = completed_job_body["result"]
        assert calculation_result["source_revision_id"] == applied_revision_id
        assert [item["stage"] for item in calculation_result["stages"]] == [
            "HEADCOUNT",
            "CLASS_SIZE",
            "REVENUE",
            "TARGET_CLOSURE",
        ]
        headcount_version_id = calculation_result["headcount_result_version_id"]
        class_size_version_id = calculation_result["class_size_result_version_id"]
        revenue_version_id = calculation_result["revenue_result_version_id"]
        target_closure_version_id = calculation_result["target_closure_result_version_id"]
        revision_id = calculation_result["final_revision_id"]
        async with get_session_factory()() as session:
            completed_job_record = await session.get(AsyncJob, uuid.UUID(calculation_job_id))
            assert completed_job_record is not None
            retry_payload = completed_job_record.payload
        replayed_calculation = await run_full_calculation_job(uuid.UUID(calculation_job_id), retry_payload)
        assert replayed_calculation == calculation_result

        archive_key = uuid.uuid4()
        archive_request = await client.post(
            f"/api/v1/revisions/{revision_id}:export",
            headers={**owner_headers, "Idempotency-Key": str(archive_key)},
            json={"reason": "归档完整计算后的预算证据"},
        )
        assert archive_request.status_code == 202, archive_request.text
        archive_request_body = archive_request.json()
        archive_job_id = archive_request_body["job_id"]
        assert archive_request_body["budget_revision_id"] == revision_id
        assert archive_request_body["status"] == "QUEUED"
        archive_replay = await client.post(
            f"/api/v1/revisions/{revision_id}:export",
            headers={**owner_headers, "Idempotency-Key": str(archive_key)},
            json={"reason": "归档完整计算后的预算证据"},
        )
        assert archive_replay.status_code == 202
        assert archive_replay.json()["job_id"] == archive_job_id
        archive_mismatch = await client.post(
            f"/api/v1/revisions/{revision_id}:export",
            headers={**owner_headers, "Idempotency-Key": str(archive_key)},
            json={"reason": "同一键不得改变归档原因"},
        )
        assert archive_mismatch.status_code == 409
        assert archive_mismatch.json()["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        assert (
            await handle_one(
                JobRepository(worker_id="integration-audit-archive-worker"),
                job_id=uuid.UUID(archive_job_id),
            )
            is True
        )
        archive_job = await client.get(f"/api/v1/jobs/{archive_job_id}", headers=owner_headers)
        assert archive_job.status_code == 200
        archive_job_body = archive_job.json()
        assert archive_job_body["status"] == "SUCCEEDED"
        audit_archive_id = archive_job_body["result"]["audit_archive_id"]
        archive_metadata = await client.get(
            f"/api/v1/audit-archives/{audit_archive_id}", headers=owner_headers
        )
        assert archive_metadata.status_code == 200, archive_metadata.text
        archive_metadata_body = archive_metadata.json()
        assert archive_metadata_body["budget_revision_id"] == revision_id
        assert archive_metadata_body["schema_version"] == "phase1-audit-archive-v1"
        archive_download = await client.get(
            f"/api/v1/audit-archives/{audit_archive_id}/download", headers=owner_headers
        )
        assert archive_download.status_code == 200, archive_download.text
        assert archive_download.headers["content-type"] == "application/zip"
        assert hashlib.sha256(archive_download.content).hexdigest() == archive_metadata_body["content_hash"]
        assert len(archive_download.content) == archive_metadata_body["size_bytes"]
        with zipfile.ZipFile(io.BytesIO(archive_download.content)) as bundle:
            names = set(bundle.namelist())
            assert "manifest.json" in names
            assert "checksums.sha256" in names
            assert "audit/events.json" in names
            assert "source/source_snapshot.json" in names
            assert "results/headcount_result_version_id.json" in names
            assert "results/target_closure_result_version_id.json" in names
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["budget_revision_id"] == revision_id
            assert manifest["audit_scope"] == "ANNUAL_BUDGET_THROUGH_CUTOFF"
            headcount_archive = json.loads(bundle.read("results/headcount_result_version_id.json"))
            assert "headcount_validation_result" in headcount_archive["tables"]
            archived_events = json.loads(bundle.read("audit/events.json"))
            assert any(item["event_type"] == "AUDIT_ARCHIVE_QUEUED" for item in archived_events)
        hidden_archive = await client.get(
            f"/api/v1/audit-archives/{audit_archive_id}",
            headers={**owner_headers, "X-Department-ID": f"other-{uuid.uuid4()}"},
        )
        assert hidden_archive.status_code == 404

        replaced_target = await client.post(
            f"/api/v1/revisions/{revision_id}/targets:replace-and-lock",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=target_payload,
        )
        assert replaced_target.status_code == 202, replaced_target.text
        replaced_target_body = replaced_target.json()
        assert replaced_target_body["parent_target_set_version_id"] == target_set_version_id
        target_set_version_id = replaced_target_body["target_set_version_id"]
        budget_target_id = replaced_target_body["items"][0]["budget_target_id"]
        revision_id = replaced_target_body["revision"]["new_revision_id"]
        target_replacement_revision = await client.get(
            f"/api/v1/revisions/{revision_id}", headers=maintainer_headers
        )
        assert target_replacement_revision.status_code == 200
        assert (
            target_replacement_revision.json()["component_manifest"]["revenue_result_version_id"]
            == revenue_version_id
        )
        assert (
            "target_closure_result_version_id" not in target_replacement_revision.json()["component_manifest"]
        )

        replaced_resource = await client.post(
            f"/api/v1/revisions/{revision_id}/resource-finance:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json=resource_payload,
        )
        assert replaced_resource.status_code == 202, replaced_resource.text
        replacement_revision_id = replaced_resource.json()["revision"]["new_revision_id"]
        replacement_revision = await client.get(
            f"/api/v1/revisions/{replacement_revision_id}", headers=maintainer_headers
        )
        assert replacement_revision.status_code == 200
        assert (
            replacement_revision.json()["component_manifest"]["headcount_result_version_id"]
            == headcount_version_id
        )
        assert "class_size_result_version_id" not in replacement_revision.json()["component_manifest"]
        assert "revenue_result_version_id" not in replacement_revision.json()["component_manifest"]
        assert "target_closure_result_version_id" not in replacement_revision.json()["component_manifest"]
        assert (
            replacement_revision.json()["component_manifest"]["target_set_version_id"]
            == target_set_version_id
        )
        assert (
            replacement_revision.json()["component_manifest"]["discount_parameter_version_id"]
            == discount_version_id
        )
        resource_detail_diff = await client.get(
            f"/api/v1/revisions/{replacement_revision_id}/detail-diff",
            params={
                "against": revision_id,
                "component_key": "resource_finance_parameter_version_id",
            },
            headers=maintainer_headers,
        )
        assert resource_detail_diff.status_code == 200, resource_detail_diff.text
        resource_detail_body = resource_detail_diff.json()
        assert resource_detail_body["changed_count"] == resource_detail_body["total_count"]
        assert any(
            item["detail_type"] == "CLASS_SIZE"
            and item["revision"]["system_value"] is not None
            and item["revision"]["final_value"] is not None
            for item in resource_detail_body["items"]
        )
        resource_version_id = replaced_resource.json()["resource_finance_parameter_version_id"]
        revision_id = replacement_revision_id

        derived = await client.post(
            f"/api/v1/revisions/{revision_id}:derive",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "验证计划新生锁定版本同财年精确继承"},
        )
        assert derived.status_code == 202, derived.text
        revision_id = derived.json()["new_revision_id"]
        inherited = await client.get(f"/api/v1/revisions/{revision_id}", headers=maintainer_headers)
        assert inherited.status_code == 200
        assert inherited.json()["component_manifest"]["planned_new_student_version_id"] == planned_version_id

        locked_plan_revision_id = revision_id
        unlock_payload = planned_item(zero_detail, 30, "负责人解锁重新评估")
        unlock_payload.pop("planned_new_students")
        unlocked = await client.post(
            f"/api/v1/revisions/{revision_id}/planned-new-students:unlock",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": [unlock_payload]},
        )
        assert unlocked.status_code == 202, unlocked.text
        unlocked_body = unlocked.json()
        assert unlocked_body["locked_count"] == 1
        assert unlocked_body["pending_lock_count"] == 1
        unlocked_item = next(item for item in unlocked_body["items"] if item["product"] == "人工计划产品")
        assert unlocked_item["handling_status"] == "FILLED_PENDING_LOCK"
        assert unlocked_item["planned_new_students"] == 30
        unlocked_version_id = unlocked_body["planned_new_student_version_id"]
        revision_id = unlocked_body["revision"]["new_revision_id"]
        planned_read = await client.get(
            f"/api/v1/planned-new-student-versions/{unlocked_version_id}",
            headers=maintainer_headers,
        )
        assert planned_read.status_code == 200, planned_read.text
        assert planned_read.json()["content_hash"] == unlocked_body["content_hash"]
        assert planned_read.json()["items"] == unlocked_body["items"]
        unlocked_revision = await client.get(f"/api/v1/revisions/{revision_id}", headers=maintainer_headers)
        assert unlocked_revision.status_code == 200
        planned_detail_diff = await client.get(
            f"/api/v1/revisions/{revision_id}/detail-diff",
            params={
                "against": locked_plan_revision_id,
                "component_key": "planned_new_student_version_id",
            },
            headers=maintainer_headers,
        )
        assert planned_detail_diff.status_code == 200, planned_detail_diff.text
        planned_detail_body = planned_detail_diff.json()
        assert planned_detail_body["changed_count"] == 1
        assert len(planned_detail_body["items"]) == 1
        assert planned_detail_body["items"][0]["detail_type"] == "PLANNED_NEW_STUDENTS"
        assert planned_detail_body["items"][0]["against"]["final_value"] == 30
        assert planned_detail_body["items"][0]["revision"]["final_value"] == 30
        assert planned_detail_body["items"][0]["against"]["confirmed"] is True
        assert planned_detail_body["items"][0]["revision"]["confirmed"] is False
        assert "headcount_result_version_id" not in unlocked_revision.json()["component_manifest"]

        internal_combination_items = [
            {
                "track": item["track"],
                "product": item["product"],
                "grade": item["grade"],
                "subject": item["subject"],
                "campus": item["campus"],
                "entry_attribute": item["entry_attribute"],
                "enabled": item["enabled"],
                "change_reason": item["change_reason"],
                "class_size_parameter_reference": item["class_size_parameter_reference"],
            }
            for item in internal_change.json()["items"]
        ]
        internal_combination_items.append(
            {
                "track": internal_combination_items[0]["track"],
                "product": "新校区产品",
                "grade": "七年级",
                "subject": "数学",
                "campus": "新校区",
                "entry_attribute": "ENTRY",
                "enabled": True,
                "change_reason": "新增完整校区",
                "class_size_parameter_reference": "class-size:new-campus:v1",
            }
        )
        campus_scope_change = await client.post(
            f"/api/v1/revisions/{revision_id}/combinations:replace-draft",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"items": internal_combination_items},
        )
        assert campus_scope_change.status_code == 202, campus_scope_change.text
        revision_id = campus_scope_change.json()["revision"]["new_revision_id"]
        final_revision = await client.get(
            f"/api/v1/revisions/{revision_id}",
            headers=maintainer_headers,
        )
        assert final_revision.status_code == 200
        assert (
            final_revision.json()["component_manifest"]["combination_set_version_id"]
            == (campus_scope_change.json()["combination_set_version_id"])
        )
        assert "entry_total_version_id" in final_revision.json()["component_manifest"]
        assert "campus_entry_suggestion_version_id" not in final_revision.json()["component_manifest"]
        assert "campus_entry_set_version_id" not in final_revision.json()["component_manifest"]
        assert "entry_scope_version_id" not in final_revision.json()["component_manifest"]
        assert "entry_detail_set_version_id" not in final_revision.json()["component_manifest"]
        assert "planned_new_student_version_id" not in final_revision.json()["component_manifest"]
        assert "resource_finance_parameter_version_id" not in final_revision.json()["component_manifest"]
        assert "discount_parameter_version_id" not in final_revision.json()["component_manifest"]

        readiness = await client.get(
            f"/api/v1/revisions/{revision_id}/publication-readiness",
            headers=owner_headers,
        )
        assert readiness.status_code == 200, readiness.text
        assert readiness.json()["status"] == "BLOCKED"
        assert readiness.json()["blocker_count"] > 0
        assert any(
            item["code"] == "REQUIRED_COMPONENTS_PRESENT" and item["passed"] is False
            for item in readiness.json()["checks"]
        )
        publish_without_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}:publish",
            headers={
                **owner_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={},
        )
        assert publish_without_confirmation.status_code == 409
        assert publish_without_confirmation.json()["code"] == "FORMAL_PLAN_CONFIRMATION_REQUIRED"
        forbidden_formal_confirmation = await client.post(
            f"/api/v1/revisions/{revision_id}:confirm-formal-plan",
            headers={
                **maintainer_headers,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "数据维护人员不得代替负责人确认"},
        )
        assert forbidden_formal_confirmation.status_code == 403
        formal_request_key = uuid.uuid4()
        formal_request = await client.post(
            f"/api/v1/revisions/{revision_id}:confirm-formal-plan",
            headers={
                **owner_headers,
                "Idempotency-Key": str(formal_request_key),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "复算后确认 FY27 完整预算方案"},
        )
        assert formal_request.status_code == 202, formal_request.text
        formal_job_id = formal_request.json()["job_id"]
        formal_replay = await client.post(
            f"/api/v1/revisions/{revision_id}:confirm-formal-plan",
            headers={
                **owner_headers,
                "Idempotency-Key": str(formal_request_key),
                "If-Match": f'"{revision_id}"',
            },
            json={"reason": "复算后确认 FY27 完整预算方案"},
        )
        assert formal_replay.status_code == 202
        assert formal_replay.json()["job_id"] == formal_job_id
        assert (
            await handle_one(
                JobRepository(worker_id="integration-formal-confirmation-worker"),
                job_id=uuid.UUID(formal_job_id),
            )
            is True
        )
        incomplete_formal_job = await client.get(f"/api/v1/jobs/{formal_job_id}", headers=owner_headers)
        assert incomplete_formal_job.status_code == 200
        assert incomplete_formal_job.json()["status"] == "INCOMPLETE"
        assert incomplete_formal_job.json()["last_error_code"] == "ENTRY_DEPENDENCY_MISSING"

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE audit_archive_artifact SET file_name = 'tampered.zip' "
                    "WHERE audit_archive_id = :archive_id"
                ),
                {"archive_id": audit_archive_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE campus_entry_set_version SET determined_total = determined_total + 1 "
                    "WHERE campus_entry_set_version_id = :version_id"
                ),
                {"version_id": campus_entry_set_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE entry_scope_version SET record_count = record_count + 1 "
                    "WHERE entry_scope_version_id = :version_id"
                ),
                {"version_id": entry_scope_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE entry_detail_set_version SET allocated_entry_total = "
                    "allocated_entry_total + 1 WHERE entry_detail_set_version_id = :version_id"
                ),
                {"version_id": entry_detail_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE resource_finance_parameter_version SET class_size_record_count = "
                    "class_size_record_count + 1 WHERE resource_finance_parameter_version_id = :version_id"
                ),
                {"version_id": resource_version_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE planned_new_student_version SET locked_count = locked_count + 1 "
                    "WHERE planned_new_student_version_id = :version_id"
                ),
                {"version_id": unlocked_version_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE headcount_result_version SET publishable = TRUE "
                    "WHERE headcount_result_version_id = :version_id"
                ),
                {"version_id": headcount_version_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE discount_parameter_version SET detail_record_count = "
                    "detail_record_count + 1 WHERE discount_parameter_version_id = :version_id"
                ),
                {"version_id": discount_version_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE discount_parameter_confirmation SET confirmed_by = 'tampered' "
                    "WHERE discount_parameter_confirmation_id = :confirmation_id"
                ),
                {"confirmation_id": discount_confirmation_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE revenue_result_version SET publishable = TRUE "
                    "WHERE revenue_result_version_id = :version_id"
                ),
                {"version_id": revenue_version_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE target_set_version SET record_count = record_count + 1 "
                    "WHERE target_set_version_id = :version_id"
                ),
                {"version_id": target_set_version_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE budget_target SET target_revenue_yuan = target_revenue_yuan + 1 "
                    "WHERE budget_target_id = :target_id"
                ),
                {"target_id": budget_target_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE target_closure_result_version SET publishable = TRUE "
                    "WHERE target_closure_result_version_id = :version_id"
                ),
                {"version_id": target_closure_version_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE target_closure_result SET variance_yuan = variance_yuan + 1 "
                    "WHERE target_closure_result_id = :result_id"
                ),
                {"result_id": target_closure_result_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE optimization_run SET evaluated_state_count = evaluated_state_count + 1 "
                    "WHERE optimization_run_id = :run_id"
                ),
                {"run_id": optimization_run_id},
            )

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE scenario_application SET reason = 'tampered' "
                    "WHERE scenario_application_id = :application_id"
                ),
                {"application_id": scenario_application_id},
            )
