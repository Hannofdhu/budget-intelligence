"""Derive acceptance status from actual pytest JUnit results; never from trace labels."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_report(trace: dict[str, Any], junit: bytes) -> dict[str, Any]:
    root = ET.fromstring(junit)
    executions: list[dict[str, str]] = []
    for case in root.iter("testcase"):
        # pytest xunit1 preserves paths (including tests with class names).
        filename = case.get("file", "").replace("\\", "/")
        class_name = case.get("classname", "").removeprefix(filename.removesuffix(".py").replace("/", "."))
        node_id = filename + "::" + (class_name.strip(".").replace(".", "::") + "::" if class_name else "") + case.get("name", "")
        status = "PASSING"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "FAILED"
        elif case.find("skipped") is not None:
            status = "SKIPPED"
        executions.append({"node_id": node_id, "status": status})
    rows = []
    for requirement in [*trace["acceptance_cases"], *trace.get("closure_capabilities", [])]:
        bindings = []
        for expected in requirement["automated_tests"]:
            matches = [test for test in executions if test["node_id"] == expected or test["node_id"].split("[", 1)[0] == expected]
            statuses = {test["status"] for test in matches}
            status = "NOT_RUN" if not matches else "FAILED" if "FAILED" in statuses else "SKIPPED" if "SKIPPED" in statuses else "PASSING"
            bindings.append({"test": expected, "status": status, "executions": matches})
        passing = bool(bindings) and all(item["status"] == "PASSING" for item in bindings)
        rows.append({"id": requirement["id"], "status": "PASSING" if passing else "NOT_PASSING", "tests": bindings})
    suite_failures = sum(int(suite.get("failures", "0")) + int(suite.get("errors", "0")) for suite in root.iter("testsuite"))
    passing = bool(rows) and all(row["status"] == "PASSING" for row in rows) and suite_failures == 0
    return {
        "schema_version": "phase1-acceptance-execution-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": trace["scope"], "status": "PASSING" if passing else "NOT_PASSING",
        "junit_sha256": hashlib.sha256(junit).hexdigest(),
        "execution_count": len(executions), "suite_failures_and_errors": suite_failures,
        "test_suites": [dict(suite.attrib) for suite in root.iter("testsuite")],
        "passing_requirements": sum(row["status"] == "PASSING" for row in rows),
        "requirements": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--trace", type=Path, default=Path("tests/acceptance/traceability.yaml"))
    parser.add_argument("--output", type=Path, default=Path("output/acceptance/report.json"))
    args = parser.parse_args()
    trace_bytes = args.trace.read_bytes()
    report = build_report(json.loads(trace_bytes), args.junit.read_bytes())
    report["trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {report['passing_requirements']}/{len(report['requirements'])} requirements; {args.output}")
    raise SystemExit(0 if report["status"] == "PASSING" else 1)


if __name__ == "__main__":
    main()
