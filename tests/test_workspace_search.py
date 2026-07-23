from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.backends.local_shell import (
    MAX_TEXT_FILE_BYTES,
    BackendFileError,
    LocalShellBackend,
)
from app.backends.task_scoped import TaskScopedBackend
from app.backends.workspace import Workspace, WorkspacePathError
from app.core.task_intent import TaskKind
from app.tools import build_workspace_tools
from app.tools.capabilities import (
    READ_ONLY_TASK_KINDS,
    TOOL_CAPABILITIES,
    ToolCategory,
)


@pytest.fixture
def backend(tmp_path: Path) -> LocalShellBackend:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    return LocalShellBackend(workspace)


def _real_path(backend: LocalShellBackend, virtual_path: str) -> Path:
    path = backend.workspace.resolve(virtual_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_workspace_search_capabilities_are_fixed_local_read_tools() -> None:
    for name in ("workspace_glob", "workspace_search"):
        capability = TOOL_CAPABILITIES[name]
        assert capability.category is ToolCategory.LOCAL_READ
        assert capability.allowed_task_kinds == READ_ONLY_TASK_KINDS
        assert capability.requires_network_access is False
        assert capability.model_callable is True


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("**/*.py", ["/projects/demo/main.py", "/projects/demo/tests/test_api.py"]),
        ("frontend/src/**/*.tsx", ["/projects/demo/frontend/src/App.tsx"]),
        ("**/package.json", ["/projects/demo/frontend/package.json"]),
        ("**/test_*.py", ["/projects/demo/tests/test_api.py"]),
        ("docs/**/*.md", ["/projects/demo/docs/guide.md"]),
    ],
)
def test_workspace_glob_supports_course_patterns(
    backend: LocalShellBackend,
    pattern: str,
    expected: list[str],
) -> None:
    for path in [
        "/projects/demo/main.py",
        "/projects/demo/tests/test_api.py",
        "/projects/demo/frontend/src/App.tsx",
        "/projects/demo/frontend/package.json",
        "/projects/demo/docs/guide.md",
    ]:
        backend.write_text(path, path)

    result = backend.glob_paths(
        "/projects/demo",
        pattern=pattern,
    )

    assert [match.path for match in result.matches] == expected
    assert result.match_count == len(expected)
    assert result.truncated is False
    assert result.duration_ms >= 0


def test_workspace_glob_has_stable_sorting_and_result_limit(
    backend: LocalShellBackend,
) -> None:
    for name in ["z.py", "B.py", "a.py", "m.py"]:
        backend.write_text(f"/projects/demo/{name}", name)

    result = backend.glob_paths(
        "/projects/demo",
        pattern="**/*.py",
        max_results=3,
    )

    assert [match.path for match in result.matches] == [
        "/projects/demo/a.py",
        "/projects/demo/B.py",
        "/projects/demo/m.py",
    ]
    assert result.match_count == 3
    assert result.truncated is True


def test_workspace_glob_returns_directories_only_when_requested(
    backend: LocalShellBackend,
) -> None:
    backend.write_text("/projects/demo/src/app.py", "pass")

    files_only = backend.glob_paths(
        "/projects/demo",
        pattern="**",
    )
    with_directories = backend.glob_paths(
        "/projects/demo",
        pattern="**",
        include_directories=True,
    )

    assert [match.kind for match in files_only.matches] == ["file"]
    assert [(match.path, match.kind, match.size_bytes) for match in with_directories.matches] == [
        ("/projects/demo/src", "directory", 0),
        ("/projects/demo/src/app.py", "file", 4),
    ]


@pytest.mark.parametrize(
    "path",
    [
        "projects/demo",
        "//projects/demo",
        "/Users/tang/private",
        "C:/Users/tang/private",
        "/projects/../tmp",
        "/projects/demo\nother",
    ],
)
def test_workspace_glob_rejects_non_virtual_or_escaping_roots(
    backend: LocalShellBackend,
    path: str,
) -> None:
    with pytest.raises((BackendFileError, WorkspacePathError)):
        backend.glob_paths(path, pattern="**/*.py")


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        "/projects/**/*.py",
        "C:/projects/**/*.py",
        "../**/*.py",
        "src/../*.py",
        "src//*.py",
        "src/./*.py",
        "**/.secrets/*",
        "**/*.py\n",
        "x" * 513,
    ],
)
def test_workspace_glob_rejects_unsafe_patterns(
    backend: LocalShellBackend,
    pattern: str,
) -> None:
    with pytest.raises((BackendFileError, WorkspacePathError)):
        backend.glob_paths("/projects", pattern=pattern)


@pytest.mark.parametrize("max_results", [0, 501, True, 1.5])
def test_workspace_glob_rejects_invalid_result_limits(
    backend: LocalShellBackend,
    max_results: object,
) -> None:
    with pytest.raises(BackendFileError):
        backend.glob_paths(
            "/projects",
            pattern="**/*",
            max_results=max_results,  # type: ignore[arg-type]
        )


