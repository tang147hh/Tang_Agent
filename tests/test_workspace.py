from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.workspace import (
    VIRTUAL_ROOTS,
    Workspace,
    WorkspacePathError,
)
from app.core.config import load_settings


def test_ensure_layout(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()

    for name in VIRTUAL_ROOTS:
        assert (workspace.root / name).is_dir()

    assert (workspace.root / ".secrets").is_dir()


def test_resolve_virtual_path(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    result = workspace.resolve("/projects/demo/main.py")

    assert result == (
        tmp_path / "workspace" / "projects" / "demo" / "main.py"
    ).resolve()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside",
        "/projects/../../outside",
        "/etc/passwd",
        "/projects/demo/.secrets/token",
        r"C:\Users\tang\secret.txt",
    ],
)
def test_rejects_unsafe_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    workspace = Workspace(tmp_path / "workspace")

    with pytest.raises(WorkspacePathError):
        workspace.resolve(unsafe_path)


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()

    outside = tmp_path / "outside"
    outside.mkdir()

    link = workspace.root / "projects" / "escape"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspacePathError):
        workspace.resolve("/projects/escape/secret.txt")


def test_virtual_path_round_trip(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    real_path = workspace.resolve("/projects/demo/README.md")

    assert workspace.to_virtual(real_path) == "/projects/demo/README.md"


def test_default_workspace_is_separate_from_project() -> None:
    settings = load_settings()
    workspace = Workspace.from_settings(settings)

    assert workspace.root != settings.project_root
    assert not workspace.root.is_relative_to(settings.project_root)
    assert not settings.project_root.is_relative_to(workspace.root)