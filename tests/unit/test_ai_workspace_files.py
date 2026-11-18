import os
from pathlib import Path

import pytest

from packages.ai_agent import files as module
from packages.ai_agent.files import MAX_BYTES, MAX_PREVIEW_BYTES, WorkspaceFileError, WorkspaceFiles

WORKSPACE = "a" * 32


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[WorkspaceFiles, Path]:
    directory = tmp_path / "runtime" / WORKSPACE / "workspace"
    directory.mkdir(parents=True)
    return WorkspaceFiles(tmp_path / "runtime"), directory


def test_lists_only_regular_supported_nonprivate_files_without_host_paths(
    workspace: tuple[WorkspaceFiles, Path],
) -> None:
    store, directory = workspace
    for name in (
        "报告.xlsx",
        "图.PNG",
        "说明.md",
        ".budget-chat.json",
        "secret.env",
        "index.html",
        "bad\nname.csv",
    ):
        (directory / name).write_bytes(b"synthetic")
    nested = directory / "reports"
    nested.mkdir()
    (nested / "数据.csv").write_text("amount\n351\n")
    os.mkfifo(directory / "fifo.csv")
    result = store.list(WORKSPACE)
    assert {item["name"] for item in result["files"]} == {"报告.xlsx", "图.PNG", "说明.md", "数据.csv"}
    assert all(len(item["id"]) == 64 for item in result["files"])
    assert str(directory) not in repr(result) and "workspace_id" not in repr(result)
    file = next(item for item in result["files"] if item["name"] == "报告.xlsx")
    assert store.read(WORKSPACE, file["id"])[1] == b"synthetic"
    assert (directory / "报告.xlsx").read_bytes() == b"synthetic"


def test_symlinks_hardlinks_and_private_directories_never_expose_other_files(
    workspace: tuple[WorkspaceFiles, Path], tmp_path: Path
) -> None:
    store, directory = workspace
    outside = tmp_path / "private.json"
    outside.write_text("private")
    (directory / "link.json").symlink_to(outside)
    (directory / "folder").symlink_to(tmp_path, target_is_directory=True)
    os.link(outside, directory / "hardlink.json")
    hidden = directory / ".private"
    hidden.mkdir()
    (hidden / "data.csv").write_text("private")
    assert store.list(WORKSPACE)["files"] == []


def test_workspace_roots_and_names_cannot_redirect_access(
    workspace: tuple[WorkspaceFiles, Path], tmp_path: Path
) -> None:
    store, _ = workspace
    for identifier in ("../private", "/etc", "a" * 33, ""):
        with pytest.raises(WorkspaceFileError):
            store.list(identifier)
    (store.root / ("b" * 32)).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(WorkspaceFileError):
        store.list("b" * 32)
    alias = tmp_path / "alias"
    alias.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(WorkspaceFileError):
        WorkspaceFiles(alias).list(WORKSPACE)
    assert store.list(None) == {"files": [], "truncated": False}


def test_files_replaced_after_listing_are_not_served(workspace: tuple[WorkspaceFiles, Path]) -> None:
    store, directory = workspace
    source = directory / "report.csv"
    source.write_text("old")
    identifier = store.list(WORKSPACE)["files"][0]["id"]
    source.write_text("new data")
    with pytest.raises(WorkspaceFileError, match="已更新"):
        store.read(WORKSPACE, identifier)
    assert store.read(WORKSPACE, store.list(WORKSPACE)["files"][0]["id"])[1] == b"new data"


def test_swap_to_symlink_between_scan_and_open_is_rejected(
    workspace: tuple[WorkspaceFiles, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, directory = workspace
    source = directory / "report.csv"
    source.write_text("safe")
    outside = tmp_path / "private.csv"
    outside.write_text("private")
    identifier = store.list(WORKSPACE)["files"][0]["id"]
    scan = store._scan

    def swapped(descriptor: int, workspace_id: str) -> tuple[list[module.WorkspaceFile], bool]:
        result = scan(descriptor, workspace_id)
        source.unlink()
        source.symlink_to(outside)
        return result

    monkeypatch.setattr(store, "_scan", swapped)
    with pytest.raises(WorkspaceFileError):
        store.read(WORKSPACE, identifier)
    assert outside.read_text() == "private"


def test_inplace_changes_during_read_cannot_be_misreported_as_complete(
    workspace: tuple[WorkspaceFiles, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, directory = workspace
    source = directory / "report.csv"
    source.write_text("safe")
    identifier = store.list(WORKSPACE)["files"][0]["id"]
    original = os.read

    def changed(descriptor: int, size: int) -> bytes:
        source.write_text("changed")
        return original(descriptor, size)

    monkeypatch.setattr(os, "read", changed)
    with pytest.raises(WorkspaceFileError, match="变化"):
        store.read(WORKSPACE, identifier)


def test_limits_empty_and_active_content_previews(workspace: tuple[WorkspaceFiles, Path]) -> None:
    store, directory = workspace
    (directory / "empty.csv").touch()
    (directory / "unsafe.svg").write_text('<svg onload="alert(1)"></svg>')
    (directory / "fake.png").write_text("<script>not an image</script>")
    with (directory / "large.csv").open("wb") as handle:
        handle.truncate(MAX_BYTES + 1)
    with (directory / "preview.png").open("wb") as handle:
        handle.truncate(MAX_PREVIEW_BYTES + 1)
    items = {item["name"]: item for item in store.list(WORKSPACE)["files"]}
    assert not items["empty.csv"]["downloadable"] and not items["large.csv"]["downloadable"]
    assert items["preview.png"]["downloadable"] and not items["preview.png"]["previewable"]
    for name in ("empty.csv", "large.csv"):
        with pytest.raises(WorkspaceFileError):
            store.read(WORKSPACE, items[name]["id"])
    for name in ("unsafe.svg", "fake.png", "preview.png"):
        with pytest.raises(WorkspaceFileError):
            store.read(WORKSPACE, items[name]["id"], preview=True)
    assert store.read(WORKSPACE, items["unsafe.svg"]["id"])[1].startswith(b"<svg")


def test_listing_has_bounded_scan_and_reports_truncation(
    workspace: tuple[WorkspaceFiles, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, directory = workspace
    for index in range(5):
        (directory / f"{index}.csv").write_text("synthetic")
    monkeypatch.setattr(module, "MAX_FILES", 2)
    result = store.list(WORKSPACE)
    assert len(result["files"]) == 2 and result["truncated"] is True
