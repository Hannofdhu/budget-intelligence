from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

import pandas as pd


DIMENSIONS = ["季节财年", "赛道", "产品", "年级", "科目", "教学区"]
LEVELS = [
    ("精确", ["季节财年", "赛道", "产品", "年级", "科目", "教学区"]),
    ("去校区", ["季节财年", "赛道", "产品", "年级", "科目"]),
    ("去科目", ["季节财年", "赛道", "产品", "年级"]),
    ("去年级", ["季节财年", "赛道", "产品"]),
    ("去产品", ["季节财年", "赛道"]),
    ("部门同季节", ["季节财年"]),
]

CLASS_FIELDS = {
    "新生": {
        "base": "新生退班基数(在班基数)",
        "process_num": "新生同期退班人次-过程值",
        "process_den": "新生同期退班基数(退班率分母)-过程值",
        "final_num": "新生同期退班人次",
        "final_den": "新生同期退班基数(退班率分母)",
    },
    "老生": {
        "base": "老生退班基数(在班基数)",
        "process_num": "老生同期退班人次-过程值",
        "process_den": "老生同期退班基数(退班率分母)-过程值",
        "final_num": "老生同期退班人次",
        "final_den": "老生同期退班基数(退班率分母)",
    },
}


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def load_data(path: Path, source_sql: Path | None = None) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if source_sql is not None:
        query = source_sql.read_text(encoding="utf-8")
        with sqlite3.connect(":memory:") as connection:
            frame.to_sql("智慧学习部指标汇总宽表", connection, index=False)
            frame = pd.read_sql_query(query, connection)
    required = {
        "dtdate",
        "财季",
        "招生人次",
        "退班基数(在班基数)",
        "同期退班人次-过程值",
        *DIMENSIONS,
        *(field for fields in CLASS_FIELDS.values() for field in fields.values()),
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"输入缺少字段: {missing}")

    non_numeric = {"dtdate", "财季", "赛道", "产品", "年级", "科目", "教学区"}
    for column in frame.columns:
        if column not in non_numeric:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def profile_quality(frame: pd.DataFrame) -> dict:
    duplicate_mask = frame.duplicated(DIMENSIONS, keep=False)
    base_sum = frame["新生退班基数(在班基数)"].fillna(0) + frame[
        "老生退班基数(在班基数)"
    ].fillna(0)
    base_reconciles = base_sum.eq(frame["退班基数(在班基数)"].fillna(0))
    enrollment_reconciles = frame["退班基数(在班基数)"].fillna(0).eq(
        frame["招生人次"].fillna(0)
    )

    complement_complete = frame[
        [
            "同期退班人次-过程值",
            "新生同期退班人次-过程值",
            "老生同期退班人次-过程值",
        ]
    ].notna().all(axis=1)
    complement_reconciles = complement_complete & (
        frame["新生同期退班人次-过程值"]
        + frame["老生同期退班人次-过程值"]
    ).eq(frame["同期退班人次-过程值"])
    positive_old_base = frame["老生退班基数(在班基数)"].fillna(0).gt(0)
    incomplete_dimensions = frame[DIMENSIONS[1:]].isna().any(axis=1)

    return {
        "rows": int(len(frame)),
        "dtdate_values": sorted(frame["dtdate"].dropna().astype(str).unique().tolist()),
        "season_values": sorted(frame["季节财年"].dropna().astype(int).unique().tolist()),
        "duplicate_detail_rows": int(duplicate_mask.sum()),
        "duplicate_detail_groups": int(
            frame.groupby(DIMENSIONS, dropna=False).size().gt(1).sum()
        ),
        "base_reconciliation_failures": int((~base_reconciles).sum()),
        "enrollment_reconciliation_failures": int((~enrollment_reconciles).sum()),
        "old_process_complement_complete_rows": int(complement_complete.sum()),
        "old_process_complement_pass_rows": int(complement_reconciles.sum()),
        "old_process_complement_failure_rows": int((~complement_reconciles).sum()),
        "positive_old_base_rows": int(positive_old_base.sum()),
        "positive_old_base_complement_failure_rows": int(
            (positive_old_base & ~complement_reconciles).sum()
        ),
        "old_base_affected_by_complement_failure": float(
            frame.loc[
                positive_old_base & ~complement_reconciles,
                "老生退班基数(在班基数)",
            ].sum()
        ),
        "old_base_total": float(frame.loc[positive_old_base, "老生退班基数(在班基数)"].sum()),
        "incomplete_dimension_rows": int(incomplete_dimensions.sum()),
        "incomplete_dimension_current_base": float(
            frame.loc[
                incomplete_dimensions,
                ["新生退班基数(在班基数)", "老生退班基数(在班基数)"],
            ]
            .fillna(0)
            .sum()
            .sum()
        ),
        "old_process_complement_gate_pass": bool(complement_reconciles.all()),
        "process_total_sum": float(frame["同期退班人次-过程值"].sum(min_count=1)),
        "process_new_sum": float(frame["新生同期退班人次-过程值"].sum(min_count=1)),
        "process_old_physical_sum": float(
            frame["老生同期退班人次-过程值"].sum(min_count=1)
        ),
        "process_old_complement_sum": float(
            (
                frame["同期退班人次-过程值"].fillna(0)
                - frame["新生同期退班人次-过程值"].fillna(0)
            ).sum()
        ),
    }


