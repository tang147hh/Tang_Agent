from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.backends.command_runner import (
    CommandPolicyError,
    CommandResult,
    CommandRunner,
)
from app.backends.workspace import Workspace, WorkspacePathError
from app.core.config import Settings, load_settings


EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_OUTPUT_TRUNCATION_MARKER = "\n... output truncated ..."


class ReviewScope(StrEnum):
    STAGED = "staged"
    UNSTAGED = "unstaged"
    ALL = "all"


class ReviewSource(StrEnum):
    WORKING_TREE = "working_tree"
    PULL_REQUEST = "pull_request"


class ReviewChangeType(StrEnum):
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    UNTRACKED = "untracked"


class ReviewLineSide(StrEnum):
    OLD = "old"
    NEW = "new"


class DiffLineType(StrEnum):
    CONTEXT = "context"
    ADDITION = "addition"
    DELETION = "deletion"
    NO_NEWLINE = "no_newline"


class ReviewSnapshotStatus(StrEnum):
    COLLECTED = "collected"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewDiffErrorCode(StrEnum):
    RUN_NOT_FOUND = "run_not_found"
    PROJECT_NOT_FOUND = "project_not_found"
    REPOSITORY_OUTSIDE_WORKSPACE = "repository_outside_workspace"
    REPOSITORY_NOT_FOUND = "repository_not_found"
    GIT_COMMAND_FAILED = "git_command_failed"
    GIT_COMMAND_TIMEOUT = "git_command_timeout"
    RUN_TIME_LIMIT = "run_time_limit"
    GIT_OUTPUT_INVALID = "git_output_invalid"


class ReviewTruncationReason(StrEnum):
    MAX_FILES = "max_files"
    FILE_PATCH_CHARS = "file_patch_chars"
    FILE_CHANGED_LINES = "file_changed_lines"
    TOTAL_PATCH_CHARS = "total_patch_chars"
    TOTAL_CHANGED_LINES = "total_changed_lines"
    GIT_OUTPUT = "git_output"
    GITHUB_PATCH_UNAVAILABLE = "github_patch_unavailable"


class ReviewDiffError(RuntimeError):
    def __init__(self, code: ReviewDiffErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ReviewDiffLimits:
    max_files: int = 50
    max_file_patch_chars: int = 40_000
    max_file_changed_lines: int = 800
    max_total_patch_chars: int = 200_000
    max_total_changed_lines: int = 3_000
    git_timeout: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_file_patch_chars",
            "max_file_changed_lines",
            "max_total_patch_chars",
            "max_total_changed_lines",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if self.git_timeout <= 0:
            raise ValueError("git_timeout 必须大于 0")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "ReviewDiffLimits":
        current = settings or load_settings()
        return cls(
            max_files=current.review_diff_max_files,
            max_file_patch_chars=current.review_diff_max_file_patch_chars,
            max_file_changed_lines=(
                current.review_diff_max_file_changed_lines
            ),
            max_total_patch_chars=(
                current.review_diff_max_total_patch_chars
            ),
            max_total_changed_lines=(
                current.review_diff_max_total_changed_lines
            ),
            git_timeout=current.review_git_timeout,
        )


@dataclass(frozen=True, slots=True)
class ReviewDiffFile:
    old_path: str | None
    new_path: str | None
    change_type: ReviewChangeType
    binary: bool
    submodule: bool
    additions: int
    deletions: int
    patch: str | None
    truncated: bool
    truncation_reason: ReviewTruncationReason | None
    changed_new_lines: tuple[int, ...]
    changed_old_lines: tuple[int, ...]
    redacted: bool = False

    @property
    def hunks(self) -> tuple[DiffHunk, ...]:
        return parse_structured_patch(self.patch or "")


@dataclass(frozen=True, slots=True)
class ReviewDiff:
    scope: ReviewScope
    repository_virtual_path: str
    base_revision: str | None
    head_revision: str | None
    files: tuple[ReviewDiffFile, ...]
    file_count: int
    total_additions: int
    total_deletions: int
    truncated: bool
    truncation_reasons: tuple[ReviewTruncationReason, ...]
    content_hash: str
    created_at: datetime
    redacted: bool = False
    source: ReviewSource = ReviewSource.WORKING_TREE
    repository: str | None = None
    pr_number: int | None = None


@dataclass(frozen=True, slots=True)
class DiffLine:
    type: DiffLineType
    old_line_number: int | None
    new_line_number: int | None
    content: str


@dataclass(frozen=True, slots=True)
class DiffHunk:
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True)
class ReviewDiffSnapshot:
    run_id: str
    status: ReviewSnapshotStatus
    diff: ReviewDiff
    summary: str
    created_at: datetime
    updated_at: datetime


