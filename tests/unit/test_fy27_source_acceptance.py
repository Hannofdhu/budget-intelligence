import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from packages.source_snapshot import fy27_review as review
from packages.source_snapshot import source_acceptance as acceptance


def cell(value: Any, formula: str | None = None) -> dict[str, Any]:
    return {"value": value, "formula": formula, "format": "General", "data_type": "n"}


def column_number(column: str) -> int:
    result = 0
    for char in column:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


@pytest.fixture
def legacy_payload() -> dict[str, Any]:
    headers: dict[str, Any] = {}
    values: dict[str, Any] = {}
    dimensions = {
        "track": "智慧空间",
        "product": "普通",
        "entry": "是",
        "grade": "七年级",
        "source_grade": "六年级",
        "subject": "数学",
        "campus": "测试教学区",
        "prior_students": "-",
        "opening": -0.01,
        "minimum": 2,
        "annual": 100,
    }
    for (field, labels), column in zip(
        review.BASE_HEADERS.items(), "A B C D E F G AE AG AH W".split(), strict=True
    ):
        headers[column] = cell(labels[0])
        values[column] = cell(dimensions[field])
    quarter = {
        "renewal": 0.6,
        "share": 0.25,
        "old": 0,
        "new": 2,
        "total": 2,
        "price": 100,
        "discount": None,
        "tuition": 90,
        "list_revenue": 200,
        "business": 180,
        "recognition": 0.8,
        "current": 20,
        "revenue": 25,
        "carryout": -0.01,
    }
    for season_labels, columns in zip(review.SEASON_HEADERS, review.COLUMNS, strict=True):
        for field, column in zip(review.FIELDS, columns.split(), strict=True):
            headers[column] = cell(season_labels[field][-1])
            formula = "=IF(ROUND(1,0),2,0)" if field == "new" else "=0" if field == "old" else None
            values[column] = cell(quarter[field], formula)
    for column, label in review.FIXED_HELPER_HEADERS.items():
        headers[column] = cell(label)
        values[column] = cell("entry-key" if column == "T" else "quarter-key")
    parameter_cells = {
        "F": cell("entry-key"),
        "G": cell(2),
        "M": cell("quarter-key"),
        "N": cell(100),
        "T": cell("quarter-key"),
        "U": cell(0.9),
        "AB": cell("quarter-key"),
        "AC": cell(0.25),
        "AM": cell("quarter-key"),
        "AN": cell(0.6),
        "AS": cell("quarter-key"),
        "AT": cell(0.8),
    }
    unused_parameters = copy.deepcopy(parameter_cells)
    for column in ("F", "M", "T", "AB", "AM", "AS"):
        unused_parameters[column]["value"] = "unused-key"
    return {
        "source_file": "source.xlsx",
        "sha256": hashlib.sha256(b"legacy source").hexdigest(),
        "extracted_at": "2026-10-08T00:00:00+00:00",
        "source_business_as_of": "2026-09-29",
        "sheets": {
            "滚动表": {
                "max_row": 2,
                "max_column": 102,
                "rows": [{"row": 1, "cells": headers}, {"row": 2, "cells": values}],
                "merged_ranges": [],
            },
            "参数表": {
                "max_row": 4,
                "max_column": 46,
                "rows": [{"row": 3, "cells": parameter_cells}, {"row": 4, "cells": unused_parameters}],
                "merged_ranges": [],
            },
        },
    }


def insert_income_columns(payload: dict[str, Any]) -> dict[str, Any]:
    shifted = copy.deepcopy(payload)
    sheet = shifted["sheets"]["滚动表"]
    for column in ("BZ", "CR"):
        at = column_number(column)
        for row in sheet["rows"]:
            row["cells"] = {
                column_letter(column_number(col) + (column_number(col) >= at)): value
                for col, value in row["cells"].items()
            }
            row["cells"][column] = cell(
                None if row["row"] == 1 else -999, None if row["row"] == 1 else "=1/0"
            )
        sheet["max_column"] += 1
    return shifted


def save_json(path: Path, payload: Any) -> bytes:
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(content)
    return content


