from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    ToolMessage,
    ToolMessageChunk,
)

from app.core.conversation import ConversationStore
from app.core.task_intent import classify_task_kind

logger = logging.getLogger(__name__)

AgentFactory = Callable[[Any], Any]
TOKEN_EVENT_FLUSH_CHARS = 80


@dataclass(frozen=True, slots=True)
class _ToolCall:
    name: str
    call_id: str | None
    subagent: str | None


def _stream_source(namespace: Any) -> str:
    if not namespace:
        return "main"

    if isinstance(namespace, str):
        parts = (namespace,)
    else:
        parts = tuple(str(part) for part in namespace)

    for part in parts:
        if not part.startswith("tools:"):
            continue

        _, _, call_id = part.partition(":")
        return f"subagent:{call_id}" if call_id else "subagent"

    return "subagent" if any(parts) else "main"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return ""

    parts: list[str] = []

    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue

        if not isinstance(item, dict):
            continue

        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)

    return "".join(parts)


def _tool_calls(message: Any) -> list[_ToolCall]:
    """提取工具名称、调用 ID 和子 Agent 类型。"""

    raw_calls = getattr(message, "tool_call_chunks", None) or []

    if not raw_calls:
        raw_calls = getattr(message, "tool_calls", None) or []

    calls: list[_ToolCall] = []
    seen: set[tuple[str, str]] = set()

    for raw_call in raw_calls:
        if isinstance(raw_call, dict):
            name = raw_call.get("name")
            call_id = raw_call.get("id")
            arguments = raw_call.get("args")
        else:
            name = getattr(raw_call, "name", None)
            call_id = getattr(raw_call, "id", None)
            arguments = getattr(raw_call, "args", None)

        if not isinstance(name, str) or not name:
            continue

        normalized_call_id = (
            call_id if isinstance(call_id, str) and call_id else None
        )
        key = (normalized_call_id or "", name)

        if key in seen:
            continue

        seen.add(key)

        subagent = None

        if name == "task" and isinstance(arguments, dict):
            raw_subagent = arguments.get("subagent_type")

            if isinstance(raw_subagent, str) and raw_subagent:
                subagent = raw_subagent

        calls.append(
            _ToolCall(
                name=name,
                call_id=normalized_call_id,
                subagent=subagent,
            )
        )

    return calls


def _subagent_name(
    source: str,
    subagents: dict[str, str],
) -> str | None:
    if not source.startswith("subagent:"):
        return None

    _, _, call_id = source.partition(":")
    return subagents.get(call_id)