class ReviewContextStore(Protocol):
    def get_run(self, run_id: str) -> Any: ...

    def get_thread(self, thread_id: str) -> Any: ...

    def get_project(self, project_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class _GitChange:
    old_path: str | None
    new_path: str | None
    change_type: ReviewChangeType
    submodule: bool = False

    @property
    def sort_path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True, slots=True)
class _ParsedPatch:
    additions: int
    deletions: int
    changed_new_lines: tuple[int, ...]
    changed_old_lines: tuple[int, ...]
    visible_new_lines: frozenset[int]
    visible_old_lines: frozenset[int]


_GITHUB_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_BEARER_TOKEN = re.compile(
    r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|secret|password|passwd|pwd|token)\b[\"']?"
    r"\s*[:=]\s*[\"']?)([^\"'\s,;#]{4,})"
)


def redact_sensitive_patch(patch: str) -> str:
    """脱敏 patch，同时保留换行数量和 unified diff 行前缀。"""

    output: list[str] = []
    inside_private_key = False
    for line in patch.splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        body = line[:-1] if ending else line
        prefix = body[:1] if body[:1] in {"+", "-", " "} else ""
        content = body[1:] if prefix else body
        upper = content.upper()

        if "-----BEGIN " in upper and "PRIVATE KEY-----" in upper:
            inside_private_key = True
            content = "[REDACTED]"
        elif inside_private_key:
            content = "[REDACTED]"
            if "-----END " in upper and "PRIVATE KEY-----" in upper:
                inside_private_key = False
        else:
            content = _GITHUB_TOKEN.sub("[REDACTED]", content)
            content = _OPENAI_STYLE_KEY.sub("[REDACTED]", content)
            content = _BEARER_TOKEN.sub(r"\1[REDACTED]", content)
            content = _CREDENTIAL_ASSIGNMENT.sub(
                r"\1[REDACTED]",
                content,
            )

        output.append(f"{prefix}{content}{ending}")
    return "".join(output)


def _parse_range(value: str) -> int:
    raw = value[1:]
    start = raw.split(",", maxsplit=1)[0]
    return int(start)


def _parse_hunk_range(value: str, prefix: str) -> tuple[int, int]:
    if not value.startswith(prefix):
        raise ValueError("invalid unified diff hunk range")
    raw = value[1:]
    parts = raw.split(",", maxsplit=1)
    start = int(parts[0])
    count = int(parts[1]) if len(parts) == 2 else 1
    if start < 0 or count < 0:
        raise ValueError("invalid unified diff hunk range")
    return start, count


def parse_structured_patch(patch: str) -> tuple[DiffHunk, ...]:
    """把已受控 unified patch 转为前端可直接渲染的 hunk 和行。"""

    hunks: list[DiffHunk] = []
    header = ""
    old_start = 0
    old_count = 0
    new_start = 0
    new_count = 0
    old_line: int | None = None
    new_line: int | None = None
    lines: list[DiffLine] = []

    def finish_hunk() -> None:
        if not header:
            return
        hunks.append(
            DiffHunk(
                header=header,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=tuple(lines),
            )
        )

    for raw_line in patch.splitlines():
        if raw_line.startswith("@@ "):
            finish_hunk()
            marker_end = raw_line.find(" @@", 3)
            if marker_end < 0:
                header = ""
                lines = []
                old_line = None
                new_line = None
                continue
            ranges = raw_line[3:marker_end].split()
            if len(ranges) < 2:
                header = ""
                lines = []
                old_line = None
                new_line = None
                continue
            try:
                old_start, old_count = _parse_hunk_range(ranges[0], "-")
                new_start, new_count = _parse_hunk_range(ranges[1], "+")
            except ValueError:
                header = ""
                lines = []
                old_line = None
                new_line = None
                continue
            header = raw_line
            lines = []
            old_line = old_start
            new_line = new_start
            continue

        if not header or old_line is None or new_line is None:
            continue
        if raw_line.startswith("\\ No newline at end of file"):
            lines.append(
                DiffLine(
                    type=DiffLineType.NO_NEWLINE,
                    old_line_number=None,
                    new_line_number=None,
                    content="No newline at end of file",
                )
            )
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            lines.append(
                DiffLine(
                    type=DiffLineType.ADDITION,
                    old_line_number=None,
                    new_line_number=new_line,
                    content=raw_line[1:],
                )
            )
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            lines.append(
                DiffLine(
                    type=DiffLineType.DELETION,
                    old_line_number=old_line,
                    new_line_number=None,
                    content=raw_line[1:],
                )
            )
            old_line += 1
        elif raw_line.startswith(" "):
            lines.append(
                DiffLine(
                    type=DiffLineType.CONTEXT,
                    old_line_number=old_line,
                    new_line_number=new_line,
                    content=raw_line[1:],
                )
            )
            old_line += 1
            new_line += 1

    finish_hunk()
    return tuple(hunks)