@pytest.fixture
def adopted_files(tmp_path: Path, legacy_payload: dict[str, Any]) -> dict[str, Any]:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"current source with inserted blank columns")
    raw_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    baseline = tmp_path / "source-cells.json"
    baseline_bytes = save_json(baseline, legacy_payload)
    current_payload = insert_income_columns(legacy_payload)
    current_payload.update(sha256=raw_sha, extracted_at="2026-10-09T00:00:00+00:00")
    extraction = tmp_path / f"source-cells-{raw_sha}.json"
    extraction_bytes = save_json(extraction, current_payload)
    manifest_payload = {
        "schema_version": 1,
        "decision": "ADOPT_EQUIVALENT_FY27_SOURCE",
        "baseline_sha256": legacy_payload["sha256"],
        "source_sha256": raw_sha,
        "baseline_extraction_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "extraction_sha256": hashlib.sha256(extraction_bytes).hexdigest(),
        "extraction_filename": extraction.name,
        "business_sha256": acceptance.business_fingerprint(legacy_payload),
        "accepted_at": "2026-10-09T01:00:00+00:00",
        "accepted_by": "test-user",
        "authorization_reason": "已确认只接纳插入空白列且业务值完全一致的当前来源",
        "verification": {"scope_count": 1, "season_count": 4, "annual_revenue": "100"},
    }
    manifest = tmp_path / "accepted-source.json"
    save_json(manifest, manifest_payload)
    return {
        "source": source,
        "baseline": baseline,
        "extraction": extraction,
        "manifest": manifest,
        "legacy_payload": legacy_payload,
        "current_payload": current_payload,
        "manifest_payload": manifest_payload,
    }


def read_files(files: dict[str, Any]) -> dict[str, Any]:
    return acceptance.read_current_review(files["source"], files["baseline"], files["manifest"])


def test_direct_legacy_read_preserves_budget_identity(tmp_path: Path, legacy_payload: dict[str, Any]) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"legacy source")
    baseline = tmp_path / "source-cells.json"
    saved = save_json(baseline, legacy_payload)
    result = acceptance.read_current_review(source, baseline, tmp_path / "absent-manifest.json")
    assert result["sha256"] == legacy_payload["sha256"]
    assert result["source_file_sha256"] == legacy_payload["sha256"]
    assert result["scope_count"] == 1
    assert result["season_count"] == 4
    assert result["annual_revenue"] == "100"
    assert baseline.read_bytes() == saved


def test_adopted_insertions_preserve_business_and_show_current_source_coordinates(
    adopted_files: dict[str, Any],
) -> None:
    files = adopted_files
    before = {key: files[key].read_bytes() for key in ("source", "baseline", "extraction", "manifest")}
    assert acceptance.business_fingerprint(files["legacy_payload"]) == acceptance.business_fingerprint(
        files["current_payload"]
    )
    result = read_files(files)
    assert result["sha256"] == files["legacy_payload"]["sha256"]
    assert result["source_file_sha256"] == files["current_payload"]["sha256"]
    assert result["business_sha256"] == files["manifest_payload"]["business_sha256"]
    assert result["scope_count"] == 1
    assert result["season_count"] == 4
    assert result["annual_revenue"] == "100"
    winter = next(row for row in result["rows"] if row["season"] == "寒假")
    spring = next(row for row in result["rows"] if row["season"] == "春季")
    assert winter["sources"]["revenue"]["cell"] == "滚动表!CG2"
    assert spring["sources"]["revenue"]["cell"] == "滚动表!CY2"
    assert spring["sources"]["carryout"]["cell"] == "滚动表!CZ2"
    assert spring["sources"]["new"]["formula"] == "=IF(ROUND(1,0),2,0)"
    assert (spring["old"], spring["discount"], spring["carryout"], spring["prior_students"]) == (
        0,
        None,
        -0.01,
        "-",
    )
    assert spring["parameters"]["renewal"]["cell"] == "参数表!AN3"
    assert {key: files[key].read_bytes() for key in before} == before


def test_rejects_current_file_without_acceptance(adopted_files: dict[str, Any]) -> None:
    adopted_files["manifest"].unlink()
    with pytest.raises(ValueError):
        read_files(adopted_files)


@pytest.mark.parametrize("invalid", [b"not json", b"[]", b"null", b'{"schema_version": 1}'])
def test_rejects_malformed_acceptance(adopted_files: dict[str, Any], invalid: bytes) -> None:
    adopted_files["manifest"].write_bytes(invalid)
    with pytest.raises(ValueError):
        read_files(adopted_files)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_version", 2),
        ("decision", "SILENT_REBASE"),
        ("baseline_sha256", "0" * 64),
        ("source_sha256", "0" * 64),
        ("baseline_extraction_sha256", "0" * 64),
        ("extraction_sha256", "0" * 64),
        ("business_sha256", "0" * 64),
        ("accepted_at", ""),
        ("accepted_by", ""),
        ("authorization_reason", ""),
    ],
)
def test_rejects_mismatched_acceptance_fields(
    adopted_files: dict[str, Any], field: str, invalid: Any
) -> None:
    changed = {**adopted_files["manifest_payload"], field: invalid}
    save_json(adopted_files["manifest"], changed)
    with pytest.raises(ValueError):
        read_files(adopted_files)


