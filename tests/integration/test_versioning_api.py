import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import create_app
from packages.contracts.versioning import BudgetPublishRequest, PublicationReadinessResponse
from packages.domain.identity.models import Actor, Role
from packages.persistence import publication as publication_module
from packages.persistence.database import get_engine, get_session_factory
from packages.persistence.models import BudgetRevision
from packages.persistence.publication import (
    finalize_formal_plan_confirmation,
    publication_input_fingerprint,
    publish_budget_revision,
)

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1",
    reason="set BUDGET_RUN_DB_TESTS=1 with a migrated PostgreSQL database",
)


def _headers(department_id: str, idempotency_key: uuid.UUID | None = None) -> dict[str, str]:
    headers = {
        "X-User-ID": "data-maintainer-test",
        "X-Department-ID": department_id,
        "X-Roles": "DATA_MAINTAINER",
        "X-Request-ID": str(uuid.uuid4()),
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = str(idempotency_key)
    return headers


async def test_source_parameter_and_revision_vertical_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    department_id = f"test-department-{uuid.uuid4()}"
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        snapshot_key = uuid.uuid4()
        snapshot_payload = {
            "cutoff_at": datetime.now(UTC).isoformat(),
            "components": [
                {
                    "dataset_id": "student-roster",
                    "dataset_name": "智慧学习部学员花名册",
                    "partition_value": "batch-20260831",
                    "task_batch_id": "roster-task-1",
                    "extracted_at": datetime.now(UTC).isoformat(),
                    "row_count": 100,
                    "field_mapping_version": "v1",
                    "content_hash": "a" * 64,
                },
                {
                    "dataset_id": "indicator-summary",
                    "dataset_name": "智慧学习部指标汇总宽表",
                    "partition_value": "dtdate=2026-08-31",
                    "task_batch_id": "summary-task-1",
                    "extracted_at": datetime.now(UTC).isoformat(),
                    "row_count": 20,
                    "field_mapping_version": "v1",
                    "content_hash": "b" * 64,
                },
            ],
        }
        created = await client.post(
            "/api/v1/source-snapshots",
            headers=_headers(department_id, snapshot_key),
            json=snapshot_payload,
        )
        assert created.status_code == 201, created.text
        snapshot = created.json()
        snapshot_id = snapshot["source_snapshot_id"]
        assert snapshot["status"] == "DRAFT"

        replay = await client.post(
            "/api/v1/source-snapshots",
            headers=_headers(department_id, snapshot_key),
            json=snapshot_payload,
        )
        assert replay.status_code == 201
        assert replay.json()["source_snapshot_id"] == snapshot_id

        blocked_freeze = await client.post(
            f"/api/v1/source-snapshots/{snapshot_id}:freeze",
            headers=_headers(department_id, uuid.uuid4()),
        )
        assert blocked_freeze.status_code == 409
        assert blocked_freeze.json()["code"] == "SNAPSHOT_NOT_RECONCILED"

        mismatch_payload = {
            "checks": [
                {
                    "metric_code": "RENEWAL_RATE",
                    "left_source": "student-roster",
                    "right_source": "indicator-summary",
                    "left_numerator": "85",
                    "left_denominator": "100",
                    "right_numerator": "84",
                    "right_denominator": "100",
                    "evidence": {"schema_version": "1.0"},
                }
            ]
        }
        mismatch = await client.post(
            f"/api/v1/source-snapshots/{snapshot_id}:reconcile",
            headers=_headers(department_id, uuid.uuid4()),
            json=mismatch_payload,
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["code"] == "SOURCE_RECONCILIATION_FAILED"

        reconcile_payload = mismatch_payload
        reconcile_payload["checks"][0]["right_numerator"] = "85"
        reconciled = await client.post(
            f"/api/v1/source-snapshots/{snapshot_id}:reconcile",
            headers=_headers(department_id, uuid.uuid4()),
            json=reconcile_payload,
        )
        assert reconciled.status_code == 200, reconciled.text
        assert reconciled.json()["status"] == "RECONCILED"

        frozen = await client.post(
            f"/api/v1/source-snapshots/{snapshot_id}:freeze",
            headers=_headers(department_id, uuid.uuid4()),
        )
        assert frozen.status_code == 200, frozen.text
        assert frozen.json()["status"] == "FROZEN"

        snapshot_get = await client.get(
            f"/api/v1/source-snapshots/{snapshot_id}", headers=_headers(department_id)
        )
        assert snapshot_get.status_code == 200
        assert snapshot_get.headers["etag"] == f'"{snapshot_get.json()["content_hash"]}"'

        parameter_key = uuid.uuid4()
        component_types = [
            "RATE_PARAMETERS",
            "PLANNED_NEW_STUDENTS",
            "RESOURCE_FINANCE",
            "DISCOUNTS",
        ]
        parameter_payload = {
            "source_snapshot_id": snapshot_id,
            "components": [
                {
                    "component_type": component_type,
                    "component_version_id": str(uuid.uuid4()),
                    "content_hash": f"{index + 1}" * 64,
                    "schema_version": "1.0",
                    "record_count": 1,
                }
                for index, component_type in enumerate(component_types)
            ],
        }
        parameter_created = await client.post(
            "/api/v1/parameter-versions",
            headers=_headers(department_id, parameter_key),
            json=parameter_payload,
        )
        assert parameter_created.status_code == 201, parameter_created.text
        parameter_id = parameter_created.json()["parameter_version_id"]

        mismatched_replay_payload = {**parameter_payload, "parent_version_id": str(uuid.uuid4())}
        mismatched_replay = await client.post(
            "/api/v1/parameter-versions",
            headers=_headers(department_id, parameter_key),
            json=mismatched_replay_payload,
        )
        assert mismatched_replay.status_code == 409
        assert mismatched_replay.json()["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

        budget_created = await client.post(
            "/api/v1/annual-budgets",
            headers=_headers(department_id, uuid.uuid4()),
            json={"fiscal_year": 2027, "parameter_version_id": parameter_id},
        )
        assert budget_created.status_code == 202, budget_created.text
        budget_command = budget_created.json()
        annual_budget_id = budget_command["annual_budget_id"]
        revision_id = budget_command["new_revision_id"]

        revision_get = await client.get(f"/api/v1/revisions/{revision_id}", headers=_headers(department_id))
        assert revision_get.status_code == 200
        assert revision_get.json()["parameter_version_id"] == parameter_id

        derive_headers_one = _headers(department_id, uuid.uuid4())
        derive_headers_one["If-Match"] = f'"{revision_id}"'
        derive_headers_two = _headers(department_id, uuid.uuid4())
        derive_headers_two["If-Match"] = f'"{revision_id}"'
        first, second = await asyncio.gather(
            client.post(
                f"/api/v1/revisions/{revision_id}:derive",
                headers=derive_headers_one,
                json={"reason": "并发派生一"},
            ),
            client.post(
                f"/api/v1/revisions/{revision_id}:derive",
                headers=derive_headers_two,
                json={"reason": "并发派生二"},
            ),
        )
        assert sorted([first.status_code, second.status_code]) == [202, 409]

        revisions = await client.get(
            f"/api/v1/annual-budgets/{annual_budget_id}/revisions",
            headers=_headers(department_id),
        )
        assert revisions.status_code == 200
        assert [item["revision_no"] for item in revisions.json()] == [1, 2]
        current_revision_id = revisions.json()[-1]["budget_revision_id"]

        revision_diff = await client.get(
            f"/api/v1/revisions/{current_revision_id}/diff",
            params={"against": revision_id},
            headers=_headers(department_id),
        )
        assert revision_diff.status_code == 200, revision_diff.text
        diff_body = revision_diff.json()
        assert diff_body["relationship"] == "DIRECT_PARENT"
        assert diff_body["revision"]["is_current_draft"] is True
        assert diff_body["against"]["is_current_draft"] is False
        assert diff_body["changed_count"] == 0
        assert diff_body["unchanged_count"] >= 2
        assert len(diff_body["content_hash"]) == 64

        derived_audit = await client.get(
            "/api/v1/audit-events",
            params={
                "annual_budget_id": annual_budget_id,
                "revision_id": current_revision_id,
                "reason_contains": "并发派生",
                "limit": 1,
            },
            headers=_headers(department_id),
        )
        assert derived_audit.status_code == 200, derived_audit.text
        assert len(derived_audit.json()["items"]) == 1
        audit_item = derived_audit.json()["items"][0]
        assert audit_item["event_type"] == "BUDGET_REVISION_DERIVED"
        assert audit_item["reason"] in {"并发派生一", "并发派生二"}
        assert audit_item["budget_revision_id"] == current_revision_id

        first_audit_page = await client.get(
            "/api/v1/audit-events",
            params={"annual_budget_id": annual_budget_id, "limit": 1},
            headers=_headers(department_id),
        )
        assert first_audit_page.status_code == 200, first_audit_page.text
        first_audit_body = first_audit_page.json()
        assert len(first_audit_body["items"]) == 1
        assert first_audit_body["next_cursor"] is not None

        second_audit_page = await client.get(
            "/api/v1/audit-events",
            params={
                "annual_budget_id": annual_budget_id,
                "limit": 1,
                "cursor": first_audit_body["next_cursor"],
            },
            headers=_headers(department_id),
        )
        assert second_audit_page.status_code == 200, second_audit_page.text
        second_audit_body = second_audit_page.json()
        assert len(second_audit_body["items"]) == 1
        assert second_audit_body["items"][0]["event_id"] != first_audit_body["items"][0]["event_id"]

        hidden_audit = await client.get(
            "/api/v1/audit-events",
            params={"annual_budget_id": str(uuid.uuid4())},
            headers=_headers(department_id),
        )
        assert hidden_audit.status_code == 404

        calculation_key = uuid.uuid4()
        calculation_headers = _headers(department_id, calculation_key)
        calculation_headers["If-Match"] = f'"{current_revision_id}"'
        calculation = await client.post(
            f"/api/v1/revisions/{current_revision_id}:calculate",
            headers=calculation_headers,
            json={},
        )
        assert calculation.status_code == 202, calculation.text
        calculation_body = calculation.json()
        assert calculation_body["source_revision_id"] == current_revision_id
        assert calculation_body["calculation_pipeline_version"] == "phase1-full-calculation-v1"
        assert calculation_body["status"] == "QUEUED"
        calculation_replay = await client.post(
            f"/api/v1/revisions/{current_revision_id}:calculate",
            headers=calculation_headers,
            json={},
        )
        assert calculation_replay.status_code == 202
        assert calculation_replay.json()["job_id"] == calculation_body["job_id"]

        async def ready_publication_gate(
            _session: AsyncSession, revision: BudgetRevision
        ) -> PublicationReadinessResponse:
            return PublicationReadinessResponse(
                budget_revision_id=revision.budget_revision_id,
                input_fingerprint=publication_input_fingerprint(revision),
                gate_rule_version="phase1-publication-gate-v1",
                status="READY",
                blocker_count=0,
                checks=[],
                planned_new_students=[],
            )

        monkeypatch.setattr(publication_module, "_build_publication_readiness", ready_publication_gate)
        owner = Actor(
            user_id="budget-owner-test",
            department_id=department_id,
            roles=frozenset({Role.DEPARTMENT_BUDGET_OWNER}),
        )
        async with get_session_factory()() as session:
            formal = await finalize_formal_plan_confirmation(
                session,
                actor=owner,
                source_revision_id=uuid.UUID(current_revision_id),
                calculated_revision_id=uuid.UUID(current_revision_id),
                reason="集成测试正式方案确认",
                idempotency_key=uuid.uuid4(),
                request_id=str(uuid.uuid4()),
            )
        assert formal.source_revision_id == uuid.UUID(current_revision_id)
        confirmed_revision_id = formal.confirmed_revision_id
        assert formal.revision.new_revision_id == confirmed_revision_id

        publication_key = uuid.uuid4()
        async with get_session_factory()() as session:
            published = await publish_budget_revision(
                session,
                actor=owner,
                revision_id=confirmed_revision_id,
                expected_revision_id=confirmed_revision_id,
                payload=BudgetPublishRequest(publication_note="集成测试发布"),
                idempotency_key=publication_key,
                request_id=str(uuid.uuid4()),
            )
        async with get_session_factory()() as session:
            replayed_publication = await publish_budget_revision(
                session,
                actor=owner,
                revision_id=confirmed_revision_id,
                expected_revision_id=confirmed_revision_id,
                payload=BudgetPublishRequest(publication_note="集成测试发布"),
                idempotency_key=publication_key,
                request_id=str(uuid.uuid4()),
            )
        assert replayed_publication == published
        assert published.published_revision_id == confirmed_revision_id
        budget_after_publish = await client.get(
            f"/api/v1/annual-budgets/{annual_budget_id}", headers=_headers(department_id)
        )
        assert budget_after_publish.status_code == 200
        assert budget_after_publish.json()["current_published_revision_id"] == str(confirmed_revision_id)

    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE budget_revision SET revision_content_hash = :value "
                    "WHERE budget_revision_id = :revision_id"
                ),
                {"value": "f" * 64, "revision_id": revision_id},
            )
    async with get_engine().begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE formal_plan_confirmation SET confirmed_by = 'tampered' "
                    "WHERE formal_plan_confirmation_id = :confirmation_id"
                ),
                {"confirmation_id": formal.formal_plan_confirmation_id},
            )


async def test_source_snapshot_build_is_queued_idempotently_and_scoped() -> None:
    department_id = f"test-build-department-{uuid.uuid4()}"
    idempotency_key = uuid.uuid4()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/source-snapshots:build",
            headers=_headers(department_id, idempotency_key),
            json={"fiscal_year": 2027},
        )
        assert first.status_code == 202, first.text
        job_id = first.json()["job_id"]
        replay = await client.post(
            "/api/v1/source-snapshots:build",
            headers=_headers(department_id, idempotency_key),
            json={"fiscal_year": 2027},
        )
        assert replay.status_code == 202
        assert replay.json()["job_id"] == job_id

        status_response = await client.get(f"/api/v1/jobs/{job_id}", headers=_headers(department_id))
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "QUEUED"

        hidden = await client.get(f"/api/v1/jobs/{job_id}", headers=_headers(f"other-{uuid.uuid4()}"))
        assert hidden.status_code == 404
