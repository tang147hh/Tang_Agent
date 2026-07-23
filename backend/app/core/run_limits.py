from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from app.core.task_intent import TaskKind


class RunTerminationReason(StrEnum):
    MODEL_CALL_LIMIT = "model_call_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    FIRST_OUTPUT_TIMEOUT = "first_output_timeout"
    TOTAL_TIME_LIMIT = "total_time_limit"
    REPEATED_TOOL_CALL = "repeated_tool_call"
    AGENT_ERROR = "agent_error"


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_model_calls: int
    max_tool_calls: int
    max_first_output_seconds: float
    max_seconds: float
    max_identical_tool_calls: int = 2


DEFAULT_RUN_BUDGETS: dict[TaskKind, RunBudget] = {
    TaskKind.QA: RunBudget(3, 4, 12.0, 45.0),
    TaskKind.PLANNING: RunBudget(5, 10, 20.0, 120.0),
    TaskKind.ANALYSIS: RunBudget(8, 20, 25.0, 180.0),
    TaskKind.CODING: RunBudget(16, 40, 30.0, 480.0),
}


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def budget_for(task_kind: TaskKind) -> RunBudget:
    """读取任务类型专属预算，环境变量只覆盖对应模式。"""

    default = DEFAULT_RUN_BUDGETS[task_kind]
    prefix = f"TANG_AGENT_{task_kind.value.upper()}_"
    return RunBudget(
        max_model_calls=_positive_int(
            f"{prefix}MAX_MODEL_CALLS",
            default.max_model_calls,
        ),
        max_tool_calls=_positive_int(
            f"{prefix}MAX_TOOL_CALLS",
            default.max_tool_calls,
        ),
        max_first_output_seconds=_positive_float(
            f"{prefix}MAX_FIRST_OUTPUT_SECONDS",
            default.max_first_output_seconds,
        ),
        max_seconds=_positive_float(
            f"{prefix}MAX_SECONDS",
            default.max_seconds,
        ),
        max_identical_tool_calls=_positive_int(
            f"{prefix}MAX_IDENTICAL_TOOL_CALLS",
            default.max_identical_tool_calls,
        ),
    )


class RunLimitExceeded(RuntimeError):
    def __init__(
        self,
        reason: RunTerminationReason,
        user_message: str,
    ) -> None:
        super().__init__(user_message)
        self.reason = reason
        self.user_message = user_message


@dataclass(frozen=True, slots=True)
class RunMetrics:
    model_calls: int
    tool_calls: int
    repeated_tool_calls: int
    tool_errors: int
    safety_rejections: int
    first_output_ms: float | None
    duration_ms: float
    termination_reason: RunTerminationReason | None


def tool_call_fingerprint(
    *,
    source: str,
    name: str,
    arguments: Any,
) -> str:
    try:
        serialized = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        serialized = repr(arguments)
    return f"{source}:{name}:{serialized}"