def grouped_stats(frame: pd.DataFrame, group_keys: list[str], fields: dict) -> pd.DataFrame:
    source_columns = [
        fields["process_num"],
        fields["process_den"],
        fields["final_num"],
        fields["final_den"],
    ]
    grouped = frame.groupby(group_keys, dropna=False, sort=False)
    totals = grouped[source_columns].sum(min_count=1).reset_index()
    counts = grouped[source_columns].count().reset_index()
    sizes = grouped.size().rename("_group_size").reset_index()
    result = totals.merge(counts, on=group_keys, suffixes=("", "_nonnull"))
    result = result.merge(sizes, on=group_keys)
    for column in source_columns:
        result[f"{column}_complete"] = result[f"{column}_nonnull"].eq(
            result["_group_size"]
        )
    return result


def assign_fallbacks(frame: pd.DataFrame, student_class: str, threshold: int) -> pd.DataFrame:
    fields = CLASS_FIELDS[student_class]
    result = frame[DIMENSIONS + ["财季", fields["base"]]].copy()
    result = result.rename(columns={fields["base"]: "current_base"})
    result["退班分类"] = student_class
    result["回退层级"] = pd.NA
    result["过程分子"] = pd.NA
    result["过程分母"] = pd.NA
    result["最终分子"] = pd.NA
    result["最终分母"] = pd.NA

    positive_base = result["current_base"].fillna(0).gt(0)
    for level_name, keys in LEVELS:
        unresolved = positive_base & result["回退层级"].isna()
        if not unresolved.any():
            break
        stats = grouped_stats(frame, keys, fields)
        merged = result.loc[unresolved, keys].merge(stats, on=keys, how="left")
        merged.index = result.index[unresolved]

        complete_columns = [
            f"{fields['process_num']}_complete",
            f"{fields['process_den']}_complete",
            f"{fields['final_num']}_complete",
            f"{fields['final_den']}_complete",
        ]
        usable = merged[complete_columns].fillna(False).all(axis=1)
        usable &= merged[fields["process_den"]].ge(threshold)
        usable &= merged[fields["final_den"]].ge(threshold)
        selected = merged.index[usable]

        result.loc[selected, "回退层级"] = level_name
        result.loc[selected, "过程分子"] = merged.loc[selected, fields["process_num"]]
        result.loc[selected, "过程分母"] = merged.loc[selected, fields["process_den"]]
        result.loc[selected, "最终分子"] = merged.loc[selected, fields["final_num"]]
        result.loc[selected, "最终分母"] = merged.loc[selected, fields["final_den"]]

    result.loc[~positive_base, "回退层级"] = "当前基数为零"
    result.loc[positive_base & result["回退层级"].isna(), "回退层级"] = "无可用历史"

    numeric_columns = ["过程分子", "过程分母", "最终分子", "最终分母"]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["同期过程率"] = result["过程分子"] / result["过程分母"]
    result["最终率"] = result["最终分子"] / result["最终分母"]
    result["历史剩余退班率"] = (result["最终率"] - result["同期过程率"]).clip(lower=0)
    result["预计剩余退班人次_未取整"] = (
        result["current_base"].fillna(0) * result["历史剩余退班率"].fillna(0)
    ).clip(lower=0, upper=result["current_base"].fillna(0))
    result.loc[result["回退层级"].eq("无可用历史"), "预计剩余退班人次_未取整"] = pd.NA
    return result


