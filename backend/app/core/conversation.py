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