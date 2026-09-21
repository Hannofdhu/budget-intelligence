import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import FromClause

from packages.config.settings import Settings
from packages.contracts.versioning import (
    AuditArchiveArtifactResponse,
    AuditArchiveExportRequest,
    AuditArchiveExportResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    AnnualBudget,
    AsyncJob,
    AuditArchiveArtifact,
    AuditEvent,
    Base,
    BudgetRevision,
    RevisionLifecycleEvent,
)
from packages.persistence.revision_audit import _assert_budget_access
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _store_receipt,
)

AUDIT_ARCHIVE_JOB_TYPE = "EXPORT_AUDIT_ARCHIVE"
AUDIT_ARCHIVE_SCHEMA_VERSION = "phase1-audit-archive-v1"
ARCHIVE_MEDIA_TYPE = "application/zip"

_EXCLUDED_COMPONENT_TABLES = {
    "annual_budget",
    "async_job",
    "audit_archive_artifact",
    "audit_event",
    "budget_revision",
    "command_receipt",
    "revision_lifecycle_event",
    "role_membership",
}


async def enqueue_audit_archive_export(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    payload: AuditArchiveExportRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> AuditArchiveExportResponse:
    command_type = "ENQUEUE_AUDIT_ARCHIVE_EXPORT"
    request_hash = content_hash({"revision_id": str(revision_id), **payload.model_dump(mode="json")})
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return AuditArchiveExportResponse.model_validate(replay)

        revision = await session.get(BudgetRevision, revision_id)
        if revision is None:
            raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
        budget = await session.get(AnnualBudget, revision.annual_budget_id)
        _assert_budget_access(budget, actor)
        assert budget is not None

        requested_at = _now()
        job_id = uuid7()
        archive_id = uuid7()
        job = AsyncJob(
            job_id=job_id,
            job_type=AUDIT_ARCHIVE_JOB_TYPE,
            dedupe_key=f"audit-archive:{job_id}",
            payload={
                "schema_version": "1.0",
                "archive_schema_version": payload.archive_schema_version,
                "audit_archive_id": str(archive_id),
                "budget_revision_id": str(revision_id),
                "annual_budget_id": str(budget.annual_budget_id),
                "department_id": actor.department_id,
                "actor_id": actor.user_id,
                "roles": sorted(role.value for role in actor.roles),
                "reason": payload.reason.strip(),
                "audit_cutoff_at": requested_at.isoformat(),
                "request_id": request_id,
            },
            result=None,
            status="QUEUED",
            priority=80,
            attempt_no=0,
            available_at=requested_at,
            locked_by=None,
            heartbeat_at=None,
            timeout_at=None,
            last_error_code=None,
            last_error_safe=None,
            created_at=requested_at,
            finished_at=None,
        )
        session.add(job)
        response = AuditArchiveExportResponse(
            job_id=job_id,
            budget_revision_id=revision_id,
            audit_cutoff_at=requested_at,
            archive_schema_version=payload.archive_schema_version,
            status="QUEUED",
            links={"job": f"/api/v1/jobs/{job_id}"},
        )
        _add_audit(
            session,
            event_type="AUDIT_ARCHIVE_QUEUED",
            actor=actor,
            acting_role=_archive_role(actor),
            object_type="ASYNC_JOB",
            object_key={"job_id": str(job_id), "audit_archive_id": str(archive_id)},
            detail={
                "budget_revision_id": str(revision_id),
                "archive_schema_version": payload.archive_schema_version,
                "audit_cutoff_at": requested_at.isoformat(),
                "reason": payload.reason.strip(),
            },
            request_id=request_id,
            occurred_at=requested_at,
            annual_budget_id=budget.annual_budget_id,
            budget_revision_id=revision_id,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response.model_dump(mode="json"),
            occurred_at=requested_at,
        )
    return response


async def build_audit_archive(
    session: AsyncSession,
    *,
    settings: Settings,
    job_id: uuid.UUID,
    payload: dict[str, object],
) -> dict[str, object]:
    actor, revision_id, archive_id, cutoff_at, request_id, reason = _parse_job_payload(payload)
    existing = await session.scalar(select(AuditArchiveArtifact).where(AuditArchiveArtifact.job_id == job_id))
    if existing is not None:
        return _job_result(existing)

    revision = await session.get(BudgetRevision, revision_id)
    if revision is None:
        raise DomainError(code="REVISION_NOT_FOUND", message="预算修订不存在", status_code=404)
    budget = await session.get(AnnualBudget, revision.annual_budget_id)
    _assert_budget_access(budget, actor)
    assert budget is not None

    files = await _archive_files(
        session,
        budget=budget,
        revision=revision,
        archive_id=archive_id,
        job_id=job_id,
        cutoff_at=cutoff_at,
        requested_by=actor.user_id,
        reason=reason,
    )
    archive_bytes, manifest_hash = _build_zip(files)
    if len(archive_bytes) > settings.audit_archive_max_bytes:
        raise DomainError(
            code="AUDIT_ARCHIVE_TOO_LARGE",
            message="审计归档包超过系统配置上限",
            status_code=422,
            details={
                "size_bytes": len(archive_bytes),
                "max_bytes": settings.audit_archive_max_bytes,
            },
        )
    archive_hash = hashlib.sha256(archive_bytes).hexdigest()
    created_at = _now()
    file_name = f"budget-audit-FY{budget.fiscal_year}-R{revision.revision_no}-{str(archive_id)[:8]}.zip"
    artifact = AuditArchiveArtifact(
        audit_archive_id=archive_id,
        job_id=job_id,
        annual_budget_id=budget.annual_budget_id,
        budget_revision_id=revision_id,
        audit_cutoff_at=cutoff_at,
        schema_version=AUDIT_ARCHIVE_SCHEMA_VERSION,
        file_name=file_name,
        media_type=ARCHIVE_MEDIA_TYPE,
        artifact_bytes=archive_bytes,
        size_bytes=len(archive_bytes),
        manifest_hash=manifest_hash,
        content_hash=archive_hash,
        created_by=actor.user_id,
        created_at=created_at,
    )
    session.add(artifact)
    _add_audit(
        session,
        event_type="AUDIT_ARCHIVE_CREATED",
        actor=actor,
        acting_role=_archive_role(actor),
        object_type="AUDIT_ARCHIVE",
        object_key={"audit_archive_id": str(archive_id)},
        detail={
            "job_id": str(job_id),
            "budget_revision_id": str(revision_id),
            "audit_cutoff_at": cutoff_at.isoformat(),
            "file_name": file_name,
            "size_bytes": len(archive_bytes),
            "manifest_hash": manifest_hash,
            "content_hash": archive_hash,
            "reason": reason,
        },
        request_id=request_id,
        occurred_at=created_at,
        annual_budget_id=budget.annual_budget_id,
        budget_revision_id=revision_id,
    )
    await session.commit()
    return _job_result(artifact)


async def get_audit_archive(
    session: AsyncSession,
    *,
    actor: Actor,
    audit_archive_id: uuid.UUID,
) -> AuditArchiveArtifactResponse:
    artifact = await session.get(AuditArchiveArtifact, audit_archive_id)
    if artifact is None:
        raise DomainError(code="AUDIT_ARCHIVE_NOT_FOUND", message="审计归档包不存在", status_code=404)
    budget = await session.get(AnnualBudget, artifact.annual_budget_id)
    _assert_budget_access(budget, actor)
    return _artifact_response(artifact)


async def get_audit_archive_bytes(
    session: AsyncSession,
    *,
    actor: Actor,
    audit_archive_id: uuid.UUID,
) -> tuple[AuditArchiveArtifactResponse, bytes]:
    artifact = await session.get(AuditArchiveArtifact, audit_archive_id)
    if artifact is None:
        raise DomainError(code="AUDIT_ARCHIVE_NOT_FOUND", message="审计归档包不存在", status_code=404)
    budget = await session.get(AnnualBudget, artifact.annual_budget_id)
    _assert_budget_access(budget, actor)
    return _artifact_response(artifact), artifact.artifact_bytes


async def _archive_files(
    session: AsyncSession,
    *,
    budget: AnnualBudget,
    revision: BudgetRevision,
    archive_id: uuid.UUID,
    job_id: uuid.UUID,
    cutoff_at: datetime,
    requested_by: str,
    reason: str,
) -> dict[str, bytes]:
    component_files: dict[str, bytes] = {}
    component_index = []
    for component_key, raw_value in sorted(revision.component_manifest.items()):
        try:
            component_id = uuid.UUID(str(raw_value))
        except (TypeError, ValueError):
            continue
        tables = await _rows_for_component(session, component_key, component_id)
        if not tables:
            continue
        category = "results" if "result_version_id" in component_key else "parameters"
        path = f"{category}/{component_key}.json"
        component_files[path] = _json_bytes(
            {
                "schema_version": "1.0",
                "component_key": component_key,
                "component_version_id": str(component_id),
                "tables": tables,
            }
        )
        component_index.append(
            {
                "component_key": component_key,
                "component_version_id": str(component_id),
                "category": category,
                "path": path,
                "record_count": sum(len(rows) for rows in tables.values()),
            }
        )

    source_tables: dict[str, list[dict[str, object]]] = {}
    if revision.source_snapshot_id is not None:
        source_tables = await _rows_for_component(session, "source_snapshot_id", revision.source_snapshot_id)

    revision_chain = list(
        (
            await session.execute(
                select(BudgetRevision.__table__)
                .where(
                    BudgetRevision.annual_budget_id == budget.annual_budget_id,
                    BudgetRevision.created_at <= cutoff_at,
                )
                .order_by(BudgetRevision.revision_no)
            )
        ).mappings()
    )
    lifecycle = list(
        (
            await session.execute(
                select(RevisionLifecycleEvent.__table__)
                .where(
                    RevisionLifecycleEvent.budget_revision_id == revision.budget_revision_id,
                    RevisionLifecycleEvent.occurred_at <= cutoff_at,
                )
                .order_by(
                    RevisionLifecycleEvent.occurred_at,
                    RevisionLifecycleEvent.revision_lifecycle_event_id,
                )
            )
        ).mappings()
    )
    audit_events = list(
        (
            await session.execute(
                select(AuditEvent.__table__)
                .where(
                    AuditEvent.annual_budget_id == budget.annual_budget_id,
                    AuditEvent.occurred_at <= cutoff_at,
                )
                .order_by(AuditEvent.occurred_at, AuditEvent.event_id)
            )
        ).mappings()
    )
    files = {
        "revision/annual_budget.json": _json_bytes(_row_dict(budget.__table__, budget)),
        "revision/target_revision.json": _json_bytes(_row_dict(revision.__table__, revision)),
        "revision/revision_chain.json": _json_bytes([_json_value(dict(row)) for row in revision_chain]),
        "source/source_snapshot.json": _json_bytes(source_tables),
        "audit/lifecycle.json": _json_bytes([_json_value(dict(row)) for row in lifecycle]),
        "audit/events.json": _json_bytes([_json_value(dict(row)) for row in audit_events]),
        **component_files,
    }
    manifest = {
        "schema_version": AUDIT_ARCHIVE_SCHEMA_VERSION,
        "audit_archive_id": str(archive_id),
        "job_id": str(job_id),
        "annual_budget_id": str(budget.annual_budget_id),
        "budget_revision_id": str(revision.budget_revision_id),
        "fiscal_year": budget.fiscal_year,
        "department_id": budget.department_id,
        "revision_no": revision.revision_no,
        "revision_content_hash": revision.revision_content_hash,
        "audit_cutoff_at": cutoff_at.isoformat(),
        "requested_by": requested_by,
        "reason": reason,
        "audit_scope": "ANNUAL_BUDGET_THROUGH_CUTOFF",
        "components": component_index,
        "files": sorted(files),
    }
    files["manifest.json"] = _json_bytes(manifest)
    return files


async def _rows_for_component(
    session: AsyncSession,
    component_key: str,
    component_id: uuid.UUID,
) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        if table.name in _EXCLUDED_COMPONENT_TABLES or component_key not in table.c:
            continue
        rows = list(
            (
                await session.execute(
                    select(table)
                    .where(table.c[component_key] == component_id)
                    .order_by(*table.primary_key.columns)
                )
            ).mappings()
        )
        if rows:
            result[table.name] = [_json_value(dict(row)) for row in rows]
    return result


def _build_zip(files: dict[str, bytes]) -> tuple[bytes, str]:
    manifest_hash = hashlib.sha256(files["manifest.json"]).hexdigest()
    checksums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {path}\n" for path, content in sorted(files.items())
    ).encode()
    complete_files = {**files, "checksums.sha256": checksums}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path, content in sorted(complete_files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, content)
    return buffer.getvalue(), manifest_hash


