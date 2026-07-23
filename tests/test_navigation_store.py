from __future__ import annotations

from pathlib import Path

import pytest

from app.core.conversation import ThreadStatus
from app.store import SQLiteProjectThreadStore


def test_projects_and_threads_survive_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.sqlite"

    first_store = SQLiteProjectThreadStore(
        database
    )

    project = first_store.create_project(
        name="Tang_Agent",
        virtual_path="/projects/Tang_Agent",
    )

    first_thread = first_store.create_thread(
        project_id=project.project_id,
        title="理解项目架构",
    )
    second_thread = first_store.create_thread(
        project_id=project.project_id,
        title="实现会话侧边栏",
    )

    assert first_thread.status is ThreadStatus.IDLE
    assert second_thread.project_id == project.project_id

    # 模拟应用重启。
    second_store = SQLiteProjectThreadStore(
        database
    )

    restored_project = second_store.get_project(
        project.project_id
    )
    restored_threads = second_store.list_threads(
        project.project_id
    )

    assert restored_project is not None
    assert restored_project.name == "Tang_Agent"
    assert restored_project.virtual_path == (
        "/projects/Tang_Agent"
    )

    assert len(restored_threads) == 2
    assert {
        thread.title
        for thread in restored_threads
    } == {
        "理解项目架构",
        "实现会话侧边栏",
    }


def test_rejects_duplicate_project_path(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectThreadStore(
        tmp_path / "tasks.sqlite"
    )

    store.create_project(
        name="Tang Agent",
        virtual_path="/projects/Tang_Agent",
    )

    with pytest.raises(
        ValueError,
        match="项目路径已经存在",
    ):
        store.create_project(
            name="重复项目",
            virtual_path="/projects/Tang_Agent",
        )


def test_rejects_thread_for_missing_project(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectThreadStore(
        tmp_path / "tasks.sqlite"
    )

    with pytest.raises(
        ValueError,
        match="项目不存在",
    ):
        store.create_thread(
            project_id="missing-project",
            title="无效会话",
        )


def test_names_new_thread_from_first_message(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectThreadStore(
        tmp_path / "tasks.sqlite"
    )
    project = store.create_project(
        name="Demo",
        virtual_path="/projects/demo",
    )
    thread = store.create_thread(
        project_id=project.project_id,
        title="新对话",
    )

    store.start_run_with_message(
        thread_id=thread.thread_id,
        content="请帮我优化前端 UI，并修复聊天布局问题",
    )

    renamed = store.get_thread(thread.thread_id)
    assert renamed is not None
    assert renamed.title == "优化前端 UI，并修复聊天布局问题"
