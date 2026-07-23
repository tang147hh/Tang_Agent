from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.core.task_intent import TaskKind
from app.core.review import (
    ReviewFindingDraft,
    ReviewFindingSnapshot,
    ReviewFindingStatus,
    ReviewSeverity,
)


class ThreadStatus(StrEnum):
    """会话当前是否正在执行 Agent。"""

    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """工作区项目的不可变快照。"""

    project_id: str
    name: str
    virtual_path: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ThreadSnapshot:
    """一个项目下的多轮会话快照。"""

    thread_id: str
    project_id: str
    title: str
    status: ThreadStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    sequence: int
    message_id: str
    thread_id: str
    run_id: str | None
    role: MessageRole
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    thread_id: str
    task_kind: TaskKind
    status: RunStatus
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunPerformanceSnapshot:
    """与业务 run_id 一对一的预算和性能指标。"""

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


@dataclass(frozen=True, slots=True)
class RunEventSnapshot:
    """一次 Run 执行过程中产生的追加式事件。"""

    event_id: int
    run_id: str
    kind: str
    source: str
    payload: dict[str, Any]
    created_at: datetime

class ProjectThreadStore(Protocol):
    """项目和会话持久化协议。"""

    def create_project(
        self,
        *,
        name: str,
        virtual_path: str,
    ) -> ProjectSnapshot: ...

    def get_project(
        self,
        project_id: str,
    ) -> ProjectSnapshot | None: ...

    def list_projects(
        self,
    ) -> list[ProjectSnapshot]: ...

    def create_thread(
        self,
        *,
        project_id: str,
        title: str,
    ) -> ThreadSnapshot: ...

    def get_thread(
        self,
        thread_id: str,
    ) -> ThreadSnapshot | None: ...

    def list_threads(
        self,
        project_id: str,
    ) -> list[ThreadSnapshot]: ...


class ConversationStore(
    ProjectThreadStore,
    Protocol,
):
    """项目、会话、消息和运行持久化协议。"""

    def create_run(
        self,
        *,
        thread_id: str,
        task_kind: TaskKind = TaskKind.QA,
    ) -> RunSnapshot: ...

    def start_run_with_message(
        self,
        *,
        thread_id: str,
        content: str,
        task_kind: TaskKind = TaskKind.QA,
    ) -> tuple[RunSnapshot, MessageSnapshot]:
        """原子创建 Run 和对应的用户消息。"""
        ...

    def get_run(
        self,
        run_id: str,
    ) -> RunSnapshot | None: ...

    def list_runs(
        self,
        thread_id: str,
    ) -> list[RunSnapshot]: ...

    def mark_run_running(
        self,
        run_id: str,
    ) -> RunSnapshot: ...

    def complete_run(
        self,
        run_id: str,
    ) -> RunSnapshot: ...

    def complete_run_with_message(
        self,
        run_id: str,
        content: str,
    ) -> tuple[RunSnapshot, MessageSnapshot]:
        """原子保存 assistant 消息并完成 Run。"""
        ...

    def fail_run(
        self,
        run_id: str,
        error: str,
    ) -> RunSnapshot: ...

    def initialize_run_performance(
        self,
        *,
        run_id: str,
        task_kind: TaskKind,
        max_model_calls: int,
        max_tool_calls: int,
        max_first_output_seconds: float,
        max_seconds: float,
        max_identical_tool_calls: int,
    ) -> RunPerformanceSnapshot: ...

    def update_run_performance(
        self,
        *,
        run_id: str,
        model_calls: int,
        tool_calls: int,
        repeated_tool_calls: int,
        tool_errors: int,
        safety_rejections: int,
        first_output_ms: float | None,
        duration_ms: float,
        termination_reason: str | None,
    ) -> RunPerformanceSnapshot: ...

    def get_run_performance(
        self,
        run_id: str,
    ) -> RunPerformanceSnapshot | None: ...

    def add_review_findings(
        self,
        *,
        run_id: str,
        findings: list[ReviewFindingDraft],
    ) -> tuple[list[ReviewFindingSnapshot], int]: ...

    def list_review_findings(
        self,
        run_id: str,
        *,
        severity: ReviewSeverity | None = None,
        status: ReviewFindingStatus | None = None,
    ) -> list[ReviewFindingSnapshot]: ...

    def update_review_finding_status(
        self,
        *,
        run_id: str,
        finding_id: str,
        status: ReviewFindingStatus,
    ) -> ReviewFindingSnapshot: ...

    def append_message(
        self,
        *,
        thread_id: str,
        role: MessageRole,
        content: str,
        run_id: str | None = None,
    ) -> MessageSnapshot: ...

    def list_messages(
        self,
        thread_id: str,
    ) -> list[MessageSnapshot]: ...

    def append_run_event(
        self,
        *,
        run_id: str,
        kind: str,
        source: str,
        payload: dict[str, Any],
    ) -> RunEventSnapshot:
        """向 Run 追加一条事件。"""
        ...

    def list_run_events(
        self,
        run_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[RunEventSnapshot]:
        """读取指定游标之后的 Run 事件。"""
        ...
