from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from app.core.conversation import ConversationStore
from app.core.task_intent import classify_task_kind

logger = logging.getLogger(__name__)

AgentFactory = Callable[[Any], Any]


def _stream_source(namespace: Any) -> str:
    if not namespace:
        return "main"

    if isinstance(namespace, str):
        parts = (namespace,)
    else:
        parts = tuple(str(part) for part in namespace)

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


def _has_tool_calls(message: Any) -> bool:
    tool_calls = getattr(message, "tool_calls", None)
    return bool(tool_calls)


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

        conversation_store.mark_run_running(run_id)

        task_kind = classify_task_kind(user_message.content)
        agent = agent_factory(task_kind)

        prompt = (
            f"当前项目虚拟路径：{project.virtual_path}\n"
            f"请只处理该项目范围内的任务。\n\n"
            f"用户请求：{user_message.content}"
        )

        answer_parts: list[str] = []

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
            namespace = part.get("ns")

            if _stream_source(namespace) != "main":
                continue

            if not isinstance(message, (AIMessage, AIMessageChunk)):
                continue

            if _has_tool_calls(message):
                continue

            text = _message_text(message)
            if text:
                answer_parts.append(text)

        answer = "".join(answer_parts).strip()

        if not answer:
            raise RuntimeError("Agent 没有返回可保存的文本")

        conversation_store.complete_run_with_message(
            run_id,
            answer,
        )

    except Exception:
        logger.exception("会话 Run 执行失败：run_id=%s", run_id)

        try:
            conversation_store.fail_run(
                run_id,
                "任务执行失败，请查看服务日志",
            )
        except Exception:
            logger.exception(
                "记录会话 Run 失败状态时发生异常：run_id=%s",
                run_id,
            )