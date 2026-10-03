"""Read-only historical Excel view; deliberately outside budget command/publish models."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "【智慧学习部】FY27预算拆解（1.13亿）-备份.xlsx"
EXTRACTION = ROOT / "output/fy27-shadow/source-cells.json"
SEASONS = ("暑假", "秋季", "寒假", "春季")
COLUMNS = (
    "AI AJ AL AM AN AO AP AQ AR AS AV AW AX AY",
    "AZ BA BC BD BE BF BG BH BI BJ BM BN BO BP",
    "BQ BR BT BU BV BW BX BY BZ CA CD CE CF CG",
    "CH CI CK CL CM CN CO CP CQ CR CU CV CW CX",
)
FIELDS = (
    "renewal share old new total price discount tuition list_revenue "
    "business recognition current revenue carryout"
).split()


def build_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy saved cell values, never replay FY28 rules or replace blank values with zero."""
    rows = payload["sheets"]["滚动表"]["rows"]
    params = payload["sheets"]["参数表"]["rows"]
    maps = {}
    for key_col, val_col in (("F", "G"), ("M", "N"), ("T", "U"), ("AB", "AC"), ("AM", "AN"), ("AS", "AT")):
        index = {}
        for row in params:
            key = row["cells"].get(key_col, {}).get("value")
            if row["row"] >= 3 and key is not None and key not in index:
                index[key] = {
                    "cell": f"参数表!{val_col}{row['row']}",
                    **row["cells"].get(val_col, {"value": None, "formula": None}),
                }
        maps[key_col] = index

    lines, annual_rows = [], []
    for row in rows:
        cells, n = row["cells"], row["row"]

        def val(col: str, cells: dict = cells) -> Any:
            return cells.get(col, {}).get("value")

        if n < 2 or val("A") is None:
            continue
        base = dict(
            zip(
                ("track", "product", "entry", "grade", "source_grade", "subject", "campus"),
                [val(c) for c in "A B C D E F G".split()],
                strict=True,
            )
        )
        base.update(row=n, prior_students=val("AE"), opening=val("AG"), minimum=val("AH"), annual=val("W"))
        annual_rows.append(base)
        for i, cols in enumerate(COLUMNS):
            mapping = dict(zip(FIELDS, cols.split(), strict=True))
            line = {**base, "season": SEASONS[i], **{key: val(col) for key, col in mapping.items()}}
            line["sources"] = {
                key: {"cell": f"滚动表!{col}{n}", **cells.get(col, {"value": None, "formula": None})}
                for key, col in {
                    **mapping,
                    "prior_students": "AE",
                    "opening": "AG",
                    "minimum": "AH",
                    "annual": "W",
                }.items()
            }
            line["parameters"] = {
                key: maps[block].get(val(helper))
                for key, block, helper in (
                    ("renewal", "AM", "U" if i == 0 else ("P", "Q", "R", "S")[i]),
                    ("share", "AB", ("P", "Q", "R", "S")[i]),
                    ("price", "M", ("H", "I", "J", "K")[i]),
                    ("discount", "T", ("L", "M", "N", "O")[i]),
                    ("recognition", "AS", ("H", "I", "J", "K")[i]),
                    ("entry", "F", "T"),
                )
            }
            line["excel_topup"] = (
                str(cells.get(mapping["new"], {}).get("formula") or "").startswith("=IF(ROUND(")
                and isinstance(line["total"], (int, float))
                and line["total"] == line["minimum"]
            )
            lines.append(line)

    def total(items: list[dict[str, Any]], field: str) -> str:
        if any(not isinstance(item[field], (int, float)) for item in items):
            raise ValueError(f"FY27 {field} 存在缺失或非数值，无法核对合计")
        return str(sum((Decimal(str(item[field])) for item in items), Decimal(0)))

    annual = total(annual_rows, "annual")
    revenue = total(lines, "revenue")
    if abs(Decimal(annual) - Decimal(revenue)) > Decimal("0.01"):
        raise ValueError("FY27 全年原表值与季度合计不一致")
    return {
        "mode": "HISTORICAL_EXCEL_READ_ONLY",
        "fiscal_year": 2027,
        "source_file": payload["source_file"],
        "sha256": payload["sha256"],
        "extracted_at": payload["extracted_at"],
        "business_snapshot_date": payload.get("source_business_as_of"),
        "scope_count": len(annual_rows),
        "season_count": len(lines),
        "annual_revenue": annual,
        "quarter_revenue_sum": revenue,
        "entry_total": total([x for x in lines if x["season"] == "暑假" and x["entry"] == "是"], "new"),
        "prior_students": total(
            [x for x in annual_rows if isinstance(x["prior_students"], (int, float))], "prior_students"
        ),
        "prior_students_not_applicable_rows": sum(x["prior_students"] == "-" for x in annual_rows),
        "opening_old": total([x for x in lines if x["season"] == "暑假"], "old"),
        "rows": lines,
    }


def read_review() -> dict[str, Any]:
    payload = json.loads(EXTRACTION.read_text(encoding="utf-8"))
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != payload["sha256"]:
        raise ValueError("FY27 Excel 已变化，须重新进行只读提取与核对；未显示旧快照")
    return build_review(payload)
