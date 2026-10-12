"""Explicit source adoption, leaving Excel, legacy snapshot and ledgers immutable.

Use the bundled Python with openpyxl. Default is read-only; --apply persists a
separate extraction and acceptance record after equivalence verification.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analysis.fy27_shadow.extract import extract_workbook
from packages.source_snapshot.fy27_review import EXTRACTION, SOURCE, build_review
from packages.source_snapshot.source_acceptance import DECISION, business_fingerprint, read_current_review


def encode(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    args = parser.parse_args()
    if args.apply and (not args.actor or not args.reason):
        parser.error("--apply requires --actor and --reason recording explicit authorization")

    original_bytes = EXTRACTION.read_bytes()
    original = json.loads(original_bytes)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    current_path = EXTRACTION.parent / f"source-cells-{source_hash}.json"
    current = extract_workbook(SOURCE)
    if current_path.exists():
        saved = json.loads(current_path.read_bytes())
        if saved["sheets"] != current["sheets"] or saved["sha256"] != source_hash:
            raise ValueError("已存在的当前提取与本次重新读取不一致，未接受该文件")
        current = saved
    if current["sha256"] != source_hash or current["source_file"] != SOURCE.name:
        raise ValueError("当前提取与原文件不一致")
    previous_fingerprint = business_fingerprint(original)
    current_fingerprint = business_fingerprint(current)
    if current_fingerprint != previous_fingerprint:
        raise ValueError("FY27 业务数据或参数变化，不能沿用已确认预测；未写入任何核对记录")
    result = build_review(current)
    print(json.dumps({
        "status": "EQUIVALENT", "apply": args.apply,
        "source_sha256": source_hash, "baseline_sha256": original["sha256"],
        "business_sha256": current_fingerprint,
        "scope_count": result["scope_count"], "season_count": result["season_count"],
        "annual_revenue": result["annual_revenue"], "entry_total": result["entry_total"],
    }, ensure_ascii=False, indent=2))
    if not args.apply:
        return
    manifest = EXTRACTION.parent / "accepted-source.json"
    if manifest.exists():
        # Replay the same accepted raw file without replacing its audit record.
        read_current_review(SOURCE, EXTRACTION, manifest)
        print("Already accepted; no files changed")
        return
    current_bytes = current_path.read_bytes() if current_path.exists() else encode(current)
    acceptance = {
        "schema_version": 1, "decision": DECISION,
        "baseline_sha256": original["sha256"], "source_sha256": source_hash,
        "baseline_extraction_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "extraction_sha256": hashlib.sha256(current_bytes).hexdigest(),
        "extraction_filename": current_path.name, "business_sha256": current_fingerprint,
        "accepted_at": datetime.now(UTC).isoformat(), "accepted_by": args.actor,
        "authorization_reason": args.reason,
        "verification": {
            "scope_count": result["scope_count"], "season_count": result["season_count"],
            "annual_revenue": result["annual_revenue"], "entry_total": result["entry_total"],
            "method": "exact business values, helper keys, complete parameter sheet; raw file pinned",
        },
    }
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != source_hash or EXTRACTION.read_bytes() != original_bytes:
        raise ValueError("核对时来源变化，未保存核对记录")
    if not current_path.exists():
        with current_path.open("xb") as output:
            output.write(current_bytes)
    # Write a complete manifest then link exclusively; never replace an existing
    # acceptance record or leave a partial JSON as an accepted source.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=EXTRACTION.parent, prefix=".acceptance-", delete=False) as output:
            temporary = Path(output.name)
            output.write(encode(acceptance))
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, manifest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    read_current_review(SOURCE, EXTRACTION, manifest)
    print("Accepted current Excel; original extraction and all ledgers unchanged")


if __name__ == "__main__":
    main()
