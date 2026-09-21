from typing import Any

import pytest

from analysis.report_acceptance import build_report


@pytest.mark.parametrize(
    "outcome,expected",
    [("", "PASSING"), ("<failure />", "FAILED"), ("<error />", "FAILED"), ("<skipped />", "SKIPPED")],
)
def test_acceptance_uses_execution_not_handwritten_status(outcome: str, expected: str) -> None:
    trace: dict[str, Any] = {
        "scope": "TEST",
        "acceptance_cases": [
            {
                "id": "TEST",
                "status": "PASSING",
                "automated_tests": ["tests/test_demo.py::test_value"],
            }
        ],
    }
    xml = (
        '<testsuites><testsuite><testcase file="tests/test_demo.py" '
        f'classname="tests.test_demo" name="test_value[1]">{outcome}</testcase></testsuite></testsuites>'
    )
    report = build_report(trace, xml.encode())
    assert report["requirements"][0]["tests"][0]["status"] == expected
    assert (report["status"] == "PASSING") is (expected == "PASSING")


def test_missing_parameterized_failure_and_collection_errors_fail_closed() -> None:
    trace = {
        "scope": "TEST",
        "acceptance_cases": [
            {
                "id": "TEST",
                "automated_tests": ["tests/test_demo.py::test_value"],
            }
        ],
    }
    missing = build_report(trace, b"<testsuites><testsuite /></testsuites>")
    assert missing["requirements"][0]["tests"][0]["status"] == "NOT_RUN"
    assert missing["status"] == "NOT_PASSING"
    cases = (
        '<testcase file="tests/test_demo.py" classname="tests.test_demo" name="test_value[1]" />'
        '<testcase file="tests/test_demo.py" classname="tests.test_demo" name="test_value[2]">'
        "<failure /></testcase>"
    )
    assert (
        build_report(trace, f"<testsuites><testsuite>{cases}</testsuite></testsuites>".encode())["status"]
        == "NOT_PASSING"
    )
    collection = (
        '<testsuites><testsuite errors="1"><testcase file="tests/test_demo.py" '
        'classname="tests.test_demo" name="test_value" /></testsuite></testsuites>'
    )
    assert build_report(trace, collection.encode())["status"] == "NOT_PASSING"
