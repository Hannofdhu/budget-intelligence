"""Audit filter contracts use dependency fakes only; no live database or writes."""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from apps.api.auth import current_actor
from apps.api.routes import versioning
from packages.contracts.versioning import AuditEventPageResponse
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.persistence import revision_audit
from packages.persistence.database import session_scope
from packages.persistence.models import AnnualBudget, AuditEvent, BudgetRevision

OWNER = Actor("reviewer", "example-department", frozenset({Role.DEPARTMENT_BUDGET_OWNER}))


@pytest.mark.parametrize(
    "roles,expected",
    [
        ([], None),
        (["DATA_MAINTAINER"], ["DATA_MAINTAINER"]),
        (
            ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"],
            ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"],
        ),
    ],
)
def test_audit_query_accepts_repeated_roles_without_changing_other_filters(
    monkeypatch: pytest.MonkeyPatch, roles: list[str], expected: list[str] | None
) -> None:
    calls: list[dict[str, Any]] = []

    async def listing(_session: AsyncSession, **kwargs: Any) -> AuditEventPageResponse:
        calls.append(kwargs)
        return AuditEventPageResponse(items=[], next_cursor=None)

    app = FastAPI()
    app.include_router(versioning.router, prefix="/api/v1")
    app.dependency_overrides[session_scope] = lambda: object()
    app.dependency_overrides[current_actor] = lambda: OWNER
    monkeypatch.setattr(versioning, "list_audit_events", listing)
    budget_id, revision_id, cursor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    params = [
        ("annual_budget_id", str(budget_id)),
        ("revision_id", str(revision_id)),
        ("cursor", str(cursor)),
        ("limit", "50"),
        ("event_type", "PUBLISHED"),
        ("reason_contains", "校验"),
        *(("acting_role", role) for role in roles),
    ]
    with TestClient(app) as client:
        response = client.get("/api/v1/audit-events", params=params)
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["acting_role"] == expected
    assert calls[0]["actor"] == OWNER
    assert calls[0]["annual_budget_id"] == budget_id
    assert calls[0]["revision_id"] == revision_id
    assert calls[0]["cursor"] == cursor
    assert calls[0]["limit"] == 50
    assert calls[0]["event_type"] == "PUBLISHED"
    assert calls[0]["reason_contains"] == "校验"


@pytest.mark.parametrize("roles", [["x" * 61], ["DATA_MAINTAINER"] * 21])
def test_audit_role_query_keeps_bounded_inputs(roles: list[str]) -> None:
    app = FastAPI()
    app.include_router(versioning.router, prefix="/api/v1")
    app.dependency_overrides[session_scope] = lambda: object()
    app.dependency_overrides[current_actor] = lambda: OWNER
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/audit-events",
            params=[
                ("annual_budget_id", str(uuid.uuid4())),
                *(("acting_role", role) for role in roles),
            ],
        )
    assert response.status_code == 422


class AuditQuerySession:
    def __init__(self, *, department: str = OWNER.department_id) -> None:
        self.budget_id = uuid.uuid4()
        self.revision_id = uuid.uuid4()
        self.anchor_id = uuid.uuid4()
        self.budget = AnnualBudget(annual_budget_id=self.budget_id, department_id=department)
        self.revision = BudgetRevision(budget_revision_id=self.revision_id, annual_budget_id=self.budget_id)
        self.anchor = AuditEvent(
            event_id=self.anchor_id,
            annual_budget_id=self.budget_id,
            occurred_at=datetime(2026, 10, 14, tzinfo=UTC),
        )
        self.statement: Select[tuple[AuditEvent]] | None = None
        self.rows = [
            AuditEvent(
                event_id=uuid.uuid4(),
                event_type="PUBLISHED",
                annual_budget_id=self.budget_id,
                budget_revision_id=self.revision_id,
                actor_id="example-user",
                acting_role="DATA_MAINTAINER",
                role_snapshot={"roles": ["DATA_MAINTAINER"]},
                object_type="BUDGET",
                object_key={},
                detail={},
                request_id=str(uuid.uuid4()),
                occurred_at=datetime(2026, 10, 13, tzinfo=UTC),
            )
            for _ in range(3)
        ]

    async def get(self, model: type[object], key: uuid.UUID) -> object | None:
        records: dict[tuple[type[object], uuid.UUID], object] = {
            (AnnualBudget, self.budget_id): self.budget,
            (BudgetRevision, self.revision_id): self.revision,
            (AuditEvent, self.anchor_id): self.anchor,
        }
        return records.get((model, key))

    async def scalars(self, statement: Select[tuple[AuditEvent]]) -> list[AuditEvent]:
        self.statement = statement
        return self.rows


async def query(session: AuditQuerySession, roles: str | list[str] | None) -> AuditEventPageResponse:
    return await revision_audit.list_audit_events(
        cast(AsyncSession, session),
        actor=OWNER,
        annual_budget_id=session.budget_id,
        revision_id=session.revision_id,
        event_type="PUBLISHED",
        object_type="BUDGET",
        actor_id="example-user",
        acting_role=roles,
        reason_contains="reason",
        occurred_from=datetime(2026, 10, 1, tzinfo=UTC),
        occurred_to=datetime(2026, 10, 31, tzinfo=UTC),
        limit=2,
        cursor=session.anchor_id,
    )


@pytest.mark.parametrize(
    "roles,expected",
    [
        ("DATA_MAINTAINER", ["DATA_MAINTAINER"]),
        (
            ["DATA_MAINTAINER", "PERMISSION_ADMIN", "DATA_MAINTAINER"],
            ["DATA_MAINTAINER", "PERMISSION_ADMIN"],
        ),
        (None, None),
    ],
)
async def test_role_or_filter_is_in_database_query_before_limit_and_preserves_paging(
    roles: str | list[str] | None, expected: list[str] | None
) -> None:
    session = AuditQuerySession()
    result = await query(session, roles)
    assert session.statement is not None
    compiled = session.statement.compile()
    sql = str(compiled)
    assert "audit_event.annual_budget_id =" in sql
    assert "audit_event.budget_revision_id =" in sql
    assert "audit_event.actor_id =" in sql
    assert "audit_event.event_type =" in sql
    assert "audit_event.object_type =" in sql
    assert "audit_event.occurred_at >=" in sql
    assert "audit_event.occurred_at <=" in sql
    assert "audit_event.event_id <" in sql
    assert "ORDER BY audit_event.occurred_at DESC, audit_event.event_id DESC" in sql
    if expected is not None:
        assert "audit_event.acting_role IN" in sql
        assert compiled.params["acting_role_1"] == expected
        assert sql.index("audit_event.acting_role IN") < sql.index("LIMIT")
    else:
        assert "acting_role_1" not in compiled.params
    assert list(compiled.params.values()).count(3) == 1  # limit + one, not a loaded-page filter
    assert len(result.items) == 2
    assert result.next_cursor == session.rows[1].event_id


@pytest.mark.parametrize("mismatch", ["department", "revision", "cursor"])
async def test_multiselect_cannot_bypass_budget_revision_or_cursor_scope(mismatch: str) -> None:
    session = AuditQuerySession()
    if mismatch == "department":
        session.budget.department_id = "other-department"
    elif mismatch == "revision":
        session.revision.annual_budget_id = uuid.uuid4()
    else:
        session.anchor.annual_budget_id = uuid.uuid4()
    with pytest.raises(DomainError) as error:
        await query(session, ["DATA_MAINTAINER", "PERMISSION_ADMIN"])
    assert error.value.status_code == 404
    assert session.statement is None
