"""Follow-up analysis retains provenance only for unchanged, actually referenced files."""

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from packages.ai_agent.chat import public_chat
from packages.ai_agent.knowledge_checks import BudgetEvidence, check_answer
from packages.ai_agent.snapshots import matching_snapshots, used_snapshots
from tests.unit.test_ai_chat import MESSAGE_ID, OTHER, OWNER, service

WORKSPACE = "d" * 32
BODY = json.dumps(
    {"draft": {"sequence": 30, "status": "DRAFT"}, "rows": [{"campus": "校区甲", "track": "赛道乙"}]}
).encode()


def evidence() -> dict[str, Any]:
    collector = BudgetEvidence("/budget/fy28-budget")
    collector.feed({"type": "response_start", "status": 200})
    collector.feed({"type": "response_chunk", "data": base64.b64encode(BODY).decode()})
    item = collector.feed({"type": "response_end"})
    assert item
    return item


def events(command: str, *, failed: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_execution_start",
            "toolCallId": "read",
            "toolName": "bash",
            "args": {"command": command},
        },
        {"type": "tool_execution_end", "toolCallId": "read", "isError": failed},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "结转收入100元"}],
            },
        },
        {"type": "agent_settled", "aborted": False},
    ]


def test_only_matching_safe_files_and_successful_references_count(tmp_path: Path) -> None:
    folder = tmp_path / WORKSPACE / "workspace"
    folder.mkdir(parents=True)
    file = folder / "预算.json"
    file.write_bytes(BODY + b"\n")
    original = evidence()
    before = matching_snapshots(tmp_path, WORKSPACE, [original])
    assert before[0]["label"] == "FY28预算编制 · 第30版（DRAFT）"
    command = {"summary": "python -c \"open('/workspace/预算.json').read()\"", "state": "complete"}
    sources = used_snapshots(before, before, [command])
    result = check_answer("结转收入100元", [], sources)
    assert result["state"] == "limited" and not result["resources"]
    assert result["snapshots"][0]["files"] == ["预算.json"]
    assert check_answer("收入100元", [], sources, require_fresh=True)["state"] == "needs_review"
    assert check_answer("收入100元", [original], sources, require_fresh=True)["snapshots"] == []
    assert check_answer("| 校区 |\n|---|\n| 赛道乙 |", [], sources)["state"] == "needs_review"
    for activity in ([], [{**command, "state": "error"}], [{**command, "summary": "open('other预算.json')"}]):
        assert used_snapshots(before, before, activity) == []
    file.write_bytes(BODY.replace(b"30", b"31"))
    assert matching_snapshots(tmp_path, WORKSPACE, [original]) == []
    assert used_snapshots(before, [], [command]) == []
    # Reordering/pretty-printing the same JSON is common after an API restart.
    file.write_text(json.dumps(json.loads(BODY), sort_keys=True, indent=2))
    assert matching_snapshots(tmp_path, WORKSPACE, [original])[0]["sha256"] == original["sha256"]
    file.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(BODY)
    file.symlink_to(outside)
    assert matching_snapshots(tmp_path, WORKSPACE, [original]) == []
    assert matching_snapshots(tmp_path, "a" * 32, [original]) == []
    assert matching_snapshots(tmp_path, "../outside", [original]) == []


@pytest.mark.parametrize(
    "change,expected",
    [
        ("reuse", "limited"),
        ("tamper", "needs_review"),
        ("failed", "needs_review"),
        ("unread", "needs_review"),
        ("latest", "needs_review"),
    ],
)
async def test_followup_survives_service_restart_without_trusting_history_alone(
    tmp_path: Path, change: str, expected: str
) -> None:
    chats, runtime = service(tmp_path)
    folder = tmp_path / WORKSPACE / "workspace"
    folder.mkdir(parents=True)
    file = folder / "budget.json"
    file.write_bytes(BODY)
    start = runtime.start

    async def first_start(**values: Any) -> Any:
        session = await start(**values)
        session._budget_evidence = [evidence()]
        return session

    runtime.start = first_start  # type: ignore[method-assign]
    runtime.events = events("budget-api get fy28-budget --output budget.json")
    record = chats.store.create(OWNER)
    await chats.send(OWNER, record["id"], "分析预算", MESSAGE_ID)
    await chats.tasks[(OWNER, record["id"])]
    saved = chats.read(OWNER, record["id"])
    assert saved["messages"][-1]["validation"]["state"] == "limited"
    assert saved["budget_evidence"][0]["sha256"] == evidence()["sha256"]
    assert "budget_evidence" not in public_chat(saved)

    # A new service/runtime must recover provenance from this owner's chat, not process memory.
    chats, runtime = service(tmp_path)
    if change == "tamper":
        file.write_bytes(BODY.replace(b"30", b"31"))
    command = "python -c \"import json; json.load(open('budget.json'))\""
    runtime.events = events(command if change != "unread" else "printf hello", failed=change == "failed")
    await chats.send(OWNER, record["id"], "查看最新预算" if change == "latest" else "为什么增长？", "e" * 32)
    await chats.tasks[(OWNER, record["id"])]
    result = chats.read(OWNER, record["id"])["messages"][-1]["validation"]
    assert result["state"] == expected
    assert result["resources"] == []
    if change == "reuse":
        assert result["snapshots"][0]["label"].endswith("第30版（DRAFT）")
        assert "指纹一致" in runtime.prompts[-1] and "原读取时间" in runtime.prompts[-1]
    assert "用户要求最新" in runtime.prompts[-1]

    other = chats.store.create(OTHER)
    await chats.send(OTHER, other["id"], "为什么增长？", "f" * 32)
    await chats.tasks[(OTHER, other["id"])]
    assert chats.read(OTHER, other["id"])["messages"][-1]["validation"]["state"] == "needs_review"