class RunBudgetTracker:
    """使用单调时钟统计一次 Run，并在事件消费侧执行硬预算。"""

    def __init__(
        self,
        budget: RunBudget,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self._clock = clock
        self._started_at = clock()
        self._first_output_at: float | None = None
        self._finished_at: float | None = None
        self._termination_reason: RunTerminationReason | None = None
        self._model_calls = 0
        self._tool_calls = 0
        self._repeated_tool_calls = 0
        self._tool_errors = 0
        self._safety_rejections = 0
        self._model_message_keys: set[str] = set()
        self._active_model_sources: set[str] = set()
        self._tool_call_keys: set[str] = set()
        self._tool_fingerprint_counts: dict[str, int] = {}

    def observe_model_message(
        self,
        message: Any,
        *,
        source: str,
        has_output: bool,
    ) -> None:
        if not isinstance(message, (AIMessage, AIMessageChunk)):
            self._active_model_sources.discard(source)
            return

        raw_message_id = getattr(message, "id", None)
        if isinstance(raw_message_id, str) and raw_message_id:
            key = f"{source}:{raw_message_id}"
            if key not in self._model_message_keys:
                self._model_message_keys.add(key)
                self._model_calls += 1
        elif source not in self._active_model_sources:
            self._active_model_sources.add(source)
            self._model_calls += 1

        if self._model_calls > self.budget.max_model_calls:
            raise RunLimitExceeded(
                RunTerminationReason.MODEL_CALL_LIMIT,
                "Run 已达到模型调用预算："
                f"最多 {self.budget.max_model_calls} 次。",
            )

        if has_output:
            self.observe_first_output()

    def observe_tool_result(
        self,
        *,
        is_error: bool,
        is_safety_rejection: bool,
    ) -> None:
        if is_error:
            self._tool_errors += 1
        if is_safety_rejection:
            self._safety_rejections += 1

    def observe_tool_call(
        self,
        *,
        source: str,
        name: str,
        arguments: Any,
        call_id: str | None,
    ) -> None:
        key = call_id or tool_call_fingerprint(
            source=source,
            name=name,
            arguments=arguments,
        )
        if key in self._tool_call_keys:
            return
        self._tool_call_keys.add(key)
        self._tool_calls += 1

        if self._tool_calls > self.budget.max_tool_calls:
            raise RunLimitExceeded(
                RunTerminationReason.TOOL_CALL_LIMIT,
                "Run 已达到工具调用预算："
                f"最多 {self.budget.max_tool_calls} 次。",
            )

        fingerprint = tool_call_fingerprint(
            source=source,
            name=name,
            arguments=arguments,
        )
        count = self._tool_fingerprint_counts.get(fingerprint, 0) + 1
        self._tool_fingerprint_counts[fingerprint] = count
        if count > 1:
            self._repeated_tool_calls += 1
        if count > self.budget.max_identical_tool_calls:
            raise RunLimitExceeded(
                RunTerminationReason.REPEATED_TOOL_CALL,
                "Run 检测到相同工具和参数被重复调用，"
                "已停止无意义循环。",
            )

    def observe_first_output(self) -> None:
        if self._first_output_at is None:
            self._first_output_at = self._clock()

    def remaining_wait_seconds(self) -> float:
        now = self._clock()
        elapsed = now - self._started_at
        total_remaining = self.budget.max_seconds - elapsed
        if self._first_output_at is None:
            first_remaining = (
                self.budget.max_first_output_seconds - elapsed
            )
            remaining = min(total_remaining, first_remaining)
        else:
            remaining = total_remaining
        if remaining <= 0:
            self.raise_deadline()
        return remaining

    def raise_deadline(self) -> None:
        elapsed = self._clock() - self._started_at
        if (
            self._first_output_at is None
            and elapsed >= self.budget.max_first_output_seconds
        ):
            raise RunLimitExceeded(
                RunTerminationReason.FIRST_OUTPUT_TIMEOUT,
                "Run 在首个输出延迟预算内没有返回内容："
                f"上限 {self.budget.max_first_output_seconds:g} 秒。",
            )
        raise RunLimitExceeded(
            RunTerminationReason.TOTAL_TIME_LIMIT,
            "Run 已达到总运行时间预算："
            f"上限 {self.budget.max_seconds:g} 秒。",
        )

    def finish(
        self,
        reason: RunTerminationReason | None = None,
    ) -> RunMetrics:
        if self._finished_at is None:
            self._finished_at = self._clock()
        if reason is not None:
            self._termination_reason = reason
        return self.metrics()

    def metrics(self) -> RunMetrics:
        finished_at = self._finished_at or self._clock()
        first_output_ms = None
        if self._first_output_at is not None:
            first_output_ms = max(
                (self._first_output_at - self._started_at) * 1000,
                0.0,
            )
        return RunMetrics(
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            repeated_tool_calls=self._repeated_tool_calls,
            tool_errors=self._tool_errors,
            safety_rejections=self._safety_rejections,
            first_output_ms=first_output_ms,
            duration_ms=max(
                (finished_at - self._started_at) * 1000,
                0.0,
            ),
            termination_reason=self._termination_reason,
        )


def iter_with_deadline(
    stream: Any,
    tracker: RunBudgetTracker,
) -> Iterator[Any]:
    """逐项推进阻塞流；超时后不再允许生成器进入下一节点。"""

    output: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    advance = threading.Event()
    stopped = threading.Event()

    def pump() -> None:
        iterator = iter(stream)
        while not stopped.is_set():
            try:
                item = next(iterator)
            except StopIteration:
                output.put(("done", None))
                return
            except BaseException as exc:  # noqa: BLE001
                output.put(("error", exc))
                return
            if stopped.is_set():
                return
            output.put(("item", item))
            advance.wait()
            advance.clear()

    worker = threading.Thread(
        target=pump,
        name="tang-agent-stream",
        daemon=True,
    )
    worker.start()

    try:
        while True:
            try:
                kind, payload = output.get(
                    timeout=tracker.remaining_wait_seconds()
                )
            except queue.Empty:
                tracker.raise_deadline()
            if kind == "done":
                return
            if kind == "error":
                raise payload
            yield payload
            advance.set()
    finally:
        stopped.set()
        advance.set()
