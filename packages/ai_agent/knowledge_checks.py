"""Limited checks over successful bridge replies and fingerprinted chat snapshots."""

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote


def budget_fingerprint(data: dict[str, Any]) -> str:
    # JSON key order/indentation may change across API restarts; values must not.
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class BudgetEvidence:
    def __init__(self, resource: str) -> None:
        self.resource = unquote(resource)
        self.body = bytearray()
        self.valid = False
        self.oversized = False

    def feed(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        kind = frame.get("type")
        if kind == "response_start":
            self.valid = frame.get("status") == 200
        elif kind == "response_chunk" and self.valid and not self.oversized:
            try:
                chunk = base64.b64decode(frame["data"], validate=True)
                if len(self.body) + len(chunk) > 8 * 1024 * 1024:
                    self.body.clear()
                    self.oversized = True
                else:
                    self.body.extend(chunk)
            except (ValueError, TypeError, KeyError):
                self.valid = False
        elif kind == "response_end" and self.valid and not self.oversized:
            try:
                data = json.loads(self.body)
            except (ValueError, UnicodeError):
                return None
            if not isinstance(data, dict):
                return None
            names: dict[str, set[str]] = {"campus": set(), "track": set()}
            stack: list[Any] = [data]
            examined = 0
            while stack and examined < 150_000:
                value = stack.pop()
                examined += 1
                if isinstance(value, dict):
                    for key, nested in value.items():
                        if key in names and isinstance(nested, str) and 0 < len(nested) <= 160:
                            names[key].add(nested)
                        elif isinstance(nested, (dict, list)):
                            stack.append(nested)
                elif isinstance(value, list):
                    stack.extend(value)
            return {
                "resource": self.resource,
                "sha256": budget_fingerprint(data),
                "captured_at": datetime.now(UTC).isoformat(),
                "label": source_label(self.resource, data),
                "saved_data": self.resource not in {"/budget/help", "/budget/next-year-catalog"},
                **{key: sorted(values)[:3000] for key, values in names.items()},
            }
        return None


def source_label(resource: str, data: dict[str, Any]) -> str:
    names = {"fy27-budget": "FY27定版预算", "fy27-forecast": "FY27全年预测", "fy28-budget": "FY28预算编制"}
    key = resource.removeprefix("/budget/").split("-version:")[0]
    label = names.get(key, resource)
    version = data.get("draft") if key == "fy28-budget" else data.get("version")
    if not isinstance(version, dict) and key in {"fy28-budget", "fy27-forecast"}:
        version = data
    if isinstance(version, dict):
        sequence, status = version.get("sequence"), version.get("status")
        if isinstance(sequence, int):
            label += f" · 第{sequence}版"
        if isinstance(status, str):
            label += f"（{status[:40]}）"
    return label


def check_answer(
    text: str,
    evidence: list[dict[str, Any]],
    snapshots: list[dict[str, Any]] | None = None,
    *,
    require_fresh: bool = False,
) -> dict[str, Any]:
    """No model-as-judge or assertion of full correctness. Only flag provable column swaps."""
    fresh_hashes = {item.get("sha256") for item in evidence}
    snapshots = [item for item in snapshots or [] if item.get("sha256") not in fresh_hashes]
    sources = [*evidence, *snapshots]
    campuses = {name for item in sources for name in item.get("campus", [])}
    tracks = {name for item in sources for name in item.get("track", [])}
    warnings: list[str] = []
    lines = text.splitlines()
    tables = 0
    for index, line in enumerate(lines[:-1]):
        if "|" not in line or not re.fullmatch(r"[\s|:\-]+", lines[index + 1]) or "-" not in lines[index + 1]:
            continue
        headers = [re.sub(r"[*`\s]", "", cell) for cell in line.strip().strip("|").split("|")]
        tables += 1
        for row in lines[index + 2 :]:
            if "|" not in row or not row.strip():
                break
            cells = [re.sub(r"[*`\s]", "", cell) for cell in row.strip().strip("|").split("|")]
            for column, header in enumerate(headers):
                if column >= len(cells):
                    continue
                if header in {"校区", "校区名称"} and cells[column] in tracks - campuses:
                    warnings.append("校区列出现了本轮接口中仅属于赛道的名称，表格待核实。")
                if header in {"赛道", "赛道名称"} and cells[column] in campuses - tracks:
                    warnings.append("赛道列出现了本轮接口中仅属于校区的名称，表格待核实。")
    has_numbers = bool(re.search(r"\d[\d,.]*\s*(?:万元|亿元|元|人次)", text))
    if has_numbers:
        if require_fresh and not any(item.get("saved_data", True) for item in evidence):
            warnings.append("本轮要求最新数据，但未成功重新读取预算接口；此前快照不能代表当前最新数据。")
        elif not any(item.get("saved_data", True) for item in sources):
            warnings.append(
                "本轮未读取到成功的预算接口结果，也未确认可追溯的快照来源，回答中的数值请核对精确来源。"
            )
    return {
        "state": "needs_review" if warnings else "limited",
        "warnings": list(dict.fromkeys(warnings)),
        "tables_examined": tables,
        "resources": list(dict.fromkeys(item["resource"] for item in evidence)),
        "snapshots": [
            {key: item[key] for key in ("resource", "label", "captured_at", "files")} for item in snapshots
        ],
        "notice": "仅检查可识别表格中的维度混列与数据来源记录；此前快照须与接口结果指纹一致，"
        "不代表本轮重新读取、当前最新版本或金额、合计及全部语义已核验。",
    }
