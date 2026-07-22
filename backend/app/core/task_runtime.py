from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from app.core.task_intent import TaskKind


logger = logging.getLogger("app.task_runtime")

AgentFactory = Callable[[TaskKind], Any]


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    thread_id: str
    prompt: str
    task_kind: TaskKind
    status: TaskStatus
    result: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

class TaskStore(Protocol):
    """业务任务持久化协议。"""

    def create(
        self,
        *,
        prompt: str,
        task_kind: TaskKind,
    ) -> TaskSnapshot:
        ...

    def get(
        self,
        thread_id: str,
    ) -> TaskSnapshot | None:
        ...

    def mark_running(
        self,
        thread_id: str,
    ) -> TaskSnapshot:
        ...

    def complete(
        self,
        thread_id: str,
        result: str,
    ) -> TaskSnapshot:
        ...

    def fail(
        self,
        thread_id: str,
        error: str,
    ) -> TaskSnapshot:
        ...


@dataclass(slots=True)
class _TaskRecord:
    thread_id: str
    prompt: str
    task_kind: TaskKind
    status: TaskStatus
    result: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class TaskRegistry:
    """线程安全的课程版内存任务注册表。"""

    def __init__(self) -> None:
        self._records: dict[str, _TaskRecord] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        prompt: str,
        task_kind: TaskKind,
    ) -> TaskSnapshot:
        now = datetime.now(timezone.utc)
        thread_id = str(uuid.uuid4())

        record = _TaskRecord(
            thread_id=thread_id,
            prompt=prompt,
            task_kind=task_kind,
            status=TaskStatus.PENDING,
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            self._records[thread_id] = record

        return self._snapshot(record)

    def get(
        self,
        thread_id: str,
    ) -> TaskSnapshot | None:
        with self._lock:
            record = self._records.get(thread_id)

            if record is None:
                return None

            return self._snapshot(record)

    def mark_running(
        self,
        thread_id: str,
    ) -> TaskSnapshot:
        return self._update(
            thread_id,
            status=TaskStatus.RUNNING,
            result=None,
            error=None,
        )

    def complete(
        self,
        thread_id: str,
        result: str,
    ) -> TaskSnapshot:
        return self._update(
            thread_id,
            status=TaskStatus.COMPLETED,
            result=result,
            error=None,
        )

    def fail(
        self,
        thread_id: str,
        error: str,
    ) -> TaskSnapshot:
        return self._update(
            thread_id,
            status=TaskStatus.FAILED,
            result=None,
            error=error,
        )

    def _update(
        self,
        thread_id: str,
        *,
        status: TaskStatus,
        result: str | None,
        error: str | None,
    ) -> TaskSnapshot:
        with self._lock:
            record = self._records.get(thread_id)

            if record is None:
                raise KeyError(
                    f"任务不存在：{thread_id}"
                )

            record.status = status
            record.result = result
            record.error = error
            record.updated_at = datetime.now(timezone.utc)

            return self._snapshot(record)

    @staticmethod
    def _snapshot(
        record: _TaskRecord,
    ) -> TaskSnapshot:
        """返回不可变快照，避免锁外修改内部记录。"""

        return TaskSnapshot(
            thread_id=record.thread_id,
            prompt=record.prompt,
            task_kind=record.task_kind,
            status=record.status,
            result=record.result,
            error=record.error,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


def _final_answer(result: Any) -> str:
    if not isinstance(result, dict):
        raise RuntimeError("Agent 返回值不是字典")

    messages = result.get("messages")

    if not isinstance(messages, list) or not messages:
        raise RuntimeError("Agent 没有返回消息")

    content = getattr(messages[-1], "content", "")

    if isinstance(content, str) and content.strip():
        return content.strip()

    if content:
        return str(content)

    raise RuntimeError("Agent 最终消息为空")


def run_agent_task(
    *,
    thread_id: str,
    task_store: TaskStore,
    agent_factory: AgentFactory,
) -> None:
    """后台执行 Agent，并把结果写回任务注册表。"""

    task = task_store.mark_running(thread_id)

    try:
        agent = agent_factory(task.task_kind)

        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": task.prompt,
                    }
                ]
            },
            config={
                "configurable": {
                    "thread_id": thread_id,
                }
            },
        )

        task_store.complete(
            thread_id,
            _final_answer(result),
        )
    except Exception:
        logger.exception(
            "Agent 后台任务失败：thread_id=%s",
            thread_id,
        )

        # 不把供应商异常、请求细节或潜在凭据返回给客户端。
        task_store.fail(
            thread_id,
            "任务执行失败，请查看服务日志",
        )