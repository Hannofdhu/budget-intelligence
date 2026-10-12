"""Keep the CI gates strict, independent and honest about executed evidence."""

from pathlib import Path

import pytest


def _named_step(name: str) -> str:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    return workflow.split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]


@pytest.mark.parametrize(
    "name,command",
    [
        ("Lint Python", "uv run ruff check apps packages migrations tests"),
        ("Check Python formatting", "uv run ruff format --check apps packages migrations tests"),
        ("Typecheck Python including tests", "uv run mypy apps packages tests"),
        (
            "Run Python and PostgreSQL tests",
            "uv run pytest --junitxml=output/acceptance/pytest.xml -o junit_family=xunit1",
        ),
        ("Audit Python dependencies", "uv run pip-audit --timeout 60"),
        ("Build and typecheck Web", "npm run build"),
        ("Run Web tests", "npm test"),
        (
            "Audit Web dependencies",
            "npm audit --audit-level=high --registry=https://registry.npmjs.org",
        ),
        ("Validate documentation", "node analysis/validate_documentation.mjs"),
    ],
)
def test_ci_gates_run_independently_and_fail_closed(name: str, command: str) -> None:
    step = _named_step(name)
    assert f"run: {command}\n" in step
    assert "!cancelled()" in step
    assert ".outcome == 'success'" in step
    assert "continue-on-error:" not in step
    assert "|| true" not in step


@pytest.mark.parametrize(
    "name", ["Derive acceptance from executed tests", "Upload test and acceptance evidence"]
)
def test_ci_only_reports_acceptance_when_junit_exists(name: str) -> None:
    step = _named_step(name)
    assert "hashFiles('output/acceptance/pytest.xml') != ''" in step
    assert "continue-on-error:" not in step


def test_ci_keeps_strict_types_and_real_database_tests() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    settings = Path("pyproject.toml").read_text(encoding="utf-8").split("[tool.mypy]", 1)[1]
    assert 'BUDGET_RUN_DB_TESTS: "1"' in workflow
    assert "postgres:17-alpine" in workflow
    assert "steps.migrations.outcome == 'success'" in _named_step("Run Python and PostgreSQL tests")
    assert "strict = true" in settings
    assert "ignore_errors" not in settings
    assert 'module = ["tests' not in settings
