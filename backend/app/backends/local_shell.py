from __future__ import annotations

import fnmatch
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.backends.command_runner import (
    CommandResult,
    CommandRunner,
)
from app.backends.workspace import Workspace, WorkspacePathError

MAX_TEXT_FILE_BYTES = 1_000_000
MAX_WORKSPACE_GLOB_PATTERN_CHARS = 512
MAX_WORKSPACE_SEARCH_QUERY_CHARS = 500
MAX_WORKSPACE_SEARCH_RESULTS = 500
MAX_WORKSPACE_SCAN_ENTRIES = 50_000
MAX_WORKSPACE_SEARCH_BYTES = 20_000_000
MAX_WORKSPACE_SEARCH_SNIPPET_CHARS = 500

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")

EXCLUDED_SEARCH_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".next",
        ".nox",
        ".nuxt",
        ".pytest_cache",
        ".ruff_cache",
        ".secrets",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "out",
        "site-packages",
        "target",
        "venv",
    }
)

EXCLUDED_SEARCH_FILES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)

EXCLUDED_SEARCH_FILE_SUFFIXES = (
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
)

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


@dataclass(frozen=True, slots=True)
class WorkspaceGlobMatch:
    path: str
    kind: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class WorkspaceGlobResult:
    ok: bool
    path: str
    pattern: str
    matches: list[WorkspaceGlobMatch]
    match_count: int
    truncated: bool
    scanned_entry_count: int
    duration_ms: float


@dataclass(frozen=True, slots=True)
class WorkspaceSearchMatch:
    path: str
    line_number: int
    column_start: int
    column_end: int
    snippet: str


@dataclass(frozen=True, slots=True)
class WorkspaceSearchResult:
    ok: bool
    path: str
    query: str
    file_pattern: str
    case_sensitive: bool
    matches: list[WorkspaceSearchMatch]
    match_count: int
    files_searched: int
    skipped_file_count: int
    scanned_bytes: int
    truncated: bool
    duration_ms: float


@dataclass(frozen=True, slots=True)
class _ScannedEntry:
    path: Path
    relative_path: str
    is_dir: bool
    size_bytes: int


def _glob_matches(relative_path: str, pattern: str) -> bool:
    """Match path segments while treating ** as zero or more segments."""

    path_parts = tuple(part for part in relative_path.split("/") if part)
    pattern_parts = tuple(part for part in pattern.split("/") if part)

    @lru_cache(maxsize=None)
    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        current = pattern_parts[pattern_index]
        if current == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts)
                and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], current)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


