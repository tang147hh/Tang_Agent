from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
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