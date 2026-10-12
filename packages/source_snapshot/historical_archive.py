"""Import accepted FY27 sources once; serve immutable budget data from PostgreSQL.

The read model preserves the original budget identity used by confirmed forecasts.
Excel and extraction files are needed only by the explicit administrative importer.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.database import get_session_factory
from packages.persistence.models import HistoricalBudgetArchive
from packages.source_snapshot.fy27_review import build_review
from packages.source_snapshot.source_acceptance import DECISION, business_fingerprint, read_current_review

Json = dict[str, Any]


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def json_hash(value: Json) -> str:
    """Stable across JSONB key reordering without rounding or zero filling."""
    return digest(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )


@dataclass(frozen=True)
class PreparedArchive:
    review: Json
    source_manifest: Json
    original_workbook: bytes
    original_extraction: bytes
    accepted_extraction: bytes
    acceptance_record: bytes | None

    @property
    def baseline_hash(self) -> str:
        return str(self.review["sha256"])

    @property
    def source_file_hash(self) -> str:
        return digest(self.original_workbook)

    @property
    def business_hash(self) -> str:
        return str(self.source_manifest["business_hash"])

    @property
    def content_hash(self) -> str:
        return json_hash(self.review)

    @property
    def source_manifest_hash(self) -> str:
        return json_hash(self.source_manifest)


def prepare_archive(source: Path, baseline: Path, manifest: Path) -> PreparedArchive:
    """Read accepted inputs without saving/recalculating Excel or any ledger."""
    workbook_bytes = source.read_bytes()
    baseline_bytes = baseline.read_bytes()
    acceptance_bytes = manifest.read_bytes() if manifest.exists() else None
    result = read_current_review(source, baseline, manifest)
    raw_hash = digest(workbook_bytes)
    current_path = (
        baseline if result["sha256"] == raw_hash else (baseline.parent / f"source-cells-{raw_hash}.json")
    )
    current_bytes = current_path.read_bytes()
    original = json.loads(baseline_bytes)
    current = json.loads(current_bytes)
    fingerprint = business_fingerprint(current)
    if (
        source.read_bytes() != workbook_bytes
        or baseline.read_bytes() != baseline_bytes
        or current_path.read_bytes() != current_bytes
        or (manifest.read_bytes() if manifest.exists() else None) != acceptance_bytes
        or result["source_file_sha256"] != raw_hash
        or fingerprint != business_fingerprint(original)
    ):
        raise ValueError("FY27 来源在导入准备期间变化，未写入数据库")

    def evidence(name: str, content: bytes) -> Json:
        return {"filename": name, "sha256": digest(content), "byte_count": len(content)}

    files = {
        "original_workbook": evidence(source.name, workbook_bytes),
        "original_extraction": evidence(baseline.name, baseline_bytes),
        "accepted_extraction": evidence(current_path.name, current_bytes),
    }
    if acceptance_bytes is not None:
        files["acceptance_record"] = evidence(manifest.name, acceptance_bytes)
    prepared = PreparedArchive(
        review=result,
        source_manifest={
            "schema_version": 1,
            "fiscal_year": result["fiscal_year"],
            "baseline_hash": result["sha256"],
            "source_file_hash": raw_hash,
            "business_hash": fingerprint,
            "files": files,
        },
        original_workbook=workbook_bytes,
        original_extraction=baseline_bytes,
        accepted_extraction=current_bytes,
        acceptance_record=acceptance_bytes,
    )
    verify_prepared(prepared)
    return prepared


def verify_prepared(prepared: PreparedArchive) -> None:
    """Reconcile the stored read model to exact source bytes, including provenance."""
    try:
        _verify_prepared(prepared)
    except (KeyError, TypeError, AttributeError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError("FY27 数据库档案的内容或来源清单无效") from exc


def _verify_prepared(prepared: PreparedArchive) -> None:
    manifest = prepared.source_manifest
    blobs = {
        "original_workbook": prepared.original_workbook,
        "original_extraction": prepared.original_extraction,
        "accepted_extraction": prepared.accepted_extraction,
    }
    if prepared.acceptance_record is not None:
        blobs["acceptance_record"] = prepared.acceptance_record
    if set(manifest["files"]) != set(blobs):
        raise ValueError("FY27 数据库档案的来源清单不完整")
    for role, blob in blobs.items():
        if manifest["files"][role]["sha256"] != digest(blob) or (
            manifest["files"][role]["byte_count"] != len(blob)
        ):
            raise ValueError("FY27 数据库档案的来源字节校验失败")
    original = json.loads(prepared.original_extraction)
    current = json.loads(prepared.accepted_extraction)
    fingerprint = business_fingerprint(current)
    if (
        manifest["schema_version"] != 1
        or manifest["fiscal_year"] != 2027
        or manifest["baseline_hash"] != original["sha256"]
        or manifest["source_file_hash"] != prepared.source_file_hash
        or current["sha256"] != prepared.source_file_hash
        or current["source_file"] != manifest["files"]["original_workbook"]["filename"]
        or fingerprint != business_fingerprint(original)
        or fingerprint != manifest["business_hash"]
    ):
        raise ValueError("FY27 数据库档案的预算身份或业务指纹不一致")
    expected = build_review(current)
    expected.update({"sha256": original["sha256"], "source_file_sha256": prepared.source_file_hash})
    if prepared.source_file_hash != original["sha256"]:
        if prepared.acceptance_record is None:
            raise ValueError("FY27 当前来源缺少明确接受记录")
        acceptance = json.loads(prepared.acceptance_record)
        if (
            acceptance["schema_version"] != 1
            or acceptance["decision"] != DECISION
            or not all(acceptance[key] for key in ("accepted_at", "accepted_by", "authorization_reason"))
            or acceptance["extraction_filename"] != manifest["files"]["accepted_extraction"]["filename"]
            or acceptance["baseline_sha256"] != original["sha256"]
            or acceptance["source_sha256"] != prepared.source_file_hash
            or acceptance["baseline_extraction_sha256"] != digest(prepared.original_extraction)
            or acceptance["extraction_sha256"] != digest(prepared.accepted_extraction)
            or acceptance["business_sha256"] != fingerprint
        ):
            raise ValueError("FY27 数据库档案的接受记录不匹配")
        expected.update({"business_sha256": fingerprint, "source_accepted_at": acceptance["accepted_at"]})
    if json_hash(expected) != prepared.content_hash:
        raise ValueError("FY27 数据库读取结果与来源明细不一致")


async def import_archive(
    session: AsyncSession, prepared: PreparedArchive, *, department_id: str, actor: str, reason: str
) -> tuple[uuid.UUID, bool]:
    """Insert atomically and idempotently; caller owns the commit/rollback boundary."""
    if not all(value.strip() for value in (department_id, actor, reason)):
        raise ValueError("导入必须记录部门、导入人和原因")
    verify_prepared(prepared)
    values = {
        "department_id": department_id,
        "fiscal_year": prepared.review["fiscal_year"],
        "baseline_hash": prepared.baseline_hash,
        "source_file_hash": prepared.source_file_hash,
        "business_hash": prepared.business_hash,
        "content_hash": prepared.content_hash,
        "review": prepared.review,
        "source_manifest": prepared.source_manifest,
        "source_manifest_hash": prepared.source_manifest_hash,
        "original_workbook": prepared.original_workbook,
        "original_extraction": prepared.original_extraction,
        "accepted_extraction": prepared.accepted_extraction,
        "acceptance_record": prepared.acceptance_record,
        "created_by": actor,
        "import_reason": reason,
        "created_at": datetime.now(UTC),
    }
    archive_id = await session.scalar(
        insert(HistoricalBudgetArchive)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_historical_budget_archive_source")
        .returning(HistoricalBudgetArchive.archive_id)
    )
    if archive_id is not None:
        return archive_id, True
    existing = (
        await session.execute(
            select(
                HistoricalBudgetArchive.archive_id,
                HistoricalBudgetArchive.content_hash,
                HistoricalBudgetArchive.source_manifest_hash,
            ).where(
                HistoricalBudgetArchive.department_id == department_id,
                HistoricalBudgetArchive.fiscal_year == prepared.review["fiscal_year"],
                HistoricalBudgetArchive.source_file_hash == prepared.source_file_hash,
            )
        )
    ).one()
    if existing.content_hash != prepared.content_hash or (
        existing.source_manifest_hash != prepared.source_manifest_hash
    ):
        raise ValueError("同一 FY27 来源已导入不同内容，拒绝覆盖数据库档案")
    return existing.archive_id, False


async def read_database_review(department_id: str) -> Json:
    """Runtime reads PostgreSQL only; no local workbook, extraction or file fallback."""
    async with get_session_factory()() as session:
        archive = (
            await session.execute(
                select(
                    HistoricalBudgetArchive.review,
                    HistoricalBudgetArchive.content_hash,
                    HistoricalBudgetArchive.baseline_hash,
                    HistoricalBudgetArchive.source_file_hash,
                    HistoricalBudgetArchive.business_hash,
                    HistoricalBudgetArchive.source_manifest,
                    HistoricalBudgetArchive.source_manifest_hash,
                )
                .where(
                    HistoricalBudgetArchive.department_id == department_id,
                    HistoricalBudgetArchive.fiscal_year == 2027,
                )
                .order_by(
                    HistoricalBudgetArchive.created_at.desc(), HistoricalBudgetArchive.archive_id.desc()
                )
                .limit(1)
            )
        ).one_or_none()
    if archive is None:
        raise FileNotFoundError("FY27 历史预算尚未导入数据库")
    result = cast(Json, archive.review)
    manifest = cast(Json, archive.source_manifest)
    try:
        valid = (
            json_hash(result) == archive.content_hash
            and json_hash(manifest) == archive.source_manifest_hash
            and result["fiscal_year"] == 2027
            and result["sha256"] == archive.baseline_hash
            and result["source_file_sha256"] == archive.source_file_hash
            and manifest["baseline_hash"] == archive.baseline_hash
            and manifest["source_file_hash"] == archive.source_file_hash
            and manifest["business_hash"] == archive.business_hash
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("FY27 数据库档案校验失败，未显示该数据") from exc
    if not valid:
        raise ValueError("FY27 数据库档案校验失败，未显示该数据")
    return result


async def verify_database_archive(session: AsyncSession, archive_id: uuid.UUID) -> Json:
    """Full administrative integrity check, not a heavyweight per-page query."""
    archive = await session.get(HistoricalBudgetArchive, archive_id)
    if archive is None:
        raise FileNotFoundError("FY27 数据库档案不存在")
    prepared = PreparedArchive(
        review=archive.review,
        source_manifest=archive.source_manifest,
        original_workbook=archive.original_workbook,
        original_extraction=archive.original_extraction,
        accepted_extraction=archive.accepted_extraction,
        acceptance_record=archive.acceptance_record,
    )
    verify_prepared(prepared)
    if (
        prepared.content_hash != archive.content_hash
        or prepared.source_manifest_hash != archive.source_manifest_hash
        or prepared.baseline_hash != archive.baseline_hash
        or prepared.source_file_hash != archive.source_file_hash
        or prepared.business_hash != archive.business_hash
    ):
        raise ValueError("FY27 数据库档案的元数据校验失败")
    return {
        "archive_id": str(archive_id),
        "storage": "POSTGRESQL",
        "verified": True,
        "fiscal_year": archive.fiscal_year,
        "department_id": archive.department_id,
        "baseline_hash": prepared.baseline_hash,
        "source_file_hash": prepared.source_file_hash,
        "business_hash": prepared.business_hash,
        "content_hash": prepared.content_hash,
        "scope_count": archive.review["scope_count"],
        "season_count": archive.review["season_count"],
        "annual_revenue": archive.review["annual_revenue"],
        "entry_total": archive.review["entry_total"],
    }
