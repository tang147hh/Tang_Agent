from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.app import create_app
from app.backends.workspace import Workspace
from app.core.task_runtime import TaskRegistry
from app.store import SQLiteProjectThreadStore


def _test_app(
    tmp_path: Path,
):
    workspace = Workspace(
        tmp_path / "workspace"
    )
    workspace.ensure_layout()

    navigation_store = SQLiteProjectThreadStore(
        tmp_path / "tasks.sqlite"
    )

    app = create_app(
        agent_factory=lambda task_kind: None,
        task_store=TaskRegistry(),
        navigation_store=navigation_store,
        workspace=workspace,
    )

    return app, workspace


def test_creates_and_lists_projects_and_threads(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)

    (
        workspace.root
        / "projects"
        / "Tang_Agent"
    ).mkdir()

    with TestClient(app) as client:
        created_project = client.post(
            "/api/projects",
            json={
                "name": "Tang Agent",
                "virtual_path": (
                    "/projects/Tang_Agent"
                ),
            },
        )

        assert created_project.status_code == 201
        project = created_project.json()

        projects = client.get(
            "/api/projects"
        )

        assert projects.status_code == 200
        assert len(projects.json()) == 1

        first_thread = client.post(
            (
                f"/api/projects/"
                f"{project['project_id']}/threads"
            ),
            json={
                "title": "理解项目架构",
            },
        )
        second_thread = client.post(
            (
                f"/api/projects/"
                f"{project['project_id']}/threads"
            ),
            json={
                "title": "实现会话侧边栏",
            },
        )

        assert first_thread.status_code == 201
        assert second_thread.status_code == 201
        assert first_thread.json()["status"] == "idle"

        threads = client.get(
            (
                f"/api/projects/"
                f"{project['project_id']}/threads"
            )
        )

        assert threads.status_code == 200
        assert {
            item["title"]
            for item in threads.json()
        } == {
            "理解项目架构",
            "实现会话侧边栏",
        }

        detail = client.get(
            (
                "/api/threads/"
                f"{first_thread.json()['thread_id']}"
            )
        )

    assert detail.status_code == 200
    assert detail.json()["project_id"] == (
        project["project_id"]
    )


def test_rejects_invalid_project_paths(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)

    with TestClient(app) as client:
        outside = client.post(
            "/api/projects",
            json={
                "name": "系统目录",
                "virtual_path": "/etc",
            },
        )

        missing = client.post(
            "/api/projects",
            json={
                "name": "不存在",
                "virtual_path": (
                    "/projects/not-found"
                ),
            },
        )

    assert outside.status_code == 422
    assert missing.status_code == 422


def test_rejects_duplicates_and_missing_resources(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)

    project_directory = (
        workspace.root
        / "projects"
        / "demo"
    )
    project_directory.mkdir()

    with TestClient(app) as client:
        first = client.post(
            "/api/projects",
            json={
                "name": "Demo",
                "virtual_path": "/projects/demo",
            },
        )

        duplicate = client.post(
            "/api/projects",
            json={
                "name": "重复 Demo",
                "virtual_path": "/projects/demo",
            },
        )

        missing_threads = client.get(
            "/api/projects/not-found/threads"
        )
        missing_thread = client.get(
            "/api/threads/not-found"
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert missing_threads.status_code == 404
    assert missing_thread.status_code == 404