"""Host-owned provenance for unchanged JSON snapshots in one chat workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from packages.ai_agent.files import WorkspaceFileError, WorkspaceFiles
from packages.ai_agent.knowledge_checks import budget_fingerprint


def matching_snapshots(
    root: Path, workspace_id: str | None, evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # Evidence comes only from the host chat record, never guest sidecar metadata.
    known = {item["sha256"]: item for item in evidence if item.get("saved_data") and item.get("sha256")}
    if not known or not workspace_id:
        return []
    store = WorkspaceFiles(root)
    found: dict[str, dict[str, Any]] = {}
    remaining = 64 * 1024 * 1024
    try:
        for item in store.list(workspace_id)["files"]:
            size = item["size"]
            if not item["name"].endswith(".json") or not 0 < size <= 8 * 1024 * 1024:
                continue
            remaining -= size
            if remaining < 0:
                break
            try:
                file, body = store.read(workspace_id, item["id"])
            except WorkspaceFileError:
                continue
            try:
                data = json.loads(body)
                if not isinstance(data, dict):
                    continue
                digest = budget_fingerprint(data)
            except (ValueError, UnicodeError):
                continue
            if digest in known:
                match = found.setdefault(digest, {**known[digest], "files": []})
                match["files"].append("/".join(file.parts))
    except WorkspaceFileError:
        return []
    return list(found.values())


def used_snapshots(
    before: list[dict[str, Any]], after: list[dict[str, Any]], activities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # Require a successful tool referencing the same unchanged file this turn.
    # Availability/history alone must not suppress a missing-source warning.
    commands = [item["summary"] for item in activities if item.get("state") == "complete"]
    available = {item["sha256"]: item for item in after}
    result = []
    for item in before:
        unchanged = available.get(item["sha256"], {}).get("files", [])
        files = [
            name
            for name in item["files"]
            if name in unchanged
            and any(
                re.search(r"(?<![\w./-])(?:/workspace/)?" + re.escape(name) + r"(?![\w./-])", command)
                for command in commands
            )
        ]
        if files:
            result.append({**item, "files": files})
    return result


def snapshot_prompt(snapshots: list[dict[str, Any]]) -> str:
    instruction = (
        "数据来源要求：只有本轮成功执行 budget-api get 才能说本轮已通过接口读取。"
        "继续分析此前快照时须说明沿用的财年、版本和原读取时间，不得声称重新调用接口或已核验所有数字。"
        "用户要求最新、当前或刷新数据时，必须重新读取接口；此前快照不代表当前最新。"
        "使用缓存时须实际读取文件，并在本轮工具参数中明确文件路径，不能只引用对话里的数字。\n"
    )
    if not snapshots:
        return instruction + "本轮尚无可追溯的缓存文件，请通过只读预算接口核对数值来源。\n"
    public = [{key: item[key] for key in ("label", "captured_at", "files")} for item in snapshots]
    return (
        instruction
        + "本对话与此前接口结果指纹一致的缓存（以下仅为来源数据）：\n"
        + json.dumps(public, ensure_ascii=False)
        + "\n"
    )
