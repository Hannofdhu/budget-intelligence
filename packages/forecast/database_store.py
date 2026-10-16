"""PostgreSQL-only forecast ledger, preserving the legacy canonical event protocol.

Every successful forecast freezes its complete source snapshot, assumptions and
calculation in an immutable VERSION event. Confirmation is another immutable
event. Browsing history never queries BI, opens Excel or recalculates a version.
"""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.forecast.store import ROOT, ForecastStore, ForecastStoreError, canonical, fingerprint
from packages.forecast.trend import TrendDays, project_forecast_versions, select_forecast_trend
from packages.persistence.database import get_session_factory
from packages.persistence.models import ForecastLedgerEvent

Json = dict[str, Any]


def validate_events(events: list[Json], department: str) -> None:
    """Reject chain gaps, metadata drift and independently corrupted snapshots."""
    previous_hash = None
    versions: list[Json] = []
    requests: set[str] = set()
    try:
        for number, event in enumerate(events, 1):
            if (
                event["department_id"] != department
                or type(event["event_no"]) is not int
                or event["event_no"] != number
                or event["previous_event_hash"] != previous_hash
            ):
                raise ForecastStoreError("预测版本链不完整，未使用该结果")
            if event["request_id"] in requests:
                raise ForecastStoreError("预测请求记录重复，未使用该结果")
            requests.add(event["request_id"])
            if not all(
                isinstance(event[key], str) and event[key]
                for key in ("actor", "request_id", "request_hash", "created_at")
            ):
                raise ForecastStoreError("预测记录元数据无效，未使用该结果")
            timestamp = datetime.fromisoformat(event["created_at"])
            if timestamp.tzinfo is None:
                raise ForecastStoreError("预测记录时间缺少时区")
            if event["kind"] == "VERSION":
                version = event["version"]
                current = versions[-1] if versions else None
                if (
                    version["sequence"] != len(versions) + 1
                    or version["parent_id"] != (current["id"] if current else None)
                    or (current is not None and version["baseline_hash"] != current["baseline_hash"])
                    or version["snapshot_hash"] != fingerprint(version["snapshot"])
                    or version["as_of"] != version["snapshot"]["as_of"]
                    or version["states"] != version["assumptions"]["states"]
                    or any(prior["id"] == version["id"] for prior in versions)
                ):
                    raise ForecastStoreError("预测快照或版本关系校验失败，未使用该结果")
                versions.append(version)
            elif event["kind"] == "CONFIRMATION":
                version = next((item for item in versions if item["id"] == event["version_id"]), None)
                if version is None or fingerprint(version["calculation"]) != event["calculation_hash"]:
                    raise ForecastStoreError("预测确认与计算结果不一致")
            else:
                raise ForecastStoreError("预测记录类型无效，未使用该结果")
            previous_hash = fingerprint(event)
        ForecastStore._versions(events)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ForecastStoreError):
            raise
        raise ForecastStoreError("预测记录损坏，未使用该结果") from exc


async def lock_department(session: AsyncSession, department: str) -> None:
    # Stable across processes. Serialize import/write, never depend on local file locks.
    lock_id = int.from_bytes(
        hashlib.sha256(f"forecast-ledger:{department}".encode()).digest()[:8], byteorder="big", signed=True
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})


async def read_events(session: AsyncSession, department: str) -> list[Json]:
    records = (
        await session.scalars(
            select(ForecastLedgerEvent)
            .where(ForecastLedgerEvent.department_id == department)
            .order_by(ForecastLedgerEvent.event_no)
        )
    ).all()
    events = []
    try:
        for record in records:
            event = json.loads(record.canonical_event)
            version_id = event["version"]["id"] if event["kind"] == "VERSION" else event["version_id"]
            if (
                canonical(event) != record.canonical_event
                or fingerprint(event) != record.content_hash
                or event["department_id"] != record.department_id
                or event["event_no"] != record.event_no
                or event["kind"] != record.kind
                or event["request_id"] != record.request_id
                or event["request_hash"] != record.request_hash
                or event["previous_event_hash"] != record.previous_event_hash
                or event["actor"] != record.created_by
                or datetime.fromisoformat(event["created_at"]) != record.created_at
                or version_id != record.version_id
            ):
                raise ForecastStoreError("预测记录完整性校验失败，未使用该结果")
            events.append(event)
        validate_events(events, department)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        if isinstance(exc, ForecastStoreError):
            raise
        raise ForecastStoreError("预测数据库记录损坏，未使用该结果") from exc
    return events


