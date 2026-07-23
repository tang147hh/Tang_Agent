from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
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
    title: str
    description: str
    suggestion: str | None
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ReviewFindingSnapshot:
    id: str
    run_id: str
    severity: ReviewSeverity
    category: ReviewCategory
    file_path: str | None
    start_line: int | None
    end_line: int | None
    title: str
    description: str
    suggestion: str | None
    status: ReviewFindingStatus
    fingerprint: str
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

        location = (self.file_path, self.start_line, self.end_line)
        if all(value is None for value in location):
            return self
        if any(value is None for value in location):
            raise ValueError(
                "file_path、start_line、end_line 必须同时提供或同时为空"
            )
        assert self.start_line is not None
        assert self.end_line is not None
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
) -> str:
    canonical_path = file_path if file_path is not None else finding.file_path
    payload = {
        "file_path": canonical_path,
        "start_line": finding.start_line,
        "end_line": finding.end_line,
        "severity": finding.severity.value,
        "category": finding.category.value,
        "title": _normalized_title(finding.title),
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
        unique: dict[str, ReviewFindingDraft] = {}
        for model_finding in output.findings:
            canonical_path = None
            if model_finding.file_path is not None:
                canonical_path = normalize_review_file_path(
                    workspace=self.workspace,
                    project_virtual_path=project.virtual_path,
                    file_path=model_finding.file_path,
                )
            fingerprint = review_fingerprint(
                model_finding,
                file_path=canonical_path,
            )
            unique.setdefault(
                fingerprint,
                ReviewFindingDraft(
                    severity=model_finding.severity,
                    category=model_finding.category,
                    file_path=canonical_path,
                    start_line=model_finding.start_line,
                    end_line=model_finding.end_line,
                    title=model_finding.title,
                    description=model_finding.description,
                    suggestion=model_finding.suggestion,
                    fingerprint=fingerprint,
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