class LocalShellBackend:
    """macOS 本地 Agent Backend。

    本课只实现文件能力，命令执行将在下一课加入。
    """

    def __init__(self, workspace: Workspace | None = None) -> None:
        self.workspace = workspace or Workspace.from_settings()
        self.command_runner = CommandRunner(self.workspace)

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

    def glob_paths(
        self,
        virtual_path: str = "/projects",
        *,
        pattern: str,
        max_results: int = 100,
        include_directories: bool = False,
    ) -> WorkspaceGlobResult:
        """Find bounded, deterministic path matches below a virtual root."""

        started = time.monotonic()
        root = self._resolve_search_root(virtual_path)
        normalized_pattern = self._validate_glob_pattern(pattern)
        self._validate_max_results(max_results)
        entries, scanned_entry_count, scan_truncated = (
            self._scan_workspace_entries(root)
        )
        matches: list[WorkspaceGlobMatch] = []

        for entry in entries:
            if entry.is_dir and not include_directories:
                continue
            if not _glob_matches(
                entry.relative_path,
                normalized_pattern,
            ):
                continue
            matches.append(
                WorkspaceGlobMatch(
                    path=self.workspace.to_virtual(entry.path),
                    kind="directory" if entry.is_dir else "file",
                    size_bytes=entry.size_bytes,
                )
            )

        matches.sort(key=lambda item: (item.path.casefold(), item.path))
        result_truncated = scan_truncated or len(matches) > max_results
        matches = matches[:max_results]
        return WorkspaceGlobResult(
            ok=True,
            path=self.workspace.to_virtual(root),
            pattern=normalized_pattern,
            matches=matches,
            match_count=len(matches),
            truncated=result_truncated,
            scanned_entry_count=scanned_entry_count,
            duration_ms=self._duration_ms(started),
        )

    def search_text(
        self,
        virtual_path: str = "/projects",
        *,
        query: str,
        file_pattern: str = "**/*",
        max_results: int = 100,
        case_sensitive: bool = True,
    ) -> WorkspaceSearchResult:
        """Search bounded UTF-8 workspace files using a literal query."""

        started = time.monotonic()
        root = self._resolve_search_root(virtual_path)
        normalized_query = self._validate_search_query(query)
        normalized_pattern = self._validate_glob_pattern(file_pattern)
        self._validate_max_results(max_results)
        if not isinstance(case_sensitive, bool):
            raise BackendFileError("case_sensitive 必须是布尔值")
        entries, _, scan_truncated = self._scan_workspace_entries(root)
        candidate_files = sorted(
            (
                entry
                for entry in entries
                if not entry.is_dir
                and _glob_matches(entry.relative_path, normalized_pattern)
            ),
            key=lambda item: (
                item.relative_path.casefold(),
                item.relative_path,
            ),
        )
        flags = 0 if case_sensitive else re.IGNORECASE
        query_pattern = re.compile(re.escape(normalized_query), flags)
        matches: list[WorkspaceSearchMatch] = []
        files_searched = 0
        skipped_file_count = 0
        scanned_bytes = 0
        result_truncated = scan_truncated

        for entry in candidate_files:
            if entry.size_bytes > MAX_TEXT_FILE_BYTES:
                skipped_file_count += 1
                continue
            if scanned_bytes + entry.size_bytes > MAX_WORKSPACE_SEARCH_BYTES:
                result_truncated = True
                break
            try:
                raw = entry.path.read_bytes()
            except OSError:
                skipped_file_count += 1
                continue
            if scanned_bytes + len(raw) > MAX_WORKSPACE_SEARCH_BYTES:
                result_truncated = True
                break
            scanned_bytes += len(raw)
            if len(raw) > MAX_TEXT_FILE_BYTES or b"\x00" in raw:
                skipped_file_count += 1
                continue
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped_file_count += 1
                continue

            files_searched += 1
            virtual_file_path = self.workspace.to_virtual(entry.path)
            for line_number, line in enumerate(content.splitlines(), 1):
                found = query_pattern.search(line)
                if found is None:
                    continue
                if len(matches) >= max_results:
                    result_truncated = True
                    return WorkspaceSearchResult(
                        ok=True,
                        path=self.workspace.to_virtual(root),
                        query=normalized_query,
                        file_pattern=normalized_pattern,
                        case_sensitive=case_sensitive,
                        matches=matches,
                        match_count=len(matches),
                        files_searched=files_searched,
                        skipped_file_count=skipped_file_count,
                        scanned_bytes=scanned_bytes,
                        truncated=result_truncated,
                        duration_ms=self._duration_ms(started),
                    )
                matches.append(
                    WorkspaceSearchMatch(
                        path=virtual_file_path,
                        line_number=line_number,
                        column_start=found.start() + 1,
                        column_end=found.end(),
                        snippet=self._search_snippet(
                            line,
                            found.start(),
                            found.end(),
                        ),
                    )
                )

        return WorkspaceSearchResult(
            ok=True,
            path=self.workspace.to_virtual(root),
            query=normalized_query,
            file_pattern=normalized_pattern,
            case_sensitive=case_sensitive,
            matches=matches,
            match_count=len(matches),
            files_searched=files_searched,
            skipped_file_count=skipped_file_count,
            scanned_bytes=scanned_bytes,
            truncated=result_truncated,
            duration_ms=self._duration_ms(started),
        )

    def _resolve_search_root(self, virtual_path: str) -> Path:
        if not isinstance(virtual_path, str):
            raise BackendFileError("path 必须是虚拟路径字符串")
        if _CONTROL_CHARACTER.search(virtual_path):
            raise WorkspacePathError("path 不能包含 NUL 或控制字符")
        if (
            _WINDOWS_ABSOLUTE_PATH.match(virtual_path)
            or "\\" in virtual_path
        ):
            raise WorkspacePathError("path 不能使用 Windows 或主机路径")
        if not virtual_path.startswith("/") or virtual_path.startswith("//"):
            raise WorkspacePathError(
                "path 必须使用 /projects/... 形式的虚拟绝对路径"
            )
        root = self.workspace.resolve(virtual_path)
        if not root.exists():
            raise BackendFileError(f"搜索目录不存在：{virtual_path}")
        if not root.is_dir():
            raise BackendFileError(f"搜索根路径不是目录：{virtual_path}")
        return root

    @staticmethod
    def _validate_glob_pattern(pattern: str) -> str:
        if not isinstance(pattern, str) or not pattern:
            raise BackendFileError("pattern 不能为空")
        if len(pattern) > MAX_WORKSPACE_GLOB_PATTERN_CHARS:
            raise BackendFileError(
                "pattern 不能超过 "
                f"{MAX_WORKSPACE_GLOB_PATTERN_CHARS} 个字符"
            )
        if _CONTROL_CHARACTER.search(pattern):
            raise WorkspacePathError("pattern 不能包含 NUL 或控制字符")
        if (
            pattern.startswith("/")
            or _WINDOWS_ABSOLUTE_PATH.match(pattern)
            or "\\" in pattern
        ):
            raise WorkspacePathError(
                "pattern 必须是相对于 path 的 Glob 模式"
            )
        parts = pattern.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise WorkspacePathError(
                "pattern 不能包含空路径段、'.' 或 '..'"
            )
        if any(part.casefold() == ".secrets" for part in parts):
            raise WorkspacePathError("pattern 禁止访问敏感目录 .secrets")
        return pattern

    @staticmethod
    def _validate_search_query(query: str) -> str:
        if not isinstance(query, str) or not query:
            raise BackendFileError("query 不能为空")
        if len(query) > MAX_WORKSPACE_SEARCH_QUERY_CHARS:
            raise BackendFileError(
                "query 不能超过 "
                f"{MAX_WORKSPACE_SEARCH_QUERY_CHARS} 个字符"
            )
        if _CONTROL_CHARACTER.search(query):
            raise BackendFileError("query 不能包含 NUL 或控制字符")
        return query

    @staticmethod
    def _validate_max_results(max_results: int) -> None:
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= MAX_WORKSPACE_SEARCH_RESULTS
        ):
            raise BackendFileError(
                "max_results 必须是 1 到 "
                f"{MAX_WORKSPACE_SEARCH_RESULTS} 的整数"
            )

    @staticmethod
    def _excluded_search_path(path: Path, *, is_dir: bool) -> bool:
        normalized_name = path.name.casefold()
        if is_dir:
            return normalized_name in EXCLUDED_SEARCH_DIRECTORIES
        if normalized_name in EXCLUDED_SEARCH_FILES:
            return True
        if (
            normalized_name.startswith(".env.")
            and normalized_name
            not in {".env.example", ".env.sample", ".env.template"}
        ):
            return True
        return normalized_name.endswith(EXCLUDED_SEARCH_FILE_SUFFIXES)

    def _scan_workspace_entries(
        self,
        root: Path,
    ) -> tuple[list[_ScannedEntry], int, bool]:
        entries: list[_ScannedEntry] = []
        pending = [root]
        scanned_entry_count = 0

        while pending:
            current = pending.pop()
            try:
                children = sorted(
                    current.iterdir(),
                    key=lambda item: (item.name.casefold(), item.name),
                )
            except OSError:
                continue
            directories: list[Path] = []
            for child in children:
                if scanned_entry_count >= MAX_WORKSPACE_SCAN_ENTRIES:
                    entries.sort(
                        key=lambda item: (
                            item.relative_path.casefold(),
                            item.relative_path,
                        )
                    )
                    return entries, scanned_entry_count, True
                scanned_entry_count += 1
                try:
                    if child.is_symlink():
                        continue
                    is_dir = child.is_dir()
                    if self._excluded_search_path(child, is_dir=is_dir):
                        continue
                    if not is_dir and not child.is_file():
                        continue
                    stat = child.stat(follow_symlinks=False)
                    self.workspace.to_virtual(child)
                except (OSError, ValueError):
                    continue
                relative_path = child.relative_to(root).as_posix()
                entries.append(
                    _ScannedEntry(
                        path=child,
                        relative_path=relative_path,
                        is_dir=is_dir,
                        size_bytes=0 if is_dir else stat.st_size,
                    )
                )
                if is_dir:
                    directories.append(child)
            pending.extend(reversed(directories))

        entries.sort(
            key=lambda item: (
                item.relative_path.casefold(),
                item.relative_path,
            )
        )
        return entries, scanned_entry_count, False

    @staticmethod
    def _search_snippet(line: str, start: int, end: int) -> str:
        sanitized = "".join(
            character
            if character == "\t" or ord(character) >= 32
            else "?"
            for character in line
            if ord(character) != 127
        )
        if len(sanitized) <= MAX_WORKSPACE_SEARCH_SNIPPET_CHARS:
            return sanitized
        available = MAX_WORKSPACE_SEARCH_SNIPPET_CHARS - 8
        left = max(start - available // 3, 0)
        right = min(max(end + (available * 2 // 3), left + available), len(sanitized))
        left = max(right - available, 0)
        prefix = "... " if left else ""
        suffix = " ..." if right < len(sanitized) else ""
        return f"{prefix}{sanitized[left:right]}{suffix}"

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round(max((time.monotonic() - started) * 1_000, 0.0), 3)

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

    def run_command(
        self,
        argv: Sequence[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        """执行一条受控的本地命令。"""

        return self.command_runner.run(
            argv,
            cwd=cwd,
            timeout=timeout,
        )