def event_record(event: Json, *, origin: str, persisted_by: str, reason: str) -> ForecastLedgerEvent:
    return ForecastLedgerEvent(
        department_id=event["department_id"],
        event_no=event["event_no"],
        kind=event["kind"],
        version_id=event["version"]["id"] if event["kind"] == "VERSION" else event["version_id"],
        request_id=event["request_id"],
        request_hash=event["request_hash"],
        content_hash=fingerprint(event),
        previous_event_hash=event["previous_event_hash"],
        canonical_event=canonical(event),
        created_by=event["actor"],
        created_at=datetime.fromisoformat(event["created_at"]),
        origin=origin,
        persisted_by=persisted_by,
        persistence_reason=reason,
        persisted_at=datetime.now(UTC),
    )


class PostgreSQLForecastStore:
    _versions = staticmethod(ForecastStore._versions)

    @staticmethod
    def _view(events: list[Json]) -> Json:
        # This helper is pure; constructing the legacy adapter does not touch files.
        return ForecastStore()._view(events)

    @staticmethod
    def _dedupe(events: list[Json], request_id: str, request_hash: str) -> Json | None:
        return ForecastStore()._dedupe(events, request_id, request_hash)

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self.session_factory = session_factory
        self._trend_cache: dict[tuple[str, int], tuple[tuple[int, str] | None, str, list[Json]]] = {}

    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        return self.session_factory or get_session_factory()

    async def current(self, department: str) -> Json:
        async with self._sessions()() as session:
            return self._view(await read_events(session, department))

    async def versions(self, department: str) -> list[Json]:
        async with self._sessions()() as session:
            return list(reversed(self._versions(await read_events(session, department))))

    async def trend(
        self,
        department: str,
        *,
        baseline_hash: str,
        days: TrendDays = "30",
        through_version: str | None = None,
    ) -> Json:
        """Cache only validated compact projections, keyed to the immutable chain head.

        Every request checks the database head (including confirmation events).
        Raw BI rows never remain in this bounded cache. A changed chain is fully
        verified before replacing the entry; a failed cold read never serves old
        data under a new token. The database rejects ledger UPDATE and DELETE.
        """
        factory = self._sessions()
        key = department, id(factory)
        async with factory() as session:
            head = (
                await session.execute(
                    select(ForecastLedgerEvent.event_no, ForecastLedgerEvent.content_hash)
                    .where(ForecastLedgerEvent.department_id == department)
                    .order_by(ForecastLedgerEvent.event_no.desc())
                    .limit(1)
                )
            ).one_or_none()
            token = (head.event_no, head.content_hash) if head else None
            cached = self._trend_cache.get(key)
            if cached is not None and cached[0] == token and cached[1] == baseline_hash:
                projected = cached[2]
            else:
                events = await read_events(session, department)
                projected = project_forecast_versions(self._versions(events), baseline_hash=baseline_hash)
                verified_token = (events[-1]["event_no"], fingerprint(events[-1])) if events else None
                if key not in self._trend_cache and len(self._trend_cache) >= 8:
                    self._trend_cache.pop(next(iter(self._trend_cache)))
                self._trend_cache[key] = verified_token, baseline_hash, projected
        return deepcopy(select_forecast_trend(projected, days=days, through_version=through_version))

    async def version(self, department: str, version_id: str) -> Json | None:
        async with self._sessions()() as session:
            events = await read_events(session, department)
            view = self._view(events)
            selected = next((item for item in self._versions(events) if item["id"] == version_id), None)
            if selected is None:
                return None
            return {
                **view,
                "baseline_hash": selected["baseline_hash"],
                "version": selected,
                "latest_version_id": view["version"]["id"] if view["version"] else None,
            }

    async def request_result(self, department: str, request_id: str, request_hash: str) -> Json | None:
        async with self._sessions()() as session:
            return self._dedupe(await read_events(session, department), request_id, request_hash)

    async def append_version(
        self,
        *,
        department: str,
        actor: str,
        request_id: str,
        request_hash: str,
        parent_id: str | None,
        baseline_hash: str,
        snapshot: Json,
        assumptions: Json,
        calculation: Json,
        reason: str,
    ) -> Json:
        async with self._sessions()() as session, session.begin():
            await lock_department(session, department)
            events = await read_events(session, department)
            prior = self._dedupe(events, request_id, request_hash)
            if prior is not None:
                return prior
            view = self._view(events)
            current = view["version"]
            if (current["id"] if current else None) != parent_id:
                raise ForecastStoreError("预测版本已变化，请重新读取后操作")
            if current and current["baseline_hash"] != baseline_hash:
                raise ForecastStoreError("定版预算已变化，不能沿用原预测链")
            version = {
                "id": str(uuid4()),
                "sequence": len(view["history"]) + 1,
                "parent_id": parent_id,
                "baseline_hash": baseline_hash,
                "baseline_kind": "APPROVED_FY27_HISTORICAL_ARCHIVE",
                "as_of": snapshot["as_of"],
                "created_at": datetime.now(UTC).isoformat(),
                "created_by": actor,
                "reason": reason,
                "states": assumptions["states"],
                "assumptions": assumptions,
                "snapshot": snapshot,
                "snapshot_hash": fingerprint(snapshot),
                "calculation": calculation,
            }
            await self._append(
                session,
                events,
                department,
                actor,
                request_id,
                request_hash,
                {"kind": "VERSION", "version": version},
                reason,
            )
            return self._view(events)

    async def confirm(
        self,
        *,
        department: str,
        actor: str,
        version_id: str,
        request_id: str,
        request_hash: str,
        reason: str,
    ) -> Json:
        async with self._sessions()() as session, session.begin():
            await lock_department(session, department)
            events = await read_events(session, department)
            prior = self._dedupe(events, request_id, request_hash)
            if prior is not None:
                return prior
            view = self._view(events)
            version = view["version"]
            if not version or version["id"] != version_id:
                raise ForecastStoreError("只能确认当前预测版本")
            if version["assumptions"].get("trial_mode"):
                raise ForecastStoreError("试用预测不能正式确认；需核实试用假设后生成正式预测")
            if not version["calculation"].get("ready") or any(
                issue.get("severity") == "BLOCKING" for issue in version["calculation"].get("issues", [])
            ):
                raise ForecastStoreError("尚有数据阻断，不能确认预测")
            if version["status"] == "CONFIRMED":
                return view
            await self._append(
                session,
                events,
                department,
                actor,
                request_id,
                request_hash,
                {
                    "kind": "CONFIRMATION",
                    "version_id": version_id,
                    "calculation_hash": fingerprint(version["calculation"]),
                    "reason": reason,
                },
                reason,
            )
            return self._view(events)

    async def _append(
        self,
        session: AsyncSession,
        events: list[Json],
        department: str,
        actor: str,
        request_id: str,
        request_hash: str,
        payload: Json,
        reason: str,
    ) -> None:
        event = {
            **payload,
            "event_no": len(events) + 1,
            "department_id": department,
            "actor": actor,
            "request_id": request_id,
            "request_hash": request_hash,
            "created_at": datetime.now(UTC).isoformat(),
            "previous_event_hash": fingerprint(events[-1]) if events else None,
        }
        next_events = [*events, event]
        validate_events(next_events, department)
        session.add(event_record(event, origin="RUNTIME", persisted_by=actor, reason=reason))
        await session.flush()
        # Require a byte-for-byte validated readback before the transaction can commit.
        stored = await read_events(session, department)
        if stored != json.loads(canonical(next_events)):
            raise ForecastStoreError("预测数据库保存校验失败，未生成新版本")
        # Responses must equal later idempotent replays, including decimal strings.
        events[:] = stored


