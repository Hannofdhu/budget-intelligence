import uuid
from datetime import datetime

from sqlalchemy import Text, and_, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    AuditEventPageResponse,
    AuditEventResponse,
    RevisionComponentDiffResponse,
    RevisionDiffResponse,
    RevisionLifecycleEventResponse,
    RevisionResponse,
    RevisionStateResponse,
)
from packages.domain.identity.models import Actor
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.models import AnnualBudget, AuditEvent, BudgetRevision, RevisionLifecycleEvent


async def get_revision_diff(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    against_revision_id: uuid.UUID,
) -> RevisionDiffResponse:
    revision = await session.get(BudgetRevision, revision_id)
    against = await session.get(BudgetRevision, against_revision_id)
    if revision is None or against is None:
        raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
    if revision.annual_budget_id != against.annual_budget_id:
        raise DomainError(
            code="REVISION_DIFF_SCOPE_MISMATCH",
            message="只能比较同一年度预算链中的修订",
            status_code=422,
        )
    budget = await session.get(AnnualBudget, revision.annual_budget_id)
    _assert_budget_access(budget, actor)
    assert budget is not None

    lifecycle_rows = list(
        await session.scalars(
            select(RevisionLifecycleEvent)
            .where(RevisionLifecycleEvent.budget_revision_id.in_([revision_id, against_revision_id]))
            .order_by(RevisionLifecycleEvent.occurred_at, RevisionLifecycleEvent.event_type)
        )
    )
    lifecycle_by_revision: dict[uuid.UUID, list[RevisionLifecycleEvent]] = {
        revision_id: [],
        against_revision_id: [],
    }
    for event in lifecycle_rows:
        lifecycle_by_revision[event.budget_revision_id].append(event)

    revision_manifest = _comparison_manifest(revision)
    against_manifest = _comparison_manifest(against)
    components = []
    for key in sorted(set(revision_manifest) | set(against_manifest)):
        revision_value = revision_manifest.get(key)
        against_value = against_manifest.get(key)
        if key not in against_manifest:
            change_type = "ADDED"
        elif key not in revision_manifest:
            change_type = "REMOVED"
        elif revision_value != against_value:
            change_type = "CHANGED"
        else:
            change_type = "UNCHANGED"
        components.append(
            RevisionComponentDiffResponse(
                component_key=key,
                change_type=change_type,
                against_value=against_value,
                revision_value=revision_value,
            )
        )

    relationship = await _revision_relationship(
        session,
        revision=revision,
        against=against,
    )
    counts = {
        change_type: sum(item.change_type == change_type for item in components)
        for change_type in ("ADDED", "REMOVED", "CHANGED", "UNCHANGED")
    }
    revision_state = _revision_state(
        revision,
        budget,
        lifecycle_by_revision[revision_id],
    )
    against_state = _revision_state(
        against,
        budget,
        lifecycle_by_revision[against_revision_id],
    )
    response_content = {
        "schema_version": "1.0",
        "revision_id": str(revision_id),
        "against_revision_id": str(against_revision_id),
        "relationship": relationship,
        "components": [item.model_dump(mode="json") for item in components],
        "revision_lifecycle": [item.model_dump(mode="json") for item in revision_state.lifecycle_events],
        "against_lifecycle": [item.model_dump(mode="json") for item in against_state.lifecycle_events],
    }
    return RevisionDiffResponse(
        revision=revision_state,
        against=against_state,
        relationship=relationship,
        changed_count=counts["ADDED"] + counts["REMOVED"] + counts["CHANGED"],
        added_count=counts["ADDED"],
        removed_count=counts["REMOVED"],
        unchanged_count=counts["UNCHANGED"],
        components=components,
        content_hash=content_hash(response_content),
    )