def _json_bytes(value: object) -> bytes:
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _json_value(value: object) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _row_dict(table: FromClause, model: object) -> dict[str, object]:
    value = _json_value({column.name: getattr(model, column.name) for column in table.columns})
    return cast(dict[str, object], value)


def _parse_job_payload(
    payload: dict[str, object],
) -> tuple[Actor, uuid.UUID, uuid.UUID, datetime, str, str]:
    try:
        raw_roles = payload["roles"]
        if not isinstance(raw_roles, list):
            raise TypeError
        actor = Actor(
            user_id=str(payload["actor_id"]),
            department_id=str(payload["department_id"]),
            roles=frozenset(Role(str(value)) for value in raw_roles),
        )
        revision_id = uuid.UUID(str(payload["budget_revision_id"]))
        archive_id = uuid.UUID(str(payload["audit_archive_id"]))
        cutoff_at = datetime.fromisoformat(str(payload["audit_cutoff_at"]))
        request_id = str(payload["request_id"])
        reason = str(payload["reason"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError(
            code="JOB_PAYLOAD_INVALID", message="审计归档作业载荷无效", status_code=422
        ) from exc
    if not (actor.has_role(Role.DATA_MAINTAINER) or actor.has_role(Role.DEPARTMENT_BUDGET_OWNER)):
        raise DomainError(code="JOB_ROLE_INVALID", message="审计归档作业角色无效", status_code=403)
    return actor, revision_id, archive_id, cutoff_at, request_id, reason


def _archive_role(actor: Actor) -> Role:
    if actor.has_role(Role.DEPARTMENT_BUDGET_OWNER):
        return Role.DEPARTMENT_BUDGET_OWNER
    return Role.DATA_MAINTAINER


def _artifact_response(artifact: AuditArchiveArtifact) -> AuditArchiveArtifactResponse:
    return AuditArchiveArtifactResponse(
        audit_archive_id=artifact.audit_archive_id,
        job_id=artifact.job_id,
        annual_budget_id=artifact.annual_budget_id,
        budget_revision_id=artifact.budget_revision_id,
        audit_cutoff_at=artifact.audit_cutoff_at,
        schema_version=artifact.schema_version,
        file_name=artifact.file_name,
        media_type=artifact.media_type,
        size_bytes=artifact.size_bytes,
        manifest_hash=artifact.manifest_hash,
        content_hash=artifact.content_hash,
        created_by=artifact.created_by,
        created_at=artifact.created_at,
        links={"download": f"/api/v1/audit-archives/{artifact.audit_archive_id}/download"},
    )


def _job_result(artifact: AuditArchiveArtifact) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "SUCCEEDED",
        "audit_archive_id": str(artifact.audit_archive_id),
        "budget_revision_id": str(artifact.budget_revision_id),
        "file_name": artifact.file_name,
        "size_bytes": artifact.size_bytes,
        "manifest_hash": artifact.manifest_hash,
        "content_hash": artifact.content_hash,
        "links": {
            "metadata": f"/api/v1/audit-archives/{artifact.audit_archive_id}",
            "download": f"/api/v1/audit-archives/{artifact.audit_archive_id}/download",
        },
    }