def test_workspace_scans_skip_dependencies_sensitive_files_and_symlinks(
    backend: LocalShellBackend,
    tmp_path: Path,
) -> None:
    backend.write_text("/projects/demo/src/app.py", "SAFE_MATCH")
    backend.write_text("/projects/demo/.env", "SAFE_MATCH")
    backend.write_text("/projects/demo/.env.example", "SAFE_MATCH")
    backend.write_text("/projects/demo/node_modules/pkg/index.py", "SAFE_MATCH")
    backend.write_text("/projects/demo/.git/config", "SAFE_MATCH")
    _real_path(backend, "/projects/demo/private.pem").write_text(
        "SAFE_MATCH",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.py"
    outside.write_text("SAFE_MATCH", encoding="utf-8")
    _real_path(backend, "/projects/demo/outside-link.py").symlink_to(outside)
    _real_path(backend, "/projects/demo/inside-link.py").symlink_to(
        backend.workspace.resolve("/projects/demo/src/app.py")
    )

    glob_result = backend.glob_paths(
        "/projects/demo",
        pattern="**/*",
    )
    search_result = backend.search_text(
        "/projects/demo",
        query="SAFE_MATCH",
    )

    assert [match.path for match in glob_result.matches] == [
        "/projects/demo/.env.example",
        "/projects/demo/src/app.py",
    ]
    assert [match.path for match in search_result.matches] == [
        "/projects/demo/.env.example",
        "/projects/demo/src/app.py",
    ]


def test_workspace_glob_rejects_symlink_search_root_escape(
    backend: LocalShellBackend,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    backend.workspace.resolve("/projects").joinpath("escape").symlink_to(outside)

    with pytest.raises(WorkspacePathError):
        backend.glob_paths("/projects/escape", pattern="**/*")


def test_workspace_search_returns_file_line_column_and_literal_snippet(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/app.py",
        "Target(other)\nvalue = Target(1)  # Target is literal\n",
    )
    backend.write_text(
        "/projects/demo/tests/test_app.py",
        "assert Target(2)\n",
    )

    result = backend.search_text(
        "/projects/demo",
        query="Target(1)",
        file_pattern="**/*.py",
    )

    assert result.match_count == 1
    assert result.files_searched == 2
    assert result.matches[0].path == "/projects/demo/app.py"
    assert result.matches[0].line_number == 2
    assert result.matches[0].column_start == 9
    assert result.matches[0].column_end == 17
    assert result.matches[0].snippet == "value = Target(1)  # Target is literal"


def test_workspace_search_supports_case_insensitive_literal_matching(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/main.py",
        "WorkspaceSearch = True\nworkspace_search = False\n",
    )

    sensitive = backend.search_text(
        "/projects/demo",
        query="workspace_search",
        case_sensitive=True,
    )
    insensitive = backend.search_text(
        "/projects/demo",
        query="workspace_search",
        case_sensitive=False,
    )

    assert [match.line_number for match in sensitive.matches] == [2]
    assert [match.line_number for match in insensitive.matches] == [2]
    mixed_case = backend.search_text(
        "/projects/demo",
        query="workspacesearch",
        case_sensitive=False,
    )
    assert [match.line_number for match in mixed_case.matches] == [1]


def test_workspace_search_skips_binary_non_utf8_and_oversized_files(
    backend: LocalShellBackend,
) -> None:
    backend.write_text("/projects/demo/ok.txt", "needle\n")
    _real_path(backend, "/projects/demo/binary.bin").write_bytes(
        b"needle\x00binary"
    )
    _real_path(backend, "/projects/demo/invalid.txt").write_bytes(
        b"needle\xff"
    )
    _real_path(backend, "/projects/demo/large.txt").write_bytes(
        b"needle" + b"x" * MAX_TEXT_FILE_BYTES
    )

    result = backend.search_text(
        "/projects/demo",
        query="needle",
    )

    assert [match.path for match in result.matches] == [
        "/projects/demo/ok.txt"
    ]
    assert result.files_searched == 1
    assert result.skipped_file_count == 3
    assert result.scanned_bytes < MAX_TEXT_FILE_BYTES


def test_workspace_search_truncates_in_stable_file_and_line_order(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/projects/demo/b.py",
        "needle b1\nneedle b2\n",
    )
    backend.write_text(
        "/projects/demo/a.py",
        "needle a1\nneedle a2\n",
    )

    result = backend.search_text(
        "/projects/demo",
        query="needle",
        max_results=3,
    )

    assert [(match.path, match.line_number) for match in result.matches] == [
        ("/projects/demo/a.py", 1),
        ("/projects/demo/a.py", 2),
        ("/projects/demo/b.py", 1),
    ]
    assert result.match_count == 3
    assert result.truncated is True


def test_workspace_search_limits_long_snippets(
    backend: LocalShellBackend,
) -> None:
    line = "a" * 400 + "needle" + "b" * 400
    backend.write_text("/projects/demo/long.py", line)

    result = backend.search_text(
        "/projects/demo",
        query="needle",
    )

    assert len(result.matches[0].snippet) <= 500
    assert "needle" in result.matches[0].snippet
    assert result.matches[0].column_start == 401


@pytest.mark.parametrize("query", ["", "needle\nnext", "x" * 501])
def test_workspace_search_rejects_unsafe_queries(
    backend: LocalShellBackend,
    query: str,
) -> None:
    with pytest.raises(BackendFileError):
        backend.search_text("/projects", query=query)


def test_workspace_tools_expose_bounded_structured_schemas(
    backend: LocalShellBackend,
) -> None:
    tools = {
        tool.name: tool
        for tool in build_workspace_tools(
            TaskScopedBackend.for_task(TaskKind.ANALYSIS, backend)
        )
    }
    glob_result = tools["workspace_glob"].invoke(
        {
            "path": "/projects",
            "pattern": "**/*.py",
            "max_results": 1,
        }
    )
    search_result = tools["workspace_search"].invoke(
        {
            "path": "/projects",
            "query": "needle",
            "max_results": 1,
        }
    )

    assert glob_result["ok"] is True
    assert search_result["ok"] is True
    with pytest.raises(ValidationError):
        tools["workspace_glob"].invoke(
            {"path": "/projects", "pattern": "**/*", "max_results": 501}
        )
    with pytest.raises(ValidationError):
        tools["workspace_search"].invoke(
            {"path": "/projects", "query": "needle", "max_results": 0}
        )
