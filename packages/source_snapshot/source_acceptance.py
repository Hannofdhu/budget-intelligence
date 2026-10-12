"""Explicitly accept a byte-changed FY27 source with identical budget semantics.

The raw workbook hash and immutable budget identity are separate. An accepted
file never invalidates confirmed forecasts merely because columns were inserted.
Unknown file hashes still fail closed, and both extraction files are anchored.
"""

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from packages.source_snapshot.fy27_review import build_review, resolve_review_columns

DECISION = "ADOPT_EQUIVALENT_FY27_SOURCE"


def _typed(value: Any) -> Any:
    """Keep blank, zero, text, dash and negative values semantically distinct."""
    if value is None:
        return ["blank"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("FY27 来源含非有限数值")
        encoded = format(number, "f") if number else "0"
        if "." in encoded:
            encoded = encoded.rstrip("0").rstrip(".")
        return ["number", encoded]
    if isinstance(value, str):
        return ["text", value]
    if isinstance(value, list):
        return [_typed(item) for item in value]
    if isinstance(value, dict):
        return {key: _typed(item) for key, item in value.items()}
    raise ValueError("FY27 来源含不支持的数据类型")


def business_fingerprint(payload: dict[str, Any]) -> str:
    """Fingerprint business values, lookup match state, helper keys and parameters.

    Cell coordinates/formulas are kept in the extraction but excluded here so a
    verified column insertion can preserve the budget identity. Acceptance also
    pins exact extraction bytes and the manually reviewed raw workbook hash.
    """
    result = build_review(payload)
    business_rows = []
    for row in result["rows"]:
        business = {key: value for key, value in row.items() if key not in ("sources", "parameters")}
        business["parameters"] = {
            key: None if cell is None else {"matched": True, "value": cell["value"]}
            for key, cell in row["parameters"].items()
        }
        business_rows.append(business)
    columns = resolve_review_columns(payload["sheets"]["滚动表"]["rows"])
    row_numbers = {row["row"] for row in result["rows"]}
    helpers = [
        {
            "row": row["row"],
            "values": {
                key: row["cells"].get(col, {}).get("value") for key, col in columns["helpers"].items()
            },
        }
        for row in payload["sheets"]["滚动表"]["rows"]
        if row["row"] in row_numbers
    ]
    # Include all parameter rows, even unused/duplicate keys, in original order.
    parameters = [
        {"row": row["row"], "cells": {col: cell.get("value") for col, cell in row["cells"].items()}}
        for row in payload["sheets"]["参数表"]["rows"]
    ]
    content = {
        "fiscal_year": result["fiscal_year"],
        "rows": business_rows,
        "helpers": helpers,
        "parameters": parameters,
    }
    encoded = json.dumps(_typed(content), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    blob = path.read_bytes()
    payload = json.loads(blob)
    if not isinstance(payload, dict):
        raise ValueError("FY27 来源核对文件格式错误")
    return blob, payload


def read_current_review(source: Path, baseline: Path, manifest: Path) -> dict[str, Any]:
    """Load original or explicitly accepted source without rewriting any ledger."""
    try:
        old_blob, original = _read_json(baseline)
        raw_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        acceptance = None
        if manifest.exists():
            _, acceptance = _read_json(manifest)
            if (
                acceptance["schema_version"] != 1
                or acceptance["decision"] != DECISION
                or acceptance["baseline_sha256"] != original["sha256"]
                or acceptance["baseline_extraction_sha256"] != hashlib.sha256(old_blob).hexdigest()
                or business_fingerprint(original) != acceptance["business_sha256"]
                or not all(acceptance[key] for key in ("accepted_at", "accepted_by", "authorization_reason"))
            ):
                raise ValueError("FY27 原始基线或来源核对记录不匹配")
        if raw_hash == original["sha256"]:
            result = build_review(original)
            if hashlib.sha256(source.read_bytes()).hexdigest() != raw_hash:
                raise ValueError("FY27 Excel 在读取时变化，请重新读取")
            result["source_file_sha256"] = raw_hash
            return result
        if acceptance is None:
            raise ValueError("FY27 Excel 已变化，须重新进行只读提取与核对；未显示旧快照")
        if acceptance["source_sha256"] != raw_hash:
            raise ValueError("FY27 Excel 已变化或来源核对记录不匹配，未接受当前文件")
        filename = f"source-cells-{raw_hash}.json"
        if acceptance["extraction_filename"] != filename:
            raise ValueError("FY27 当前提取文件名不匹配")
        current_path = baseline.parent / filename
        if current_path.resolve().parent != baseline.parent.resolve():
            raise ValueError("FY27 当前提取必须在来源目录内")
        current_blob, current = _read_json(current_path)
        if (
            current["sha256"] != raw_hash
            or current["source_file"] != source.name
            or acceptance["extraction_sha256"] != hashlib.sha256(current_blob).hexdigest()
        ):
            raise ValueError("FY27 当前提取与原文件或核对记录不一致")
        expected = acceptance["business_sha256"]
        if business_fingerprint(original) != expected or business_fingerprint(current) != expected:
            raise ValueError("FY27 业务数据已变化，不能沿用已确认预测")
        result = build_review(current)
        result.update(
            {
                "sha256": original["sha256"],
                "source_file_sha256": raw_hash,
                "business_sha256": expected,
                "source_accepted_at": acceptance["accepted_at"],
            }
        )
        if hashlib.sha256(source.read_bytes()).hexdigest() != raw_hash:
            raise ValueError("FY27 Excel 在读取时变化，请重新读取")
        return result
    except FileNotFoundError:
        raise
    except (
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        InvalidOperation,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("FY27 来源提取或核对记录无效，须重新进行只读核对") from exc
