from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from app.backends.local_shell import LocalShellBackend
from app.backends.workspace import Workspace
from app.core.agent import build_agent
from app.core.task_intent import TaskKind


class ToolCallingFakeModel(
    FakeMessagesListChatModel
):
    def bind_tools(self, tools, **kwargs):
        return self


def test_checkpoint_survives_agent_rebuild(
    tmp_path: Path,
) -> None:
    workspace = Workspace(
        tmp_path / "workspace"
    )
    workspace.ensure_layout()

    backend = LocalShellBackend(workspace)
    database = tmp_path / "checkpoints.sqlite"

    config = {
        "configurable": {
            "thread_id": "checkpoint-test-thread",
        }
    }

    first_connection = sqlite3.connect(
        database,
        check_same_thread=False,
    )
    first_saver = SqliteSaver(first_connection)

    first_model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="CHECKPOINT_SAVED"
            )
        ]
    )

    first_agent = build_agent(
        TaskKind.QA,
        backend=backend,
        model=first_model,
        subagent_model=first_model,
        checkpointer=first_saver,
    )

    first_agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "保存这条消息",
                }
            ]
        },
        config=config,
    )

    first_connection.close()

    # 模拟服务重启：新连接、新 Saver、新 Agent。
    second_connection = sqlite3.connect(
        database,
        check_same_thread=False,
    )
    second_saver = SqliteSaver(second_connection)

    second_model = ToolCallingFakeModel(
        responses=[
            AIMessage(content="unused")
        ]
    )

    second_agent = build_agent(
        TaskKind.QA,
        backend=backend,
        model=second_model,
        subagent_model=second_model,
        checkpointer=second_saver,
    )

    state = second_agent.get_state(config)

    contents = [
        str(message.content)
        for message in state.values["messages"]
    ]

    second_connection.close()

    assert "保存这条消息" in contents
    assert "CHECKPOINT_SAVED" in contents
    assert state.next == ()