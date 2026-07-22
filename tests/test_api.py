from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    ToolMessage,
)

from app.app import create_app
from app.core.task_intent import TaskKind
from app.core.task_runtime import TaskRegistry


class SuccessfulAgent:
    def stream(
        self,
        input_data: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        stream_mode: str | None = None,
        subgraphs: bool = False,
        version: str | None = None,
    ):
        assert input_data["messages"][0]["content"] == ("分析项目结构")
        assert config is not None
        assert "thread_id" in config["configurable"]
        assert stream_mode == "messages"
        assert subgraphs is True
        assert version == "v2"

        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(content="API_TASK_COMPLETED"),
                {},
            ),
        }


class FailingAgent:
    def stream(
        self,
        input_data: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        stream_mode: str | None = None,
        subgraphs: bool = False,
        version: str | None = None,
    ):
        raise RuntimeError("internal provider details")


def test_creates_and_completes_background_task() -> None:
    captured_kinds: list[TaskKind] = []

    def agent_factory(
        task_kind: TaskKind,
    ) -> SuccessfulAgent:
        captured_kinds.append(task_kind)
        return SuccessfulAgent()

    app = create_app(agent_factory=agent_factory)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            json={"prompt": "分析项目结构"},
        )

        assert response.status_code == 202

        accepted = response.json()

        assert accepted["status"] == "pending"
        assert accepted["task_kind"] == "analysis"
        assert accepted["result"] is None

        detail_response = client.get(f"/api/tasks/{accepted['thread_id']}")

    assert detail_response.status_code == 200

    completed = detail_response.json()

    assert completed["status"] == "completed"
    assert completed["result"] == "API_TASK_COMPLETED"
    assert captured_kinds == [TaskKind.ANALYSIS]


def test_background_failure_is_recorded_safely() -> None:
    def agent_factory(
        task_kind: TaskKind,
    ) -> FailingAgent:
        return FailingAgent()

    app = create_app(
        agent_factory=agent_factory,
        task_store=TaskRegistry(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            json={"prompt": "解释这个项目"},
        )

        thread_id = response.json()["thread_id"]

        detail = client.get(f"/api/tasks/{thread_id}").json()

    assert detail["status"] == "failed"
    assert detail["result"] is None
    assert detail["error"] == ("任务执行失败，请查看服务日志")
    assert "internal provider details" not in str(detail)


def test_validates_request_and_missing_task() -> None:
    app = create_app(agent_factory=lambda task_kind: SuccessfulAgent())

    with TestClient(app) as client:
        invalid = client.post(
            "/api/tasks",
            json={"prompt": "   "},
        )
        missing = client.get("/api/tasks/not-found")
        health = client.get("/health")

    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert health.status_code == 200
    assert health.json() == {"ok": True}


def test_streams_task_lifecycle_events() -> None:
    app = create_app(
        agent_factory=lambda task_kind: SuccessfulAgent(),
        task_store=TaskRegistry(),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks",
            json={
                "prompt": "分析项目结构",
            },
        )

        assert created.status_code == 202

        thread_id = created.json()["thread_id"]

        response = client.get(f"/api/tasks/{thread_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    body = response.text

    assert "event: created" in body
    assert "event: running" in body
    assert "event: token" in body
    assert "event: completed" in body
    assert "API_TASK_COMPLETED" in body
    assert "id:" in body

    assert body.index("event: created") < body.index("event: running")
    assert body.index("event: running") < body.index("event: token")
    assert body.index("event: token") < body.index("event: completed")


def test_missing_task_event_stream_returns_404() -> None:
    app = create_app(
        agent_factory=lambda task_kind: SuccessfulAgent(),
        task_store=TaskRegistry(),
    )

    with TestClient(app) as client:
        response = client.get("/api/tasks/not-found/events")

    assert response.status_code == 404


class ToolStreamingAgent:
    def stream(
        self,
        input_data: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        stream_mode: str | None = None,
        subgraphs: bool = False,
        version: str | None = None,
    ):
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "workspace_read",
                            "args": {
                                "path": ("/projects/demo/README.md"),
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
            "ns": (),
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
            "ns": (),
            "data": (
                AIMessageChunk(content="README 分析完成"),
                {},
            ),
        }


def test_streams_normalized_tool_events() -> None:
    app = create_app(
        agent_factory=lambda task_kind: (ToolStreamingAgent()),
        task_store=TaskRegistry(),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks",
            json={
                "prompt": "分析项目结构",
            },
        )

        thread_id = created.json()["thread_id"]

        response = client.get(f"/api/tasks/{thread_id}/events")

        detail = client.get(f"/api/tasks/{thread_id}")

    assert response.status_code == 200

    body = response.text

    assert "event: tool_started" in body
    assert "event: tool_finished" in body
    assert "workspace_read" in body
    assert "event: token" in body

    payloads = [
        json.loads(
            line.removeprefix("data: ")
        )
        for line in body.splitlines()
        if line.startswith("data: ")
    ]

    assert any(
        payload.get("text") == "README 分析完成"
        for payload in payloads
    )

    assert body.index("event: tool_started") < body.index("event: tool_finished")
    assert body.index("event: tool_finished") < body.index("event: token")

    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["result"] == ("README 分析完成")
