"""Environment contract checks require no Docker, model or budget data."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "apps/ai-agent"


def test_analysis_requirements_are_complete_pinned_and_hashed() -> None:
    requirements = (ASSETS / "analysis-requirements.txt").read_text()
    entries = re.split(r"\n(?=[a-zA-Z])", requirements)
    packages = {}
    for entry in entries:
        if entry.startswith("#"):
            continue
        match = re.match(r"([\w-]+)==([^\s\\]+)", entry)
        assert match, entry
        assert re.search(r"--hash=sha256:[a-f0-9]{64}", entry)
        packages[match[1]] = match[2]
    assert {"numpy", "pandas", "openpyxl", "xlsxwriter", "matplotlib", "seaborn"} <= packages.keys()
    assert not re.search(
        r"https?://|git\+|file:",
        "\n".join(line for line in requirements.splitlines() if not line.startswith("#")),
    )


def test_image_uses_readonly_analysis_venv_and_writable_temporary_plot_cache() -> None:
    dockerfile = (ASSETS / "Dockerfile").read_text()
    for required in (
        "python3-venv",
        "fonts-noto-cjk",
        "python3 -m venv /opt/budget-python",
        "--only-binary=:all:",
        "--require-hashes -r analysis-requirements.txt",
        'PATH="/opt/budget-python/bin:${PATH}"',
        "MPLBACKEND=Agg",
        "MPLCONFIGDIR=/tmp/budget-matplotlib",
        "PYTHONDONTWRITEBYTECODE=1",
        "OPENBLAS_NUM_THREADS=1",
        "USER node",
        "WORKDIR /workspace",
    ):
        assert required in dockerfile
    assert "pip install" not in (ASSETS / "launch.mjs").read_text()
    assert "pip install" not in (ASSETS / "relay.mjs").read_text()
    rc = (ASSETS / "matplotlibrc").read_text()
    assert "Noto Sans CJK JP" in rc and "backend: Agg" in rc


def test_tool_help_exposes_analysis_capability_without_network_or_budget_mutation() -> None:
    result = subprocess.run(
        ["node", str(ASSETS / "budget-api.mjs"), "help"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    help_data = json.loads(result.stdout)
    assert help_data["analysis_environment"]["libraries"] == [
        "numpy",
        "pandas",
        "openpyxl",
        "xlsxwriter",
        "matplotlib",
        "seaborn",
    ]
    assert "no Excel/LibreOffice formula calculation engine" in help_data["analysis_environment"]["excel"]
    assert "read-only" in help_data["analysis_environment"]["boundary"]


def test_ci_runs_the_real_isolated_analysis_image_test() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "BUDGET_AI_ANALYSIS_IMAGE=budget-pi-agent:ci" in workflow
    assert "tests/integration/test_ai_analysis_image.py" in workflow
