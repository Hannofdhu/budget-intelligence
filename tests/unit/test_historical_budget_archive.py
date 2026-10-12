"""Archive import preserves the exact budget model and never uses runtime files."""

import copy
import json
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.source_snapshot import historical_archive as archive
from tests.unit.test_fy27_source_acceptance import (
    adopted_files as adopted_files,
)
from tests.unit.test_fy27_source_acceptance import (
    legacy_payload as legacy_payload,
)
from tests.unit.test_fy27_source_acceptance import (
    save_json,
)


@pytest.fixture
def prepared(adopted_files: dict[str, Any]) -> archive.PreparedArchive:
    return archive.prepare_archive(
        adopted_files["source"], adopted_files["baseline"], adopted_files["manifest"]
    )


def test_import_preparation_is_read_only_and_keeps_identity_and_signed_values(
    adopted_files: dict[str, Any], prepared: archive.PreparedArchive
) -> None:
    files = adopted_files
    for field, name in (
        ("original_workbook", "source"),
        ("original_extraction", "baseline"),
        ("accepted_extraction", "extraction"),
        ("acceptance_record", "manifest"),
    ):
        assert getattr(prepared, field) == files[name].read_bytes()
    assert prepared.baseline_hash == files["legacy_payload"]["sha256"]
    assert prepared.source_file_hash == files["current_payload"]["sha256"]
    assert prepared.baseline_hash != prepared.source_file_hash
    assert prepared.review["annual_revenue"] == "100"
    row = prepared.review["rows"][0]
    assert (row["old"], row["discount"], row["carryout"], row["prior_students"]) == (0, None, -0.01, "-")
    assert row["sources"]["new"]["formula"] == "=IF(ROUND(1,0),2,0)"
    archive.verify_prepared(prepared)


def test_original_source_without_layout_adoption_can_be_imported(
    tmp_path: Path, legacy_payload: dict[str, Any]
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"legacy source")
    extraction = tmp_path / "source-cells.json"
    save_json(extraction, legacy_payload)
    prepared = archive.prepare_archive(source, extraction, tmp_path / "missing.json")
    assert prepared.baseline_hash == prepared.source_file_hash
    assert prepared.acceptance_record is None
    assert "business_sha256" not in prepared.review
    archive.verify_prepared(prepared)


@pytest.mark.parametrize(
    "field", ["original_workbook", "original_extraction", "accepted_extraction", "acceptance_record"]
)
def test_tampered_source_bytes_are_rejected(prepared: archive.PreparedArchive, field: str) -> None:
    with pytest.raises(ValueError, match="字节校验"):
        archive.verify_prepared(replace(prepared, **{field: b"tampered"}))


@pytest.mark.parametrize("field", ["sha256", "source_file_sha256", "annual_revenue", "scope_count"])
def test_tampered_read_model_is_rejected(prepared: archive.PreparedArchive, field: str) -> None:
    review = copy.deepcopy(prepared.review)
    review[field] = "incorrect"
    with pytest.raises(ValueError, match="读取结果"):
        archive.verify_prepared(replace(prepared, review=review))


def test_missing_acceptance_is_not_invented(prepared: archive.PreparedArchive) -> None:
    manifest = copy.deepcopy(prepared.source_manifest)
    del manifest["files"]["acceptance_record"]
    with pytest.raises(ValueError, match="缺少明确接受记录"):
        archive.verify_prepared(replace(prepared, source_manifest=manifest, acceptance_record=None))


def test_invalid_acceptance_decision_is_not_accepted(prepared: archive.PreparedArchive) -> None:
    assert prepared.acceptance_record is not None
    acceptance = json.loads(prepared.acceptance_record)
    acceptance["decision"] = "SILENT_REBASE"
    blob = json.dumps(acceptance).encode()
    manifest = copy.deepcopy(prepared.source_manifest)
    manifest["files"]["acceptance_record"].update(sha256=archive.digest(blob), byte_count=len(blob))
    with pytest.raises(ValueError, match="接受记录不匹配"):
        archive.verify_prepared(replace(prepared, source_manifest=manifest, acceptance_record=blob))


