from __future__ import annotations

from collections.abc import Awaitable, Callable
from threading import RLock

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelCallResult,
    ModelRequest,
)

from app.core.run_limits import (
    RunBudget,
    RunLimitExceeded,
    RunTerminationReason,
)


class RunModelCallLimitMiddleware(AgentMiddleware):
    """在主 Agent 和子 Agent 之间共享一次 Run 的模型调用预算。"""

    state_schema = AgentState

    def __init__(self, budget: RunBudget) -> None:
        super().__init__()
        self.budget = budget
        self._model_calls = 0
        self._lock = RLock()

    def _before_call(self) -> None:
        with self._lock:
            if self._model_calls >= self.budget.max_model_calls:
                raise RunLimitExceeded(
                    RunTerminationReason.MODEL_CALL_LIMIT,
                    "Run 已达到模型调用预算："
                    f"最多 {self.budget.max_model_calls} 次。",
                )
            self._model_calls += 1

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelCallResult],
    ) -> ModelCallResult:
        self._before_call()
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[
            [ModelRequest],
            Awaitable[ModelCallResult],
        ],
    ) -> ModelCallResult:
        self._before_call()
        return await handler(request)
