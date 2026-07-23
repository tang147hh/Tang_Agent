from __future__ import annotations

import json
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
from langchain.agents.middleware.model_call_limit import (
    ModelCallLimitExceededError,
)

from app.core.conversation import ConversationStore, MessageSnapshot
from app.backends.workspace import Workspace
from app.core.review import ReviewFindingService, ReviewOutputError
from app.core.run_limits import (
    RunBudget,
    RunBudgetTracker,
    RunLimitExceeded,
    RunTerminationReason,
    budget_for,
    iter_with_deadline,
)

logger = logging.getLogger(__name__)

AgentFactory = Callable[[Any], Any]
TOKEN_EVENT_FLUSH_CHARS = 80


@dataclass(frozen=True, slots=True)
class _ToolCall:
    name: str
    call_id: str | None
    subagent: str | None
    arguments: Any


def _model_messages(
    messages: list[MessageSnapshot],
    *,
    current_message_id: str,
    project_virtual_path: str,
) -> list[dict[str, str]]:
    """从 SQLite 消息快照重建本次 Run 的完整模型上下文。"""

    model_messages: list[dict[str, str]] = []

    for message in sorted(messages, key=lambda item: item.sequence):
        content = message.content

        if message.message_id == current_message_id:
            content = (
                f"当前项目虚拟路径：{project_virtual_path}\n"
                "请只处理该项目范围内的任务。\n\n"
                f"用户请求：{message.content}"
            )

        model_messages.append(
            {
                "role": message.role.value,
                "content": content,
            }
        )

    return model_messages


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
                arguments=arguments,
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


def _tool_result_flags(message: Any) -> tuple[bool, bool]:
    """判断工具结果是否为错误以及是否属于安全策略拒绝。"""

    is_error = getattr(message, "status", None) == "error"
    content = getattr(message, "content", None)
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
    else:
        parsed = content
    if not isinstance(parsed, dict):
        return is_error, False
    status = str(parsed.get("status") or "").lower()
    error_type = str(parsed.get("error_type") or "")
    is_error = is_error or status in {"error", "rejected"}
    is_safety_rejection = status == "rejected" or error_type in {
        "CommandPolicyError",
        "TaskPermissionError",
        "WorkspacePathError",
    }
    return is_error, is_safety_rejection


def _initialize_performance(
    *,
    conversation_store: ConversationStore,
    run_id: str,
    task_kind: Any,
    budget: RunBudget,
) -> None:
    if conversation_store.get_run_performance(run_id) is not None:
        return
    conversation_store.initialize_run_performance(
        run_id=run_id,
        task_kind=task_kind,
        max_model_calls=budget.max_model_calls,
        max_tool_calls=budget.max_tool_calls,
        max_first_output_seconds=budget.max_first_output_seconds,
        max_seconds=budget.max_seconds,
        max_identical_tool_calls=budget.max_identical_tool_calls,
    )


def _persist_performance(
    *,
    conversation_store: ConversationStore,
    run_id: str,
    tracker: RunBudgetTracker,
    termination_reason: RunTerminationReason | None,
) -> None:
    metrics = tracker.finish(termination_reason)
    conversation_store.update_run_performance(
        run_id=run_id,
        model_calls=metrics.model_calls,
        tool_calls=metrics.tool_calls,
        repeated_tool_calls=metrics.repeated_tool_calls,
        tool_errors=metrics.tool_errors,
        safety_rejections=metrics.safety_rejections,
        first_output_ms=metrics.first_output_ms,
        duration_ms=metrics.duration_ms,
        termination_reason=(
            metrics.termination_reason.value
            if metrics.termination_reason is not None
            else None
        ),
    )
    logger.info(
        "Run 性能已记录：run_id=%s model_calls=%s tool_calls=%s "
        "repeated_tool_calls=%s tool_errors=%s safety_rejections=%s "
        "first_output_ms=%s duration_ms=%.3f termination_reason=%s",
        run_id,
        metrics.model_calls,
        metrics.tool_calls,
        metrics.repeated_tool_calls,
        metrics.tool_errors,
        metrics.safety_rejections,
        metrics.first_output_ms,
        metrics.duration_ms,
        (
            metrics.termination_reason.value
            if metrics.termination_reason is not None
            else None
        ),
    )


def _terminate_run(
    *,
    conversation_store: ConversationStore,
    run_id: str,
    limit: RunLimitExceeded,
) -> None:
    conversation_store.fail_run(run_id, limit.user_message)
    conversation_store.append_run_event(
        run_id=run_id,
        kind="terminated",
        source="system",
        payload={
            "status": "failed",
            "termination_reason": limit.reason.value,
            "error": limit.user_message,
        },
    )


