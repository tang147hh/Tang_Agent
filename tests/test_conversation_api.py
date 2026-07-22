from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.app import create_app
from app.backends.workspace import Workspace
from app.core.conversation import MessageRole
from app.core.task_runtime import TaskRegistry
from app.store import SQLiteProjectThreadStore


def _conversation_app(
    tmp_path: Path,
):
    workspace = Workspace(
        tmp_path / "workspace"
    )
    workspace.ensure_layout()

    store = SQLiteProjectThreadStore(
        tmp_path / "tasks.sqlite"
    )

    project = store.create_project(
        name="Demo",
        virtual_path="/projects/demo",
    )
    thread = store.create_thread(
        project_id=project.project_id,
        title="多轮对话",
    )

    run = store.create_run(
        thread_id=thread.thread_id
    )
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

    app = create_app(
        agent_factory=lambda task_kind: None,
        task_store=TaskRegistry(),
        navigation_store=store,
        workspace=workspace,
    )

    return app, thread, run


def test_reads_thread_messages_and_runs(
    tmp_path: Path,
) -> None:
    app, thread, run = _conversation_app(
        tmp_path
    )

    with TestClient(app) as client:
        messages_response = client.get(
            (
                f"/api/threads/"
                f"{thread.thread_id}/messages"
            )
        )
        runs_response = client.get(
            (
                f"/api/threads/"
                f"{thread.thread_id}/runs"
            )
        )
        run_response = client.get(
            f"/api/runs/{run.run_id}"
        )

    assert messages_response.status_code == 200
    assert runs_response.status_code == 200
    assert run_response.status_code == 200

    messages = messages_response.json()

    assert [
        message["role"]
        for message in messages
    ] == [
        "user",
        "assistant",
    ]
    assert [
        message["content"]
        for message in messages
    ] == [
        "分析项目结构",
        "项目结构分析完成",
    ]
    assert messages[0]["sequence"] < (
        messages[1]["sequence"]
    )

    runs = runs_response.json()

    assert len(runs) == 1
    assert runs[0]["run_id"] == run.run_id
    assert runs[0]["status"] == "completed"

    assert run_response.json()["status"] == (
        "completed"
    )


def test_missing_conversation_resources_return_404(
    tmp_path: Path,
) -> None:
    app, _, _ = _conversation_app(tmp_path)

    with TestClient(app) as client:
        messages = client.get(
            "/api/threads/not-found/messages"
        )
        runs = client.get(
            "/api/threads/not-found/runs"
        )
        run = client.get(
            "/api/runs/not-found"
        )

    assert messages.status_code == 404
    assert runs.status_code == 404
    assert run.status_code == 404