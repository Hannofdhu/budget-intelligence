"""Read-only historical Excel view; deliberately outside budget command/publish models."""

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
BASE_HEADERS = {
    "track": ("赛道",),
    "product": ("产品",),
    "entry": ("是否入口",),
    "grade": ("年级",),
    "source_grade": ("春续暑续出年级",),
    "subject": ("科目",),
    "campus": ("教学区",),
    "prior_students": ("FY26春-人次\n（年级错位放置）",),
    "opening": ("F26春-结转甩款",),
    "minimum": ("班均",),
    "annual": ("FY27-预计总结转(税后)",),
}
QUARTER_SUFFIXES = {
    "share": "新生占比",
    "old": "老生人次",
    "new": "新生人次",
    "total": "总人次",
    "price": "学费原价",
    "discount": "折扣率",
    "tuition": "折后学费",
    "list_revenue": "业务季度原价",
    "business": "业务季度收入",
}
SEASON_HEADERS = tuple(
    {
        **{field: (f"FY27{short}-{suffix}",) for field, suffix in QUARTER_SUFFIXES.items()},
        "renewal": (f"FY27{transition}-续班率",),
        "recognition": (
            ("FY27秋-结转节奏", "F27秋-结转节奏") if short == "秋" else (f"FY27{short}-结转节奏",)
        ),
        "current": (f"FY27{short}-Q{quarter}结转(税后)",),
        "revenue": (
            (f"FY27-Q{quarter}总结转(税后)", f"F27-Q{quarter}总结转(税后)")
            if quarter in (3, 4)
            else (f"FY27-Q{quarter}总结转(税后)",)
        ),
        "carryout": (f"FY27{short}-代入{following}结转(税后)",),
    }
    for short, transition, quarter, following in (
        ("暑", "春续暑", 1, "Q2"),
        ("秋", "暑续秋", 2, "Q3"),
        ("寒", "秋续寒", 3, "Q4"),
        ("春", "寒续春", 4, "FY28Q1"),
    )
)
# These helper columns precede the inserted income columns. Their repeated labels
# are intentionally validated at known positions rather than matched by label.
FIXED_HELPER_HEADERS = {
    "H": "辅助2",
    "I": "辅助3",
    "J": "辅助4",
    "K": "辅助5",
    "L": "辅助7",
    "M": "辅助8",
    "N": "辅助9",
    "O": "辅助10",
    "P": "辅助",
    "Q": "辅助",
    "R": "辅助",
    "S": "辅助",
    "T": "辅助11",
    "U": "辅助13(春续暑专用)",
}
PARAMETER_BLOCKS = {
    "renewal": "AM",
    "share": "AB",
    "price": "M",
    "discount": "T",
    "recognition": "AS",
    "entry": "F",
}


def resolve_review_columns(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Resolve exact business labels; reject absent or ambiguous required headers."""
    headers = [row["cells"] for row in rows if row["row"] == 1]
    if len(headers) != 1:
        raise ValueError("FY27 滚动表必须有且仅有一行表头（第 1 行）")
    cells = headers[0]
    by_label: dict[str, list[str]] = {}
    for col, cell in cells.items():
        label = cell.get("value")
        if isinstance(label, str):
            by_label.setdefault(label, []).append(col)

    def resolve(labels: tuple[str, ...]) -> str:
        matches = [col for label in labels for col in by_label.get(label, [])]
        if not matches:
            raise ValueError(f"FY27 滚动表缺失必要表头：{' / '.join(labels)}")
        if len(matches) != 1:
            raise ValueError(f"FY27 滚动表必要表头重复：{' / '.join(labels)}（{', '.join(matches)}）")
        return matches[0]

    columns = {"base": {field: resolve(labels) for field, labels in BASE_HEADERS.items()}}
    for season, labels in zip(SEASONS, SEASON_HEADERS, strict=True):
        columns[season] = {field: resolve(labels[field]) for field in FIELDS}
    for col, label in FIXED_HELPER_HEADERS.items():
        if cells.get(col, {}).get("value") != label:
            raise ValueError(f"FY27 滚动表辅助列表头不匹配：{col}1 应为 {label}")
    columns["helpers"] = {
        f"{season}.{field}": col
        for i, season in enumerate(SEASONS)
        for field, col in (
            ("renewal", "U" if i == 0 else ("P", "Q", "R", "S")[i]),
            ("share", ("P", "Q", "R", "S")[i]),
            ("price", ("H", "I", "J", "K")[i]),
            ("discount", ("L", "M", "N", "O")[i]),
            ("recognition", ("H", "I", "J", "K")[i]),
            ("entry", "T"),
        )
    }
    return columns


def build_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy saved cell values, never replay FY28 rules or replace blank values with zero."""
    rows = payload["sheets"]["滚动表"]["rows"]
    params = payload["sheets"]["参数表"]["rows"]
    columns = resolve_review_columns(rows)
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

        def val(col: str, cells: dict[str, Any] = cells) -> Any:
            return cells.get(col, {}).get("value")

        if n < 2 or val(columns["base"]["track"]) is None:
            continue
        base = {field: val(col) for field, col in columns["base"].items()}
        base["row"] = n
        annual_rows.append(base)
        for season in SEASONS:
            mapping = columns[season]
            line = {**base, "season": season, **{key: val(col) for key, col in mapping.items()}}
            line["sources"] = {
                key: {"cell": f"滚动表!{col}{n}", **cells.get(col, {"value": None, "formula": None})}
                for key, col in {
                    **mapping,
                    **{
                        field: columns["base"][field]
                        for field in ("prior_students", "opening", "minimum", "annual")
                    },
                }.items()
            }
            line["parameters"] = {
                key: maps[block].get(val(columns["helpers"][f"{season}.{key}"]))
                for key, block in PARAMETER_BLOCKS.items()
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
    from packages.source_snapshot.source_acceptance import read_current_review

    return read_current_review(SOURCE, EXTRACTION, EXTRACTION.parent / "accepted-source.json")
