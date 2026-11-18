"""Offline smoke tests using synthetic data only; never reads product budgets."""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.metadata
import json
import os
import socket
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(output: Path, check_isolation: bool) -> dict[str, object]:
    import matplotlib
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
    from matplotlib import font_manager
    from matplotlib.ft2font import FT2Font
    from openpyxl import load_workbook
    from PIL import Image

    output.mkdir(parents=True, exist_ok=True)
    checks = []
    frame = pd.DataFrame(
        {
            "SKU": ["001", "002", "003"],
            "校区": ["测试甲", "测试乙", "测试甲"],
            "人次": [10, 20, 5],
            "模拟收入（元）": [100.25, 200.5, 50.25],
            "备注": ["=1+1", "https://example.invalid", "仅用于运行测试"],
        }
    )
    source = output / "模拟明细.xlsx"
    with pd.ExcelWriter(
        source,
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_formulas": False, "strings_to_urls": False}},
    ) as writer:
        frame.to_excel(writer, sheet_name="模拟明细", index=False)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    loaded = pd.read_excel(source, sheet_name="模拟明细", dtype={"SKU": str}, engine="openpyxl")
    pd.testing.assert_frame_equal(frame, loaded)
    check(np.isclose(loaded["模拟收入（元）"].sum(), 351), "Excel numeric values changed")
    workbook = load_workbook(source, read_only=True, data_only=False)
    try:
        check(workbook["模拟明细"]["E2"].data_type == "s", "Untrusted text became a formula")
    finally:
        workbook.close()
    check(hashlib.sha256(source.read_bytes()).hexdigest() == digest, "Reading modified source Excel")
    summary = loaded.groupby("校区", as_index=False)[["人次", "模拟收入（元）"]].sum()
    check(summary["人次"].sum() == 35, "Grouped enrolment total is incorrect")
    checks.append("xlsx_roundtrip_and_source_unchanged")

    report = output / "模拟汇总.xlsx"
    with pd.ExcelWriter(report, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="模拟汇总", index=False)
    workbook = load_workbook(report)
    workbook["模拟汇总"]["E1"] = "公式保留测试（不计算）"
    workbook["模拟汇总"]["E2"] = "=SUM(C2:C3)"
    workbook.save(report)
    workbook.close()
    workbook = load_workbook(report, read_only=True, data_only=False)
    try:
        check(workbook["模拟汇总"]["E2"].value == "=SUM(C2:C3)", "Excel formula was not preserved")
    finally:
        workbook.close()
    csv_file = output / "模拟汇总.csv"
    summary.to_csv(csv_file, index=False, encoding="utf-8-sig")
    pd.testing.assert_frame_equal(summary, pd.read_csv(csv_file, encoding="utf-8-sig"))
    checks.append("openpyxl_formula_preservation_and_csv")

    check(matplotlib.get_backend().lower() == "agg", "Plot backend must be headless Agg")
    font_path = font_manager.findfont(
        font_manager.FontProperties(family="Noto Sans CJK JP"), fallback_to_default=False
    )
    glyphs = FT2Font(font_path).get_charmap()
    title = "预算分析运行测试（模拟数据）"
    check(all(ord(char) in glyphs for char in title), "Chinese plotting font lacks required glyphs")
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=r"Glyph .* missing")
        figure, axis = plt.subplots(figsize=(6, 3.5))
        sns.barplot(data=summary, x="校区", y="模拟收入（元）", color="#d97757", ax=axis)
        axis.set_title(title)
        figure.tight_layout()
        for suffix in ("png", "svg", "pdf"):
            figure.savefig(output / f"中文分析图.{suffix}")
        plt.close(figure)
    with Image.open(output / "中文分析图.png") as image:
        check(image.width >= 600 and image.height >= 350, "PNG dimensions are incorrect")
        image.verify()
    check("<svg" in (output / "中文分析图.svg").read_text(), "SVG was not generated")
    check((output / "中文分析图.pdf").read_bytes().startswith(b"%PDF"), "PDF was not generated")
    checks.append("chinese_headless_png_svg_pdf")

    python_file = output / "script_test.py"
    python_file.write_text("import json\nprint(json.dumps({'result': sum([10, 20, 5])}))\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(python_file)], capture_output=True, text=True, check=True, timeout=10
    )
    check(json.loads(result.stdout) == {"result": 35}, "Python script failed")
    node_file = output / "script_test.mjs"
    node_file.write_text(
        "console.log(JSON.stringify({result: [10,20,5].reduce((a,b)=>a+b,0)}));\n", encoding="utf-8"
    )
    result = subprocess.run(["node", str(node_file)], capture_output=True, text=True, check=True, timeout=10)
    check(json.loads(result.stdout) == {"result": 35}, "Node script failed")
    result = subprocess.run(
        [sys.executable, "-c", "raise ValueError('synthetic failure')"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    check(result.returncode != 0 and "synthetic failure" in result.stderr, "Script errors were hidden")
    try:
        subprocess.run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            capture_output=True,
            timeout=0.2,
            check=True,
        )
    except subprocess.TimeoutExpired:
        pass
    else:
        raise RuntimeError("Script test timeout was not enforced")
    checks.append("python_node_errors_and_test_timeout")

    if check_isolation:
        check(os.geteuid() != 0, "Analysis must not run as root")
        # Some Docker kernels expose inactive tunl0/sit0 devices even with
        # network=none. Reject usable non-loopback interfaces, not their names.
        active_external = [
            name
            for _, name in socket.if_nameindex()
            if name != "lo" and int(Path(f"/sys/class/net/{name}/flags").read_text(), 16) & 1
        ]
        check(not active_external, "Unexpected active external network interface")
        with socket.socket() as probe:
            probe.settimeout(1)
            check(
                probe.connect_ex(("198.51.100.1", 443)) == errno.ENETUNREACH,
                "External network must be unreachable",
            )
        check(not Path("/var/run/docker.sock").exists(), "Docker socket must not be mounted")
        for protected in (Path("/etc/budget-analysis-test"), Path("/opt/budget-python/budget-analysis-test")):
            try:
                with protected.open("xb"):
                    pass
            except OSError:
                pass
            else:
                protected.unlink()
                raise RuntimeError("Analysis could write outside its workspace")
        checks.append("nonroot_network_none_readonly_root")
    return {
        "ok": True,
        "synthetic_data_only": True,
        "checks": checks,
        "python": sys.version.split()[0],
        "libraries": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "openpyxl", "xlsxwriter", "matplotlib", "seaborn")
        },
        "artifacts": sorted(path.name for path in output.iterdir()),
        "formula_note": (
            "Can preserve/write formulas; no Excel/LibreOffice formula calculation engine is installed."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check-isolation", action="store_true")
    args = parser.parse_args()
    directory = args.output_dir or Path(tempfile.mkdtemp(prefix="budget-analysis-smoke-"))
    print(json.dumps(run(directory, args.check_isolation), ensure_ascii=False))