@pytest.mark.parametrize(
    "filename", ["../source-cells.json", "/tmp/source-cells.json", "subdir/source-cells.json"]
)
def test_rejects_extraction_filename_traversal(adopted_files: dict[str, Any], filename: str) -> None:
    changed = {**adopted_files["manifest_payload"], "extraction_filename": filename}
    save_json(adopted_files["manifest"], changed)
    with pytest.raises(ValueError):
        read_files(adopted_files)


def test_rejects_extraction_symlink_outside_source_directory(adopted_files: dict[str, Any]) -> None:
    extraction = adopted_files["extraction"]
    external = extraction.parent / "outside" / "copy.json"
    external.parent.mkdir()
    external.write_bytes(extraction.read_bytes())
    extraction.unlink()
    extraction.symlink_to(external)
    with pytest.raises(ValueError):
        read_files(adopted_files)


def test_rejects_unknown_raw_workbook_after_acceptance(adopted_files: dict[str, Any]) -> None:
    adopted_files["source"].write_bytes(b"unaccepted newer workbook")
    with pytest.raises(ValueError):
        read_files(adopted_files)


@pytest.mark.parametrize("artifact", ["baseline", "extraction"])
def test_rejects_either_extraction_byte_tamper(adopted_files: dict[str, Any], artifact: str) -> None:
    path = adopted_files[artifact]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        read_files(adopted_files)


def test_restored_legacy_source_still_validates_accepted_baseline_bytes(
    adopted_files: dict[str, Any],
) -> None:
    adopted_files["source"].write_bytes(b"legacy source")
    result = read_files(adopted_files)
    assert result["source_file_sha256"] == adopted_files["legacy_payload"]["sha256"]
    baseline = adopted_files["baseline"]
    baseline.write_bytes(baseline.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="基线或来源核对记录不匹配"):
        read_files(adopted_files)


@pytest.mark.parametrize("restore_legacy", [False, True])
def test_rejects_source_changing_during_read(
    adopted_files: dict[str, Any], monkeypatch: pytest.MonkeyPatch, restore_legacy: bool
) -> None:
    source = adopted_files["source"]
    if restore_legacy:
        source.write_bytes(b"legacy source")
    read_bytes = Path.read_bytes
    source_reads = 0

    def changing_read(path: Path) -> bytes:
        nonlocal source_reads
        content = read_bytes(path)
        if path == source:
            source_reads += 1
            if source_reads == 1:
                path.write_bytes(b"workbook changed during review")
        return content

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    with pytest.raises(ValueError, match="读取时变化"):
        read_files(adopted_files)
    assert source_reads == 2


def test_rejects_unequal_business_even_when_current_hash_fields_are_forged(
    adopted_files: dict[str, Any],
) -> None:
    current = copy.deepcopy(adopted_files["current_payload"])
    current["sheets"]["滚动表"]["rows"][1]["cells"]["AL"]["value"] = 1
    content = save_json(adopted_files["extraction"], current)
    changed = {
        **adopted_files["manifest_payload"],
        "extraction_sha256": hashlib.sha256(content).hexdigest(),
        "business_sha256": acceptance.business_fingerprint(current),
    }
    save_json(adopted_files["manifest"], changed)
    with pytest.raises(ValueError):
        read_files(adopted_files)


@pytest.mark.parametrize(
    ("sheet", "row_index", "column", "changed_value"),
    [
        ("滚动表", 1, "P", "unused-key"),
        ("参数表", 1, "N", 101),
        ("滚动表", 1, "AP", 0),
        ("滚动表", 1, "AE", "0"),
        ("滚动表", 1, "AE", 0),
        ("滚动表", 1, "BC", "0"),
    ],
)
def test_fingerprint_distinguishes_helper_unused_parameter_and_typed_business_values(
    legacy_payload: dict[str, Any], sheet: str, row_index: int, column: str, changed_value: Any
) -> None:
    changed = copy.deepcopy(legacy_payload)
    changed["sheets"][sheet]["rows"][row_index]["cells"][column]["value"] = changed_value
    assert acceptance.business_fingerprint(changed) != acceptance.business_fingerprint(legacy_payload)


@pytest.mark.parametrize("operation", ["missing", "duplicate"])
@pytest.mark.parametrize("column", ["A", "W", "AE", "AG", "AH", "BZ", "CF", "CW"])
def test_fingerprint_rejects_missing_or_duplicate_business_headers(
    legacy_payload: dict[str, Any], operation: str, column: str
) -> None:
    changed = copy.deepcopy(legacy_payload)
    headers = changed["sheets"]["滚动表"]["rows"][0]["cells"]
    if operation == "missing":
        headers[column]["value"] = None
    else:
        headers["ZZ"] = copy.deepcopy(headers[column])
    with pytest.raises(ValueError):
        acceptance.business_fingerprint(changed)
