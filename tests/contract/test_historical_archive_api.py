"""FY27 archive reads use the database after local-trial and identity checks."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from apps.api.routes import historical_review as route
from packages.domain.identity.models import Actor, Role
from packages.source_snapshot import fy27_review as extraction

DEPARTMENT = "archive-department"


def _request(address: str = "127.0.0.1") -> Request:
    return Request({"type": "http", "headers": [(b"x-budget-client-address", address.encode())]})


def _actor(
    *,
    department: str = DEPARTMENT,
    roles: frozenset[Role] = frozenset({Role.DEPARTMENT_BUDGET_OWNER}),
) -> Actor:
    return Actor(user_id="archive-reader", department_id=department, roles=roles)


@pytest.fixture
def baseline() -> dict[str, Any]:
    return {
        "fiscal_year": 2027,
        "mode": "HISTORICAL_EXCEL_READ_ONLY",
        "sha256": "immutable-archive-source",
        "annual_revenue": "113004618.59",
        "rows": [{"row": 14, "season": "暑假", "revenue": "356689.39"}],
    }


@pytest.fixture
def archive_reader(monkeypatch: pytest.MonkeyPatch, baseline: dict[str, Any]) -> AsyncMock:
    reader = AsyncMock(return_value=baseline)
    monkeypatch.setattr(route, "read_database_review", reader, raising=False)

    def forbidden_file_read() -> dict[str, Any]:
        raise AssertionError("runtime FY27 reads must not load the Excel extraction")

    monkeypatch.setattr(route, "read_review", forbidden_file_read, raising=False)
    monkeypatch.setattr(extraction, "read_review", forbidden_file_read)
    return reader


@pytest.fixture(autouse=True)
def local_trial(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    session = AsyncMock(return_value={"departmentId": DEPARTMENT})
    monkeypatch.setattr(route, "trial_session", session)
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.DEPARTMENT_BUDGET_OWNER, Role.DATA_MAINTAINER])
@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
async def test_authorized_local_roles_read_department_database_archive(
    role: Role,
    address: str,
    baseline: dict[str, Any],
    archive_reader: AsyncMock,
    local_trial: AsyncMock,
) -> None:
    request = _request(address)
    actor = _actor(roles=frozenset({role}))

    assert await route.fy27_review(request, actor) == baseline
    local_trial.assert_awaited_once_with(request)
    archive_reader.assert_awaited_once_with(DEPARTMENT)


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["", "192.168.1.10", "unknown"])
async def test_lan_or_missing_client_address_is_denied_before_archive_load(
    address: str, archive_reader: AsyncMock
) -> None:
    with pytest.raises(HTTPException) as denied:
        await route.fy27_review(_request(address), _actor())

    assert denied.value.status_code == 403
    assert "仅在本机" in denied.value.detail
    archive_reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_department_is_denied_before_archive_load(archive_reader: AsyncMock) -> None:
    with pytest.raises(HTTPException) as denied:
        await route.fy27_review(_request(), _actor(department="other-department"))

    assert denied.value.status_code == 403
    assert denied.value.detail == "无权查看本部门历史预算"
    archive_reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", [frozenset(), frozenset({Role.PERMISSION_ADMIN})])
async def test_missing_budget_read_role_is_denied_before_archive_load(
    roles: frozenset[Role], archive_reader: AsyncMock
) -> None:
    with pytest.raises(HTTPException) as denied:
        await route.fy27_review(_request(), _actor(roles=roles))

    assert denied.value.status_code == 403
    assert denied.value.detail == "无权查看本部门历史预算"
    archive_reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_trial_session_rejection_precedes_archive_load(
    archive_reader: AsyncMock, local_trial: AsyncMock
) -> None:
    local_trial.side_effect = HTTPException(403, "本机试用会话无效")

    with pytest.raises(HTTPException) as denied:
        await route.fy27_review(_request(), _actor())

    assert denied.value.status_code == 403
    assert denied.value.detail == "本机试用会话无效"
    archive_reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_database_archive_returns_404(archive_reader: AsyncMock) -> None:
    archive_reader.side_effect = FileNotFoundError("FY27 archive not imported")

    with pytest.raises(HTTPException) as missing:
        await route.fy27_review(_request(), _actor())

    assert missing.value.status_code == 404
    assert "FY27" in missing.value.detail
    assert "数据库" in missing.value.detail
    assert "原表" not in missing.value.detail
    archive_reader.assert_awaited_once_with(DEPARTMENT)


@pytest.mark.asyncio
async def test_corrupt_database_archive_returns_409(archive_reader: AsyncMock) -> None:
    detail = "FY27 数据库归档校验失败"
    archive_reader.side_effect = ValueError(detail)

    with pytest.raises(HTTPException) as corrupt:
        await route.fy27_review(_request(), _actor())

    assert corrupt.value.status_code == 409
    assert corrupt.value.detail == detail
    archive_reader.assert_awaited_once_with(DEPARTMENT)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT archive", {}, OSError("database offline")),
        ConnectionRefusedError("database offline"),
        OSError("database offline"),
        TimeoutError("database offline"),
    ],
    ids=["sqlalchemy", "connection-refused", "os-error", "timeout"],
)
async def test_database_unavailable_returns_503(error: Exception, archive_reader: AsyncMock) -> None:
    archive_reader.side_effect = error

    with pytest.raises(HTTPException) as unavailable:
        await route.fy27_review(_request(), _actor())

    assert unavailable.value.status_code == 503
    assert "数据库" in unavailable.value.detail
    assert "SELECT archive" not in unavailable.value.detail
    archive_reader.assert_awaited_once_with(DEPARTMENT)


@pytest.mark.asyncio
async def test_missing_local_excel_and_extraction_do_not_block_database_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    baseline: dict[str, Any],
    archive_reader: AsyncMock,
) -> None:
    source = tmp_path / "missing-source.xlsx"
    extracted = tmp_path / "missing-extraction.json"
    monkeypatch.setattr(extraction, "SOURCE", source)
    monkeypatch.setattr(extraction, "EXTRACTION", extracted)

    assert not source.exists()
    assert not extracted.exists()
    assert await route.fy27_review(_request(), _actor()) == baseline
    archive_reader.assert_awaited_once_with(DEPARTMENT)
