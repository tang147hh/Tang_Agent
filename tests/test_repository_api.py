from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.app import create_app
from app.backends.workspace import Workspace
from app.core.task_runtime import TaskRegistry
from app.repositories import (
    GitHubClient,
    GitHubConfigurationError,
    PullRequestResult,
    RepositoryCatalog,
    RepositoryPushResult,
    RepositorySnapshot,
)
from app.store import SQLiteProjectThreadStore


def _test_app(tmp_path: Path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    app = create_app(
        agent_factory=lambda task_kind: None,
        task_store=TaskRegistry(),
        navigation_store=SQLiteProjectThreadStore(
            tmp_path / "tasks.sqlite"
        ),
        workspace=workspace,
    )
    return app, workspace


def _git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _initialize_repository(workspace: Workspace) -> None:
    target = workspace.resolve("/projects/demo")
    target.mkdir()
    _git(target, "init", "-b", "main")
    _git(target, "config", "user.name", "Tang Agent Tests")
    _git(target, "config", "user.email", "tests@example.com")
    target.joinpath("README.md").write_text("# Demo\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "Initial commit")
    _git(
        target,
        "remote",
        "add",
        "origin",
        "https://github.com/example/demo",
    )


def test_repository_list_branch_and_checkout_api(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)
    _initialize_repository(workspace)

    with TestClient(app) as client:
        listed = client.get("/api/repositories")
        created = client.post(
            "/api/repositories/demo/branches",
            json={"name": "feature/course"},
        )
        checked_out = client.post(
            "/api/repositories/demo/checkout",
            json={"name": "main"},
        )

    assert listed.status_code == 200
    assert listed.json() == [
        {
            "name": "demo",
            "path": "/projects/demo",
            "remote_url": "https://github.com/example/demo",
            "current_branch": "main",
            "branches": ["main"],
            "dirty": False,
        }
    ]
    assert created.status_code == 200
    assert created.json()["current_branch"] == "feature/course"
    assert checked_out.status_code == 200
    assert checked_out.json()["current_branch"] == "main"
    assert str(workspace.root) not in listed.text


def test_project_file_changes_api_returns_virtual_paths(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)
    _initialize_repository(workspace)
    target = workspace.resolve("/projects/demo")
    target.joinpath("README.md").write_text(
        "# Demo\nAPI change\n",
        encoding="utf-8",
    )
    target.joinpath("new.py").write_text(
        "print('safe')\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        project = client.post(
            "/api/projects",
            json={
                "name": "Demo",
                "virtual_path": "/projects/demo",
            },
        ).json()
        response = client.get(
            f"/api/projects/{project['project_id']}/file-changes"
        )
        missing = client.get(
            "/api/projects/missing-project/file-changes"
        )

    assert response.status_code == 200
    assert response.json()["changed_files"] == 2
    assert response.json()["additions"] == 2
    assert response.json()["deletions"] == 0
    assert [item["path"] for item in response.json()["files"]] == [
        "/projects/demo/new.py",
        "/projects/demo/README.md",
    ]
    assert str(workspace.root) not in response.text
    assert missing.status_code == 404


def test_project_file_changes_api_identifies_non_git_project(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)
    workspace.resolve("/projects/demo").mkdir()

    with TestClient(app) as client:
        project = client.post(
            "/api/projects",
            json={
                "name": "Demo",
                "virtual_path": "/projects/demo",
            },
        ).json()
        response = client.get(
            f"/api/projects/{project['project_id']}/file-changes"
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "repository_not_found",
            "message": "当前项目不是 Git 仓库",
        }
    }


def test_clone_and_fetch_api_status_codes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app, _ = _test_app(tmp_path)
    snapshot = RepositorySnapshot(
        name="demo",
        path="/projects/demo",
        remote_url="https://github.com/example/demo",
        current_branch="main",
        branches=("main",),
        dirty=False,
    )
    monkeypatch.setattr(
        RepositoryCatalog,
        "clone",
        lambda self, url: snapshot,
    )
    monkeypatch.setattr(
        RepositoryCatalog,
        "fetch",
        lambda self, name: snapshot,
    )

    with TestClient(app) as client:
        cloned = client.post(
            "/api/repositories/clone",
            json={"url": "https://github.com/example/demo.git"},
        )
        fetched = client.post("/api/repositories/demo/fetch")

    assert cloned.status_code == 201
    assert cloned.json()["path"] == "/projects/demo"
    assert fetched.status_code == 200


def test_repository_api_maps_validation_and_missing_errors(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)
    _initialize_repository(workspace)

    with TestClient(app) as client:
        invalid_clone = client.post(
            "/api/repositories/clone",
            json={"url": "git@github.com:example/demo.git"},
        )
        invalid_branch = client.post(
            "/api/repositories/demo/branches",
            json={"name": "bad branch"},
        )
        missing_repository = client.post(
            "/api/repositories/missing/fetch"
        )
        missing_branch = client.post(
            "/api/repositories/demo/checkout",
            json={"name": "missing"},
        )
        duplicate_branch = client.post(
            "/api/repositories/demo/branches",
            json={"name": "main"},
        )

    assert invalid_clone.status_code == 422
    assert invalid_branch.status_code == 422
    assert missing_repository.status_code == 404
    assert missing_branch.status_code == 404
    assert duplicate_branch.status_code == 409


def test_commit_repository_api(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)
    _initialize_repository(workspace)
    workspace.resolve("/projects/demo/feature.txt").write_text(
        "implemented\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/repositories/demo/commit",
            json={"message": "feat: implement course"},
        )

    assert response.status_code == 200
    assert response.json()["subject"] == "feat: implement course"
    assert len(response.json()["sha"]) == 40
    assert response.json()["repository"]["dirty"] is False


def test_push_and_pull_request_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app, _ = _test_app(tmp_path)
    snapshot = RepositorySnapshot(
        name="demo",
        path="/projects/demo",
        remote_url="https://github.com/example/demo",
        current_branch="feature/course",
        branches=("feature/course", "main"),
        dirty=False,
    )
    monkeypatch.setattr(
        RepositoryCatalog,
        "push",
        lambda self, name: RepositoryPushResult(
            repository=snapshot,
            branch="feature/course",
        ),
    )
    monkeypatch.setattr(
        RepositoryCatalog,
        "prepare_pull_request",
        lambda self, name, base: snapshot,
    )
    monkeypatch.setattr(
        GitHubClient,
        "create_pull_request",
        lambda self, **kwargs: PullRequestResult(
            number=42,
            url="https://github.com/example/demo/pull/42",
            title=kwargs["title"],
            base=kwargs["base"],
            head=kwargs["head"],
        ),
    )

    with TestClient(app) as client:
        pushed = client.post("/api/repositories/demo/push")
        pull_request = client.post(
            "/api/repositories/demo/pull-requests",
            json={
                "title": "feat: implement course",
                "body": "Course implementation",
                "base": "main",
            },
        )

    assert pushed.status_code == 200
    assert pushed.json()["branch"] == "feature/course"
    assert pull_request.status_code == 201
    assert pull_request.json() == {
        "number": 42,
        "url": "https://github.com/example/demo/pull/42",
        "title": "feat: implement course",
        "base": "main",
        "head": "feature/course",
    }


def test_pull_request_api_reports_missing_github_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app, _ = _test_app(tmp_path)
    snapshot = RepositorySnapshot(
        name="demo",
        path="/projects/demo",
        remote_url="https://github.com/example/demo",
        current_branch="feature/course",
        branches=("feature/course", "main"),
        dirty=False,
    )
    monkeypatch.setattr(
        RepositoryCatalog,
        "prepare_pull_request",
        lambda self, name, base: snapshot,
    )

    def unavailable_github(self, **kwargs):
        raise GitHubConfigurationError("后端主机尚未安装 GitHub CLI")

    monkeypatch.setattr(
        GitHubClient,
        "create_pull_request",
        unavailable_github,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/repositories/demo/pull-requests",
            json={"title": "Course", "base": "main"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "后端主机尚未安装 GitHub CLI",
    }
