"""Reproducible desktop-only synthetic trial. Never starts against a business database."""

import argparse
import asyncio
import ipaddress
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATABASE = "budget_acceptance_local_trial"
DATABASE_URL = f"postgresql+asyncpg://budget:budget@127.0.0.1:54329/{DATABASE}"
RUNTIME = ROOT / "output/local-trial"
STATE = RUNTIME / "processes.json"
FIXTURE = RUNTIME / "fixture.json"
WEB = "http://127.0.0.1:5188"
API = "http://127.0.0.1:8018"
OPENER = build_opener(ProxyHandler({}))


def environment() -> dict[str, str]:
    return {
        **os.environ,
        "BUDGET_DATABASE_URL": DATABASE_URL,
        "BUDGET_ENVIRONMENT": "local",
        "BUDGET_AUTH_MODE": "dev_headers",
        "BUDGET_LOCAL_TRIAL_ENABLED": "true",
        "BUDGET_LOCAL_API_URL": API,
        "PYTHONPATH": str(ROOT),
    }


def run(*command: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def free_port(port: int) -> bool:
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", port)) != 0


def wait_url(url: str, seconds: int = 40) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with OPENER.open(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"服务未就绪：{url}，请检查 {RUNTIME} 中的日志")


async def seed() -> None:
    # Reuse the authoritative synthetic API fixture, never the real BI adapter.
    # This desktop package deliberately includes the dev/test runtime; not a production seeder.
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from apps.api.main import create_app
    from packages.config.settings import get_settings
    from packages.persistence.database import dispose_engine, get_session_factory
    from packages.persistence.models import AnnualBudget
    from tests.integration.test_opening_api import test_fy28_opening_and_headcount_api
    from tests.integration.test_rebalancing_real_journey import Journey

    if get_settings().database_url != DATABASE_URL or get_settings().environment != "local":
        raise RuntimeError("拒绝在非专用本机试用库初始化样例")
    async with get_session_factory()() as session:
        budget = await session.scalar(
            select(AnnualBudget)
            .where(
                AnnualBudget.department_id.like("opening-api-%"),
                AnnualBudget.fiscal_year == 2028,
            )
            .order_by(AnnualBudget.created_at.desc())
        )
    if budget is None:
        print("首次准备 FY28 合成样例：验证真实 API 链路，不访问 BI…", flush=True)
        await test_fy28_opening_and_headcount_api(1, False)
        async with get_session_factory()() as session:
            budget = await session.scalar(
                select(AnnualBudget)
                .where(
                    AnnualBudget.department_id.like("opening-api-%"),
                    AnnualBudget.fiscal_year == 2028,
                )
                .order_by(AnnualBudget.created_at.desc())
            )
        assert budget is not None
        # The regression ends with a deliberately stale opening. Prepare a usable fresh draft.
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            journey = Journey(
                client, budget.department_id, str(budget.current_draft_revision_id), budget.annual_budget_id
            )
            data = await journey.get(f"revisions/{journey.revision}/opening-students/workspace")
            source = next(s for s in data["sources"] if s["rows"])
            await journey.post(
                "/opening-students",
                {
                    "source_component_id": source["source_component_id"],
                    "source_fiscal_season": 20274,
                    "rows": [
                        {k: value for k, value in row.items() if k != "renewal_rate_ppm"}
                        for row in source["rows"]
                    ],
                    "zero_combination_ids": [
                        c["combination_id"] for c in data["combinations"] if c["grade"] == "七年级"
                    ],
                    "reason": "本机合成试用初始化：七年级明确无期初老生，来源仅为合成",
                },
            )
            job = await journey.post(":calculate", {})
            await journey.run_job(job["job_id"])
    FIXTURE.write_text(
        json.dumps(
            {
                "kind": "SYNTHETIC_LOCAL_TRIAL",
                "department_id": budget.department_id,
                "annual_budget_id": str(budget.annual_budget_id),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    await dispose_engine()


def lan_address() -> str:
    """Return a private LAN address suitable for a colleague-facing trial link."""
    for interface in ("en0", "en1"):
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            text=True,
            capture_output=True,
            check=False,
        )
        address = result.stdout.strip()
        if address:
            parsed = ipaddress.ip_address(address)
            if parsed.version == 4 and parsed.is_private and not parsed.is_loopback:
                return address
    raise RuntimeError("没有找到可用的局域网 IPv4 地址；请先连接与同事相同的网络")


def start(open_browser: bool, lan: bool = False) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    mode = "lan" if lan else "local"
    web_host = "0.0.0.0" if lan else "127.0.0.1"
    share_url = f"http://{lan_address()}:5188" if lan else WEB
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if (
            state.get("database") == DATABASE
            and state.get("mode", "local") == mode
            and all(process_matches(p) for p in state.get("processes", []))
            and len(state.get("processes", [])) == 3
        ):
            wait_url(API + "/api/v1/local-trial/session")
            print(f"{'局域网' if lan else '本机'}试用已运行：{share_url}")
            if open_browser:
                subprocess.run(["open", WEB], check=True)
            return
        stop()
    if not all(free_port(p) for p in (5188, 8018)):
        raise RuntimeError("本机试用端口 5188 或 8018 被其他程序占用；不会停止或覆盖其他服务")
    run("docker", "compose", "start", "postgres")
    for _ in range(40):
        try:
            run("docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "budget")
            break
        except subprocess.CalledProcessError:
            time.sleep(0.3)
    exists = run(
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "budget",
        "-d",
        "postgres",
        "-Atc",
        f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}'",
    )
    if exists != "1":
        run("docker", "compose", "exec", "-T", "postgres", "createdb", "-U", "budget", DATABASE)
    env = environment()
    print("检查本机试用数据库迁移…", flush=True)
    run(str(ROOT / ".venv/bin/alembic"), "upgrade", "head", env=env)
    run(str(ROOT / ".venv/bin/python"), str(Path(__file__)), "seed", env=env)
    fixture = json.loads(FIXTURE.read_text())
    env["BUDGET_LOCAL_TRIAL_DEPARTMENT_ID"] = fixture["department_id"]
    commands = [
        (
            "api",
            [str(ROOT / ".venv/bin/uvicorn"), "apps.api.main:app", "--host", "127.0.0.1", "--port", "8018"],
            "uvicorn apps.api.main:app --host 127.0.0.1 --port 8018",
        ),
        ("worker", [str(ROOT / ".venv/bin/python"), "-m", "apps.worker.main"], "-m apps.worker.main"),
        (
            "web",
            [
                "node",
                str(ROOT / "apps/web/node_modules/vite/bin/vite.js"),
                str(ROOT / "apps/web"),
                "--host",
                web_host,
                "--port",
                "5188",
                "--strictPort",
            ],
            f"vite/bin/vite.js {ROOT / 'apps/web'} --host {web_host}",
        ),
    ]
    processes = []
    try:
        for name, command, signature in commands:
            with (RUNTIME / f"{name}.log").open("a") as log:
                child = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            processes.append(
                {
                    "name": name,
                    "pid": child.pid,
                    "signature": signature,
                    "started": run("ps", "-p", str(child.pid), "-o", "lstart="),
                }
            )
            STATE.write_text(
                json.dumps({"database": DATABASE, "mode": mode, "processes": processes}, indent=2)
            )
        wait_url(API + "/api/v1/local-trial/session")
        wait_url(WEB)
    except Exception:
        stop()
        raise
    if lan:
        print(
            "局域网完整流程试用已启动："
            f"{share_url}\n把这个地址发给同一网络内的同事；仅合成数据，所有人共用同一试用身份和草稿。"
            f"\nAPI 与数据库仍仅限本机；日志 {RUNTIME}"
        )
    else:
        print(f"本机完整流程试用已启动：{WEB}\n仅合成数据；数据库 {DATABASE}；日志 {RUNTIME}")
    if open_browser:
        subprocess.run(["open", WEB], check=True)


def process_matches(process: dict[str, object]) -> bool:
    try:
        command = run("ps", "-p", str(process["pid"]), "-o", "command=")
        started = run("ps", "-p", str(process["pid"]), "-o", "lstart=")
        return str(process["signature"]) in command and started == process["started"]
    except subprocess.CalledProcessError:
        return False


def restart_api() -> None:
    """Reload API code without seeding, migrating, or altering existing budget data."""
    state = json.loads(STATE.read_text())
    if state.get("database") != DATABASE:
        raise RuntimeError("运行记录不匹配")
    process = next(p for p in state["processes"] if p["name"] == "api")
    if not process_matches(process):
        raise RuntimeError("API 进程身份不匹配，拒绝重启")
    env = environment()
    env["BUDGET_LOCAL_TRIAL_DEPARTMENT_ID"] = json.loads(FIXTURE.read_text())["department_id"]
    os.kill(int(process["pid"]), signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not free_port(8018):
        time.sleep(0.1)
    if not free_port(8018):
        raise RuntimeError("API 尚未退出，未启动新进程")
    with (RUNTIME / "api.log").open("a") as log:
        child = subprocess.Popen(
            [str(ROOT / ".venv/bin/uvicorn"), "apps.api.main:app", "--host", "127.0.0.1", "--port", "8018"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    process.update(pid=child.pid, started=run("ps", "-p", str(child.pid), "-o", "lstart="))
    STATE.write_text(json.dumps(state, indent=2))
    wait_url(API + "/api/v1/local-trial/session")
    print("API 已重启；未执行数据初始化或迁移")


def stop() -> None:
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    if state.get("database") != DATABASE:
        raise RuntimeError("运行记录不匹配，拒绝停止未知服务")
    for process in reversed(state["processes"]):
        if process_matches(process):
            os.kill(int(process["pid"]), signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(process_matches(p) for p in state["processes"]):
        time.sleep(0.2)
    STATE.unlink()
    print("已停止本机试用 API、Worker 和网页；试用数据与共享 PostgreSQL 容器保留。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "seed", "restart-api"])
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--lan", action="store_true", help="只将网页端口开放给当前局域网")
    args = parser.parse_args()
    try:
        if args.command == "seed":
            asyncio.run(seed())
        elif args.command == "stop":
            stop()
        elif args.command == "restart-api":
            restart_api()
        else:
            start(not args.no_open, lan=args.lan)
    except Exception as error:
        print(f"未完成：{error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
