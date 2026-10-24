"""Limited deterministic checks over THIS turn's successful, read-only bridge replies."""

import base64
import json
import re
from typing import Any
from urllib.parse import unquote


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
                "saved_data": self.resource not in {"/budget/help", "/budget/next-year-catalog"},
                **{key: sorted(values)[:3000] for key, values in names.items()},
            }
        return None


def check_answer(text: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """No model-as-judge or assertion of full correctness. Only flag provable column swaps."""
    campuses = {name for item in evidence for name in item.get("campus", [])}
    tracks = {name for item in evidence for name in item.get("track", [])}
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
    if not any(item.get("saved_data", True) for item in evidence) and re.search(
        r"\d[\d,.]*\s*(?:万元|亿元|元|人次)", text
    ):
        warnings.append("本轮未读取到成功的预算接口结果，回答中的数值请核对精确来源。")
    return {
        "state": "needs_review" if warnings else "limited",
        "warnings": list(dict.fromkeys(warnings)),
        "tables_examined": tables,
        "resources": list(dict.fromkeys(item["resource"] for item in evidence)),
        "notice": "仅检查可识别表格中的维度混列与本轮读取情况，不代表金额、合计或全部语义已核验。",
    }
