from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, ToolMessage

from app.backends.local_shell import LocalShellBackend
from app.backends.workspace import Workspace
from app.core.agent import build_agent
from app.core.subagents import (
    build_analysis_subagent,
    build_reviewer_subagent,
)
from app.core.task_intent import TaskKind


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def backend(tmp_path: Path) -> LocalShellBackend:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    return LocalShellBackend(workspace)


def test_analysis_subagent_only_receives_read_tools(
    backend: LocalShellBackend,
) -> None:
    model = ToolCallingFakeModel(
        responses=[AIMessage(content="done")]
    )

    subagent = build_analysis_subagent(
        backend,
        model,
        shared_context="SHARED_MEMORY_MARKER",
    )

    tool_names = {
        tool.name
        for tool in subagent["tools"]
    }

    assert subagent["name"] == "general-purpose"
    assert tool_names == {
        "workspace_list",
        "workspace_read",
    }
    assert "workspace_write" not in tool_names
    assert "workspace_edit" not in tool_names
    assert "workspace_execute" not in tool_names
    assert "SHARED_MEMORY_MARKER" in subagent[
        "system_prompt"
    ]


def test_reviewer_subagent_is_read_only_and_requires_structured_output(
    backend: LocalShellBackend,
) -> None:
    model = ToolCallingFakeModel(
        responses=[AIMessage(content='{"findings": [], "summary": "ok"}')]
    )
    subagent = build_reviewer_subagent(backend, model)

    assert subagent["name"] == "reviewer"
    assert {tool.name for tool in subagent["tools"]} == {
        "workspace_list",
        "workspace_read",
    }
    assert "workspace_write" not in {
        tool.name for tool in subagent["tools"]
    }
    assert "workspace_execute" not in {
        tool.name for tool in subagent["tools"]
    }
    assert '"findings"' in subagent["system_prompt"]
    assert "不得使用 workspace_write" in subagent["system_prompt"]


def test_main_agent_can_delegate_read_only_analysis(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/README.md",
        "SUBAGENT_READ_VERIFIED\n",
    )

    parent_model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": (
                                "读取 /projects/demo/README.md，"
                                "返回文件中的验证标记。"
                            ),
                            "subagent_type": "general-purpose",
                        },
                        "id": "call_subagent",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="子 Agent 已完成只读分析",
            ),
        ]
    )

    child_model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {
                            "path": (
                                "/projects/demo/README.md"
                            ),
                            "offset": 0,
                            "limit": 100,
                        },
                        "id": "call_child_read",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "验证标记是 "
                    "SUBAGENT_READ_VERIFIED"
                ),
            ),
        ]
    )

    agent = build_agent(
        TaskKind.CODING,
        backend=backend,
        model=parent_model,
        subagent_model=child_model,
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "委派子 Agent 分析 README",
                }
            ]
        }
    )

    assert any(
        isinstance(message, ToolMessage)
        and "SUBAGENT_READ_VERIFIED"
        in str(message.content)
        for message in result["messages"]
    )

    assert (
        result["messages"][-1].content
        == "子 Agent 已完成只读分析"
    )