async def list_audit_events(
    session: AsyncSession,
    *,
    actor: Actor,
    annual_budget_id: uuid.UUID,
    revision_id: uuid.UUID | None,
    event_type: str | None,
    object_type: str | None,
    actor_id: str | None,
    acting_role: str | list[str] | None,
    reason_contains: str | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
    limit: int,
    cursor: uuid.UUID | None,
) -> AuditEventPageResponse:
    budget = await session.get(AnnualBudget, annual_budget_id)
    _assert_budget_access(budget, actor)
    if revision_id is not None:
        revision = await session.get(BudgetRevision, revision_id)
        if revision is None or revision.annual_budget_id != annual_budget_id:
            raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)

    conditions = [AuditEvent.annual_budget_id == annual_budget_id]
    if revision_id is not None:
        conditions.append(AuditEvent.budget_revision_id == revision_id)
    if event_type:
        conditions.append(AuditEvent.event_type == event_type)
    if object_type:
        conditions.append(AuditEvent.object_type == object_type)
    if actor_id:
        conditions.append(AuditEvent.actor_id == actor_id)
    if acting_role:
        roles = [acting_role] if isinstance(acting_role, str) else acting_role
        conditions.append(AuditEvent.acting_role.in_(list(dict.fromkeys(roles))))
    if reason_contains:
        conditions.append(cast(AuditEvent.detail, Text).ilike(f"%{reason_contains}%"))
    if occurred_from is not None:
        conditions.append(AuditEvent.occurred_at >= occurred_from)
    if occurred_to is not None:
        conditions.append(AuditEvent.occurred_at <= occurred_to)
    if cursor is not None:
        anchor = await session.get(AuditEvent, cursor)
        if anchor is None or anchor.annual_budget_id != annual_budget_id:
            raise DomainError(
                code="AUDIT_CURSOR_NOT_FOUND",
                message="审计游标不存在",
                status_code=404,
            )
        conditions.append(
            or_(
                AuditEvent.occurred_at < anchor.occurred_at,
                and_(
                    AuditEvent.occurred_at == anchor.occurred_at,
                    AuditEvent.event_id < anchor.event_id,
                ),
            )
        )

    rows = list(
        await session.scalars(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.event_id.desc())
            .limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    return AuditEventPageResponse(
        items=[_audit_event_response(row) for row in page_rows],
        next_cursor=page_rows[-1].event_id if has_more else None,
    )


def _comparison_manifest(revision: BudgetRevision) -> dict[str, object]:
    return {
        key: value
        for key, value in {
            **revision.component_manifest,
            "source_snapshot_id": (str(revision.source_snapshot_id) if revision.source_snapshot_id else None),
            "parameter_version_id": (
                str(revision.parameter_version_id) if revision.parameter_version_id else None
            ),
        }.items()
        if key != "schema_version" and value is not None
    }


def _revision_response(revision: BudgetRevision) -> RevisionResponse:
    if revision.source_snapshot_id is None or revision.parameter_version_id is None:
        raise DomainError(
            code="LEGACY_REVISION_INCOMPLETE",
            message="该历史修订尚未绑定来源快照和参数版本",
            status_code=409,
        )
    return RevisionResponse(
        budget_revision_id=revision.budget_revision_id,
        annual_budget_id=revision.annual_budget_id,
        revision_no=revision.revision_no,
        parent_revision_id=revision.parent_revision_id,
        command_type=revision.command_type,
        source_snapshot_id=revision.source_snapshot_id,
        parameter_version_id=revision.parameter_version_id,
        revision_content_hash=revision.revision_content_hash,
        component_manifest=revision.component_manifest,
        created_by=revision.created_by,
        created_at=revision.created_at,
    )


def _revision_state(
    revision: BudgetRevision,
    budget: AnnualBudget,
    lifecycle_rows: list[RevisionLifecycleEvent],
) -> RevisionStateResponse:
    lifecycle_events = [_lifecycle_response(event) for event in lifecycle_rows]
    return RevisionStateResponse(
        revision=_revision_response(revision),
        is_current_draft=budget.current_draft_revision_id == revision.budget_revision_id,
        is_current_published=budget.current_published_revision_id == revision.budget_revision_id,
        is_ever_published=any(event.event_type == "PUBLISHED" for event in lifecycle_rows),
        is_formally_confirmed=(
            "formal_confirmation_id" in revision.component_manifest
            or any(event.event_type == "FORMAL_PLAN_CONFIRMED" for event in lifecycle_rows)
        ),
        lifecycle_events=lifecycle_events,
    )


def _lifecycle_response(event: RevisionLifecycleEvent) -> RevisionLifecycleEventResponse:
    return RevisionLifecycleEventResponse(
        revision_lifecycle_event_id=event.revision_lifecycle_event_id,
        budget_revision_id=event.budget_revision_id,
        event_type=event.event_type,
        actor_id=event.actor_id,
        detail=event.detail,
        occurred_at=event.occurred_at,
    )


def _audit_event_response(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        event_id=event.event_id,
        event_type=event.event_type,
        annual_budget_id=event.annual_budget_id,
        budget_revision_id=event.budget_revision_id,
        actor_id=event.actor_id,
        acting_role=event.acting_role,
        role_snapshot=event.role_snapshot,
        object_type=event.object_type,
        object_key=event.object_key,
        detail=event.detail,
        reason=_extract_reason(event.detail),
        request_id=event.request_id,
        occurred_at=event.occurred_at,
    )


def _extract_reason(detail: dict[str, object]) -> str | None:
    for key in (
        "reason",
        "change_reason",
        "manual_reason",
        "application_reason",
        "publication_note",
    ):
        value = detail.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _revision_relationship(
    session: AsyncSession,
    *,
    revision: BudgetRevision,
    against: BudgetRevision,
) -> str:
    if revision.budget_revision_id == against.budget_revision_id:
        return "SAME"
    if revision.parent_revision_id == against.budget_revision_id:
        return "DIRECT_PARENT"
    if against.parent_revision_id == revision.budget_revision_id:
        return "DIRECT_CHILD"
    rows = list(
        await session.scalars(
            select(BudgetRevision).where(BudgetRevision.annual_budget_id == revision.annual_budget_id)
        )
    )
    parent_by_id = {row.budget_revision_id: row.parent_revision_id for row in rows}
    if _is_ancestor(against.budget_revision_id, revision.budget_revision_id, parent_by_id):
        return "ANCESTOR"
    if _is_ancestor(revision.budget_revision_id, against.budget_revision_id, parent_by_id):
        return "DESCENDANT"
    return "SAME_BUDGET"


def _is_ancestor(
    candidate_id: uuid.UUID,
    revision_id: uuid.UUID,
    parent_by_id: dict[uuid.UUID, uuid.UUID | None],
) -> bool:
    visited: set[uuid.UUID] = set()
    current = parent_by_id.get(revision_id)
    while current is not None and current not in visited:
        if current == candidate_id:
            return True
        visited.add(current)
        current = parent_by_id.get(current)
    return False


def _assert_budget_access(budget: AnnualBudget | None, actor: Actor) -> None:
    if budget is None or budget.department_id != actor.department_id:
        raise DomainError(code="ANNUAL_BUDGET_NOT_FOUND", message="年度预算链不存在", status_code=404)
