from __future__ import annotations

import json

from langchain_core.messages import AIMessageChunk, ToolMessage

from app.core.conversation_runtime import (
    _tool_calls,
    _workspace_search_finished_payload,
    _workspace_search_started_payload,
)
from app.core.run_limits import RunBudget, RunBudgetTracker


def _streaming_tool_chunk(
    *,
    call_id: str,
    arguments: str,
) -> AIMessageChunk:
    return AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": "workspace_execute",
                "args": arguments,
                "id": call_id,
                "index": 0,
                "type": "tool_call_chunk",
            }
        ],
    )


def test_incomplete_streaming_arguments_do_not_create_false_repeats() -> None:
    tracker = RunBudgetTracker(
        RunBudget(5, 5, 5.0, 30.0, max_identical_tool_calls=2)
    )

    for index in range(3):
        message = _streaming_tool_chunk(
            call_id=f"call-{index}",
            arguments="",
        )
        [tool_call] = _tool_calls(message)
        tracker.observe_tool_call(
            source="main",
            name=tool_call.name,
            arguments=tool_call.arguments,
            call_id=tool_call.call_id,
        )

    metrics = tracker.finish()

    assert metrics.tool_calls == 3
    assert metrics.repeated_tool_calls == 0


def test_complete_streaming_arguments_are_parsed_for_fingerprinting() -> None:
    message = _streaming_tool_chunk(
        call_id="call-1",
        arguments=(
            '{"argv":["python","socket_demo.py"],'
            '"cwd":"/projects/demo"}'
        ),
    )

    [tool_call] = _tool_calls(message)

    assert tool_call.arguments == {
        "argv": ["python", "socket_demo.py"],
        "cwd": "/projects/demo",
    }


def test_workspace_search_started_event_omits_query_and_host_paths() -> None:
    payload = _workspace_search_started_payload(
        "workspace_search",
        {
            "path": "/Users/tang/private",
            "query": "SECRET_LOCAL_CODE",
            "file_pattern": "**/*.py",
            "max_results": 999,
        },
    )

    assert payload == {
        "path": "/projects",
        "file_pattern": "**/*.py",
        "max_results": 500,
    }
    assert "SECRET_LOCAL_CODE" not in json.dumps(payload)


def test_workspace_search_finished_event_contains_metrics_not_matches() -> None:
    message = ToolMessage(
        name="workspace_search",
        tool_call_id="search-1",
        content=json.dumps(
            {
                "ok": True,
                "query": "SECRET_LOCAL_CODE",
                "matches": [
                    {
                        "path": "/projects/demo/app.py",
                        "line_number": 7,
                        "snippet": "SECRET_LOCAL_CODE = True",
                    }
                ],
                "match_count": 1,
                "files_searched": 3,
                "skipped_file_count": 2,
                "scanned_bytes": 1234,
                "truncated": True,
                "duration_ms": 4.5,
            }
        ),
    )

    payload = _workspace_search_finished_payload(message)

    assert payload == {
        "match_count": 1,
        "files_searched": 3,
        "skipped_file_count": 2,
        "scanned_entry_count": 0,
        "scanned_bytes": 1234,
        "duration_ms": 4.5,
        "truncated": True,
    }
    serialized = json.dumps(payload)
    assert "SECRET_LOCAL_CODE" not in serialized
    assert "snippet" not in serialized
