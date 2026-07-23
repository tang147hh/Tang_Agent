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


def _write_skill(
    workspace: Workspace,
) -> None:
    skill_directory = workspace.resolve(
        "/skills/repo-analysis"
    )
    skill_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    skill_directory.joinpath("SKILL.md").write_text(
        (
            "---\n"
            "name: repo-analysis\n"
            "description: 分析陌生代码仓库\n"
            "---\n\n"
            "# Repository Analysis\n\n"
            "DETAIL_BODY_LOADED\n"
        ),
        encoding="utf-8",
    )


def test_lists_and_gets_skills(
    tmp_path: Path,
) -> None:
    app, workspace = _test_app(tmp_path)
    _write_skill(workspace)

    with TestClient(app) as client:
        summaries_response = client.get(
            "/api/skills"
        )
        detail_response = client.get(
            "/api/skills/repo-analysis"
        )

    assert summaries_response.status_code == 200

    summaries = summaries_response.json()

    assert summaries == [
        {
            "name": "repo-analysis",
            "description": "分析陌生代码仓库",
            "path": "/skills/repo-analysis/SKILL.md",
        }
    ]
    assert "content" not in summaries[0]
    assert str(workspace.root) not in summaries_response.text

    assert detail_response.status_code == 200

    detail = detail_response.json()

    assert detail["name"] == "repo-analysis"
    assert detail["description"] == "分析陌生代码仓库"
    assert detail["path"] == (
        "/skills/repo-analysis/SKILL.md"
    )
    assert "# Repository Analysis" in detail["content"]
    assert "DETAIL_BODY_LOADED" in detail["content"]
    assert str(workspace.root) not in detail_response.text


def test_returns_404_for_missing_skill(
    tmp_path: Path,
) -> None:
    app, _ = _test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get(
            "/api/skills/missing-skill"
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "skill not found",
    }


def test_rejects_invalid_skill_name(
    tmp_path: Path,
) -> None:
    app, _ = _test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get(
            "/api/skills/bad_name"
        )

    assert response.status_code == 422