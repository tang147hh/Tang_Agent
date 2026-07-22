from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    ToolMessage,
    ToolMessageChunk,
)

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


@dataclass(frozen=True, slots=True)
class TaskEvent:
    id: int
    thread_id: str
    kind: str
    source: str
    payload: dict[str, Any]
    created_at: datetime


class TaskStore(Protocol):
    """业务任务持久化协议。"""

    def create(
        self,
        *,
        prompt: str,
        task_kind: TaskKind,
    ) -> TaskSnapshot: ...

    def get(
        self,
        thread_id: str,
    ) -> TaskSnapshot | None: ...

    def mark_running(
        self,
        thread_id: str,
    ) -> TaskSnapshot: ...

    def complete(
        self,
        thread_id: str,
        result: str,
    ) -> TaskSnapshot: ...

    def fail(
        self,
        thread_id: str,
        error: str,
    ) -> TaskSnapshot: ...

    def append_event(
        self,
        *,
        thread_id: str,
        kind: str,
        source: str,
        payload: dict[str, Any],
    ) -> TaskEvent: ...

    def list_events(
        self,
        thread_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[TaskEvent]: ...


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
        self._events: dict[str, list[TaskEvent]] = {}
        self._next_event_id = 1
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
            self._events[thread_id] = []

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
                raise KeyError(f"任务不存在：{thread_id}")

            record.status = status
            record.result = result
            record.error = error
            record.updated_at = datetime.now(timezone.utc)

            return self._snapshot(record)

    def append_event(
        self,
        *,
        thread_id: str,
        kind: str,
        source: str,
        payload: dict[str, Any],
    ) -> TaskEvent:
        with self._lock:
            if thread_id not in self._records:
                raise KeyError(f"任务不存在：{thread_id}")

            event = TaskEvent(
                id=self._next_event_id,
                thread_id=thread_id,
                kind=kind,
                source=source,
                payload=dict(payload),
                created_at=datetime.now(timezone.utc),
            )

            self._next_event_id += 1
            self._events[thread_id].append(event)

            return event

    def list_events(
        self,
        thread_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[TaskEvent]:
        with self._lock:
            if thread_id not in self._records:
                raise KeyError(f"任务不存在：{thread_id}")

            events = self._events[thread_id]

            return [event for event in events if event.id > after_id][:limit]

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

TOKEN_EVENT_FLUSH_CHARS = 80

def _stream_source(namespace: Any) -> str:
    """把 LangGraph namespace 转换成稳定事件来源。"""

    if not isinstance(namespace, (tuple, list)):
        return "main"

    for segment in namespace:
        if isinstance(segment, str) and segment.startswith("tools:"):
            _, _, call_id = segment.partition(":")

            if call_id:
                return f"subagent:{call_id}"

            return "subagent"

    return "main"

def _message_text(message: Any) -> str:
    """从 LangChain 消息中提取普通文本。"""

    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return ""

    parts: list[str] = []

    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue

        if not isinstance(block, dict):
            continue

        if block.get("type") != "text":
            continue

        text = block.get("text")

        if isinstance(text, str):
            parts.append(text)

    return "".join(parts)

def _tool_names(message: Any) -> list[str]:
    """从完整工具调用或流式工具调用块中提取名称。"""

    names: list[str] = []

    tool_call_chunks = (
        getattr(
            message,
            "tool_call_chunks",
            None,
        )
        or []
    )

    for chunk in tool_call_chunks:
        if isinstance(chunk, dict):
            name = chunk.get("name")
        else:
            name = getattr(chunk, "name", None)

        if isinstance(name, str) and name:
            if name not in names:
                names.append(name)

    # 流式 Chunk 已经给出名称时，不再处理完整调用，
    # 避免同一个工具产生两次 started 事件。
    if names:
        return names

    tool_calls = (
        getattr(
            message,
            "tool_calls",
            None,
        )
        or []
    )

    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            name = tool_call.get("name")
        else:
            name = getattr(
                tool_call,
                "name",
                None,
            )

        if isinstance(name, str) and name:
            if name not in names:
                names.append(name)

    return names

def run_agent_task(
    *,
    thread_id: str,
    task_store: TaskStore,
    agent_factory: AgentFactory,
) -> None:
    """流式执行 Agent，并持久化标准化任务事件。"""

    task = task_store.mark_running(thread_id)

    task_store.append_event(
        thread_id=thread_id,
        kind="running",
        source="system",
        payload={
            "status": TaskStatus.RUNNING.value,
        },
    )

    try:
        agent = agent_factory(task.task_kind)

        answer_parts: list[str] = []
        pending_text = ""
        pending_source = "main"

        def flush_text() -> None:
            nonlocal pending_text
            nonlocal pending_source

            if not pending_text:
                return

            task_store.append_event(
                thread_id=thread_id,
                kind="token",
                source=pending_source,
                payload={
                    "text": pending_text,
                },
            )

            # 只有主 Agent 的文本才属于最终回答。
            # 子 Agent 文本只用于过程展示。
            if pending_source == "main":
                answer_parts.append(pending_text)

            pending_text = ""

        stream = agent.stream(
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
            stream_mode="messages",
            subgraphs=True,
            version="v2",
        )

        for part in stream:
            if not isinstance(part, dict):
                continue

            if part.get("type") != "messages":
                continue

            data = part.get("data")

            if not isinstance(data, tuple) or len(data) != 2:
                continue

            message, _metadata = data

            source = _stream_source(part.get("ns", ()))

            tool_names = _tool_names(message)

            # 工具事件前先把已有正文写出，
            # 保证数据库中的事件顺序正确。
            if tool_names:
                flush_text()

                for tool_name in tool_names:
                    task_store.append_event(
                        thread_id=thread_id,
                        kind="tool_started",
                        source=source,
                        payload={
                            "name": tool_name,
                        },
                    )

            if isinstance(
                message,
                (ToolMessage, ToolMessageChunk),
            ):
                flush_text()

                tool_name = getattr(message, "name", None) or "unknown"

                task_store.append_event(
                    thread_id=thread_id,
                    kind="tool_finished",
                    source=source,
                    payload={
                        "name": tool_name,
                    },
                )

                continue

            text = _message_text(message)

            # 带工具调用的 AIMessage 不算最终回答正文。
            if (
                not isinstance(
                    message,
                    (AIMessage, AIMessageChunk),
                )
                or not text
                or tool_names
            ):
                continue

            # 主 Agent 与子 Agent 切换时先刷新旧来源，
            # 防止两个来源的文本混到同一事件里。
            if pending_text and source != pending_source:
                flush_text()

            pending_source = source
            pending_text += text

            # 不为每个单字 Token 写一次 SQLite。
            # 达到一定长度或完成一行时再批量写入。
            if len(pending_text) >= TOKEN_EVENT_FLUSH_CHARS or "\n" in text:
                flush_text()

        flush_text()

        answer = "".join(answer_parts).strip()

        if not answer:
            raise RuntimeError("Agent 流中没有主 Agent 最终文本")

        task_store.complete(
            thread_id,
            answer,
        )

        task_store.append_event(
            thread_id=thread_id,
            kind="completed",
            source="system",
            payload={
                "status": TaskStatus.COMPLETED.value,
            },
        )
    except Exception:
        logger.exception(
            "Agent 后台任务失败：thread_id=%s",
            thread_id,
        )

        safe_error = "任务执行失败，请查看服务日志"

        task_store.fail(
            thread_id,
            safe_error,
        )

        task_store.append_event(
            thread_id=thread_id,
            kind="failed",
            source="system",
            payload={
                "status": TaskStatus.FAILED.value,
                "error": safe_error,
            },
        )
