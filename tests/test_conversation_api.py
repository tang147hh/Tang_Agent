from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.app import create_app
from app.backends.workspace import Workspace
from app.core.conversation import MessageRole
from app.core.task_runtime import TaskRegistry
from app.store import SQLiteProjectThreadStore
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    ToolMessage,
)


class SuccessfulConversationAgent:
    def __init__(self) -> None:
        self.received_input = None
        self.received_config = None

    def stream(
        self,
        input_data,
        *,
        config,
        stream_mode,
        subgraphs,
        version,
    ):
        self.received_input = input_data
        self.received_config = config

        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(content="任务"),
                {},
            ),
        }

        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(content="已经完成"),
                {},
            ),
        }


class FailingConversationAgent:
    def stream(self, *args, **kwargs):
        raise RuntimeError("provider secret details")


class ObservableConversationAgent:
    def stream(self, *args, **kwargs):
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "subagent_type": "general-purpose",
                                "description": "分析项目结构",
                            },
                            "id": "call_subagent",
                            "type": "tool_call",
                        }
                    ],
                ),
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": ("tools:call_subagent",),
            "data": (
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "workspace_read",
                            "args": {
                                "path": "/projects/demo/README.md",
                            },
                            "id": "call_read",
                            "type": "tool_call",
                        }
                    ],
                ),
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": ("tools:call_subagent",),
            "data": (
                ToolMessage(
                    content="文件读取成功",
                    tool_call_id="call_read",
                    name="workspace_read",
                ),
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": ("tools:call_subagent",),
            "data": (
                AIMessageChunk(content="子 Agent 分析完成"),
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                ToolMessage(
                    content="分析结果已返回",
                    tool_call_id="call_subagent",
                    name="task",
                ),
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(content="主 Agent 完成回答"),
                {},
            ),
        }


def _conversation_app(
    tmp_path: Path,
    *,
    agent_factory=None,
):
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()

    store = SQLiteProjectThreadStore(tmp_path / "tasks.sqlite")

    project = store.create_project(
        name="Demo",
        virtual_path="/projects/demo",
    )
    thread = store.create_thread(
        project_id=project.project_id,
        title="多轮对话",
    )

    run = store.create_run(thread_id=thread.thread_id)
    store.append_message(
        thread_id=thread.thread_id,
        run_id=run.run_id,
        role=MessageRole.USER,
        content="分析项目结构",
    )
    store.mark_run_running(run.run_id)
    store.append_message(
        thread_id=thread.thread_id,
        run_id=run.run_id,
        role=MessageRole.ASSISTANT,
        content="项目结构分析完成",
    )
    store.complete_run(run.run_id)

    if agent_factory is None:
        agent = SuccessfulConversationAgent()
        agent_factory = lambda task_kind: agent

    app = create_app(
        agent_factory=agent_factory,
        task_store=TaskRegistry(),
        navigation_store=store,
        workspace=workspace,
    )

    return app, thread, run


def test_reads_thread_messages_and_runs(
    tmp_path: Path,
) -> None:
    app, thread, run = _conversation_app(tmp_path)

    with TestClient(app) as client:
        messages_response = client.get(
            (f"/api/threads/" f"{thread.thread_id}/messages")
        )
        runs_response = client.get((f"/api/threads/" f"{thread.thread_id}/runs"))
        run_response = client.get(f"/api/runs/{run.run_id}")

    assert messages_response.status_code == 200
    assert runs_response.status_code == 200
    assert run_response.status_code == 200

    messages = messages_response.json()

    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
    ]
    assert [message["content"] for message in messages] == [
        "分析项目结构",
        "项目结构分析完成",
    ]
    assert messages[0]["sequence"] < (messages[1]["sequence"])

    runs = runs_response.json()

    assert len(runs) == 1
    assert runs[0]["run_id"] == run.run_id
    assert runs[0]["status"] == "completed"

    assert run_response.json()["status"] == ("completed")


def test_missing_conversation_resources_return_404(
    tmp_path: Path,
) -> None:
    app, _, _ = _conversation_app(tmp_path)

    with TestClient(app) as client:
        messages = client.get("/api/threads/not-found/messages")
        runs = client.get("/api/threads/not-found/runs")
        run = client.get("/api/runs/not-found")

    assert messages.status_code == 404
    assert runs.status_code == 404
    assert run.status_code == 404


def test_starts_run_with_user_message(
    tmp_path: Path,
) -> None:
    app, thread, _ = _conversation_app(tmp_path)

    with TestClient(app) as client:
        agent = app.state.agent_factory(None)

        created = client.post(
            (f"/api/threads/" f"{thread.thread_id}/runs"),
            json={
                "content": "继续增加测试",
            },
        )

        assert created.status_code == 202

        payload = created.json()

        assert payload["run"]["status"] == ("pending")
        assert payload["message"]["role"] == ("user")
        assert payload["message"]["content"] == ("继续增加测试")
        assert payload["message"]["run_id"] == (payload["run"]["run_id"])

        run_response = client.get(
            f"/api/runs/{payload['run']['run_id']}"
        )
        messages = client.get(
            f"/api/threads/{thread.thread_id}/messages"
        ).json()

    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"

    assert [message["content"] for message in messages] == [
        "分析项目结构",
        "项目结构分析完成",
        "继续增加测试",
        "任务已经完成",
    ]

    run_id = payload["run"]["run_id"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["run_id"] == run_id

    assert agent.received_config == {
        "configurable": {
            "thread_id": thread.thread_id,
        }
    }

    prompt = agent.received_input["messages"][0]["content"]
    assert "/projects/demo" in prompt
    assert "继续增加测试" in prompt


def test_streams_conversation_run_events(
    tmp_path: Path,
) -> None:
    app, thread, _ = _conversation_app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            f"/api/threads/{thread.thread_id}/runs",
            json={
                "content": "继续分析项目",
            },
        )

        assert created.status_code == 202
        run_id = created.json()["run"]["run_id"]

        response = client.get(
            f"/api/runs/{run_id}/events"
        )

    assert response.status_code == 200
    assert response.headers[
        "content-type"
    ].startswith("text/event-stream")

    body = response.text
    event_names = [
        line.removeprefix("event: ")
        for line in body.splitlines()
        if line.startswith("event: ")
    ]

    assert event_names == [
        "created",
        "running",
        "token",
        "completed",
    ]

    token_payloads = [
        json.loads(line.removeprefix("data: "))
        for block in body.split("\n\n")
        if "event: token" in block
        for line in block.splitlines()
        if line.startswith("data: ")
    ]

    assert [payload["text"] for payload in token_payloads] == [
        "任务已经完成",
    ]