def run_conversation_agent(
    *,
    run_id: str,
    conversation_store: ConversationStore,
    agent_factory: AgentFactory,
    workspace: Workspace | None = None,
) -> None:
    """执行一个已经创建的会话 Run。"""

    tracker: RunBudgetTracker | None = None
    termination_reason: RunTerminationReason | None = None
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

        budget = budget_for(run.task_kind)
        _initialize_performance(
            conversation_store=conversation_store,
            run_id=run_id,
            task_kind=run.task_kind,
            budget=budget,
        )
        tracker = RunBudgetTracker(budget)
        agent = agent_factory(run.task_kind)

        model_messages = _model_messages(
            messages,
            current_message_id=user_message.message_id,
            project_virtual_path=project.virtual_path,
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
                "task_kind": run.task_kind.value,
                "budget": {
                    "max_model_calls": budget.max_model_calls,
                    "max_tool_calls": budget.max_tool_calls,
                    "max_first_output_seconds": (
                        budget.max_first_output_seconds
                    ),
                    "max_seconds": budget.max_seconds,
                    "max_identical_tool_calls": (
                        budget.max_identical_tool_calls
                    ),
                },
            },
        )

        conversation_store.mark_run_running(run_id)

        conversation_store.append_run_event(
            run_id=run_id,
            kind="running",
            source="system",
            payload={
                "status": "running",
                "task_kind": run.task_kind.value,
                "budget": {
                    "max_model_calls": budget.max_model_calls,
                    "max_tool_calls": budget.max_tool_calls,
                    "max_first_output_seconds": (
                        budget.max_first_output_seconds
                    ),
                    "max_seconds": budget.max_seconds,
                    "max_identical_tool_calls": (
                        budget.max_identical_tool_calls
                    ),
                },
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
                "messages": model_messages,
            },
            config={
                "configurable": {
                    "thread_id": run_id,
                }
            },
            stream_mode="messages",
            subgraphs=True,
            version="v2",
        )

        for part in iter_with_deadline(stream, tracker):
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
            text = _message_text(message)
            tracker.observe_model_message(
                message,
                source=source,
                has_output=bool(tool_calls or text),
            )

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

                    tracker.observe_tool_call(
                        source=source,
                        name=tool_call.name,
                        arguments=tool_call.arguments,
                        call_id=tool_call.call_id,
                    )

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
                is_error, is_safety_rejection = _tool_result_flags(
                    message
                )
                tracker.observe_tool_result(
                    is_error=is_error,
                    is_safety_rejection=is_safety_rejection,
                )
                payload = {
                    "name": tool_name,
                    "status": "error" if is_error else "completed",
                    "recoverable": is_error,
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

                if subagent == "reviewer" and not is_error:
                    if workspace is None:
                        raise ReviewOutputError(
                            "Reviewer 缺少工作区上下文，无法校验路径"
                        )
                    result = ReviewFindingService(
                        conversation_store,
                        workspace,
                    ).save_model_output(
                        run_id=run_id,
                        raw_output=getattr(message, "content", None),
                    )
                    conversation_store.append_run_event(
                        run_id=run_id,
                        kind="review_findings_saved",
                        source="reviewer",
                        payload={
                            "created_count": result.created_count,
                            "duplicate_count": result.duplicate_count,
                            "rejected_count": 0,
                            "summary": result.summary,
                        },
                    )
                    logger.info(
                        "Reviewer Finding 已保存：run_id=%s created=%s "
                        "duplicates=%s",
                        run_id,
                        result.created_count,
                        result.duplicate_count,
                    )

                conversation_store.append_run_event(
                    run_id=run_id,
                    kind="tool_finished",
                    source=source,
                    payload=payload,
                )

                continue

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

    except ModelCallLimitExceededError:
        limit = RunLimitExceeded(
            RunTerminationReason.MODEL_CALL_LIMIT,
            "Run 已达到模型调用预算，已停止继续推理。",
        )
        termination_reason = limit.reason
        logger.warning(
            "会话 Run 达到模型调用预算：run_id=%s",
            run_id,
        )
        _terminate_run(
            conversation_store=conversation_store,
            run_id=run_id,
            limit=limit,
        )
    except RunLimitExceeded as limit:
        termination_reason = limit.reason
        logger.warning(
            "会话 Run 达到运行预算：run_id=%s reason=%s",
            run_id,
            limit.reason.value,
        )
        _terminate_run(
            conversation_store=conversation_store,
            run_id=run_id,
            limit=limit,
        )
    except ReviewOutputError as exc:
        termination_reason = RunTerminationReason.AGENT_ERROR
        logger.exception(
            "Reviewer 结构化输出被拒绝：run_id=%s reason=%s",
            run_id,
            exc,
        )
        safe_error = str(exc)
        try:
            conversation_store.fail_run(run_id, safe_error)
            conversation_store.append_run_event(
                run_id=run_id,
                kind="failed",
                source="reviewer",
                payload={
                    "status": "failed",
                    "error": safe_error,
                },
            )
        except Exception:
            logger.exception(
                "记录 Reviewer 失败状态时发生异常：run_id=%s",
                run_id,
            )
    except Exception:
        termination_reason = RunTerminationReason.AGENT_ERROR
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
    finally:
        if tracker is not None:
            try:
                _persist_performance(
                    conversation_store=conversation_store,
                    run_id=run_id,
                    tracker=tracker,
                    termination_reason=termination_reason,
                )
            except Exception:
                logger.exception(
                    "记录会话 Run 性能指标失败：run_id=%s",
                    run_id,
                )
