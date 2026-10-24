"""Browser-owned durable chats, separate from guest files and process lifetimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from packages.ai_agent.chat_title import clean_title
from packages.ai_agent.knowledge import (
    PROPOSAL_INSTRUCTIONS,
    KnowledgeDraft,
    KnowledgeError,
    KnowledgeStore,
    knowledge_prompt,
    memory_intent,
    proposal_from_answer,
    visible_answer,
)
from packages.ai_agent.knowledge_checks import check_answer
from packages.ai_agent.runtime import RuntimeCapacityError, RuntimeManager, Session

ID = re.compile(r"[a-f0-9]{32}\Z")
OWNER = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
BUSY = {"queued", "preparing", "running", "stopping"}
MAX_FILE = 8 * 1024 * 1024
CLEANUP_NOTICE = "回答内容已保留，但运行进程退出尚未确认，请重试停止。"


class ChatError(ValueError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def plain(value: Any, limit: int = 200_000) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)[:limit]


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return plain(content)
    if not isinstance(content, list):
        return ""
    return "\n".join(
        plain(block.get("text"))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )[:200_000]


def source_context(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ChatError("来源上下文无效")
    result = {key: value.get(key, "") for key in ("label", "scope", "href", "prompt")}
    if any(not isinstance(item, str) or len(item) > 16_384 for item in result.values()):
        raise ChatError("来源上下文过长或无效")
    link = urlsplit(result["href"])
    if (
        link.scheme
        or link.netloc
        or link.path not in {"/budget/cockpit", "/budget/workspace", "/budget/forecast", "/budget/next-year"}
        or "\\" in result["href"]
        or any(ord(c) < 32 for c in result["href"])
    ):
        raise ChatError("只能引用本产品的已保存预算页面")
    return result


class ChatStore:
    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise ChatError("对话存储目录无效")
        self.root = root.resolve()

    def _directory(self, owner: str, *, create: bool = False) -> Path:
        if not OWNER.fullmatch(owner):
            raise ChatError("浏览器身份无效")
        directory = self.root / hashlib.sha256(owner.encode()).hexdigest()
        if self.root.is_symlink() or directory.is_symlink():
            raise ChatError("对话存储目录无效")
        if create:
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.root.chmod(0o700)
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        return directory

    def known_owner(self, owner: str) -> bool:
        return bool(OWNER.fullmatch(owner)) and self._directory(owner).is_dir()

    def read(self, owner: str, identifier: str) -> dict[str, Any]:
        if not ID.fullmatch(identifier):
            raise ChatError("对话不存在或不属于当前浏览器")
        path = self._directory(owner) / f"{identifier}.json"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                    raise ChatError("对话记录无法读取，原记录仍保留")
                result: dict[str, Any] = json.load(handle)
            if result.get("id") != identifier:
                raise ChatError("对话记录无法读取，原记录仍保留")
            return result
        except (OSError, json.JSONDecodeError) as exc:
            raise ChatError("对话不存在或暂时无法读取，未改动原记录") from exc

    def save(self, owner: str, record: dict[str, Any]) -> None:
        directory = self._directory(owner, create=True)
        identifier = record["id"]
        if not ID.fullmatch(identifier):
            raise ChatError("对话标识无效")
        record["revision"] = record.get("revision", 0) + 1
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_FILE:
            raise ChatError("对话记录已达大小上限，请新建对话，原记录保留")
        temporary = directory / f".{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(directory / f"{identifier}.json")
        finally:
            temporary.unlink(missing_ok=True)

    def list(self, owner: str) -> list[dict[str, Any]]:
        directory = self._directory(owner)
        if not directory.exists():
            return []
        records = []
        for path in directory.glob("*.json"):
            if ID.fullmatch(path.stem):
                records.append(public_chat(self.read(owner, path.stem), detail=False))
        return sorted(records, key=lambda item: (item.get("pinned", False), item["updated_at"]), reverse=True)

    def create(self, owner: str, context: Any = None, identifier: str | None = None) -> dict[str, Any]:
        identifier = identifier or uuid.uuid4().hex
        if not ID.fullmatch(identifier):
            raise ChatError("对话标识无效")
        if (self._directory(owner) / f"{identifier}.json").exists():
            return self.read(owner, identifier)
        if len(self.list(owner)) >= 512:
            raise ChatError("本浏览器对话已达上限，已有记录仍可回看")
        record: dict[str, Any] = {
            "id": identifier,
            "title": "新对话",
            "title_source": "default",
            "created_at": now(),
            "updated_at": now(),
            "pinned": False,
            "archived": False,
            "state": "idle",
            "messages": [],
            "context": source_context(context),
            "workspace_id": None,
            "revision": 0,
        }
        self.save(owner, record)
        return record


def public_chat(record: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
    keys = ["id", "title", "created_at", "updated_at", "pinned", "archived", "state", "context", "revision"]
    result = {key: record.get(key) for key in keys}
    result["message_count"] = len(record["messages"])
    result["needs_stop"] = bool(record.get("cleanup_pending"))
    result["completion"] = record.get("completion")
    result["unread"] = bool(
        record.get("completion") and record["completion"]["id"] != record.get("seen_completion_id")
    )
    result["progress"] = record["messages"][-1].get("progress", "") if record["messages"] else ""
    result["queue_position"] = None
    result["queued_at"] = record.get("queued_at", record["updated_at"])
    if detail:
        result["messages"] = record["messages"]
    return result


class ChatService:
    def __init__(
        self,
        store: ChatStore,
        manager: Callable[[], Awaitable[RuntimeManager]],
        *,
        title_generator: Callable[[str, str], Awaitable[str | None]] | None = None,
    ) -> None:
        self.store, self.manager = store, manager
        self.knowledge = KnowledgeStore(store.root.parent / "knowledge")
        self.tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self.processes: dict[tuple[str, str], str] = {}
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.pending: deque[tuple[str, str]] = deque()
        self.dispatch = asyncio.Condition()
        self.closing = False
        self.title_generator = title_generator
        self.title_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self.title_slots = asyncio.Semaphore(2)

    def lock(self, owner: str, identifier: str) -> asyncio.Lock:
        return self.locks.setdefault((owner, identifier), asyncio.Lock())

    def read(self, owner: str, identifier: str, *, summarize_title: bool = False) -> dict[str, Any]:
        record = self.store.read(owner, identifier)
        # A restart must never leave a stale, permanent "running" indicator.
        task = self.tasks.get((owner, identifier))
        if record["state"] == "queued" and (task is None or task.done()) and not self.closing:
            # Only an accepted, never-submitted question is resumed. Running or
            # ambiguous provider requests are never replayed after a restart.
            self._enqueue(owner, record)
        elif record["state"] in BUSY and (task is None or task.done()):
            record["state"] = "interrupted"
            for message in record["messages"]:
                if message.get("state") in BUSY:
                    message["state"] = "interrupted"
            self._complete(record)
            self.store.save(owner, record)
        if summarize_title:
            self._schedule_title(owner, record)
        return record

    @staticmethod
    def _title_exchange(record: dict[str, Any]) -> tuple[str, str] | None:
        messages = record["messages"]
        if record.get("title_source") in {"manual", "ai"} or record.get("title_attempted"):
            return None
        if len(messages) < 2 or messages[1].get("state") != "complete":
            return None
        question, answer = messages[0]["text"], messages[1]["text"]
        legacy = re.sub(r"\s+", " ", question.strip())[:40]
        if record.get("title_source") != "default" and not (len(legacy) >= 24 and record["title"] == legacy):
            return None  # Unknown/custom legacy names are kept.
        return (question, answer) if question.strip() and answer.strip() else None

    def _schedule_title(self, owner: str, record: dict[str, Any]) -> None:
        key = (owner, record["id"])
        if (
            self.closing
            or self.title_generator is None
            or key in self.title_tasks
            or len(self.title_tasks) >= 16
            or not self._title_exchange(record)
        ):
            return
        self.title_tasks[key] = asyncio.create_task(self._summarize_title(owner, record["id"]))

    async def _summarize_title(self, owner: str, identifier: str) -> None:
        key = (owner, identifier)
        try:
            generator = self.title_generator
            if generator is None:
                return
            async with self.title_slots:
                async with self.lock(owner, identifier):
                    exchange = self._title_exchange(self.store.read(owner, identifier))
                if exchange is None:
                    return
                try:
                    async with asyncio.timeout(30):
                        title = clean_title(await generator(*exchange))
                except Exception:
                    title = None  # Naming must never turn a successful answer into an error.
                async with self.lock(owner, identifier):
                    current = self.store.read(owner, identifier)
                    if self._title_exchange(current) != exchange:
                        return  # Includes a manual rename while the model was answering.
                    current["title_attempted"] = True
                    if title:
                        current["title"], current["title_source"] = title, "ai"
                    self.store.save(owner, current)  # Do not reorder history or mark replies read.
        finally:
            self.title_tasks.pop(key, None)

    def present(self, owner: str, record: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
        result = public_chat(record, detail=detail)
        key = (owner, record["id"])
        if record["state"] == "queued" and key in self.pending:
            result["queue_position"] = self.pending.index(key) + 1
        return result

    def list(self, owner: str) -> list[dict[str, Any]]:
        items = self.store.list(owner)
        # Restore the original FIFO order of this browser's accepted queue.
        for item in sorted(items, key=lambda item: item["queued_at"]):
            if item["state"] == "queued":
                self.read(owner, item["id"])
        return [self.present(owner, self.read(owner, item["id"]), detail=False) for item in items]

    def _enqueue(self, owner: str, record: dict[str, Any]) -> None:
        key = (owner, record["id"])
        self.pending.append(key)
        self.tasks[key] = asyncio.create_task(self._run(owner, record["id"], record["messages"][-2]["text"]))

    async def _start_queued(self, owner: str, record: dict[str, Any], runtime: RuntimeManager) -> Session:
        key = (owner, record["id"])
        while True:
            # The FIFO head is stable until it starts or is cancelled. Never
            # hold the queue condition during slow Docker/model preparation:
            # cancelling a different queued conversation must remain immediate.
            if self.pending and self.pending[0] == key:
                try:
                    session = await runtime.start(mode="chat", workspace_id=record.get("workspace_id"))
                except RuntimeCapacityError:
                    pass  # Capacity rejection is before any provider call.
                else:
                    async with self.dispatch:
                        self.pending.remove(key)
                        self.dispatch.notify_all()
                    return session
            async with self.dispatch:
                try:
                    await asyncio.wait_for(self.dispatch.wait(), 1)
                except TimeoutError:
                    pass  # Also notice slots released by the legacy terminal.

    @staticmethod
    def _complete(record: dict[str, Any]) -> None:
        if not record["messages"]:
            return
        message = record["messages"][-1]
        record["completion"] = {"id": message["id"], "state": record["state"], "at": now()}
        if record["state"] == "cancelled":
            record["seen_completion_id"] = message["id"]

    async def acknowledge(self, owner: str, identifier: str, completion_id: str) -> dict[str, Any]:
        async with self.lock(owner, identifier):
            record = self.read(owner, identifier)
            if (record.get("completion") or {}).get("id") == completion_id:
                record["seen_completion_id"] = completion_id
                self.store.save(owner, record)
            return record

    async def update(self, owner: str, identifier: str, values: dict[str, Any]) -> dict[str, Any]:
        async with self.lock(owner, identifier):
            record = self.read(owner, identifier)
            if set(values) - {"title", "pinned", "archived"}:
                raise ChatError("不支持修改这些对话字段")
            if "title" in values:
                if not isinstance(values["title"], str) or not 1 <= len(values["title"].strip()) <= 80:
                    raise ChatError("对话名称请输入1至80个字")
                values["title"] = plain(values["title"].strip(), 80)
            if any(type(values[key]) is not bool for key in ("pinned", "archived") if key in values):
                raise ChatError("对话设置无效")
            if values.get("archived") and (record["state"] in BUSY or record.get("cleanup_pending")):
                raise ChatError("请先停止当前回答，再归档对话")
            record.update(values)
            if "title" in values:
                record["title_source"] = "manual"
            self.store.save(owner, record)
            return record

    async def send(self, owner: str, identifier: str, text: str, request_id: str) -> dict[str, Any]:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 32_000:
            raise ChatError("消息请输入1至32000个字")
        if not isinstance(request_id, str) or not ID.fullmatch(request_id):
            raise ChatError("消息标识无效，请重新发送")
        async with self.lock(owner, identifier):
            if self.closing:
                raise ChatError("对话服务正在关闭，请稍后重试")
            record = self.read(owner, identifier)
            duplicate = next((message for message in record["messages"] if message["id"] == request_id), None)
            if duplicate:
                if duplicate["text"] != text.strip():
                    raise ChatError("该消息标识已用于其他内容，请重新发送")
                return record  # A retried HTTP request never repeats a provider submission.
            if record["state"] in BUSY or record["archived"] or record.get("cleanup_pending"):
                raise ChatError("请等待本轮完成、停止回答或先恢复此对话")
            if len(record["messages"]) >= 500:
                raise ChatError("此对话较长，请新建对话；已有记录保留")
            if len(self.pending) >= 128 or sum(key[0] == owner for key in self.pending) >= 32:
                raise ChatError("等待处理的对话较多，请稍后再发送；已接收的任务仍会继续")
            record["messages"].append(
                {
                    "id": request_id,
                    "role": "user",
                    "text": text.strip(),
                    "created_at": now(),
                    "state": "complete",
                }
            )
            record["messages"].append(
                {
                    "id": uuid.uuid4().hex,
                    "role": "assistant",
                    "text": "",
                    "created_at": now(),
                    "state": "queued",
                    "activities": [],
                }
            )
            record["state"], record["updated_at"], record["completion"] = "queued", now(), None
            record["queued_at"] = record["updated_at"]
            self.store.save(owner, record)
            self._enqueue(owner, record)
            return record

    def _remember(
        self,
        owner: str,
        record: dict[str, Any],
        message: dict[str, Any],
        user: dict[str, Any],
        draft: KnowledgeDraft | None = None,
    ) -> dict[str, Any]:
        if draft is None:
            content = re.sub(
                r"[，。！!\s]*(?:请)?(?:记住这个口径|记住这一点|记住这个|记下来)[。！!\s]*$", "", user["text"]
            ).strip()
            content = re.sub(r"^(?:请)?(?:记住|记一下)[：:，\s]*", "", content)
            kind: Literal["preference", "business"] = (
                "preference"
                if re.search(r"先.{0,6}结论|回答.{0,10}(?:格式|简洁)|偏好|语气", content)
                else "business"
            )
            draft = KnowledgeDraft(title=content[:40], content=content, kind=kind)
        context = record.get("context") or {}
        item = self.knowledge.propose(
            owner,
            draft,
            {
                "conversation_id": record["id"],
                "message_id": user["id"],
                "answer_id": message["id"],
                "excerpt": user["text"][:1200],
                "label": context.get("label", "本次对话"),
                "href": context.get("href", ""),
            },
        )
        message["knowledge_proposal_id"] = item["id"]
        return item

    async def remember(self, owner: str, identifier: str, answer_id: str) -> dict[str, Any]:
        async with self.lock(owner, identifier):
            record = self.read(owner, identifier)
            if record["state"] in BUSY:
                raise ChatError("请等待本轮完成后，再整理这次纠正")
            index = next(
                (
                    index
                    for index, item in enumerate(record["messages"])
                    if item["id"] == answer_id and item["role"] == "assistant" and item["state"] == "complete"
                ),
                -1,
            )
            if index < 1 or record["messages"][index - 1]["role"] != "user":
                raise ChatError("只能从本浏览器已完成回答的用户原话整理知识")
            item = self._remember(owner, record, record["messages"][index], record["messages"][index - 1])
            self.store.save(owner, record)
            return item

    async def _run(self, owner: str, identifier: str, text: str) -> None:
        runtime: RuntimeManager | None = None
        session_id: str | None = None
        record = self.store.read(owner, identifier)
        message = record["messages"][-1]
        prefix, partial, last_save = "", "", 0.0
        proposal: KnowledgeDraft | None = None
        failed, settled = False, False
        try:
            runtime = await self.manager()
            session = await self._start_queued(owner, record, runtime)
            session_id = session.id
            self.processes[(owner, identifier)] = session_id
            async with self.lock(owner, identifier):
                record["workspace_id"] = session.workspace_id
                record["state"], message["state"] = "running", "running"
                self._save_current(owner, record)
            context = record.get("context")
            related_text = "\n".join(
                item["text"] for item in record["messages"][:-2][-6:] if item["role"] == "user"
            )
            snapshot = self.knowledge.snapshot(owner, text, context, related_text)
            message["knowledge"] = snapshot
            async with self.lock(owner, identifier):
                self._save_current(owner, record)
            prompt = (
                f"阅读上下文（仅为引用，须通过只读接口核对）：\n{context['prompt']}\n\n用户问题：\n{text}"
                if context
                else text
            )
            prompt = knowledge_prompt(snapshot) + "\n" + PROPOSAL_INSTRUCTIONS + "\n\n" + prompt
            response = await runtime.rpc_command(session_id, "prompt", message=prompt)
            if response.get("disposition") == "handled":
                raise ChatError("这条输入未产生回答，请使用普通问题而不是终端命令")
            async with asyncio.timeout(600):
                async for frame in runtime.output(session_id):
                    if frame["type"] == "gap":
                        raise ChatError("实时记录不完整，已保留收到的内容，未将其标为完整回答")
                    if frame["type"] in {"exit", "notice"}:
                        raise ChatError("本轮连接中断，已收到的内容保留；可以继续提问")
                    if frame["type"] != "rpc":
                        continue
                    event = frame["data"]
                    kind = event.get("type")
                    if kind == "message_start" and event.get("message", {}).get("role") == "assistant":
                        prefix = message["text"] + ("\n\n" if message["text"] else "")
                        partial = ""
                    elif kind == "message_update":
                        delta = event.get("assistantMessageEvent", {})
                        if delta.get("type") == "text_delta":
                            partial += plain(delta.get("delta"))
                            message["text"] = (prefix + visible_answer(partial))[:200_000]
                    elif kind == "message_end" and event.get("message", {}).get("role") == "assistant":
                        final = event["message"]
                        raw = content_text(final.get("content"))
                        proposal = proposal_from_answer(raw) or proposal
                        message["text"] = (prefix + visible_answer(raw))[:200_000]
                        failed = failed or final.get("stopReason") == "error"
                        message["model"] = plain(final.get("responseModel") or final.get("model"), 120)
                    elif kind == "tool_execution_start" and len(message["activities"]) < 200:
                        message["progress"] = "正在执行分析工具"
                        arguments = event.get("args") or {}
                        message["activities"].append(
                            {
                                "id": plain(event.get("toolCallId"), 120),
                                "tool": plain(event.get("toolName"), 60),
                                "summary": plain(
                                    arguments.get("command") or arguments.get("path") or "处理分析任务", 240
                                ),
                                "state": "running",
                                "output": "",
                            }
                        )
                    elif kind == "tool_execution_end":
                        message.pop("progress", None)
                        for activity in message["activities"]:
                            if activity["id"] == event.get("toolCallId"):
                                activity["state"] = "error" if event.get("isError") else "complete"
                                activity["output"] = content_text((event.get("result") or {}).get("content"))[
                                    :4000
                                ]
                    elif kind == "auto_retry_start":
                        message["progress"] = "连接暂不稳定，正在重试"
                    elif kind == "auto_compaction_start":
                        message["progress"] = "正在整理较长的上下文"
                    elif kind in {"auto_compaction_end", "auto_retry_end"}:
                        message.pop("progress", None)
                    elif kind == "agent_settled":
                        settled = True
                        record["state"] = (
                            "interrupted" if event.get("aborted") else "error" if failed else "idle"
                        )
                        message["state"] = (
                            "interrupted" if event.get("aborted") else "error" if failed else "complete"
                        )
                        if failed:
                            message["error"] = "本轮未完整完成，已有内容保留。请检查模型连接后继续提问。"
                        break
                    if time.monotonic() - last_save > 0.3 or kind in {
                        "message_end",
                        "tool_execution_start",
                        "tool_execution_end",
                        "auto_retry_start",
                        "auto_retry_end",
                        "auto_compaction_start",
                        "auto_compaction_end",
                    }:
                        async with self.lock(owner, identifier):
                            self._save_current(owner, record)
                        last_save = time.monotonic()
            if not settled:
                raise ChatError("本轮尚未完成，已保留收到的内容")
            if message["state"] == "complete":
                message["validation"] = check_answer(
                    message["text"], getattr(session, "_budget_evidence", [])
                )
            if message["state"] == "complete" and memory_intent(text):
                try:
                    self._remember(owner, record, message, record["messages"][-2], proposal)
                    message["knowledge_notice"] = "候选已暂存，尚未作为长期知识生效，请核对原话后确认。"
                except (KnowledgeError, ValueError):
                    message["knowledge_notice"] = (
                        "未保存长期知识。临时数据、权限或计算规则不适合作为记忆；回答仍保留。"
                    )
        except asyncio.CancelledError:
            if not (self.closing and record["state"] == "queued"):
                state = "cancelled" if record["state"] == "queued" else "interrupted"
                record["state"], message["state"] = state, state
        except KnowledgeError:
            record["state"], message["state"] = "error", "error"
            message["error"] = "知识记录暂时无法读取，未使用不完整口径生成回答。请打开知识面板重新读取。"
        except Exception:
            record["state"], message["state"] = "error", "error"
            message["error"] = "本轮未完成，已收到的内容保留；请检查运行环境后继续提问。"
        finally:
            if runtime and session_id:
                try:
                    await runtime.stop(session_id)
                except Exception:
                    record["cleanup_pending"] = session_id
                    record["state"] = "error"
                    message["error"] = CLEANUP_NOTICE
            for activity in message["activities"]:
                if activity["state"] == "running":
                    activity["state"] = "interrupted"
            message.pop("progress", None)
            if record["state"] not in BUSY:
                self._complete(record)
            async with self.lock(owner, identifier):
                self._save_current(owner, record)
            self.processes.pop((owner, identifier), None)
            self.tasks.pop((owner, identifier), None)
            async with self.dispatch:
                if (owner, identifier) in self.pending:
                    self.pending.remove((owner, identifier))
                self.dispatch.notify_all()

            self._schedule_title(owner, record)

    def _save_current(self, owner: str, record: dict[str, Any]) -> None:
        # Metadata edits during generation must survive the next stream update.
        current = self.store.read(owner, record["id"])
        record["revision"] = current["revision"]
        for key in ("title", "pinned", "archived"):
            record[key] = current[key]
        for key in ("title_source", "title_attempted"):
            if key in current:
                record[key] = current[key]
        record["seen_completion_id"] = current.get("seen_completion_id")
        if current["state"] == "stopping" and record["state"] in BUSY:
            record["state"] = "stopping"
            record["messages"][-1]["state"] = "stopping"
        record["updated_at"] = (
            now()
            if record["state"] != current["state"] or record["state"] not in BUSY
            else current["updated_at"]
        )
        self.store.save(owner, record)

    async def stop(self, owner: str, identifier: str) -> dict[str, Any]:
        async with self.lock(owner, identifier):
            record = self.store.read(owner, identifier)
            was_queued = record["state"] == "queued"
            if was_queued:
                record["state"], record["messages"][-1]["state"] = "cancelled", "cancelled"
                self._complete(record)
                self.store.save(owner, record)
            elif record["state"] in BUSY:
                record["state"], record["messages"][-1]["state"] = "stopping", "stopping"
                self.store.save(owner, record)
        task = self.tasks.get((owner, identifier))
        if task and not task.done():
            session_id = self.processes.get((owner, identifier))
            if was_queued:
                # A container may have been reserved, but no prompt has been
                # submitted while the persisted state is queued. Cancel the
                # producer, not merely an idle Pi stream before its next prompt.
                task.cancel()
            elif session_id:
                try:
                    await (await self.manager()).rpc_command(session_id, "abort")
                except Exception:
                    task.cancel()
            else:
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 35)
            except asyncio.CancelledError:
                # A stop may arrive before _run gets its first event-loop turn.
                # Only consume cancellation of that child, not request cancellation.
                if not task.cancelled():
                    raise
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with self.dispatch:
            if (owner, identifier) in self.pending:
                self.pending.remove((owner, identifier))
            self.dispatch.notify_all()
        if task and task.done():
            self.tasks.pop((owner, identifier), None)
        record = self.read(owner, identifier)
        if record.get("cleanup_pending"):
            try:
                await (await self.manager()).stop(record["cleanup_pending"])
            except Exception as exc:
                raise ChatError(CLEANUP_NOTICE) from exc
            record.pop("cleanup_pending", None)
            record["state"] = "interrupted"
            for message in record["messages"]:
                if message.get("error") == CLEANUP_NOTICE:
                    message.pop("error", None)
            self.store.save(owner, record)
        return self.read(owner, identifier)

    async def close(self) -> None:
        self.closing = True
        tasks = [*self.tasks.values(), *self.title_tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
