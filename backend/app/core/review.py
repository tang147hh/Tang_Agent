from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.backends.workspace import Workspace, WorkspacePathError
from app.core.review_diff import (
    ReviewDiff,
    ReviewDiffFile,
    ReviewLineSide,
    ReviewScope,
    redact_sensitive_patch,
    visible_lines_for_file,
)


class ReviewSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewCategory(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    DOCUMENTATION = "documentation"


class ReviewFindingStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class ReviewFindingDraft:
    """已经过模型字段和工作区路径校验、尚未持久化的 Finding。"""

    severity: ReviewSeverity
    category: ReviewCategory
    file_path: str | None
    start_line: int | None
    end_line: int | None
    line_side: ReviewLineSide | None
    title: str
    description: str
    suggestion: str | None
    fingerprint: str
    review_diff_hash: str | None
    review_scope: ReviewScope | None
    base_revision: str | None
    head_revision: str | None


@dataclass(frozen=True, slots=True)
class ReviewFindingSnapshot:
    id: str
    run_id: str
    severity: ReviewSeverity
    category: ReviewCategory
    file_path: str | None
    start_line: int | None
    end_line: int | None
    line_side: ReviewLineSide | None
    title: str
    description: str
    suggestion: str | None
    status: ReviewFindingStatus
    fingerprint: str
    review_diff_hash: str | None
    review_scope: ReviewScope | None
    base_revision: str | None
    head_revision: str | None
    created_at: datetime
    updated_at: datetime


class ModelReviewFinding(BaseModel):
    """Reviewer 模型唯一可以提供的 Finding 字段。"""

    model_config = ConfigDict(extra="forbid")

    severity: ReviewSeverity
    category: ReviewCategory
    file_path: str | None
    start_line: int | None = Field(default=None, strict=True, ge=1)
    end_line: int | None = Field(default=None, strict=True, ge=1)
    line_side: ReviewLineSide | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10_000)
    suggestion: str | None = Field(default=None, max_length=10_000)

    @field_validator("title", "description")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("file_path")
    @classmethod
    def strip_file_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("file_path 不能为空")
        return normalized

    @field_validator("suggestion")
    @classmethod
    def strip_suggestion(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_location_and_required_text(self) -> "ModelReviewFinding":
        if not self.title:
            raise ValueError("title 不能为空")
        if not self.description:
            raise ValueError("description 不能为空")

        if self.file_path is None:
            if any(
                value is not None
                for value in (self.start_line, self.end_line, self.line_side)
            ):
                raise ValueError(
                    "file_path、start_line、end_line 必须同时提供；"
                    "全局 Finding 不能提供行号或 line_side"
                )
            return self

        if self.start_line is None and self.end_line is None:
            if self.line_side is not None:
                raise ValueError("文件级 Finding 不能提供 line_side")
            return self
        if self.start_line is None or self.end_line is None:
            raise ValueError(
                "start_line、end_line 必须同时提供或同时为空"
            )
        if self.end_line < self.start_line:
            raise ValueError("end_line 不能小于 start_line")
        return self


class ReviewerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[ModelReviewFinding] = Field(max_length=500)
    summary: str = Field(min_length=1, max_length=10_000)

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("summary 不能为空")
        return normalized


@dataclass(frozen=True, slots=True)
class ReviewSaveResult:
    findings: tuple[ReviewFindingSnapshot, ...]
    summary: str
    created_count: int
    duplicate_count: int


class ReviewOutputError(ValueError):
    """Reviewer 返回值不是可安全持久化的结构化结果。"""


class ReviewStore(Protocol):
    def get_run(self, run_id: str) -> Any: ...

    def get_thread(self, thread_id: str) -> Any: ...

    def get_project(self, project_id: str) -> Any: ...

    def add_review_findings(
        self,
        *,
        run_id: str,
        findings: list[ReviewFindingDraft],
    ) -> tuple[list[ReviewFindingSnapshot], int]: ...


_JSON_FENCE = re.compile(
    r"\A\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)


def parse_reviewer_output(raw_output: Any) -> ReviewerModelOutput:
    """解析 JSON 对象或单个 Markdown JSON 代码块，并整批校验。"""

    parsed: Any
    if isinstance(raw_output, str):
        candidate = raw_output.strip()
        fence = _JSON_FENCE.fullmatch(candidate)
        if fence is not None:
            candidate = fence.group("body").strip()
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError) as exc:
            raise ReviewOutputError("Reviewer 输出不是合法 JSON") from exc
    elif isinstance(raw_output, dict):
        parsed = raw_output
    else:
        raise ReviewOutputError("Reviewer 输出必须是 JSON 对象")

    try:
        return ReviewerModelOutput.model_validate(parsed)
    except ValidationError as exc:
        reasons = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        raise ReviewOutputError(
            f"Reviewer 结构化输出校验失败：{reasons}"
        ) from exc


def _normalized_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def review_fingerprint(
    finding: ReviewFindingDraft | ModelReviewFinding,
    file_path: str | None = None,
    *,
    line_side: ReviewLineSide | None = None,
    review_diff_hash: str | None = None,
) -> str:
    canonical_path = file_path if file_path is not None else finding.file_path
    payload = {
        "file_path": canonical_path,
        "start_line": finding.start_line,
        "end_line": finding.end_line,
        "line_side": (
            line_side.value
            if line_side is not None
            else (
                finding.line_side.value
                if finding.line_side is not None
                else None
            )
        ),
        "severity": finding.severity.value,
        "category": finding.category.value,
        "title": _normalized_title(finding.title),
        "review_diff_hash": review_diff_hash,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_review_file_path(
    *,
    workspace: Workspace,
    project_virtual_path: str,
    file_path: str,
) -> str:
    """把 Reviewer 路径规范化为当前项目内的虚拟工作区路径。"""

    raw_path = file_path.strip()
    if not raw_path:
        raise ReviewOutputError("file_path 不能为空")
    if PureWindowsPath(raw_path).drive:
        raise ReviewOutputError(
            "file_path 不是安全的虚拟工作区路径"
        )

    try:
        project_real_path = workspace.resolve(project_virtual_path)
        if PurePosixPath(raw_path).is_absolute():
            candidate = workspace.resolve(raw_path)
        else:
            candidate = workspace.resolve(
                str(PurePosixPath(project_virtual_path) / raw_path)
            )
        canonical_path = workspace.to_virtual(candidate)
    except WorkspacePathError as exc:
        raise ReviewOutputError(
            "file_path 不是安全的虚拟工作区路径"
        ) from exc

    if candidate == project_real_path or not candidate.is_relative_to(
        project_real_path
    ):
        raise ReviewOutputError("file_path 不属于当前 Run 的项目")

    return canonical_path


class ReviewFindingService:
    """模型结果到 run 级 Finding 的唯一持久化入口。"""

    def __init__(self, store: ReviewStore, workspace: Workspace) -> None:
        self.store = store
        self.workspace = workspace

    def save_model_output(
        self,
        *,
        run_id: str,
        raw_output: Any,
        review_diff: ReviewDiff | None = None,
        require_review_diff: bool = False,
    ) -> ReviewSaveResult:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"Run 不存在：{run_id}")
        thread = self.store.get_thread(run.thread_id)
        if thread is None:
            raise KeyError(f"Thread 不存在：{run.thread_id}")
        project = self.store.get_project(thread.project_id)
        if project is None:
            raise KeyError(f"Project 不存在：{thread.project_id}")

        output = parse_reviewer_output(raw_output)
        if require_review_diff and review_diff is None:
            raise ReviewOutputError(
                "Reviewer 缺少本次受控 ReviewDiff，拒绝持久化 Finding"
            )
        unique: dict[str, ReviewFindingDraft] = {}
        for model_finding in output.findings:
            canonical_path = None
            if model_finding.file_path is not None:
                canonical_path = normalize_review_file_path(
                    workspace=self.workspace,
                    project_virtual_path=project.virtual_path,
                    file_path=model_finding.file_path,
                )
            line_side = model_finding.line_side
            if (
                line_side is None
                and review_diff is None
                and model_finding.start_line is not None
            ):
                # 第 34 课历史输出没有 line_side，默认按新文件侧保存。
                line_side = ReviewLineSide.NEW
            if review_diff is not None:
                line_side = self._validate_diff_location(
                    finding=model_finding,
                    canonical_path=canonical_path,
                    review_diff=review_diff,
                )
            fingerprint = review_fingerprint(
                model_finding,
                file_path=canonical_path,
                line_side=line_side,
                review_diff_hash=(
                    review_diff.content_hash
                    if review_diff is not None
                    else None
                ),
            )
            title = redact_sensitive_patch(model_finding.title)
            description = redact_sensitive_patch(model_finding.description)
            suggestion = (
                redact_sensitive_patch(model_finding.suggestion)
                if model_finding.suggestion is not None
                else None
            )
            unique.setdefault(
                fingerprint,
                ReviewFindingDraft(
                    severity=model_finding.severity,
                    category=model_finding.category,
                    file_path=canonical_path,
                    start_line=model_finding.start_line,
                    end_line=model_finding.end_line,
                    line_side=line_side,
                    title=title,
                    description=description,
                    suggestion=suggestion,
                    fingerprint=fingerprint,
                    review_diff_hash=(
                        review_diff.content_hash
                        if review_diff is not None
                        else None
                    ),
                    review_scope=(
                        review_diff.scope
                        if review_diff is not None
                        else None
                    ),
                    base_revision=(
                        review_diff.base_revision
                        if review_diff is not None
                        else None
                    ),
                    head_revision=(
                        review_diff.head_revision
                        if review_diff is not None
                        else None
                    ),
                ),
            )

        stored, created_count = self.store.add_review_findings(
            run_id=run_id,
            findings=list(unique.values()),
        )
        return ReviewSaveResult(
            findings=tuple(stored),
            summary=output.summary,
            created_count=created_count,
            duplicate_count=len(output.findings) - created_count,
        )

    @staticmethod
    def _validate_diff_location(
        *,
        finding: ModelReviewFinding,
        canonical_path: str | None,
        review_diff: ReviewDiff,
    ) -> ReviewLineSide | None:
        if canonical_path is None:
            return None

        candidates: list[tuple[ReviewDiffFile, ReviewLineSide]] = []
        for file in review_diff.files:
            if canonical_path == file.new_path:
                candidates.append((file, ReviewLineSide.NEW))
            if canonical_path == file.old_path:
                candidates.append((file, ReviewLineSide.OLD))
        if not candidates:
            raise ReviewOutputError(
                "Finding file_path 不在本次受控 Diff 中"
            )

        if finding.start_line is None or finding.end_line is None:
            return None

        if any(file.binary for file, _ in candidates):
            raise ReviewOutputError("二进制文件只能产生文件级 Finding")

        requested_side = finding.line_side
        if requested_side is not None:
            candidates = [
                item for item in candidates if item[1] is requested_side
            ]
            if not candidates:
                raise ReviewOutputError(
                    "Finding line_side 与 Diff 文件路径不一致"
                )

        requested_lines = set(
            range(finding.start_line, finding.end_line + 1)
        )
        for file, side in candidates:
            changed_lines = set(
                file.changed_old_lines
                if side is ReviewLineSide.OLD
                else file.changed_new_lines
            )
            visible_lines = visible_lines_for_file(file, side)
            if (
                requested_lines <= visible_lines
                and requested_lines & changed_lines
            ):
                return side

        raise ReviewOutputError(
            "Finding 行号不属于模型实际看到的 Diff 变更区域"
        )
