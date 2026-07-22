from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.app import create_app
from app.core.task_intent import TaskKind


class SuccessfulAgent:
    def invoke(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        assert input_data["messages"][0]["content"] == (
            "分析项目结构"
        )

        return {
            "messages": [
                AIMessage(content="API_TASK_COMPLETED")
            ]
        }


class FailingAgent:
    def invoke(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
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

        detail_response = client.get(
            f"/api/tasks/{accepted['thread_id']}"
        )

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

    app = create_app(agent_factory=agent_factory)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            json={"prompt": "解释这个项目"},
        )

        thread_id = response.json()["thread_id"]

        detail = client.get(
            f"/api/tasks/{thread_id}"
        ).json()

    assert detail["status"] == "failed"
    assert detail["result"] is None
    assert detail["error"] == (
        "任务执行失败，请查看服务日志"
    )
    assert "internal provider details" not in str(detail)


def test_validates_request_and_missing_task() -> None:
    app = create_app(
        agent_factory=lambda task_kind: SuccessfulAgent()
    )

    with TestClient(app) as client:
        invalid = client.post(
            "/api/tasks",
            json={"prompt": "   "},
        )
        missing = client.get(
            "/api/tasks/not-found"
        )
        health = client.get("/health")

    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert health.status_code == 200
    assert health.json() == {"ok": True}