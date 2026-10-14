"""Local-only Pi terminal. The model/runtime never receives host credentials.

Development identity headers are NOT authentication for this surface. A trusted
loopback proxy, exact browser origin, HttpOnly session and one-use WS ticket are
all required. Budget access is a second, GET-only allowlist inside the backend.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from packages.ai_agent.bridge import BridgeHandler
from packages.ai_agent.config import ModelConfigError, load_model_config
from packages.ai_agent.runtime import RuntimeErrorSafe, RuntimeManager
from packages.config.settings import get_settings

router = APIRouter(prefix="/ai-agent", tags=["AI 产品助手"])
COOKIE = "budget_ai_session"
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient"}
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
RESOURCES = {
    "fy27-budget": "/historical-budgets/fy27",
    "fy27-forecast": "/forecasts/fy27/current",
    "fy27-forecast-versions": "/forecasts/fy27/current",
    "fy28-budget": "/next-year-budgets/fy28/current",
    "fy28-budget-versions": "/next-year-budgets/fy28/versions",
    "next-year-catalog": "/next-year-budgets/catalog",
}


def _session_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "state": item["status"],
        "created_at": datetime.fromtimestamp(item["created_at"], UTC).isoformat(),
    }


def _guard(connection: Request | WebSocket, *, mutation: bool = False) -> None:
    settings = get_settings()
    if not settings.ai_agent_enabled or settings.environment != "local":
        raise HTTPException(404, "AI 智解仅在启用的本机环境开放")
    origin = settings.ai_agent_browser_origin
    parsed = urlsplit(origin)
    # Never turn this development-only surface into a public remote shell.
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(503, "AI 本机访问配置无效")
    expected_host = parsed.netloc
    supplied_origin = connection.headers.get("origin")
    if (
        connection.client is None
        or connection.client.host not in LOOPBACK
        or connection.headers.get("x-budget-client-address") not in LOOPBACK - {"testclient"}
        or connection.headers.get("host") != expected_host
        or not settings.ai_agent_proxy_secret
        or not secrets.compare_digest(
            connection.headers.get("x-budget-ai-proxy", ""), settings.ai_agent_proxy_secret
        )
        or (supplied_origin is not None and supplied_origin != origin)
        or (mutation and supplied_origin != origin)
        or connection.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}
    ):
        raise HTTPException(403, "AI 智解仅允许从本机产品页面访问")


class AiService:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.runtime: RuntimeManager | None = None
        self.handler: BridgeHandler | None = None
        self.browsers: dict[str, float] = {}
        self.owners: dict[str, str] = {}
        self.tickets: dict[str, tuple[str, str, float]] = {}
        self.lock = asyncio.Lock()

    def browser(self, connection: Request | WebSocket) -> str:
        key = connection.cookies.get(COOKIE, "")
        if self.browsers.get(key, 0) < time.monotonic():
            raise HTTPException(401, "请重新打开 AI 智解页面")
        self.browsers[key] = time.monotonic() + 3600
        return key

    def own(self, connection: Request | WebSocket, session_id: str) -> str:
        key = self.browser(connection)
        if not SAFE_ID.fullmatch(session_id) or self.owners.get(session_id) != key:
            raise HTTPException(404, "会话不存在或不属于当前浏览器")
        return key

    async def manager(self) -> RuntimeManager:
        async with self.lock:
            if self.runtime is None:
                self.runtime = await asyncio.to_thread(self._build_manager)
        return self.runtime

    def _build_manager(self) -> RuntimeManager:
        settings = get_settings()
        config = load_model_config(Path(settings.ai_agent_model_config).expanduser())
        self.handler = BridgeHandler(config, self.read_budget)
        return RuntimeManager(
            runtime_root=Path(settings.ai_agent_runtime_root).resolve(),
            image=settings.ai_agent_image,
            model_config=config,
            handler=self.handler,
        )

    async def read_budget(self, resource: str) -> dict[str, Any]:
        """No client URL/method/identity is accepted; existing read guards still run."""
        if resource == "help":
            return {
                "role": "预算智解产品使用助手，帮助理解数据与使用产品，不修改产品源码或预算",
                "resources": list(RESOURCES),
                "version_resources": ["fy27-forecast-version:<id>", "fy28-budget-version:<id>"],
                "modules": {
                    "查看预算": "FY27 已定版预算，只读看板与透视；可筛选、复制表格、导出明细",
                    "达成预测": "使用已保存预测及来源快照回顾；更新、调整、确认在原产品中进行",
                    "下一年预算": "FY28 以已确认 FY27 预测为基础，经营目标由用户设置；五步拆解",
                },
                "rules": [
                    "预算、预测、下一年草稿是不同对象，不把预测收入当经营目标",
                    "以接口返回的版本、财年、状态、单位和时间为准，不编造缺失数据",
                    "面向用户，季度和全年预算、预测中的对应收入指标统一称为“结转收入”，保留原单位；"
                    "不要与业务收入（含税）、原价收入或结转比例混称",
                    "面向用户，将预算业务范围统一称为 SKU；内部字段、工具和数据键保持原样",
                    "不能修改预算、保存、自动调平、确认、发布或定版；指导用户在产品内完成",
                    "工具仅在隔离工作区运行，无产品源码、宿主机文件或任意网络权限",
                ],
            }
        path = RESOURCES.get(resource)
        if path is None:
            kind, separator, identifier = resource.partition(":")
            if not separator or not SAFE_ID.fullmatch(identifier):
                raise ValueError("不允许访问这个预算资源")
            if kind == "fy27-forecast-version":
                path = f"/forecasts/fy27/versions/{identifier}"
            elif kind == "fy28-budget-version":
                path = f"/next-year-budgets/fy28/versions/{identifier}"
            else:
                raise ValueError("不允许访问这个预算资源")
        settings = get_settings()
        headers = {
            "X-User-ID": "local-budget-owner",
            "X-Department-ID": settings.local_trial_department_id,
            "X-Roles": "DATA_MAINTAINER,DEPARTMENT_BUDGET_OWNER",
            "X-Budget-Client-Address": "127.0.0.1",
        }
        transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 0))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", timeout=60) as client:
            response = await client.get("/api/v1" + path, headers=headers)
        if response.status_code != 200:
            raise ValueError(f"预算资源暂不可读（{response.status_code}）；请在原模块检查数据状态")
        data: dict[str, Any] = response.json()
        if resource == "fy27-forecast-versions":
            return {"history": data.get("history", [])}
        return data


def _service(connection: Request | WebSocket) -> AiService:
    service = getattr(connection.app.state, "ai_agent_service", None)
    if service is None:
        service = AiService(connection.app)
        connection.app.state.ai_agent_service = service
    return service


async def close_ai_agent(app: FastAPI) -> None:
    service = getattr(app.state, "ai_agent_service", None)
    if service and service.runtime:
        await service.runtime.close()


@router.get("/status")
async def status(request: Request, response: Response) -> dict[str, Any]:
    _guard(request)
    response.headers["Cache-Control"] = "no-store"
    service = _service(request)
    now = time.monotonic()
    service.browsers = {key: expiry for key, expiry in service.browsers.items() if expiry > now}
    service.tickets = {key: item for key, item in service.tickets.items() if item[2] > now}
    key = request.cookies.get(COOKIE, "")
    if key not in service.browsers:
        if len(service.browsers) >= 64:
            raise HTTPException(429, "本机浏览器会话过多，请稍后重试")
        key = secrets.token_urlsafe(32)
    service.browsers[key] = now + 3600
    response.set_cookie(COOKIE, key, httponly=True, samesite="strict", max_age=3600, path="/api/v1/ai-agent")
    result: dict[str, Any] = {
        "available": False,
        "reason": "AI 运行环境未准备好",
        "models": [],
        "sessions": [],
        "isolation": "docker-network-none",
        "permissions": {"budget": "read-only", "source_files": "none"},
    }
    try:
        runtime = await service.manager()
        result.update(await runtime.status())
        assert service.handler is not None
        result.update(service.handler.config.public_status())
        result["last_actual_model"] = service.handler.last_actual_model
        result["sessions"] = [
            _session_view(item)
            for item in runtime.list_sessions()
            if service.owners.get(str(item["id"])) == key
        ]
    except (ModelConfigError, OSError, ValueError):
        result["reason"] = "请先准备本机 OpenClaw 模型配置和 Pi 隔离运行环境"
    return result


@router.post("/sessions", status_code=201)
async def start(request: Request) -> dict[str, Any]:
    _guard(request, mutation=True)
    service = _service(request)
    owner = service.browser(request)
    try:
        session = await (await service.manager()).start()
    except (ModelConfigError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(503, "Pi 暂时无法启动，请检查 Docker 和本机模型配置后重试") from exc
    service.owners[session.id] = owner
    return _session_view(session.to_dict())


@router.post("/sessions/{session_id}/attach-ticket")
async def attach_ticket(request: Request, session_id: str) -> dict[str, str]:
    _guard(request, mutation=True)
    service = _service(request)
    owner = service.own(request, session_id)
    # Bound unconsumed credentials; repeated reconnects cannot grow memory indefinitely.
    service.tickets = {key: value for key, value in service.tickets.items() if value[2] > time.monotonic()}
    if len(service.tickets) >= 128:
        raise HTTPException(429, "连接请求过多，请稍后重试")
    ticket = secrets.token_urlsafe(32)
    service.tickets[ticket] = (owner, session_id, time.monotonic() + 30)
    return {"ticket": ticket}


@router.delete("/sessions/{session_id}", status_code=204)
async def stop(request: Request, session_id: str) -> Response:
    _guard(request, mutation=True)
    service = _service(request)
    service.own(request, session_id)
    try:
        await (await service.manager()).stop(session_id)
    except RuntimeErrorSafe as exc:
        raise HTTPException(503, str(exc)) from exc
    service.owners.pop(session_id, None)
    return Response(status_code=204)


@router.websocket("/sessions/{session_id}/terminal")
async def terminal(websocket: WebSocket, session_id: str) -> None:
    try:
        _guard(websocket, mutation=True)
        service = _service(websocket)
        owner = service.own(websocket, session_id)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    tasks: set[asyncio.Task[None]] = set()
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), 10)
        if not isinstance(hello, dict) or hello.get("type") != "attach":
            await websocket.close(code=1008)
            return
        ticket = hello.get("ticket")
        entry = service.tickets.pop(ticket, None) if isinstance(ticket, str) else None
        after = hello.get("after", 0)
        if (
            entry is None
            or entry[:2] != (owner, session_id)
            or entry[2] < time.monotonic()
            or type(after) is not int
            or not 0 <= after <= 2**53
        ):
            await websocket.close(code=1008)
            return
        runtime = await service.manager()
        selected = runtime.get(session_id)
        if selected.status != "running":
            state: dict[str, Any] = {
                "type": "state",
                "state": "failed" if selected.status == "cleanup_failed" else "stopped",
            }
            if selected.cleanup_error:
                state["reason"] = selected.cleanup_error
            await websocket.send_json(state)
            return

        async def output() -> None:
            async for event in runtime.output(session_id, after):
                if event["type"] == "exit":
                    await websocket.send_json({"type": "state", "state": "stopped"})
                    await websocket.close(code=1000)
                    return
                if event["type"] == "notice":
                    await websocket.send_json(
                        {
                            "type": "state",
                            "state": "failed",
                            "reason": event.get("message", "AI 会话已断开"),
                        }
                    )
                    return
                await websocket.send_json(event)

        async def input_loop() -> None:
            while True:
                message = await asyncio.wait_for(websocket.receive_json(), 1800)
                if not isinstance(message, dict):
                    return
                if message.get("type") == "input":
                    data = message.get("data")
                    if not isinstance(data, str) or len(data.encode("utf-8")) > 65536:
                        return
                    await runtime.write(session_id, data)
                elif message.get("type") == "resize":
                    cols, rows = message.get("cols"), message.get("rows")
                    if (
                        type(cols) is not int
                        or type(rows) is not int
                        or not (20 <= cols <= 400 and 5 <= rows <= 160)
                    ):
                        return
                    await runtime.resize(session_id, cols, rows)
                else:
                    return

        await websocket.send_json({"type": "state", "state": "running"})
        tasks = {asyncio.create_task(output()), asyncio.create_task(input_loop())}
        # An exited/failed output channel must not leave a fake connected socket
        # waiting for the next keystroke (or the 30-minute input timeout).
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, TimeoutError, ValueError, KeyError, RuntimeError, OSError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(RuntimeError):
            await websocket.close(code=1000)