def summarize_assignments(assignments: pd.DataFrame) -> list[dict]:
    rows = []
    total_positive_base = float(
        assignments.loc[assignments["current_base"].fillna(0).gt(0), "current_base"].sum()
    )
    ordered_levels = [name for name, _ in LEVELS] + ["无可用历史", "当前基数为零"]
    for student_class in ["新生", "老生"]:
        class_rows = assignments[assignments["退班分类"].eq(student_class)]
        class_positive_base = float(
            class_rows.loc[class_rows["current_base"].fillna(0).gt(0), "current_base"].sum()
        )
        for level in ordered_levels:
            subset = class_rows[class_rows["回退层级"].eq(level)]
            base = float(subset["current_base"].fillna(0).sum())
            expected = subset["预计剩余退班人次_未取整"].sum(min_count=1)
            rows.append(
                {
                    "退班分类": student_class,
                    "回退层级": level,
                    "明细数": int(len(subset)),
                    "当前基数": base,
                    "分类内基数覆盖率": (
                        base / class_positive_base if class_positive_base else None
                    ),
                    "全部分类基数占比": base / total_positive_base if total_positive_base else None,
                    "预计剩余退班人次_未取整": (
                        None if pd.isna(expected) else float(expected)
                    ),
                }
            )
    return rows


def rounded_totals(assignments: pd.DataFrame) -> list[dict]:
    usable = assignments[~assignments["回退层级"].isin(["无可用历史", "当前基数为零"])].copy()
    grouped = (
        usable.groupby(["退班分类", "财季", "教学区"], dropna=False)[
            "预计剩余退班人次_未取整"
        ]
        .sum(min_count=1)
        .reset_index()
    )
    grouped["预计剩余退班人次_取整"] = grouped[
        "预计剩余退班人次_未取整"
    ].map(round_half_up)
    return (
        grouped.groupby("退班分类", dropna=False)
        .agg(
            校区分类组数=("教学区", "size"),
            预计剩余退班人次_未取整=("预计剩余退班人次_未取整", "sum"),
            预计剩余退班人次_按校区分类取整=("预计剩余退班人次_取整", "sum"),
        )
        .reset_index()
        .to_dict(orient="records")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--threshold", type=int, default=30)
    parser.add_argument("--source-sql", type=Path)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    frame = load_data(args.input_csv, args.source_sql)
    quality = profile_quality(frame)
    assignments = pd.concat(
        [assign_fallbacks(frame, student_class, args.threshold) for student_class in CLASS_FIELDS],
        ignore_index=True,
    )
    output = {
        "source": {
            "dataset": "智慧学习部指标汇总宽表",
            "dataset_id": "l932b728584e3496685341a5",
            "input_csv": str(args.input_csv),
            "source_sql": str(args.source_sql) if args.source_sql else None,
            "threshold": args.threshold,
        },
        "quality": quality,
        "coverage": summarize_assignments(assignments),
        "rounded_totals": rounded_totals(assignments),
        "formal_publishability": {
            "new_student": "可继续评估",
            "old_student": (
                "可继续评估" if quality["old_process_complement_gate_pass"] else "阻断"
            ),
            "overall": (
                "可继续评估" if quality["old_process_complement_gate_pass"] else "阻断"
            ),
            "reason": (
                None
                if quality["old_process_complement_gate_pass"]
                else "老生同期过程退班分子未通过逐明细严格补集勾稽"
            ),
        },
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
