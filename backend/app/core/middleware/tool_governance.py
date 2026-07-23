from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from threading import RLock
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.backends.command_runner import CommandPolicyError
from app.backends.local_shell import BackendFileError
from app.backends.task_scoped import TaskPermissionError
from app.backends.workspace import WorkspacePathError
from app.core.run_limits import (
    RunBudget,
    RunLimitExceeded,
    RunTerminationReason,
    tool_call_fingerprint,
)


RECOVERABLE_TOOL_ERRORS = (
    BackendFileError,
    CommandPolicyError,
    FileNotFoundError,
    IsADirectoryError,
    NotADirectoryError,
    PermissionError,
    TaskPermissionError,
    TimeoutError,
    UnicodeError,
    WorkspacePathError,
)


def _tool_call(request: ToolCallRequest) -> dict[str, Any]:
    return request.tool_call if isinstance(request.tool_call, dict) else {}


def _tool_message(
    request: ToolCallRequest,
    payload: dict[str, Any],
) -> ToolMessage:
    call_id = _tool_call(request).get("id")
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=call_id if isinstance(call_id, str) else None,
        status="error",
    )


class ToolGovernanceMiddleware(AgentMiddleware):
    """在工具边界执行预算、重复检测和可恢复错误转换。"""

    state_schema = AgentState

    def __init__(self, budget: RunBudget) -> None:
        super().__init__()
        self.budget = budget
        self._tool_calls = 0
        self._fingerprints: dict[str, int] = {}
        self._lock = RLock()

    def _before_call(
        self,
        request: ToolCallRequest,
    ) -> ToolMessage | None:
        tool_call = _tool_call(request)
        name = str(tool_call.get("name") or "unknown")
        arguments = tool_call.get("args", {})
        fingerprint = tool_call_fingerprint(
            source="tool",
            name=name,
            arguments=arguments,
        )
        with self._lock:
            self._tool_calls += 1
            if self._tool_calls > self.budget.max_tool_calls:
                raise RunLimitExceeded(
                    RunTerminationReason.TOOL_CALL_LIMIT,
                    "Run 已达到工具调用预算："
                    f"最多 {self.budget.max_tool_calls} 次。",
                )
            count = self._fingerprints.get(fingerprint, 0) + 1
            self._fingerprints[fingerprint] = count

        if count > self.budget.max_identical_tool_calls:
            raise RunLimitExceeded(
                RunTerminationReason.REPEATED_TOOL_CALL,
                "Run 检测到相同工具和参数被重复调用，"
                "已停止无意义循环。",
            )
        if count > 1:
            return _tool_message(
                request,
                {
                    "status": "rejected",
                    "error_type": "RepeatedToolCall",
                    "error": "相同工具和参数已经执行过，请使用已有结果。",
                    "recoverable": True,
                    "hint": "调整参数或继续下一步，不要重复执行相同调用。",
                },
            )
        return None

    @staticmethod
    def _recoverable_error(
        request: ToolCallRequest,
        error: Exception,
    ) -> ToolMessage:
        safety_rejection = isinstance(
            error,
            (
                CommandPolicyError,
                TaskPermissionError,
                WorkspacePathError,
            ),
        )
        return _tool_message(
            request,
            {
                "status": "rejected" if safety_rejection else "error",
                "error_type": error.__class__.__name__,
                "error": str(error),
                "recoverable": True,
                "hint": (
                    "请改用工作区内的虚拟路径或符合策略的参数，"
                    "必要时先列目录和读取文件再重试。"
                ),
            },
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        rejected = self._before_call(request)
        if rejected is not None:
            return rejected
        try:
            return handler(request)
        except RunLimitExceeded:
            raise
        except RECOVERABLE_TOOL_ERRORS as exc:
            return self._recoverable_error(request, exc)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command],
        ],
    ) -> ToolMessage | Command:
        rejected = self._before_call(request)
        if rejected is not None:
            return rejected
        try:
            return await handler(request)
        except RunLimitExceeded:
            raise
        except RECOVERABLE_TOOL_ERRORS as exc:
            return self._recoverable_error(request, exc)