def test_racing_source_edit_is_rejected_before_persistence(
    adopted_files: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_reader = archive.read_current_review

    def changed(source: Path, baseline: Path, manifest: Path) -> dict[str, Any]:
        result = original_reader(source, baseline, manifest)
        source.write_bytes(b"concurrent edit")
        return result

    monkeypatch.setattr(archive, "read_current_review", changed)
    with pytest.raises(ValueError, match="导入准备期间变化"):
        archive.prepare_archive(adopted_files["source"], adopted_files["baseline"], adopted_files["manifest"])


def test_jsonb_reordering_keeps_hash_while_blank_zero_dash_and_negative_differ() -> None:
    assert archive.json_hash({"b": 0, "a": None}) == archive.json_hash({"a": None, "b": 0})
    assert len({archive.json_hash({"value": v}) for v in (0, None, "-", -0.01, "0")}) == 5
    with pytest.raises(ValueError):
        archive.json_hash({"number": float("nan")})


def _row(prepared: archive.PreparedArchive) -> SimpleNamespace:
    return SimpleNamespace(
        review=copy.deepcopy(prepared.review),
        content_hash=prepared.content_hash,
        baseline_hash=prepared.baseline_hash,
        source_file_hash=prepared.source_file_hash,
        business_hash=prepared.business_hash,
        source_manifest=copy.deepcopy(prepared.source_manifest),
        source_manifest_hash=prepared.source_manifest_hash,
    )


def _session_reader(monkeypatch: pytest.MonkeyPatch, row: SimpleNamespace | None) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = row
    session.execute.return_value = result
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(archive, "get_session_factory", lambda: factory)
    return session


@pytest.mark.asyncio
async def test_database_read_uses_department_query_and_never_source_files(
    prepared: archive.PreparedArchive, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session_reader(monkeypatch, _row(prepared))

    def forbidden_read(_: Path) -> bytes:
        raise AssertionError("Runtime must not read any source file")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    assert await archive.read_database_review("owner-dept") == prepared.review
    statement = session.execute.call_args.args[0]
    assert "original_workbook" not in str(statement)
    assert "original_extraction" not in str(statement)
    assert statement.compile().params["department_id_1"] == "owner-dept"


@pytest.mark.asyncio
async def test_database_without_archive_reports_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _session_reader(monkeypatch, None)
    with pytest.raises(FileNotFoundError):
        await archive.read_database_review("empty-dept")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["content_hash", "baseline_hash", "source_file_hash", "business_hash", "source_manifest_hash"]
)
async def test_database_read_rejects_metadata_corruption(
    prepared: archive.PreparedArchive, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    row = _row(prepared)
    setattr(row, field, "0" * 64)
    _session_reader(monkeypatch, row)
    with pytest.raises(ValueError, match="档案校验失败"):
        await archive.read_database_review("owner-dept")


@pytest.mark.asyncio
async def test_idempotent_import_returns_existing_id_and_does_not_commit(
    prepared: archive.PreparedArchive,
) -> None:
    session = AsyncMock()
    existing_id = uuid.uuid4()
    session.scalar.return_value = None
    result = MagicMock()
    result.one.return_value = SimpleNamespace(
        archive_id=existing_id,
        content_hash=prepared.content_hash,
        source_manifest_hash=prepared.source_manifest_hash,
    )
    session.execute.return_value = result
    assert await archive.import_archive(
        session, prepared, department_id="dept", actor="owner", reason="import"
    ) == (existing_id, False)
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflicting_repeat_import_cannot_replace_existing(prepared: archive.PreparedArchive) -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    result = MagicMock()
    result.one.return_value = SimpleNamespace(
        archive_id=uuid.uuid4(),
        content_hash="different",
        source_manifest_hash=prepared.source_manifest_hash,
    )
    session.execute.return_value = result
    with pytest.raises(ValueError, match="拒绝覆盖"):
        await archive.import_archive(session, prepared, department_id="dept", actor="owner", reason="import")
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["department_id", "actor", "reason"])
async def test_import_requires_audit_identity(prepared: archive.PreparedArchive, field: str) -> None:
    kwargs = {"department_id": "dept", "actor": "owner", "reason": "import"}
    kwargs[field] = " "
    session = AsyncMock()
    with pytest.raises(ValueError, match="必须记录"):
        await archive.import_archive(session, prepared, **kwargs)
    session.scalar.assert_not_awaited()