def review_diff_to_dict(review_diff: ReviewDiff) -> dict[str, Any]:
    """序列化已脱敏且受限的 ReviewDiff，用于 SQLite 快照。"""

    return {
        "source": review_diff.source.value,
        "repository": review_diff.repository,
        "pr_number": review_diff.pr_number,
        "scope": review_diff.scope.value,
        "repository_virtual_path": review_diff.repository_virtual_path,
        "base_revision": review_diff.base_revision,
        "head_revision": review_diff.head_revision,
        "files": [
            {
                "old_path": file.old_path,
                "new_path": file.new_path,
                "change_type": file.change_type.value,
                "binary": file.binary,
                "submodule": file.submodule,
                "additions": file.additions,
                "deletions": file.deletions,
                "patch": file.patch,
                "truncated": file.truncated,
                "truncation_reason": (
                    file.truncation_reason.value
                    if file.truncation_reason is not None
                    else None
                ),
                "changed_new_lines": list(file.changed_new_lines),
                "changed_old_lines": list(file.changed_old_lines),
                "redacted": file.redacted,
            }
            for file in review_diff.files
        ],
        "file_count": review_diff.file_count,
        "total_additions": review_diff.total_additions,
        "total_deletions": review_diff.total_deletions,
        "truncated": review_diff.truncated,
        "truncation_reasons": [
            reason.value for reason in review_diff.truncation_reasons
        ],
        "content_hash": review_diff.content_hash,
        "created_at": review_diff.created_at.isoformat(),
        "redacted": review_diff.redacted,
    }


def review_diff_from_dict(payload: dict[str, Any]) -> ReviewDiff:
    """读取本服务写入的 ReviewDiff 快照，不访问当前工作树。"""

    files = tuple(
        ReviewDiffFile(
            old_path=item.get("old_path"),
            new_path=item.get("new_path"),
            change_type=ReviewChangeType(item["change_type"]),
            binary=bool(item["binary"]),
            submodule=bool(item["submodule"]),
            additions=int(item["additions"]),
            deletions=int(item["deletions"]),
            patch=item.get("patch"),
            truncated=bool(item["truncated"]),
            truncation_reason=(
                ReviewTruncationReason(item["truncation_reason"])
                if item.get("truncation_reason") is not None
                else None
            ),
            changed_new_lines=tuple(int(line) for line in item["changed_new_lines"]),
            changed_old_lines=tuple(int(line) for line in item["changed_old_lines"]),
            redacted=bool(item.get("redacted", False)),
        )
        for item in payload["files"]
    )
    return ReviewDiff(
        source=ReviewSource(
            payload.get("source", ReviewSource.WORKING_TREE.value)
        ),
        repository=payload.get("repository"),
        pr_number=(
            int(payload["pr_number"])
            if payload.get("pr_number") is not None
            else None
        ),
        scope=ReviewScope(payload["scope"]),
        repository_virtual_path=str(payload["repository_virtual_path"]),
        base_revision=payload.get("base_revision"),
        head_revision=payload.get("head_revision"),
        files=files,
        file_count=int(payload["file_count"]),
        total_additions=int(payload["total_additions"]),
        total_deletions=int(payload["total_deletions"]),
        truncated=bool(payload["truncated"]),
        truncation_reasons=tuple(
            ReviewTruncationReason(reason)
            for reason in payload["truncation_reasons"]
        ),
        content_hash=str(payload["content_hash"]),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        redacted=bool(payload.get("redacted", False)),
    )


