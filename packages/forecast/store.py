"""Local-only append-only forecast ledger, separate from annual-budget persistence.

Every event contains its input snapshot, result and a content hash. A file lock
serializes compare-and-append; exclusive creation prevents overwriting a version.
This desktop MVP ledger is not the intranet deployment's database adapter.
"""

import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2] / "output" / "forecast-ledger"
Json = dict[str, Any]


class ForecastStoreError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_number,
    ).encode("utf-8")


def _json_number(value: Any) -> str:
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    raise TypeError("预测记录含不可序列化的值")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _recorded_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def forecast_generation(version: Json) -> str | None:
    """Classify a saved version's author, never its possibly reused source alone.

    Older records without a trustworthy author or scheduler provenance remain
    unknown. This is a read projection and does not change ledger contents.
    """
    actor = version.get("created_by")
    if not isinstance(actor, str) or not actor.strip():
        return None
    if actor != "forecast-daily-scheduler":
        return "MANUAL"
    snapshot = version.get("snapshot")
    provenance = snapshot.get("provenance") if isinstance(snapshot, dict) else None
    if isinstance(provenance, dict) and provenance.get("refresh_mode") == "AUTOMATIC":
        return "AUTOMATIC"
    return None


def forecast_version_summary(version: Json) -> Json:
    """Expose only saved presentation metadata, not source rows or calculations."""
    confirmation = version.get("confirmation")
    return {
        **{key: version[key] for key in ("id", "sequence", "status", "as_of", "created_at")},
        "reason": _recorded_text(version.get("reason")),
        "created_by": _recorded_text(version.get("created_by")),
        "confirmation": (
            {key: _recorded_text(confirmation.get(key)) for key in ("actor", "at", "reason")}
            if isinstance(confirmation, dict)
            else None
        ),
        "generation": forecast_generation(version),
    }


class ForecastStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / "ledger.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _events(self, department: str) -> list[Json]:
        if not self.root.exists():
            return []
        events = []
        for path in sorted(self.root.glob("event-*.json")):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                event = envelope["event"]
                if fingerprint(event) != envelope["sha256"]:
                    raise ForecastStoreError("预测记录完整性校验失败，未使用该结果")
                if event["department_id"] == department:
                    events.append(event)
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ForecastStoreError("预测记录损坏，未使用该结果") from exc
        events.sort(key=lambda event: event["event_no"])
        prior = None
        for number, event in enumerate(events, 1):
            if event["event_no"] != number or event.get("previous_event_hash") != prior:
                raise ForecastStoreError("预测版本链不完整，未使用该结果")
            prior = fingerprint(event)
        return events

    @staticmethod
    def _versions(events: list[Json]) -> list[Json]:
        versions = []
        for event in events:
            if event["kind"] == "VERSION":
                versions.append({**event["version"], "status": "DRAFT", "confirmation": None})
            elif event["kind"] == "CONFIRMATION":
                version = next((v for v in versions if v["id"] == event["version_id"]), None)
                if not version or fingerprint(version["calculation"]) != event["calculation_hash"]:
                    raise ForecastStoreError("预测确认与计算结果不一致")
                version.update(
                    status="CONFIRMED",
                    confirmation={
                        "actor": event["actor"],
                        "at": event["created_at"],
                        "reason": event["reason"],
                    },
                )
        return versions

    def current(self, department: str) -> Json:
        with self._lock():
            return self._view(self._events(department))

    def versions(self, department: str) -> list[Json]:
        """Legacy reader for import verification and isolated contract tests."""
        with self._lock():
            return list(reversed(self._versions(self._events(department))))

    def trend(
        self,
        department: str,
        *,
        baseline_hash: str,
        days: Any = "30",
        through_version: str | None = None,
    ) -> Json:
        # Isolated legacy contract tests only; production uses PostgreSQL above.
        from packages.forecast.trend import build_forecast_trend

        return build_forecast_trend(
            self.versions(department), baseline_hash=baseline_hash, days=days, through_version=through_version
        )

    def version(self, department: str, version_id: str) -> Json | None:
        with self._lock():
            events = self._events(department)
            view = self._view(events)
            selected = next((item for item in self._versions(events) if item["id"] == version_id), None)
            if selected is None:
                return None
            return {
                **view,
                "version": selected,
                "latest_version_id": view["version"]["id"] if view["version"] else None,
            }

    def _view(self, events: list[Json]) -> Json:
        versions = self._versions(events)
        return {
            "baseline_year": 2027,
            "baseline_hash": versions[-1]["baseline_hash"] if versions else None,
            "version": versions[-1] if versions else None,
            "latest_version_id": versions[-1]["id"] if versions else None,
            "history": [forecast_version_summary(version) for version in reversed(versions)],
        }

    def request_result(self, department: str, request_id: str, request_hash: str) -> Json | None:
        with self._lock():
            events = self._events(department)
            match = next((e for e in events if e["request_id"] == request_id), None)
            if match is None:
                return None
            if match["request_hash"] != request_hash:
                raise ForecastStoreError("同一请求编号不得用于不同操作")
            # Return the response at this command's cutoff, not an unrelated newer version.
            return self._view([e for e in events if e["event_no"] <= match["event_no"]])

    def append_version(
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
        with self._lock():
            events = self._events(department)
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
            self._append(
                events, department, actor, request_id, request_hash, {"kind": "VERSION", "version": version}
            )
            return self._view(events)

    def confirm(
        self, *, department: str, actor: str, version_id: str, request_id: str, request_hash: str, reason: str
    ) -> Json:
        with self._lock():
            events = self._events(department)
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
            self._append(
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
            )
            return self._view(events)

    def _dedupe(self, events: list[Json], request_id: str, request_hash: str) -> Json | None:
        match = next((e for e in events if e["request_id"] == request_id), None)
        if not match:
            return None
        if match["request_hash"] != request_hash:
            raise ForecastStoreError("同一请求编号不得用于不同操作")
        return self._view([e for e in events if e["event_no"] <= match["event_no"]])

    def _append(
        self,
        events: list[Json],
        department: str,
        actor: str,
        request_id: str,
        request_hash: str,
        payload: Json,
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
        path = self.root / f"event-{uuid4()}.json"
        with path.open("xb") as destination:
            destination.write(canonical({"sha256": fingerprint(event), "event": event}))
            destination.flush()
            os.fsync(destination.fileno())
        path.chmod(0o600)
        events.append(event)
