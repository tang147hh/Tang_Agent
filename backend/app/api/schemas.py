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
    status: RunStatus
    error: str | None
    created_at: datetime
    updated_at: datetime
