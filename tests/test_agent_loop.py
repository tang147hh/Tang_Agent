from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, ToolMessage

from app.backends.local_shell import LocalShellBackend
from app.backends.task_scoped import TaskScopedBackend
from app.backends.workspace import Workspace
from app.core.agent import build_agent
from app.core.task_intent import TaskKind
from app.tools import build_workspace_tools


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """允许测试模型接收 DeepAgent 绑定的工具。"""

    def bind_tools(
        self,
        tools,
        **kwargs,
    ):
        return self


@pytest.fixture
def backend(tmp_path: Path) -> LocalShellBackend:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    return LocalShellBackend(workspace)


@pytest.mark.parametrize(
    ("task_kind", "expected_names"),
    [
        (
            TaskKind.ANALYSIS,
            {
                "workspace_list",
                "workspace_read",
            },
        ),
        (
            TaskKind.CODING,
            {
                "workspace_list",
                "workspace_read",
                "workspace_write",
                "workspace_edit",
                "workspace_execute",
            },
        ),
    ],
)
def test_tool_visibility_matches_task_policy(
    backend: LocalShellBackend,
    task_kind: TaskKind,
    expected_names: set[str],
) -> None:
    scoped = TaskScopedBackend.for_task(
        task_kind,
        backend,
    )
    tools = build_workspace_tools(scoped)

    assert {tool.name for tool in tools} == expected_names


def test_read_only_tools_can_read_workspace(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/README.md",
        "# Demo\n",
    )

    scoped = TaskScopedBackend.for_task(
        TaskKind.ANALYSIS,
        backend,
    )
    tools = {
        tool.name: tool
        for tool in build_workspace_tools(scoped)
    }

    result = tools["workspace_read"].invoke(
        {
            "path": "/projects/demo/README.md",
            "offset": 0,
            "limit": 100,
        }
    )

    assert result == "# Demo"


def test_coding_tools_write_and_execute(
    backend: LocalShellBackend,
) -> None:
    scoped = TaskScopedBackend.for_task(
        TaskKind.CODING,
        backend,
    )
    tools = {
        tool.name: tool
        for tool in build_workspace_tools(scoped)
    }

    write_result = tools["workspace_write"].invoke(
        {
            "path": "/tmp/lesson_8.py",
            "content": "print('tool loop')\n",
        }
    )
    command_result = tools["workspace_execute"].invoke(
        {
            "argv": [
                "python",
                "/tmp/lesson_8.py",
            ],
            "cwd": "/tmp",
            "timeout": 10,
        }
    )

    assert write_result["status"] == "created"
    assert command_result["exit_code"] == 0
    assert command_result["stdout"].strip() == "tool loop"


def test_deep_agent_completes_tool_loop(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/README.md",
        "hello from Tang Agent\n",
    )

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {
                            "path": "/projects/demo/README.md",
                            "offset": 0,
                            "limit": 100,
                        },
                        "id": "call_readme",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="README 内容是 hello from Tang Agent",
            ),
        ]
    )

    agent = build_agent(
        TaskKind.ANALYSIS,
        backend=backend,
        model=model,
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "读取 demo 的 README",
                }
            ]
        }
    )

    assert (
        result["messages"][-1].content
        == "README 内容是 hello from Tang Agent"
    )

    assert any(
        isinstance(message, ToolMessage)
        and "hello from Tang Agent" in str(message.content)
        for message in result["messages"]
    )