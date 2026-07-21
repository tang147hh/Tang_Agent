from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.backends.workspace import Workspace


MAX_TEXT_FILE_BYTES = 1_000_000

WRITABLE_ROOTS = {
    "projects",
    "reviews",
    "tmp",
}


class BackendFileError(RuntimeError):
    """Backend 文件操作失败。"""


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    is_dir: bool
    size: int


class LocalShellBackend:
    """macOS 本地 Agent Backend。

    本课只实现文件能力，命令执行将在下一课加入。
    """

    def __init__(self, workspace: Workspace | None = None) -> None:
        self.workspace = workspace or Workspace.from_settings()

    def list_dir(self, virtual_path: str = "/") -> list[FileEntry]:
        """列出目录的下一层内容。"""

        path = self.workspace.resolve(virtual_path)

        if not path.exists():
            raise BackendFileError(f"目录不存在：{virtual_path}")

        if not path.is_dir():
            raise BackendFileError(f"路径不是目录：{virtual_path}")

        entries: list[FileEntry] = []

        for child in sorted(
            path.iterdir(),
            key=lambda item: item.name.lower(),
        ):
            # Agent 不应该知道内部敏感目录存在。
            if child.name == ".secrets":
                continue

            try:
                child_virtual_path = self.workspace.to_virtual(child)
            except ValueError:
                # 跳过指向工作区外部的符号链接。
                continue

            entries.append(
                FileEntry(
                    path=child_virtual_path,
                    is_dir=child.is_dir(),
                    size=child.stat().st_size if child.is_file() else 0,
                )
            )

        return entries

    def read_text(
        self,
        virtual_path: str,
        *,
        offset: int = 0,
        limit: int = 2_000,
    ) -> str:
        """读取 UTF-8 文本文件，可按行分页。"""

        if offset < 0:
            raise BackendFileError("offset 不能小于 0")

        if limit <= 0:
            raise BackendFileError("limit 必须大于 0")

        path = self.workspace.resolve(virtual_path)

        if not path.exists():
            raise BackendFileError(f"文件不存在：{virtual_path}")

        if not path.is_file():
            raise BackendFileError(f"路径不是文件：{virtual_path}")

        raw = path.read_bytes()

        if len(raw) > MAX_TEXT_FILE_BYTES:
            raise BackendFileError(
                f"文件超过读取上限：{len(raw)} bytes"
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BackendFileError(
                f"文件不是 UTF-8 文本：{virtual_path}"
            ) from exc

        lines = text.splitlines()

        return "\n".join(lines[offset : offset + limit])

    def write_text(
        self,
        virtual_path: str,
        content: str,
    ) -> str:
        """创建新的 UTF-8 文件，不覆盖已有文件。"""

        path = self.workspace.resolve(virtual_path)
        self._assert_writable(path)

        if path.exists():
            raise BackendFileError(
                f"文件已存在，请使用 edit_text：{virtual_path}"
            )

        encoded = content.encode("utf-8")

        if len(encoded) > MAX_TEXT_FILE_BYTES:
            raise BackendFileError(
                f"写入内容超过上限：{len(encoded)} bytes"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")

        return self.workspace.to_virtual(path)

    def edit_text(
        self,
        virtual_path: str,
        old_text: str,
        new_text: str,
        *,
        replace_all: bool = False,
    ) -> int:
        """通过精确字符串替换修改已有文件。"""

        if not old_text:
            raise BackendFileError("old_text 不能为空")

        path = self.workspace.resolve(virtual_path)
        self._assert_writable(path)

        if not path.exists():
            raise BackendFileError(f"文件不存在：{virtual_path}")

        if not path.is_file():
            raise BackendFileError(f"路径不是文件：{virtual_path}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise BackendFileError(
                f"文件不是 UTF-8 文本：{virtual_path}"
            ) from exc

        occurrences = content.count(old_text)

        if occurrences == 0:
            raise BackendFileError(
                f"没有找到待替换内容：{virtual_path}"
            )

        if occurrences > 1 and not replace_all:
            raise BackendFileError(
                f"待替换内容出现 {occurrences} 次，"
                "请提供更精确的 old_text 或设置 replace_all=True"
            )

        updated = content.replace(
            old_text,
            new_text,
            -1 if replace_all else 1,
        )

        if len(updated.encode("utf-8")) > MAX_TEXT_FILE_BYTES:
            raise BackendFileError("修改后的文件超过大小上限")

        path.write_text(updated, encoding="utf-8", newline="")

        return occurrences if replace_all else 1

    def _assert_writable(self, path: Path) -> None:
        """限制 Agent 可以写入的顶层目录。"""

        relative = path.relative_to(self.workspace.root)

        if not relative.parts:
            raise BackendFileError("不能直接写入工作区根目录")

        root_name = relative.parts[0]

        if root_name not in WRITABLE_ROOTS:
            raise BackendFileError(
                f"目录只读或禁止写入：/{root_name}"
            )