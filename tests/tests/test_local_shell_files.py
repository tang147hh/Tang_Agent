from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.local_shell import (
    BackendFileError,
    LocalShellBackend,
)
from app.backends.workspace import Workspace


@pytest.fixture
def backend(tmp_path: Path) -> LocalShellBackend:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    return LocalShellBackend(workspace)


def test_list_dir_uses_virtual_paths(
    backend: LocalShellBackend,
) -> None:
    backend.write_text("/projects/demo/README.md", "hello")

    entries = backend.list_dir("/projects/demo")

    assert len(entries) == 1
    assert entries[0].path == "/projects/demo/README.md"
    assert entries[0].is_dir is False
    assert entries[0].size == 5


def test_workspace_root_hides_secrets(
    backend: LocalShellBackend,
) -> None:
    paths = {entry.path for entry in backend.list_dir("/")}

    assert "/.secrets" not in paths
    assert "/projects" in paths


def test_read_text_supports_pagination(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/example.txt",
        "line-1\nline-2\nline-3\n",
    )

    result = backend.read_text(
        "/projects/demo/example.txt",
        offset=1,
        limit=2,
    )

    assert result == "line-2\nline-3"


def test_write_text_does_not_overwrite(
    backend: LocalShellBackend,
) -> None:
    path = "/projects/demo/existing.txt"
    backend.write_text(path, "first")

    with pytest.raises(BackendFileError):
        backend.write_text(path, "second")

    assert backend.read_text(path) == "first"


@pytest.mark.parametrize(
    "path",
    [
        "/skills/example.md",
        "/policies/security.md",
        "/runtimes/python.txt",
        "/logs/backend.log",
    ],
)
def test_write_rejects_read_only_roots(
    backend: LocalShellBackend,
    path: str,
) -> None:
    with pytest.raises(BackendFileError):
        backend.write_text(path, "forbidden")


def test_edit_text_replaces_unique_content(
    backend: LocalShellBackend,
) -> None:
    path = "/projects/demo/config.py"
    backend.write_text(path, "DEBUG = False\n")

    count = backend.edit_text(
        path,
        "DEBUG = False",
        "DEBUG = True",
    )

    assert count == 1
    assert backend.read_text(path) == "DEBUG = True"


def test_edit_text_rejects_ambiguous_content(
    backend: LocalShellBackend,
) -> None:
    path = "/projects/demo/items.txt"
    backend.write_text(path, "item\nitem\n")

    with pytest.raises(BackendFileError):
        backend.edit_text(path, "item", "updated")

    assert backend.read_text(path) == "item\nitem"


def test_edit_text_can_replace_all(
    backend: LocalShellBackend,
) -> None:
    path = "/projects/demo/items.txt"
    backend.write_text(path, "item\nitem\n")

    count = backend.edit_text(
        path,
        "item",
        "updated",
        replace_all=True,
    )

    assert count == 2
    assert backend.read_text(path) == "updated\nupdated"