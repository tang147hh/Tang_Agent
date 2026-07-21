from __future__ import annotations

from pathlib import Path

import pytest

import app.core.agent as agent_module
from app.backends.local_shell import (
    BackendFileError,
    LocalShellBackend,
)
from app.backends.workspace import Workspace
from app.core.task_intent import TaskKind
from app.memory import (
    MAX_MEMORY_FILE_BYTES,
    MemoryLoadError,
    WorkspaceMemoryLoader,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    current = Workspace(tmp_path / "workspace")
    current.ensure_layout()
    return current


def write_memory(
    workspace: Workspace,
    content: str,
) -> None:
    workspace.resolve(
        "/policies/AGENTS.md"
    ).write_text(
        content,
        encoding="utf-8",
    )


def test_missing_memory_returns_empty_prompt(
    workspace: Workspace,
) -> None:
    loader = WorkspaceMemoryLoader(workspace)

    assert loader.load() is None
    assert loader.render_prompt() == ""


def test_loads_memory_and_removes_html_comments(
    workspace: Workspace,
) -> None:
    write_memory(
        workspace,
        (
            "<!-- 仅供维护者阅读 -->\n"
            "# Project Rules\n\n"
            "- Git 平台使用 GitHub。\n"
        ),
    )

    prompt = WorkspaceMemoryLoader(
        workspace
    ).render_prompt()

    assert "Git 平台使用 GitHub" in prompt
    assert "仅供维护者阅读" not in prompt
    assert "/policies/AGENTS.md" in prompt


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (b"\xff", "UTF-8"),
        (
            b"x" * (MAX_MEMORY_FILE_BYTES + 1),
            "大小限制",
        ),
    ],
)
def test_rejects_invalid_memory(
    workspace: Workspace,
    payload: bytes,
    expected_message: str,
) -> None:
    workspace.resolve(
        "/policies/AGENTS.md"
    ).write_bytes(payload)

    with pytest.raises(
        MemoryLoadError,
        match=expected_message,
    ):
        WorkspaceMemoryLoader(workspace).load()


def test_agent_system_prompt_contains_memory(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_memory(
        workspace,
        "MEMORY_INJECTION_VERIFIED",
    )
    backend = LocalShellBackend(workspace)
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return captured

    monkeypatch.setattr(
        agent_module,
        "create_deep_agent",
        fake_create_deep_agent,
    )

    result = agent_module.build_agent(
        TaskKind.QA,
        backend=backend,
        model=object(),
    )

    assert result is captured
    assert "MEMORY_INJECTION_VERIFIED" in str(
        captured["system_prompt"]
    )


def test_agent_cannot_modify_policy_memory(
    workspace: Workspace,
) -> None:
    write_memory(workspace, "# Original\n")
    backend = LocalShellBackend(workspace)

    with pytest.raises(
        BackendFileError,
        match="只读",
    ):
        backend.edit_text(
            "/policies/AGENTS.md",
            "# Original",
            "# Modified",
        )