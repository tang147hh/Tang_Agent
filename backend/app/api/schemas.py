from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
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
from app.core.code_review import CodeReviewStatus
from app.core.github_review import (
    GitHubPublicationStatus,
    GitHubReviewEvent,
)
from app.core.review_diff import (
    DiffLineType,
    ReviewChangeType,
    ReviewLineSide,
    ReviewScope,
    ReviewSource,
    ReviewSnapshotStatus,
    ReviewTruncationReason,
)
from app.repositories import FileChangeStatus


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
    network_access: bool
    network_provider: str
    network_request_count: int
    network_result_count: int
    network_bytes_received: int
    network_cache_hit_count: int
    network_limit_reached: bool
    network_limit_reason: str | None
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


class NetworkBudgetResponse(BaseModel):
    max_searches: int
    max_results_per_search: int
    request_timeout_seconds: float
    max_result_chars_per_search: int
    max_total_result_chars: int
    max_bytes_received: int


class WebSearchCapabilityResponse(BaseModel):
    available: bool
    provider: str
    configured: bool
    provider_available: bool
    allowed_in_mode: bool
    enabled_for_run: bool
    unavailable_reason: str | None


class ToolCapabilityResponse(BaseModel):
    name: str
    category: str
    risk_level: str
    allowed_task_kinds: list[TaskKind]
    requires_network_access: bool
    model_callable: bool
    description: str
    availability: bool
    unavailable_reason: str | None


class ToolCapabilitiesResponse(BaseModel):
    task_kind: TaskKind
    run_id: str | None
    network_access: bool
    network_provider: str
    web_search: WebSearchCapabilityResponse
    network_budget: NetworkBudgetResponse
    tools: list[ToolCapabilityResponse]


class ReviewFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class ReviewFindingStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReviewFindingStatus


class CodeReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ReviewScope = ReviewScope.ALL
    source: ReviewSource = ReviewSource.WORKING_TREE
    pr_number: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def validate_review_source(self) -> "CodeReviewRequest":
        if self.source is ReviewSource.PULL_REQUEST and self.pr_number is None:
            raise ValueError("pull_request Review 必须提供 pr_number")
        if self.source is ReviewSource.WORKING_TREE and self.pr_number is not None:
            raise ValueError("working_tree Review 不能提供 pr_number")
        return self


class DiffLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: DiffLineType
    old_line_number: int | None
    new_line_number: int | None
    content: str


class DiffHunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLineResponse]


class ReviewDiffFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    old_path: str | None
    new_path: str | None
    change_type: ReviewChangeType
    binary: bool
    submodule: bool
    additions: int
    deletions: int
    truncated: bool
    truncation_reason: ReviewTruncationReason | None
    changed_new_lines: list[int]
    changed_old_lines: list[int]
    redacted: bool
    hunks: list[DiffHunkResponse]


class ReviewDiffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope: ReviewScope
    source: ReviewSource
    repository: str | None
    pr_number: int | None
    repository_virtual_path: str
    base_revision: str | None
    head_revision: str | None
    files: list[ReviewDiffFileResponse]
    file_count: int
    total_additions: int
    total_deletions: int
    truncated: bool
    truncation_reasons: list[ReviewTruncationReason]
    content_hash: str
    created_at: datetime
    redacted: bool


class CodeReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    status: CodeReviewStatus
    scope: ReviewScope
    diff: ReviewDiffResponse
    findings: list[ReviewFindingResponse]
    finding_count: int
    created_count: int
    duplicate_count: int
    summary: str


class ReviewSnapshotResponse(BaseModel):
    run_id: str
    status: ReviewSnapshotStatus
    scope: ReviewScope
    diff: ReviewDiffResponse
    findings: list[ReviewFindingResponse]
    finding_count: int
    summary: str
    created_at: datetime
    updated_at: datetime


class GitHubPullRequestResponse(BaseModel):
    pr_number: int
    title: str
    url: str
    state: str
    is_draft: bool
    base_branch: str
    head_branch: str
    base_sha: str
    head_sha: str
    author: str
    repository: str


class GitHubReviewCapabilityResponse(BaseModel):
    gh_installed: bool
    authenticated: bool
    remote_found: bool
    publish_enabled: bool
    can_publish: bool
    reason: str | None
    repository: str | None
    current_user: str | None
    pull_requests: list[GitHubPullRequestResponse]


class GitHubReviewPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_number: int = Field(strict=True, ge=1)
    selected_finding_ids: list[str] = Field(max_length=500)
    event: GitHubReviewEvent = GitHubReviewEvent.COMMENT
    summary: str | None = Field(default=None, max_length=20_000)

    @field_validator("selected_finding_ids")
    @classmethod
    def validate_finding_ids(cls, value: list[str]) -> list[str]:
        if any(not identifier.strip() or len(identifier) > 100 for identifier in value):
            raise ValueError("selected_finding_ids 包含无效 ID")
        return value


class GitHubReviewPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: str = Field(min_length=1, max_length=100)


class GitHubInlineCommentPreview(BaseModel):
    finding_id: str
    path: str
    line: int
    side: str
    body: str
    start_line: int | None = None
    start_side: str | None = None


class GitHubFindingPublicationNote(BaseModel):
    finding_id: str
    title: str
    reason: str


class GitHubReviewPrepareResponse(BaseModel):
    publication_id: str
    repository: str
    pr_number: int
    pr_title: str
    pr_url: str
    base_sha: str
    head_sha: str
    event: GitHubReviewEvent
    inline_comments: list[GitHubInlineCommentPreview]
    summary_comments: list[GitHubFindingPublicationNote]
    summary_body: str
    skipped_findings: list[GitHubFindingPublicationNote]
    warnings: list[str]
    payload_hash: str
    expires_at: datetime


class GitHubReviewPublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    repository: str
    pr_number: int
    base_sha: str
    head_sha: str
    event: GitHubReviewEvent
    selected_finding_ids: list[str]
    payload_hash: str
    status: GitHubPublicationStatus
    github_review_id: str | None
    github_review_url: str | None
    github_user: str | None
    prepared_at: datetime
    expires_at: datetime
    published_at: datetime | None
    error_code: str | None
    error_message: str | None


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_kind: TaskKind | None = None
    network_access: bool = False
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


class FileChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    additions: int | None
    deletions: int | None
    binary: bool
    status: FileChangeStatus


class ProjectFileChangesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_path: str
    changed_files: int
    additions: int
    deletions: int
    binary_files: int
    hidden_files: int
    files: list[FileChangeResponse]


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