def test_streams_tool_and_subagent_run_events(
    tmp_path: Path,
) -> None:
    app, thread, _ = _conversation_app(
        tmp_path,
        agent_factory=lambda task_kind: (
            ObservableConversationAgent()
        ),
    )

    with TestClient(app) as client:
        created = client.post(
            f"/api/threads/{thread.thread_id}/runs",
            json={
                "content": "委派子 Agent 分析项目",
            },
        )
        run_id = created.json()["run"]["run_id"]
        response = client.get(f"/api/runs/{run_id}/events")
        messages = client.get(
            f"/api/threads/{thread.thread_id}/messages"
        ).json()

    assert response.status_code == 200

    events = []

    for block in response.text.split("\n\n"):
        event_name = next(
            (
                line.removeprefix("event: ")
                for line in block.splitlines()
                if line.startswith("event: ")
            ),
            None,
        )
        payload = next(
            (
                json.loads(line.removeprefix("data: "))
                for line in block.splitlines()
                if line.startswith("data: ")
            ),
            None,
        )

        if event_name is not None and payload is not None:
            events.append((event_name, payload))

    assert [name for name, _ in events] == [
        "created",
        "running",
        "tool_started",
        "tool_started",
        "tool_finished",
        "token",
        "tool_finished",
        "token",
        "completed",
    ]

    task_started = events[2][1]
    assert task_started["name"] == "task"
    assert task_started["tool_call_id"] == "call_subagent"
    assert task_started["subagent"] == "general-purpose"
    assert task_started["source"] == "main"

    child_tool_started = events[3][1]
    assert child_tool_started["name"] == "workspace_read"
    assert child_tool_started["tool_call_id"] == "call_read"
    assert child_tool_started["subagent"] == "general-purpose"
    assert child_tool_started["source"] == (
        "subagent:call_subagent"
    )

    subagent_token = events[5][1]
    assert subagent_token["text"] == "子 Agent 分析完成"
    assert subagent_token["source"] == "subagent:call_subagent"
    assert subagent_token["subagent"] == "general-purpose"

    main_token = events[7][1]
    assert main_token["text"] == "主 Agent 完成回答"
    assert main_token["source"] == "main"

    run_messages = [
        message
        for message in messages
        if message["run_id"] == run_id
    ]
    assert [message["content"] for message in run_messages] == [
        "委派子 Agent 分析项目",
        "主 Agent 完成回答",
    ]


def test_resumes_run_events_after_last_event_id(
    tmp_path: Path,
) -> None:
    app, thread, _ = _conversation_app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            f"/api/threads/{thread.thread_id}/runs",
            json={
                "content": "测试事件续传",
            },
        )

        run_id = created.json()["run"]["run_id"]
        store = app.state.navigation_store
        events = store.list_run_events(run_id)
        created_event = events[0]

        response = client.get(
            f"/api/runs/{run_id}/events",
            headers={
                "Last-Event-ID": str(
                    created_event.event_id
                ),
            },
        )

    body = response.text

    assert "event: created" not in body
    assert "event: running" in body
    assert "event: token" in body
    assert "event: completed" in body


def test_missing_run_event_stream_returns_404(
    tmp_path: Path,
) -> None:
    app, _, _ = _conversation_app(tmp_path)

    with TestClient(app) as client:
        response = client.get(
            "/api/runs/not-found/events"
        )

    assert response.status_code == 404


def test_start_run_rejects_missing_thread(
    tmp_path: Path,
) -> None:
    app, _, _ = _conversation_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/threads/not-found/runs",
            json={
                "content": "测试",
            },
        )

    assert response.status_code == 404


def test_background_agent_failure_updates_state(
    tmp_path: Path,
) -> None:
    app, thread, _ = _conversation_app(
        tmp_path,
        agent_factory=lambda task_kind: (
            FailingConversationAgent()
        ),
    )

    with TestClient(app) as client:
        created = client.post(
            f"/api/threads/{thread.thread_id}/runs",
            json={
                "content": "执行失败任务",
            },
        )

        assert created.status_code == 202
        run_id = created.json()["run"]["run_id"]

        run_response = client.get(
            f"/api/runs/{run_id}"
        )
        messages = client.get(
            f"/api/threads/{thread.thread_id}/messages"
        ).json()

    run_payload = run_response.json()

    assert run_payload["status"] == "failed"
    assert run_payload["error"] == (
        "任务执行失败，请查看服务日志"
    )
    assert "provider secret details" not in (
        run_payload["error"]
    )

    run_messages = [
        message
        for message in messages
        if message["run_id"] == run_id
    ]

    assert len(run_messages) == 1
    assert run_messages[0]["role"] == "user"