def run_conversation_agent(
    *,
    run_id: str,
    conversation_store: ConversationStore,
    agent_factory: AgentFactory,
) -> None:
    """执行一个已经创建的会话 Run。"""

    try:
        run = conversation_store.get_run(run_id)
        if run is None:
            raise KeyError(f"Run 不存在：{run_id}")

        thread = conversation_store.get_thread(run.thread_id)
        if thread is None:
            raise KeyError(f"Thread 不存在：{run.thread_id}")

        project = conversation_store.get_project(thread.project_id)
        if project is None:
            raise KeyError(f"Project 不存在：{thread.project_id}")

        messages = conversation_store.list_messages(thread.thread_id)

        user_message = next(
            (
                message
                for message in reversed(messages)
                if message.run_id == run_id
                and message.role.value == "user"
            ),
            None,
        )

        if user_message is None:
            raise RuntimeError(f"Run 没有关联的用户消息：{run_id}")

        task_kind = classify_task_kind(user_message.content)
        agent = agent_factory(task_kind)

        prompt = (
            f"当前项目虚拟路径：{project.virtual_path}\n"
            f"请只处理该项目范围内的任务。\n\n"
            f"用户请求：{user_message.content}"
        )

        answer_parts: list[str] = []
        pending_text = ""
        pending_source = "main"
        started_call_ids: set[str] = set()
        finished_call_ids: set[str] = set()
        subagents: dict[str, str] = {}

        conversation_store.append_run_event(
            run_id=run_id,
            kind="created",
            source="system",
            payload={
                "status": "pending",
            },
        )

        conversation_store.mark_run_running(run_id)

        conversation_store.append_run_event(
            run_id=run_id,
            kind="running",
            source="system",
            payload={
                "status": "running",
            },
        )

        def flush_text() -> None:
            nonlocal pending_text
            nonlocal pending_source

            if not pending_text:
                return

            payload = {
                "text": pending_text,
            }
            subagent = _subagent_name(
                pending_source,
                subagents,
            )

            if subagent is not None:
                payload["subagent"] = subagent

            conversation_store.append_run_event(
                run_id=run_id,
                kind="token",
                source=pending_source,
                payload=payload,
            )

            # 子 Agent 文本用于过程展示，不进入最终 Assistant 消息。
            if pending_source == "main":
                answer_parts.append(pending_text)

            pending_text = ""

        stream = agent.stream(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ]
            },
            config={
                "configurable": {
                    "thread_id": thread.thread_id,
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
            if not isinstance(data, tuple) or not data:
                continue

            message = data[0]
            source = _stream_source(part.get("ns"))
            tool_calls = _tool_calls(message)

            if tool_calls:
                flush_text()

                for tool_call in tool_calls:
                    if (
                        tool_call.call_id is not None
                        and tool_call.call_id in started_call_ids
                    ):
                        continue

                    if tool_call.call_id is not None:
                        started_call_ids.add(tool_call.call_id)

                    if (
                        tool_call.name == "task"
                        and tool_call.call_id is not None
                        and tool_call.subagent is not None
                    ):
                        subagents[tool_call.call_id] = tool_call.subagent

                    payload: dict[str, Any] = {
                        "name": tool_call.name,
                    }

                    if tool_call.call_id is not None:
                        payload["tool_call_id"] = tool_call.call_id

                    subagent = tool_call.subagent or _subagent_name(
                        source,
                        subagents,
                    )

                    if subagent is not None:
                        payload["subagent"] = subagent

                    conversation_store.append_run_event(
                        run_id=run_id,
                        kind="tool_started",
                        source=source,
                        payload=payload,
                    )

            if isinstance(
                message,
                (ToolMessage, ToolMessageChunk),
            ):
                flush_text()

                raw_call_id = getattr(
                    message,
                    "tool_call_id",
                    None,
                )
                call_id = (
                    raw_call_id
                    if isinstance(raw_call_id, str) and raw_call_id
                    else None
                )

                if call_id is not None and call_id in finished_call_ids:
                    continue

                if call_id is not None:
                    finished_call_ids.add(call_id)

                tool_name = getattr(message, "name", None) or "unknown"
                payload = {
                    "name": tool_name,
                }

                if call_id is not None:
                    payload["tool_call_id"] = call_id

                subagent = (
                    subagents.get(call_id)
                    if call_id is not None
                    else None
                ) or _subagent_name(source, subagents)

                if subagent is not None:
                    payload["subagent"] = subagent

                conversation_store.append_run_event(
                    run_id=run_id,
                    kind="tool_finished",
                    source=source,
                    payload=payload,
                )

                continue

            text = _message_text(message)

            if (
                not isinstance(message, (AIMessage, AIMessageChunk))
                or not text
                or tool_calls
            ):
                continue

            if pending_text and source != pending_source:
                flush_text()

            pending_source = source
            pending_text += text

            if (
                len(pending_text)
                >= TOKEN_EVENT_FLUSH_CHARS
                or "\n" in text
            ):
                flush_text()

        flush_text()

        answer = "".join(answer_parts).strip()

        if not answer:
            raise RuntimeError("Agent 没有返回可保存的文本")

        conversation_store.complete_run_with_message(
            run_id,
            answer,
        )

        conversation_store.append_run_event(
            run_id=run_id,
            kind="completed",
            source="system",
            payload={
                "status": "completed",
            },
        )

    except Exception:
        logger.exception("会话 Run 执行失败：run_id=%s", run_id)

        safe_error = "任务执行失败，请查看服务日志"

        try:
            conversation_store.fail_run(
                run_id,
                safe_error,
            )

            conversation_store.append_run_event(
                run_id=run_id,
                kind="failed",
                source="system",
                payload={
                    "status": "failed",
                    "error": safe_error,
                },
            )
        except Exception:
            logger.exception(
                "记录会话 Run 失败状态时发生异常：run_id=%s",
                run_id,
            )
