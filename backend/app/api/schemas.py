from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.conversation import (
    MessageRole,
    RunStatus,
    ThreadStatus,
)
from app.core.task_intent import TaskKind
from app.core.task_runtime import TaskStatus
from app.core.review import (
    ReviewCategory,
    ReviewFindingStatus,
    ReviewSeverity,
)


class TaskCreateRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=20_000,
    )

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("prompt 不能为空")

        return normalized


class TaskResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    thread_id: str
    prompt: str
    task_kind: TaskKind
    status: TaskStatus
    result: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class ProjectCreateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
    )
    virtual_path: str = Field(
        min_length=1,
        max_length=1_000,
    )

    @field_validator(
        "name",
        "virtual_path",
    )
    @classmethod
    def normalize_project_text(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("字段不能为空")

        return normalized


class ProjectResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    project_id: str
    name: str
    virtual_path: str
    created_at: datetime
    updated_at: datetime


class ThreadCreateRequest(BaseModel):
    title: str = Field(
        default="新对话",
        min_length=1,
        max_length=200,
    )

    @field_validator("title")
    @classmethod
    def normalize_title(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("title 不能为空")

        return normalized


class ThreadResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    thread_id: str
    project_id: str
    title: str
    status: ThreadStatus
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    sequence: int
    message_id: str
    thread_id: str
    run_id: str | None
    role: MessageRole
    content: str
    created_at: datetime


class RunResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    run_id: str
    thread_id: str
    task_kind: TaskKind
    status: RunStatus
    error: str | None
    created_at: datetime
    updated_at: datetime


class RunPerformanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    task_kind: TaskKind
    max_model_calls: int
    max_tool_calls: int
    max_first_output_seconds: float
    max_seconds: float
    max_identical_tool_calls: int
    model_calls: int
    tool_calls: int
    repeated_tool_calls: int
    tool_errors: int
    safety_rejections: int
    first_output_ms: float | None
    duration_ms: float | None
    termination_reason: str | None
    created_at: datetime
    updated_at: datetime


class ReviewFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class ReviewFindingStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReviewFindingStatus


class RunCreateRequest(BaseModel):
    task_kind: TaskKind | None = None
    content: str = Field(
        min_length=1,
        max_length=20_000,
    )

    @field_validator("content")
    @classmethod
    def normalize_content(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("content 不能为空")

        return normalized


class RunStartResponse(BaseModel):
    run: RunResponse
    message: MessageResponse


class SkillSummaryResponse(BaseModel):
    name: str
    description: str
    path: str

class SkillDetailResponse(SkillSummaryResponse):
    content: str


class RepositoryCloneRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2_048)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("url 不能为空")

        return normalized


class RepositoryBranchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("name 不能为空")

        return normalized


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    path: str
    remote_url: str
    current_branch: str
    branches: list[str]
    dirty: bool


class RepositoryCommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("message 不能为空")

        return normalized


class RepositoryCommitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repository: RepositoryResponse
    sha: str
    subject: str


class RepositoryPushResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repository: RepositoryResponse
    branch: str


class PullRequestCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=10_000)
    base: str = Field(default="main", min_length=1, max_length=255)

    @field_validator("title", "base")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("字段不能为空")

        return normalized

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        return value.strip()


class PullRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    url: str
    title: str
    base: str
    head: str