def parse_unified_patch(patch: str) -> _ParsedPatch:
    """确定性解析 unified diff hunk，不依赖文件名或内容正则。"""

    additions = 0
    deletions = 0
    changed_new: list[int] = []
    changed_old: list[int] = []
    visible_new: set[int] = set()
    visible_old: set[int] = set()
    old_line: int | None = None
    new_line: int | None = None

    for line in patch.splitlines():
        if line.startswith("@@ "):
            marker_end = line.find(" @@", 3)
            if marker_end < 0:
                old_line = None
                new_line = None
                continue
            ranges = line[3:marker_end].split()
            if len(ranges) < 2:
                old_line = None
                new_line = None
                continue
            try:
                old_line = _parse_range(ranges[0])
                new_line = _parse_range(ranges[1])
            except (IndexError, ValueError):
                old_line = None
                new_line = None
            continue

        if old_line is None or new_line is None:
            continue
        if line.startswith("\\ No newline at end of file"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
            changed_new.append(new_line)
            visible_new.add(new_line)
            new_line += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            deletions += 1
            changed_old.append(old_line)
            visible_old.add(old_line)
            old_line += 1
            continue
        if line.startswith(" "):
            visible_old.add(old_line)
            visible_new.add(new_line)
            old_line += 1
            new_line += 1

    return _ParsedPatch(
        additions=additions,
        deletions=deletions,
        changed_new_lines=tuple(changed_new),
        changed_old_lines=tuple(changed_old),
        visible_new_lines=frozenset(visible_new),
        visible_old_lines=frozenset(visible_old),
    )


def visible_lines_for_file(
    file: ReviewDiffFile,
    side: ReviewLineSide,
) -> frozenset[int]:
    if file.patch is None:
        return frozenset()
    parsed = parse_unified_patch(file.patch)
    if side is ReviewLineSide.OLD:
        return parsed.visible_old_lines
    return parsed.visible_new_lines


class ReviewDiffCollector:
    def __init__(
        self,
        *,
        workspace: Workspace,
        store: ReviewContextStore,
        runner: CommandRunner | None = None,
        limits: ReviewDiffLimits | None = None,
        deadline: float | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.workspace = workspace
        self.store = store
        self.runner = runner or CommandRunner(workspace, allowed_commands={"git"})
        self.limits = limits or ReviewDiffLimits.from_settings()
        self.deadline = deadline
        self.clock = clock

    def collect_for_run(
        self,
        *,
        run_id: str,
        scope: ReviewScope = ReviewScope.ALL,
    ) -> ReviewDiff:
        run = self.store.get_run(run_id)
        if run is None:
            raise ReviewDiffError(
                ReviewDiffErrorCode.RUN_NOT_FOUND,
                "Run 不存在",
            )
        thread = self.store.get_thread(run.thread_id)
        if thread is None:
            raise ReviewDiffError(
                ReviewDiffErrorCode.PROJECT_NOT_FOUND,
                "Run 没有关联的会话",
            )
        project = self.store.get_project(thread.project_id)
        if project is None:
            raise ReviewDiffError(
                ReviewDiffErrorCode.PROJECT_NOT_FOUND,
                "Run 没有关联的已注册项目",
            )
        return self.collect_project(
            project_virtual_path=project.virtual_path,
            scope=scope,
        )

    def collect_project(
        self,
        *,
        project_virtual_path: str,
        scope: ReviewScope = ReviewScope.ALL,
    ) -> ReviewDiff:
        repository_path = self._validated_repository(project_virtual_path)
        del repository_path

        status = self._git(
            [
                "git",
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
            ],
            cwd=project_virtual_path,
        )
        status_metadata = self._parse_status(status.stdout)

        head_result = self._git_optional(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=project_virtual_path,
        )
        head_sha = head_result.stdout.strip() if head_result is not None else None
        base = head_sha or EMPTY_TREE_SHA

        changes = list(
            self._tracked_changes(
                scope=scope,
                base=base,
                cwd=project_virtual_path,
                status_metadata=status_metadata,
            )
        )
        if scope is ReviewScope.ALL:
            changes.extend(
                self._untracked_changes(
                    cwd=project_virtual_path,
                    existing=changes,
                )
            )
        changes.sort(key=lambda item: item.sort_path.casefold())

        reasons: list[ReviewTruncationReason] = []
        if len(changes) > self.limits.max_files:
            changes = changes[: self.limits.max_files]
            reasons.append(ReviewTruncationReason.MAX_FILES)

        files: list[ReviewDiffFile] = []
        total_chars = 0
        total_changed = 0
        for change in changes:
            file = self._collect_file(
                change=change,
                project_virtual_path=project_virtual_path,
                scope=scope,
                base=base,
                remaining_chars=max(
                    self.limits.max_total_patch_chars - total_chars,
                    0,
                ),
                remaining_changed=max(
                    self.limits.max_total_changed_lines - total_changed,
                    0,
                ),
            )
            files.append(file)
            total_chars += len(file.patch or "")
            total_changed += file.additions + file.deletions
            if (
                file.truncation_reason is not None
                and file.truncation_reason not in reasons
            ):
                reasons.append(file.truncation_reason)

        created_at = datetime.now(timezone.utc)
        content_hash = self._content_hash(
            scope=scope,
            repository_virtual_path=project_virtual_path,
            base_revision=head_sha,
            files=files,
            reasons=reasons,
        )
        return ReviewDiff(
            scope=scope,
            repository_virtual_path=project_virtual_path,
            base_revision=head_sha,
            head_revision=None,
            files=tuple(files),
            file_count=len(files),
            total_additions=sum(file.additions for file in files),
            total_deletions=sum(file.deletions for file in files),
            truncated=bool(reasons),
            truncation_reasons=tuple(reasons),
            content_hash=content_hash,
            created_at=created_at,
            redacted=any(file.redacted for file in files),
            source=ReviewSource.WORKING_TREE,
            repository=None,
            pr_number=None,
        )

    def _validated_repository(self, virtual_path: str) -> Path:
        pure_path = PurePosixPath(virtual_path)
        if (
            not pure_path.is_absolute()
            or len(pure_path.parts) < 3
            or pure_path.parts[1] != "projects"
        ):
            raise ReviewDiffError(
                ReviewDiffErrorCode.REPOSITORY_OUTSIDE_WORKSPACE,
                "项目仓库不属于允许的 /projects 工作区",
            )
        try:
            real_path = self.workspace.resolve(virtual_path)
        except WorkspacePathError as exc:
            raise ReviewDiffError(
                ReviewDiffErrorCode.REPOSITORY_OUTSIDE_WORKSPACE,
                "项目仓库越过工作区边界",
            ) from exc
        if not real_path.is_dir():
            raise ReviewDiffError(
                ReviewDiffErrorCode.REPOSITORY_NOT_FOUND,
                "项目仓库目录不存在",
            )

        top_level = self._git_optional(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=virtual_path,
        )
        if top_level is None:
            raise ReviewDiffError(
                ReviewDiffErrorCode.REPOSITORY_NOT_FOUND,
                "已注册项目不是 Git 仓库",
            )
        try:
            discovered_root = Path(top_level.stdout.strip()).resolve()
        except (OSError, ValueError) as exc:
            raise ReviewDiffError(
                ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                "Git 返回了无效的仓库根目录",
            ) from exc
        if discovered_root != real_path:
            raise ReviewDiffError(
                ReviewDiffErrorCode.REPOSITORY_NOT_FOUND,
                "已注册项目目录不是独立 Git 仓库根目录",
            )
        return real_path

    def _tracked_changes(
        self,
        *,
        scope: ReviewScope,
        base: str,
        cwd: str,
        status_metadata: dict[str, bool],
    ) -> tuple[_GitChange, ...]:
        argv = self._diff_argv(
            scope=scope,
            base=base,
            options=[
                "--name-status",
                "-z",
                "--find-renames",
                "--find-copies",
                "--find-copies-harder",
                "--no-ext-diff",
                "--no-textconv",
            ],
        )
        result = self._git(argv, cwd=cwd)
        if result.truncated:
            raise ReviewDiffError(
                ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                "Git 文件列表超过安全输出上限",
            )
        return self._parse_name_status(result.stdout, status_metadata)

    def _untracked_changes(
        self,
        *,
        cwd: str,
        existing: list[_GitChange],
    ) -> tuple[_GitChange, ...]:
        result = self._git(
            [
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
            ],
            cwd=cwd,
        )
        if result.truncated:
            raise ReviewDiffError(
                ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                "Git 未跟踪文件列表超过安全输出上限",
            )
        known = {
            path
            for change in existing
            for path in (change.old_path, change.new_path)
            if path is not None
        }
        changes: list[_GitChange] = []
        for path in result.stdout.split("\0"):
            if not path or path in known:
                continue
            self._validate_relative_path(path)
            changes.append(
                _GitChange(
                    old_path=None,
                    new_path=path,
                    change_type=ReviewChangeType.UNTRACKED,
                )
            )
        return tuple(changes)

    def _collect_file(
        self,
        *,
        change: _GitChange,
        project_virtual_path: str,
        scope: ReviewScope,
        base: str,
        remaining_chars: int,
        remaining_changed: int,
    ) -> ReviewDiffFile:
        old_virtual = self._virtual_file_path(
            project_virtual_path,
            change.old_path,
        )
        new_virtual = self._virtual_file_path(
            project_virtual_path,
            change.new_path,
        )
        binary = False
        submodule = change.submodule
        git_output_truncated = False

        if change.change_type is ReviewChangeType.UNTRACKED:
            patch, binary, submodule, source_truncated = self._untracked_patch(
                project_virtual_path,
                change.new_path,
            )
            git_output_truncated = source_truncated
        else:
            paths = [
                path
                for path in (change.old_path, change.new_path)
                if path is not None
            ]
            result = self._git(
                self._diff_argv(
                    scope=scope,
                    base=base,
                    options=[
                        "--unified=3",
                        "--no-color",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--submodule=short",
                        "--find-renames",
                        "--find-copies",
                        "--find-copies-harder",
                    ],
                    paths=paths,
                ),
                cwd=project_virtual_path,
            )
            patch = result.stdout
            if result.truncated:
                git_output_truncated = True
                patch = patch.removesuffix(_OUTPUT_TRUNCATION_MARKER)
            binary = (
                "Binary files " in patch
                or "GIT binary patch" in patch
            )
            submodule = submodule or (
                " 160000" in patch
                or "Subproject commit " in patch
            )

        redacted = False
        if binary:
            patch = None
        elif patch is not None:
            redacted_patch = redact_sensitive_patch(patch)
            redacted = redacted_patch != patch
            patch = redacted_patch

        limited_patch, reason = self._limit_patch(
            patch,
            remaining_chars=remaining_chars,
            remaining_changed=remaining_changed,
        )
        if git_output_truncated and reason is None:
            reason = ReviewTruncationReason.GIT_OUTPUT
        parsed = parse_unified_patch(limited_patch or "")
        return ReviewDiffFile(
            old_path=old_virtual,
            new_path=new_virtual,
            change_type=change.change_type,
            binary=binary,
            submodule=submodule,
            additions=parsed.additions,
            deletions=parsed.deletions,
            patch=limited_patch,
            truncated=reason is not None,
            truncation_reason=reason,
            changed_new_lines=parsed.changed_new_lines,
            changed_old_lines=parsed.changed_old_lines,
            redacted=redacted,
        )

    def _limit_patch(
        self,
        patch: str | None,
        *,
        remaining_chars: int,
        remaining_changed: int,
    ) -> tuple[str | None, ReviewTruncationReason | None]:
        if patch is None:
            return None, None
        output: list[str] = []
        chars = 0
        changed = 0
        reason: ReviewTruncationReason | None = None
        for line in patch.splitlines(keepends=True):
            is_changed = (
                line.startswith("+") and not line.startswith("+++")
            ) or (
                line.startswith("-") and not line.startswith("---")
            )
            next_changed = changed + int(is_changed)
            next_chars = chars + len(line)
            if next_chars > self.limits.max_file_patch_chars:
                reason = ReviewTruncationReason.FILE_PATCH_CHARS
                break
            if next_changed > self.limits.max_file_changed_lines:
                reason = ReviewTruncationReason.FILE_CHANGED_LINES
                break
            if next_chars > remaining_chars:
                reason = ReviewTruncationReason.TOTAL_PATCH_CHARS
                break
            if next_changed > remaining_changed:
                reason = ReviewTruncationReason.TOTAL_CHANGED_LINES
                break
            output.append(line)
            chars = next_chars
            changed = next_changed
        return "".join(output), reason

    def _untracked_patch(
        self,
        project_virtual_path: str,
        relative_path: str | None,
    ) -> tuple[str | None, bool, bool, bool]:
        if relative_path is None:
            return None, True, False, False
        self._validate_relative_path(relative_path)
        repository = self.workspace.resolve(project_virtual_path)
        candidate = repository.joinpath(*PurePosixPath(relative_path).parts)
        if candidate.is_symlink():
            return None, True, False, False
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(repository) or not resolved.is_file():
                return None, True, resolved.is_dir(), False
            read_limit = self.limits.max_file_patch_chars * 4 + 4096
            with resolved.open("rb") as stream:
                raw = stream.read(read_limit + 1)
        except OSError:
            return None, True, False, False
        source_truncated = len(raw) > read_limit
        raw = raw[:read_limit]
        if b"\0" in raw:
            return None, True, False, source_truncated
        text: str | None = None
        for trim in range(4):
            candidate_bytes = raw[: len(raw) - trim] if trim else raw
            try:
                text = candidate_bytes.decode("utf-8")
                break
            except UnicodeDecodeError as exc:
                if not source_truncated or exc.end < len(candidate_bytes):
                    return None, True, False, source_truncated
        if text is None:
            return None, True, False, source_truncated

        lines = text.splitlines()
        header = [
            f"diff --git a/{relative_path} b/{relative_path}\n",
            "new file mode 100644\n",
            "--- /dev/null\n",
            f"+++ b/{relative_path}\n",
        ]
        if lines:
            header.append(f"@@ -0,0 +1,{len(lines)} @@\n")
            header.extend(f"+{line}\n" for line in lines)
            if not text.endswith(("\n", "\r")):
                header.append("\\ No newline at end of file\n")
        return "".join(header), False, False, source_truncated

    @staticmethod
    def _parse_status(output: str) -> dict[str, bool]:
        tokens = output.split("\0")
        metadata: dict[str, bool] = {}
        index = 0
        while index < len(tokens):
            item = tokens[index]
            index += 1
            if not item:
                continue
            kind = item[:1]
            if kind == "1":
                fields = item.split(" ", maxsplit=8)
                if len(fields) != 9:
                    raise ReviewDiffError(
                        ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                        "Git status 输出无法解析",
                    )
                metadata[fields[8]] = fields[2] != "N..."
            elif kind == "2":
                fields = item.split(" ", maxsplit=9)
                if len(fields) != 10 or index >= len(tokens):
                    raise ReviewDiffError(
                        ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                        "Git rename status 输出无法解析",
                    )
                old_path = tokens[index]
                index += 1
                metadata[fields[9]] = fields[2] != "N..."
                metadata[old_path] = fields[2] != "N..."
            elif kind == "u":
                fields = item.split(" ", maxsplit=10)
                if len(fields) == 11:
                    metadata[fields[10]] = fields[2] != "N..."
            elif kind == "?":
                metadata[item[2:]] = False
            elif kind == "!":
                continue
            else:
                raise ReviewDiffError(
                    ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                    "Git status 输出包含未知记录",
                )
        return metadata

    def _parse_name_status(
        self,
        output: str,
        status_metadata: dict[str, bool],
    ) -> tuple[_GitChange, ...]:
        tokens = output.split("\0")
        changes: list[_GitChange] = []
        index = 0
        while index < len(tokens):
            code = tokens[index]
            index += 1
            if not code:
                continue
            kind = code[:1]
            path_count = 2 if kind in {"R", "C"} else 1
            if index + path_count > len(tokens):
                raise ReviewDiffError(
                    ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                    "Git diff 文件列表无法解析",
                )
            paths = tokens[index : index + path_count]
            index += path_count
            for path in paths:
                self._validate_relative_path(path)
            if kind == "A":
                old_path, new_path = None, paths[0]
                change_type = ReviewChangeType.ADDED
            elif kind == "D":
                old_path, new_path = paths[0], None
                change_type = ReviewChangeType.DELETED
            elif kind == "R":
                old_path, new_path = paths
                change_type = ReviewChangeType.RENAMED
            elif kind == "C":
                old_path, new_path = paths
                change_type = ReviewChangeType.COPIED
            else:
                old_path = new_path = paths[0]
                change_type = ReviewChangeType.MODIFIED
            submodule = any(status_metadata.get(path, False) for path in paths)
            changes.append(
                _GitChange(
                    old_path=old_path,
                    new_path=new_path,
                    change_type=change_type,
                    submodule=submodule,
                )
            )
        return tuple(changes)

    @staticmethod
    def _validate_relative_path(path: str) -> None:
        pure_path = PurePosixPath(path)
        if (
            not path
            or "\0" in path
            or "\\" in path
            or pure_path.is_absolute()
            or ".." in pure_path.parts
        ):
            raise ReviewDiffError(
                ReviewDiffErrorCode.GIT_OUTPUT_INVALID,
                "Git 返回了不安全的文件路径",
            )

    def _virtual_file_path(
        self,
        project_virtual_path: str,
        relative_path: str | None,
    ) -> str | None:
        if relative_path is None:
            return None
        self._validate_relative_path(relative_path)
        return str(PurePosixPath(project_virtual_path) / relative_path)

    @staticmethod
    def _diff_argv(
        *,
        scope: ReviewScope,
        base: str,
        options: list[str],
        paths: list[str] | None = None,
    ) -> list[str]:
        argv = ["git", "diff", *options]
        if scope is ReviewScope.STAGED:
            argv.append("--cached")
        elif scope is ReviewScope.ALL:
            argv.append(base)
        argv.append("--")
        if paths:
            argv.extend(dict.fromkeys(paths))
        return argv

    def _git(self, argv: list[str], *, cwd: str) -> CommandResult:
        timeout = self._command_timeout()
        try:
            result = self.runner.run(
                argv,
                cwd=cwd,
                timeout=timeout,
            )
        except (CommandPolicyError, OSError) as exc:
            raise ReviewDiffError(
                ReviewDiffErrorCode.GIT_COMMAND_FAILED,
                "无法安全执行 Git 命令",
            ) from exc
        if result.timed_out:
            raise self._timeout_error()
        if result.exit_code != 0:
            raise ReviewDiffError(
                ReviewDiffErrorCode.GIT_COMMAND_FAILED,
                "Git 命令执行失败",
            )
        return result

    def _git_optional(self, argv: list[str], *, cwd: str) -> CommandResult | None:
        timeout = self._command_timeout()
        try:
            result = self.runner.run(
                argv,
                cwd=cwd,
                timeout=timeout,
            )
        except (CommandPolicyError, OSError):
            return None
        if result.timed_out:
            raise self._timeout_error()
        if result.exit_code != 0:
            return None
        return result

    def _command_timeout(self) -> float:
        timeout = self.limits.git_timeout
        if self.deadline is not None:
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                raise ReviewDiffError(
                    ReviewDiffErrorCode.RUN_TIME_LIMIT,
                    "Git Diff 收集达到 Run 总时长限制",
                )
            timeout = min(timeout, remaining)
        return timeout

    def _timeout_error(self) -> ReviewDiffError:
        if self.deadline is not None and self.clock() >= self.deadline:
            return ReviewDiffError(
                ReviewDiffErrorCode.RUN_TIME_LIMIT,
                "Git Diff 收集达到 Run 总时长限制",
            )
        return ReviewDiffError(
            ReviewDiffErrorCode.GIT_COMMAND_TIMEOUT,
            "Git 命令执行超时",
        )

    @staticmethod
    def _content_hash(
        *,
        scope: ReviewScope,
        repository_virtual_path: str,
        base_revision: str | None,
        files: list[ReviewDiffFile],
        reasons: list[ReviewTruncationReason],
    ) -> str:
        payload = {
            "scope": scope.value,
            "repository_virtual_path": repository_virtual_path,
            "base_revision": base_revision,
            "head_revision": None,
            "truncation_reasons": [reason.value for reason in reasons],
            "files": [
                {
                    "old_path": file.old_path,
                    "new_path": file.new_path,
                    "change_type": file.change_type.value,
                    "binary": file.binary,
                    "submodule": file.submodule,
                    "additions": file.additions,
                    "deletions": file.deletions,
                    "patch": file.patch,
                    "truncated": file.truncated,
                    "truncation_reason": (
                        file.truncation_reason.value
                        if file.truncation_reason is not None
                        else None
                    ),
                    "changed_new_lines": file.changed_new_lines,
                    "changed_old_lines": file.changed_old_lines,
                    "redacted": file.redacted,
                }
                for file in files
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
