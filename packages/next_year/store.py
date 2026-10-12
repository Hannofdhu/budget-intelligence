"""Local planning drafts, never annual-budget or forecast confirmations.

Reuse the forecast ledger's hash chain, file lock and exclusive append. The
separate directory makes every saved draft an auditable, reversible preparation
step without writing to PostgreSQL or changing the confirmed FY27 forecast.
"""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.forecast.store import ForecastStore, ForecastStoreError, fingerprint

ROOT = Path(__file__).resolve().parents[2] / "output" / "next-year-ledger"
Json = dict[str, Any]


class NextYearStoreError(ForecastStoreError):
    pass


def _source_changed(source: Json | None, draft: Json | None) -> bool:
    if draft is None:
        return False
    bound = draft["source"]
    return source is None or any(bound.get(key) != source.get(key) for key in ("id", "calculation_hash"))


class NextYearStore(ForecastStore):
    def __init__(self, root: Path = ROOT) -> None:
        super().__init__(root)

    @staticmethod
    def _drafts(events: list[Json]) -> list[Json]:
        drafts: list[Json] = []
        parent: str | None = None
        for event in events:
            if event.get("kind") != "NEXT_YEAR_DRAFT":
                raise NextYearStoreError("下一年预算准备记录类型无效")
            draft = event.get("draft")
            if not isinstance(draft, dict) or draft.get("status") != "DRAFT":
                raise NextYearStoreError("下一年预算只能保存为准备草稿")
            if (
                any(
                    not isinstance(draft.get(key), str) or not draft[key].strip()
                    for key in ("id", "created_at", "created_by", "reason")
                )
                or type(draft.get("sequence")) is not int
            ):
                raise NextYearStoreError("下一年预算准备版本信息无效")
            if any(
                not isinstance(draft.get(key), dict)
                for key in ("inputs", "prepared_inputs", "source_snapshot", "calculation", "source")
            ) or not isinstance(draft.get("observed_source"), dict):
                raise NextYearStoreError("下一年预算准备内容结构无效")
            if any(
                not isinstance(draft[key].get(field), str) or not draft[key][field]
                for key in ("source", "observed_source")
                for field in ("id", "calculation_hash")
            ):
                raise NextYearStoreError("下一年预算准备来源信息无效")
            if draft.get("parent_id") != parent or draft.get("sequence") != len(drafts) + 1:
                raise NextYearStoreError("下一年预算准备版本链不完整")
            for value, digest in (
                ("inputs", "inputs_hash"),
                ("prepared_inputs", "prepared_inputs_hash"),
                ("source_snapshot", "source_snapshot_hash"),
                ("calculation", "calculation_hash"),
                ("source", "source_hash"),
            ):
                if value not in draft or fingerprint(draft[value]) != draft.get(digest):
                    raise NextYearStoreError("下一年预算准备内容指纹不一致")
            if draft.get("operation") not in {"PREPARE", "REVISE", "RESTORE"}:
                raise NextYearStoreError("下一年预算准备操作类型无效")
            if draft["operation"] == "REVISE":
                if not drafts or any(
                    draft[key] != drafts[-1][key] for key in ("source", "source_snapshot", "prepared_inputs")
                ):
                    raise NextYearStoreError("修订不得替换已冻结的预测来源")
            elif draft["operation"] == "RESTORE":
                restored = next(
                    (item for item in drafts if item["id"] == draft.get("restored_from_id")), None
                )
                if restored is None or any(
                    draft[key] != restored[key]
                    for key in ("inputs", "source", "source_snapshot", "prepared_inputs", "calculation")
                ):
                    raise NextYearStoreError("恢复草稿必须完整保留选定历史版本，不能覆盖或改写历史")
            drafts.append(draft)
            parent = draft.get("id")
            if not isinstance(parent, str) or not parent:
                raise NextYearStoreError("下一年预算准备版本编号无效")
        return drafts

    def _view(self, events: list[Json]) -> Json:
        drafts = self._drafts(events)
        current = drafts[-1] if drafts else None
        source = current.get("observed_source") if current else None
        return {
            "source": source,
            "draft": current,
            "source_changed": _source_changed(source, current),
            "history": [
                {
                    **{key: draft[key] for key in ("id", "sequence", "status", "created_at", "reason")},
                    "created_by": draft["created_by"],
                    "operation": draft["operation"],
                    "restored_from_id": draft.get("restored_from_id"),
                    "fiscal_year": draft["inputs"].get("fiscal_year"),
                    "source_id": draft["source"]["id"],
                    "source_sequence": draft["source"].get("sequence"),
                    "annual": deepcopy(draft["calculation"].get("annual", {})),
                }
                for draft in reversed(drafts)
            ],
        }

    def versions(self, department: str) -> list[Json]:
        """Read validated planning versions, not the inherited forecast schema."""
        with self._lock():
            return deepcopy(list(reversed(self._drafts(self._events(department)))))

    def version(self, department: str, version_id: str) -> Json | None:
        with self._lock():
            events = self._events(department)
            selected = next((item for item in self._drafts(events) if item["id"] == version_id), None)
            if selected is None:
                return None
            view = self._view(events)
            return deepcopy(
                {
                    **view,
                    "draft": selected,
                    "source_changed": _source_changed(view["source"], selected),
                    "latest_draft_id": view["draft"]["id"],
                    "read_only": True,
                }
            )

    def append_draft(
        self,
        *,
        department: str,
        actor: str,
        request_id: str,
        request_hash: str,
        parent_id: str | None,
        source: Json,
        inputs: Json,
        calculation: Json,
        reason: str,
        operation: str,
        source_snapshot: Json | None = None,
        observed_source: Json | None = None,
        restored_from_id: str | None = None,
    ) -> Json:
        if operation not in {"PREPARE", "REVISE", "RESTORE"}:
            raise NextYearStoreError("不支持的下一年预算准备操作")
        if not reason.strip():
            raise NextYearStoreError("必须填写操作原因")
        with self._lock():
            events = self._events(department)
            if replay := self._dedupe(events, request_id, request_hash):
                return replay
            view = self._view(events)
            current = view["draft"]
            if (current["id"] if current else None) != parent_id:
                raise NextYearStoreError("预算准备草稿已变化，请重新读取后操作")
            if operation == "RESTORE":
                restored = next(
                    (item for item in self._drafts(events) if item["id"] == restored_from_id), None
                )
                if restored is None:
                    raise NextYearStoreError("待恢复的预算版本不存在")
                if (
                    source != restored["source"]
                    or inputs != restored["inputs"]
                    or calculation != restored["calculation"]
                ):
                    raise NextYearStoreError("恢复草稿必须完整保留选定历史版本，不能覆盖或改写历史")
                if source_snapshot is not None and source_snapshot != restored["source_snapshot"]:
                    raise NextYearStoreError("恢复草稿不得替换已冻结的预测快照")
                frozen = restored["source_snapshot"]
                prepared = restored["prepared_inputs"]
            elif operation == "REVISE":
                if current is None:
                    raise NextYearStoreError("请先准备下一年预算草稿")
                if current["source"] != source:
                    raise NextYearStoreError("修订不得替换已冻结的预测来源")
                if source_snapshot is not None and source_snapshot != current["source_snapshot"]:
                    raise NextYearStoreError("修订不得替换已冻结的预测快照")
                frozen = current["source_snapshot"]
                prepared = current["prepared_inputs"]
            else:
                if not isinstance(source_snapshot, dict) or not source_snapshot:
                    raise NextYearStoreError("准备草稿必须保存完整预测来源快照")
                frozen = source_snapshot
                prepared = inputs
            draft = deepcopy(
                {
                    "id": str(uuid4()),
                    "sequence": len(view["history"]) + 1,
                    "parent_id": parent_id,
                    "status": "DRAFT",
                    "source": source,
                    "source_hash": fingerprint(source),
                    "inputs": inputs,
                    "inputs_hash": fingerprint(inputs),
                    "prepared_inputs": prepared,
                    "prepared_inputs_hash": fingerprint(prepared),
                    "source_snapshot": frozen,
                    "source_snapshot_hash": fingerprint(frozen),
                    "calculation": calculation,
                    "calculation_hash": fingerprint(calculation),
                    "observed_source": observed_source if observed_source is not None else source,
                    "operation": operation,
                    "restored_from_id": restored_from_id if operation == "RESTORE" else None,
                    "created_at": datetime.now(UTC).isoformat(),
                    "created_by": actor,
                    "reason": reason,
                }
            )
            # Validate before the exclusive append; bad callers must never leave
            # a corrupt event behind even when they bypass the HTTP adapter.
            self._drafts([*events, {"kind": "NEXT_YEAR_DRAFT", "draft": draft}])
            self._append(
                events,
                department,
                actor,
                request_id,
                request_hash,
                {"kind": "NEXT_YEAR_DRAFT", "draft": draft},
            )
            return self._view(events)

    def confirm(self, **_: Any) -> Json:
        raise NextYearStoreError("下一年预算准备草稿不能确认或发布")

    def append_version(self, **_: Any) -> Json:
        raise NextYearStoreError("下一年预算准备只能追加草稿，不能创建预测版本")
