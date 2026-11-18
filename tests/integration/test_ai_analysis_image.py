"""Opt-in real container tests; no model credentials, product data or database."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from packages.ai_agent.bridge import BridgeHandler
from packages.ai_agent.config import ModelConfig
from packages.ai_agent.runtime import RuntimeManager, Session

IMAGE = os.environ.get("BUDGET_AI_ANALYSIS_IMAGE", "")
pytestmark = pytest.mark.skipif(
    not IMAGE, reason="Set BUDGET_AI_ANALYSIS_IMAGE to test an already-built image"
)


def command(*args: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, result.stderr + result.stdout
    return result


async def test_analysis_scripts_excel_plots_and_existing_isolation(tmp_path: Path) -> None:
    reads = []

    async def synthetic_reader(resource: str) -> dict[str, Any]:
        reads.append(resource)
        if resource == "help":
            return {"role": "synthetic-help", "resources": ["help"]}
        assert resource == "fy28-budget"
        return {"synthetic_only": True, "saved_value": 351}

    config = ModelConfig({}, "", ())
    manager = RuntimeManager(tmp_path / "runtime", IMAGE, config, BridgeHandler(config, synthetic_reader))
    session = Session(uuid.uuid4().hex, manager.root / "session")
    session._container = f"budget-ai-analysis-test-{uuid.uuid4().hex[:12]}"
    manager._sessions[session.id] = session
    # Use the exact production container arguments, not a weaker test-only sandbox.
    workspace = tmp_path / "workspace"
    agent = tmp_path / "agent"
    for directory in (workspace, agent):
        directory.mkdir(mode=0o777)
        directory.chmod(0o777)
    args = manager._container_args(session, workspace, agent)
    try:
        command(*args)
        result = command(
            "exec",
            session._container,
            "python",
            "/opt/budget-ai/analysis_smoke.py",
            "--check-isolation",
            "--output-dir",
            "/workspace/proof",
        )
        report = json.loads(result.stdout)
        assert report["ok"] is True and report["synthetic_data_only"] is True
        assert report["checks"] == [
            "xlsx_roundtrip_and_source_unchanged",
            "openpyxl_formula_preservation_and_csv",
            "chinese_headless_png_svg_pdf",
            "python_node_errors_and_test_timeout",
            "nonroot_network_none_readonly_root",
        ]
        assert (workspace / "proof/模拟明细.xlsx").is_file()
        assert (workspace / "proof/中文分析图.png").is_file()
        assert command("exec", session._container, "pi", "--version").stdout.strip() == "1.2.0"
        help_data = json.loads(command("exec", session._container, "budget-api", "help").stdout)
        assert "pandas" in help_data["analysis_environment"]["libraries"]
        details = json.loads(command("inspect", session._container).stdout)[0]
        assert details["HostConfig"]["NetworkMode"] == "none"
        assert details["HostConfig"]["ReadonlyRootfs"] is True
        assert details["HostConfig"]["Memory"] == 1024**3
        assert details["HostConfig"]["NanoCpus"] == 2_000_000_000
        assert details["Config"]["User"] == "node"
        assert {mount["Destination"] for mount in details["Mounts"] if mount["Type"] == "bind"} == {
            "/workspace",
            "/agent",
        }
        assert "/tmp" in details["HostConfig"]["Tmpfs"]
        await manager._start_relay(session)
        result = await asyncio.to_thread(command, "exec", session._container, "budget-api", "get", "help")
        product_help = json.loads(result.stdout)
        assert product_help["role"] == "synthetic-help"
        assert product_help["analysis_environment"] == help_data["analysis_environment"]
        result = await asyncio.to_thread(
            command, "exec", session._container, "budget-api", "get", "fy28-budget"
        )
        assert json.loads(result.stdout) == {"synthetic_only": True, "saved_value": 351}
        denied = await asyncio.to_thread(
            command,
            "exec",
            session._container,
            "curl",
            "--silent",
            "--output",
            "/dev/null",
            "--write-out",
            "%{http_code}",
            "--request",
            "POST",
            "http://127.0.0.1:4081/budget/fy28-budget",
        )
        assert denied.stdout == "405"
        assert reads == ["help", "fy28-budget"]
    finally:
        await manager.close()
