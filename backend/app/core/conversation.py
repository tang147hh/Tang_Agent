from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


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
    status: RunStatus
    error: str | None
    created_at: datetime
    updated_at: datetime

class ProjectThreadStore(Protocol):
    """项目和会话持久化协议。"""

    def create_project(
        self,
        *,
        name: str,
        virtual_path: str,
    ) -> ProjectSnapshot:
        ...

    def get_project(
        self,
        project_id: str,
    ) -> ProjectSnapshot | None:
        ...

    def list_projects(
        self,
    ) -> list[ProjectSnapshot]:
        ...

    def create_thread(
        self,
        *,
        project_id: str,
        title: str,
    ) -> ThreadSnapshot:
        ...

    def get_thread(
        self,
        thread_id: str,
    ) -> ThreadSnapshot | None:
        ...

    def list_threads(
        self,
        project_id: str,
    ) -> list[ThreadSnapshot]:
        ...

class ConversationStore(
    ProjectThreadStore,
    Protocol,
):
    """项目、会话、消息和运行持久化协议。"""

    def create_run(
        self,
        *,
        thread_id: str,
    ) -> RunSnapshot:
        ...

    def start_run_with_message(
        self,
        *,
        thread_id: str,
        content: str,
    ) -> tuple[RunSnapshot, MessageSnapshot]:
        """原子创建 Run 和对应的用户消息。"""
        ...

    def get_run(
        self,
        run_id: str,
    ) -> RunSnapshot | None:
        ...

    def list_runs(
        self,
        thread_id: str,
    ) -> list[RunSnapshot]:
        ...

    def mark_run_running(
        self,
        run_id: str,
    ) -> RunSnapshot:
        ...

    def complete_run(
        self,
        run_id: str,
    ) -> RunSnapshot:
        ...

    def fail_run(
        self,
        run_id: str,
        error: str,
    ) -> RunSnapshot:
        ...

    def append_message(
        self,
        *,
        thread_id: str,
        role: MessageRole,
        content: str,
        run_id: str | None = None,
    ) -> MessageSnapshot:
        ...

    def list_messages(
        self,
        thread_id: str,
    ) -> list[MessageSnapshot]:
        ...