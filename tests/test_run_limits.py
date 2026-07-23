from __future__ import annotations

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from app.core.run_limits import (
    DEFAULT_RUN_BUDGETS,
    RunBudget,
    RunBudgetTracker,
    RunLimitExceeded,
    RunTerminationReason,
    budget_for,
)
from app.core.task_intent import TaskKind


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_task_budgets_are_distinct_and_stricter_than_reference() -> None:
    assert budget_for(TaskKind.QA) == DEFAULT_RUN_BUDGETS[TaskKind.QA]
    assert budget_for(TaskKind.PLANNING).max_tool_calls == 10
    assert budget_for(TaskKind.ANALYSIS).max_seconds == 180
    assert budget_for(TaskKind.CODING).max_model_calls == 16
    assert budget_for(TaskKind.CODING).max_tool_calls == 40
    assert budget_for(TaskKind.CODING).max_seconds == 480


def test_first_output_and_total_deadlines_use_fake_clock() -> None:
    clock = FakeClock()
    tracker = RunBudgetTracker(
        RunBudget(3, 4, 2.0, 5.0),
        clock=clock,
    )
    clock.advance(2.1)
    with pytest.raises(RunLimitExceeded) as first_error:
        tracker.remaining_wait_seconds()
    assert first_error.value.reason is (
        RunTerminationReason.FIRST_OUTPUT_TIMEOUT
    )

    clock = FakeClock()
    tracker = RunBudgetTracker(
        RunBudget(3, 4, 2.0, 5.0),
        clock=clock,
    )
    clock.advance(0.25)
    tracker.observe_first_output()
    clock.advance(4.8)
    with pytest.raises(RunLimitExceeded) as total_error:
        tracker.remaining_wait_seconds()
    assert total_error.value.reason is (
        RunTerminationReason.TOTAL_TIME_LIMIT
    )


def test_tracker_records_first_output_errors_and_repeated_calls() -> None:
    clock = FakeClock()
    tracker = RunBudgetTracker(
        RunBudget(3, 5, 2.0, 5.0, max_identical_tool_calls=2),
        clock=clock,
    )
    clock.advance(0.125)
    tracker.observe_model_message(
        AIMessageChunk(content="answer", id="model-1"),
        source="main",
        has_output=True,
    )
    for index in range(2):
        tracker.observe_tool_call(
            source="main",
            name="workspace_read",
            arguments={"path": "/projects/demo/README.md"},
            call_id=f"call-{index}",
        )
    tracker.observe_tool_result(
        is_error=True,
        is_safety_rejection=True,
    )
    tracker.observe_model_message(
        ToolMessage(content="done", tool_call_id="call-1"),
        source="main",
        has_output=False,
    )
    clock.advance(0.375)
    metrics = tracker.finish()

    assert metrics.model_calls == 1
    assert metrics.tool_calls == 2
    assert metrics.repeated_tool_calls == 1
    assert metrics.tool_errors == 1
    assert metrics.safety_rejections == 1
    assert metrics.first_output_ms == pytest.approx(125.0)
    assert metrics.duration_ms == pytest.approx(500.0)


def test_tracker_terminates_a_repeated_tool_loop() -> None:
    tracker = RunBudgetTracker(
        RunBudget(3, 5, 2.0, 5.0, max_identical_tool_calls=2)
    )
    for index in range(2):
        tracker.observe_tool_call(
            source="main",
            name="workspace_list",
            arguments={"path": "/projects"},
            call_id=f"call-{index}",
        )
    with pytest.raises(RunLimitExceeded) as error:
        tracker.observe_tool_call(
            source="main",
            name="workspace_list",
            arguments={"path": "/projects"},
            call_id="call-3",
        )
    assert error.value.reason is RunTerminationReason.REPEATED_TOOL_CALL


def test_tracker_enforces_total_tool_call_limit() -> None:
    tracker = RunBudgetTracker(RunBudget(3, 2, 2.0, 5.0))
    for index in range(2):
        tracker.observe_tool_call(
            source="main",
            name="workspace_read",
            arguments={"path": f"/projects/demo/{index}.md"},
            call_id=f"call-{index}",
        )

    with pytest.raises(RunLimitExceeded) as error:
        tracker.observe_tool_call(
            source="subagent:call-1",
            name="workspace_list",
            arguments={"path": "/projects/demo"},
            call_id="call-3",
        )

    assert error.value.reason is RunTerminationReason.TOOL_CALL_LIMIT


def test_tracker_enforces_aggregate_model_call_limit() -> None:
    tracker = RunBudgetTracker(RunBudget(2, 5, 2.0, 5.0))

    tracker.observe_model_message(
        AIMessageChunk(content="first", id="model-1"),
        source="main",
        has_output=True,
    )
    tracker.observe_model_message(
        AIMessageChunk(content="second", id="model-2"),
        source="subagent:call-1",
        has_output=True,
    )

    with pytest.raises(RunLimitExceeded) as error:
        tracker.observe_model_message(
            AIMessageChunk(content="third", id="model-3"),
            source="main",
            has_output=True,
        )

    assert error.value.reason is RunTerminationReason.MODEL_CALL_LIMIT
