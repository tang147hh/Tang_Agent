from __future__ import annotations

import sys
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
from app.core.run_limits import RunLimitExceeded, RunTerminationReason
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
    runtime_bin = workspace.root / "runtimes" / "python" / "bin"
    runtime_bin.mkdir(parents=True)
    (runtime_bin / "python").symlink_to(sys.executable)
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


def test_rejected_command_returns_recoverable_tool_result(
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

    result = tools["workspace_execute"].invoke(
        {
            "argv": ["python", "-c", "print('unsafe')"],
            "cwd": "/projects",
            "timeout": 10,
        }
    )

    assert result == {
        "status": "rejected",
        "error": "禁止使用 python -c 执行任意内联代码",
        "recoverable": True,
        "hint": (
            "命令未执行。请改用符合策略的命令；"
            "验证文件内容时优先使用 workspace_read。"
        ),
    }


def test_deep_agent_recovers_from_rejected_command(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/docs/course-test.md",
        "# Course test\n",
    )

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_execute",
                        "args": {
                            "argv": [
                                "python",
                                "-c",
                                "print('verify')",
                            ],
                            "cwd": "/projects/demo",
                            "timeout": 10,
                        },
                        "id": "call_rejected",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {
                            "path": (
                                "/projects/demo/docs/"
                                "course-test.md"
                            ),
                            "offset": 0,
                            "limit": 100,
                        },
                        "id": "call_read",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="文件内容验证完成"),
        ]
    )

    agent = build_agent(
        TaskKind.CODING,
        backend=backend,
        model=model,
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "验证课程测试文档",
                }
            ]
        }
    )

    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert len(tool_messages) == 2
    assert "rejected" in str(tool_messages[0].content)
    assert "workspace_read" in str(tool_messages[0].content)
    assert "# Course test" in str(tool_messages[1].content)
    assert result["messages"][-1].content == "文件内容验证完成"


def test_deep_agent_recovers_from_missing_file_error(
    backend: LocalShellBackend,
) -> None:
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {
                            "path": "/projects/demo/missing.md",
                            "offset": 0,
                            "limit": 100,
                        },
                        "id": "call_missing",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="文件不存在，已停止读取并继续回答"),
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
                    "content": "读取不存在的文件后继续处理",
                }
            ]
        }
    )
    tool_message = next(
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    )

    assert tool_message.status == "error"
    assert '"recoverable": true' in str(tool_message.content)
    assert result["messages"][-1].content == (
        "文件不存在，已停止读取并继续回答"
    )


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


def test_model_budget_is_shared_with_subagent(
    backend: LocalShellBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANG_AGENT_CODING_MAX_MODEL_CALLS", "2")
    backend.write_text(
        "/projects/demo/README.md",
        "shared budget\n",
    )
    parent_model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "读取 README",
                            "subagent_type": "general-purpose",
                        },
                        "id": "call_subagent",
                        "type": "tool_call",
                    }
                ],
            )
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
                            "path": "/projects/demo/README.md",
                            "offset": 0,
                            "limit": 100,
                        },
                        "id": "call_read",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="不会执行到这里"),
        ]
    )
    agent = build_agent(
        TaskKind.CODING,
        backend=backend,
        model=parent_model,
        subagent_model=child_model,
    )

    with pytest.raises(RunLimitExceeded) as error:
        agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "委派子 Agent 读取 README",
                    }
                ]
            }
        )

    assert error.value.reason is RunTerminationReason.MODEL_CALL_LIMIT