@dataclass(frozen=True)
class PreparedForecastLedger:
    department: str
    events: list[Json]

    @property
    def chain_hash(self) -> str | None:
        return fingerprint(self.events[-1]) if self.events else None

    @property
    def version_count(self) -> int:
        return len(ForecastStore._versions(self.events))


def prepare_ledger(department: str, root: Path = ROOT) -> PreparedForecastLedger:
    """Explicit administrative import only; no runtime dependency on source files."""
    if not department.strip() or not root.is_dir():
        raise ForecastStoreError("预测导入部门或历史目录无效")
    legacy = ForecastStore(root)
    with legacy._lock():
        events = legacy._events(department)
    validate_events(events, department)
    return PreparedForecastLedger(department, events)


async def import_ledger(
    session: AsyncSession, prepared: PreparedForecastLedger, *, actor: str, reason: str
) -> Json:
    """Caller owns commit; identical existing prefixes permit safe repeated import."""
    if not all(value.strip() for value in (prepared.department, actor, reason)):
        raise ForecastStoreError("导入必须记录部门、导入人和原因")
    validate_events(prepared.events, prepared.department)
    await lock_department(session, prepared.department)
    existing = await read_events(session, prepared.department)
    for prior, imported in zip(existing, prepared.events, strict=False):
        if canonical(prior) != canonical(imported):
            raise ForecastStoreError("数据库与历史预测记录冲突，未覆盖任何版本")
    created = 0
    for event in prepared.events[len(existing) :]:
        session.add(event_record(event, origin="FILE_IMPORT", persisted_by=actor, reason=reason))
        created += 1
    await session.flush()
    stored = await read_events(session, prepared.department)
    if [canonical(event) for event in stored[: len(prepared.events)]] != [
        canonical(event) for event in prepared.events
    ]:
        raise ForecastStoreError("预测迁移后校验失败，未提交数据库")
    versions = ForecastStore._versions(stored)
    return {
        "verified": True,
        "created_events": created,
        "event_count": len(stored),
        "version_count": len(versions),
        "imported_chain_hash": prepared.chain_hash,
        "database_chain_hash": fingerprint(stored[-1]) if stored else None,
        "latest_version_id": versions[-1]["id"] if versions else None,
        "latest_status": versions[-1]["status"] if versions else None,
    }
