"""Read-only, descriptor-relative access to one host-authorized chat workspace."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ID = re.compile(r"[a-f0-9]{32}\Z")
FILE_ID = re.compile(r"[a-f0-9]{64}\Z")
MAX_BYTES = 32 * 1024 * 1024
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_FILES, MAX_ENTRIES, MAX_DEPTH = 200, 4000, 5
TYPES = {
    ".xlsx": ("Excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
    ".csv": ("CSV", "text/csv", False),
    ".png": ("PNG 图片", "image/png", True),
    ".jpg": ("JPEG 图片", "image/jpeg", True),
    ".jpeg": ("JPEG 图片", "image/jpeg", True),
    ".webp": ("WebP 图片", "image/webp", True),
    ".pdf": ("PDF", "application/pdf", False),
    ".svg": ("SVG · 仅下载", "application/octet-stream", False),
    ".json": ("JSON", "application/json", False),
    ".txt": ("文本", "text/plain", False),
    ".md": ("Markdown", "text/markdown", False),
}
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class WorkspaceFileError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def safe_name(name: str) -> bool:
    return (
        bool(name)
        and not name.startswith(".")
        and not any(
            char in "/\\"
            or ord(char) < 32
            or ord(char) == 127
            or "\u202a" <= char <= "\u202e"
            or "\u2066" <= char <= "\u2069"
            for char in name
        )
    )


def signature(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class WorkspaceFile:
    id: str
    parts: tuple[str, ...]
    info: os.stat_result

    def public(self) -> dict[str, Any]:
        label, mime, preview = TYPES[Path(self.parts[-1]).suffix.lower()]
        available = 0 < self.info.st_size <= MAX_BYTES
        return {
            "id": self.id,
            "name": self.parts[-1],
            "size": self.info.st_size,
            "modified_at": datetime.fromtimestamp(self.info.st_mtime, UTC).isoformat(),
            "type_label": label,
            "media_type": mime,
            "previewable": preview and self.info.st_size <= MAX_PREVIEW_BYTES and available,
            "downloadable": available,
            "notice": "文件尚未写入内容"
            if self.info.st_size == 0
            else "超过32MB，暂不提供下载"
            if not available
            else "",
        }


class WorkspaceFiles:
    def __init__(self, runtime_root: Path) -> None:
        self.root = runtime_root

    @contextmanager
    def _workspace(self, workspace_id: str) -> Iterator[int]:
        if not isinstance(workspace_id, str) or not ID.fullmatch(workspace_id):
            raise WorkspaceFileError(404, "本对话文件工作区不可用，未切换至其他工作区")
        try:
            with ExitStack() as stack:
                descriptor = os.open(self.root, DIRECTORY_FLAGS)
                stack.callback(os.close, descriptor)
                for name in (workspace_id, "workspace"):
                    descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=descriptor)
                    stack.callback(os.close, descriptor)
                yield descriptor
        except OSError as exc:
            raise WorkspaceFileError(404, "本对话文件工作区不可用，原对话仍保留") from exc

    def _scan(self, descriptor: int, workspace_id: str) -> tuple[list[WorkspaceFile], bool]:
        files: list[WorkspaceFile] = []
        examined, truncated = 0, False

        def walk(directory: int, parent: tuple[str, ...]) -> None:
            nonlocal examined, truncated
            with os.scandir(directory) as entries:
                for entry in entries:
                    examined += 1
                    if examined > MAX_ENTRIES or len(files) >= MAX_FILES:
                        truncated = True
                        return
                    if not safe_name(entry.name):
                        continue
                    try:
                        info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                        parts = (*parent, entry.name)
                        if stat.S_ISDIR(info.st_mode):
                            if len(parts) >= MAX_DEPTH:
                                truncated = True
                                continue
                            child = os.open(entry.name, DIRECTORY_FLAGS, dir_fd=directory)
                            try:
                                walk(child, parts)
                            finally:
                                os.close(child)
                        elif (
                            stat.S_ISREG(info.st_mode)
                            and info.st_nlink == 1
                            and Path(entry.name).suffix.lower() in TYPES
                        ):
                            if info.st_size > 2**53 - 1:
                                continue
                            datetime.fromtimestamp(info.st_mtime, UTC)
                            key = repr((workspace_id, parts, signature(info))).encode("utf-8")
                            files.append(WorkspaceFile(hashlib.sha256(key).hexdigest(), parts, info))
                    except (OSError, OverflowError, ValueError):
                        continue  # Files may be created/renamed by the guest while listing.

        walk(descriptor, ())
        return sorted(files, key=lambda item: (-item.info.st_mtime_ns, item.parts)), truncated

    def list(self, workspace_id: str | None) -> dict[str, Any]:
        if workspace_id is None:
            return {"files": [], "truncated": False}
        with self._workspace(workspace_id) as descriptor:
            files, truncated = self._scan(descriptor, workspace_id)
        return {"files": [item.public() for item in files], "truncated": truncated}

    def read(
        self, workspace_id: str | None, identifier: str, *, preview: bool = False
    ) -> tuple[WorkspaceFile, bytes]:
        if workspace_id is None or not FILE_ID.fullmatch(identifier):
            raise WorkspaceFileError(404, "文件不存在或已更新，请刷新文件列表")
        with self._workspace(workspace_id) as root, ExitStack() as stack:
            files, _ = self._scan(root, workspace_id)
            item = next((entry for entry in files if entry.id == identifier), None)
            if item is None:
                raise WorkspaceFileError(404, "文件不存在或已更新，请刷新文件列表")
            details = item.public()
            if not details["downloadable"]:
                raise WorkspaceFileError(413 if item.info.st_size else 409, details["notice"])
            if preview and not details["previewable"]:
                raise WorkspaceFileError(415, "此文件不支持图片预览，请下载后查看")
            directory = root
            try:
                for name in item.parts[:-1]:
                    directory = os.open(name, DIRECTORY_FLAGS, dir_fd=directory)
                    stack.callback(os.close, directory)
                descriptor = os.open(
                    item.parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=directory,
                )
                stack.callback(os.close, descriptor)
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or signature(info) != signature(item.info)
                ):
                    raise WorkspaceFileError(409, "文件正在变化，请稍后刷新文件列表")
                chunks, length = [], 0
                while chunk := os.read(descriptor, min(256 * 1024, MAX_BYTES + 1 - length)):
                    chunks.append(chunk)
                    length += len(chunk)
                    if length > MAX_BYTES:
                        raise WorkspaceFileError(413, "文件超过32MB，暂不提供下载")
                if signature(os.fstat(descriptor)) != signature(info) or length != info.st_size:
                    raise WorkspaceFileError(409, "文件正在变化，请稍后刷新文件列表")
            except OSError as exc:
                raise WorkspaceFileError(404, "文件不可读取或已更新，请刷新文件列表") from exc
            content = b"".join(chunks)
            if preview:
                mime = details["media_type"]
                valid = (
                    mime == "image/png"
                    and content.startswith(b"\x89PNG\r\n\x1a\n")
                    or mime == "image/jpeg"
                    and content.startswith(b"\xff\xd8\xff")
                    or mime == "image/webp"
                    and content.startswith(b"RIFF")
                    and content[8:12] == b"WEBP"
                )
                if not valid:
                    raise WorkspaceFileError(415, "图片内容无法预览，请下载后核对")
            return item, content
