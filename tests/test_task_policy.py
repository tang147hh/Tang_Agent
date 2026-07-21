from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.local_shell import LocalShellBackend
from app.backends.task_scoped import (
    TaskPermissionError,
    TaskScopedBackend,
)
from app.backends.workspace import Workspace
from app.core.prompt import get_system_prompt
from app.core.task_intent import (
    TaskKind,
    classify_task_kind,
    is_read_only_task,
)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("修复登录接口的问题", TaskKind.CODING),
        ("新增用户注销功能", TaskKind.CODING),
        ("把 JSON 存储迁移到 SQLite", TaskKind.CODING),
        ("先给方案，由我确认", TaskKind.PLANNING),
        ("不要修改代码，只分析迁移方案", TaskKind.PLANNING),
        ("分析这个项目的目录结构", TaskKind.ANALYSIS),
        ("帮我看看登录模块为什么失败", TaskKind.ANALYSIS),
        ("这个项目使用什么数据库？", TaskKind.QA),
    ],
)
def test_classifies_task_kind(
    prompt: str,
    expected: TaskKind,
) -> None:
    assert classify_task_kind(prompt) is expected


def test_non_coding_tasks_are_read_only() -> None:
    assert is_read_only_task(TaskKind.CODING) is False
    assert is_read_only_task(TaskKind.ANALYSIS) is True
    assert is_read_only_task(TaskKind.PLANNING) is True
    assert is_read_only_task(TaskKind.QA) is True


@pytest.fixture
def backend(tmp_path: Path) -> LocalShellBackend:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    return LocalShellBackend(workspace)


def test_read_only_backend_blocks_write(
    backend: LocalShellBackend,
) -> None:
    scoped = TaskScopedBackend.for_task(
        TaskKind.ANALYSIS,
        backend,
    )

    with pytest.raises(
        TaskPermissionError,
        match="analysis 任务禁止修改文件",
    ):
        scoped.write_text("/tmp/result.txt", "unsafe")


def test_read_only_backend_blocks_commands(
    backend: LocalShellBackend,
) -> None:
    scoped = TaskScopedBackend.for_task(
        TaskKind.PLANNING,
        backend,
    )

    with pytest.raises(
        TaskPermissionError,
        match="planning 任务禁止执行命令",
    ):
        scoped.run_command(["git", "status"], cwd="/projects")


def test_coding_backend_allows_write_and_command(
    backend: LocalShellBackend,
) -> None:
    scoped = TaskScopedBackend.for_task(
        TaskKind.CODING,
        backend,
    )

    path = scoped.write_text(
        "/tmp/lesson_7.py",
        "print('coding allowed')\n",
    )
    result = scoped.run_command(
        ["python", path],
        cwd="/tmp",
    )

    assert path == "/tmp/lesson_7.py"
    assert result.exit_code == 0
    assert result.stdout.strip() == "coding allowed"


def test_prompt_matches_task_policy() -> None:
    coding_prompt = get_system_prompt(TaskKind.CODING)
    analysis_prompt = get_system_prompt(TaskKind.ANALYSIS)

    assert "允许读取、创建和修改" in coding_prompt
    assert "这是只读任务" in analysis_prompt
    assert "GitHub" in coding_prompt
    assert "Gitee" not in coding_prompt