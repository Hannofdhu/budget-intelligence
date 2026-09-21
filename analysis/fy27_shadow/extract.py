"""Read original OOXML caches and formulas, without executing or saving the workbook."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "【智慧学习部】FY27预算拆解（1.13亿）-备份.xlsx"
DESTINATION = ROOT / "output/fy27-shadow"


def main() -> None:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    formulas = openpyxl.load_workbook(SOURCE, data_only=False, read_only=False)
    cached = openpyxl.load_workbook(SOURCE, data_only=True, read_only=False)
    payload = {"source_file": SOURCE.name, "sha256": digest,
               "extracted_at": datetime.now(UTC).isoformat(),
               "document_modified_at": str(formulas.properties.modified),
               "source_business_as_of": None, "sheets": {}}
    for sheet in formulas:
        rows = []
        for row in sheet:
            populated = {}
            for cell in row:
                if cell.value is None:
                    continue
                formula = cell.value if cell.data_type == "f" else None
                populated[cell.column_letter] = {
                    "value": cached[sheet.title][cell.coordinate].value,
                    "formula": formula, "format": cell.number_format,
                    "data_type": cached[sheet.title][cell.coordinate].data_type,
                }
            if populated:
                rows.append({"row": row[0].row, "cells": populated})
        payload["sheets"][sheet.title] = {"max_row": sheet.max_row, "max_column": sheet.max_column,
                                         "rows": rows, "merged_ranges": [str(r) for r in sheet.merged_cells.ranges]}
        print(sheet.title, sheet.max_row, sheet.max_column, len(rows))
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "source-cells.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str)+'\n',encoding='utf-8')
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == digest
    print('Read-only extraction complete; source SHA-256 unchanged:', digest)


if __name__ == '__main__':
    main()
