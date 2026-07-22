from __future__ import annotations

from pathlib import Path

import pytest

from app.core.conversation import (
    MessageRole,
    RunStatus,
    ThreadStatus,
)
from app.store import SQLiteProjectThreadStore


def _create_thread(
    store: SQLiteProjectThreadStore,
):
    project = store.create_project(
        name="Demo",
        virtual_path="/projects/demo",
    )

    return store.create_thread(
        project_id=project.project_id,
        title="实现多轮对话",
    )


def test_persists_messages_and_runs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.sqlite"
    store = SQLiteProjectThreadStore(database)
    thread = _create_thread(store)

    run = store.create_run(thread_id=thread.thread_id)

    user_message = store.append_message(
        thread_id=thread.thread_id,
        run_id=run.run_id,
        role=MessageRole.USER,
        content="分析项目结构",
    )

    running = store.mark_run_running(run.run_id)

    assistant_message = store.append_message(
        thread_id=thread.thread_id,
        run_id=run.run_id,
        role=MessageRole.ASSISTANT,
        content="项目结构分析完成",
    )

    completed = store.complete_run(run.run_id)

    assert running.status is RunStatus.RUNNING
    assert completed.status is RunStatus.COMPLETED
    assert user_message.sequence < (assistant_message.sequence)

    reopened = SQLiteProjectThreadStore(database)

    messages = reopened.list_messages(thread.thread_id)
    runs = reopened.list_runs(thread.thread_id)
    restored_thread = reopened.get_thread(thread.thread_id)

    assert [message.role for message in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert [message.content for message in messages] == [
        "分析项目结构",
        "项目结构分析完成",
    ]

    assert len(runs) == 1
    assert runs[0].status is RunStatus.COMPLETED

    assert restored_thread is not None
    assert restored_thread.status is ThreadStatus.IDLE


def test_allows_only_one_active_run(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectThreadStore(tmp_path / "tasks.sqlite")
    thread = _create_thread(store)

    first_run = store.create_run(thread_id=thread.thread_id)

    with pytest.raises(
        ValueError,
        match="已经有正在执行",
    ):
        store.create_run(thread_id=thread.thread_id)

    store.mark_run_running(first_run.run_id)
    store.complete_run(first_run.run_id)

    second_run = store.create_run(thread_id=thread.thread_id)

    assert second_run.status is RunStatus.PENDING


def test_rejects_cross_thread_run_message(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectThreadStore(tmp_path / "tasks.sqlite")

    first_project = store.create_project(
        name="First",
        virtual_path="/projects/first",
    )
    second_project = store.create_project(
        name="Second",
        virtual_path="/projects/second",
    )

    first_thread = store.create_thread(
        project_id=first_project.project_id,
        title="第一个会话",
    )
    second_thread = store.create_thread(
        project_id=second_project.project_id,
        title="第二个会话",
    )

    run = store.create_run(thread_id=first_thread.thread_id)

    with pytest.raises(
        ValueError,
        match="不属于当前会话",
    ):
        store.append_message(
            thread_id=second_thread.thread_id,
            run_id=run.run_id,
            role=MessageRole.USER,
            content="错误关联",
        )


def test_rejects_illegal_run_transition(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectThreadStore(tmp_path / "tasks.sqlite")
    thread = _create_thread(store)

    run = store.create_run(thread_id=thread.thread_id)

    store.mark_run_running(run.run_id)
    store.complete_run(run.run_id)

    with pytest.raises(
        ValueError,
        match="非法 Run 状态转换",
    ):
        store.complete_run(run.run_id)


def test_atomically_starts_run_with_user_message(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectThreadStore(tmp_path / "tasks.sqlite")
    thread = _create_thread(store)

    run, message = store.start_run_with_message(
        thread_id=thread.thread_id,
        content="  分析项目结构  ",
    )

    assert run.status is RunStatus.PENDING
    assert message.role is MessageRole.USER
    assert message.content == "分析项目结构"
    assert message.run_id == run.run_id

    active_thread = store.get_thread(thread.thread_id)

    assert active_thread is not None
    assert active_thread.status is ThreadStatus.RUNNING

    with pytest.raises(
        ValueError,
        match="已经有正在执行",
    ):
        store.start_run_with_message(
            thread_id=thread.thread_id,
            content="重复运行",
        )

    # 第二次启动失败后，不能多出 Run 或 Message。
    assert len(store.list_runs(thread.thread_id)) == 1
    assert len(store.list_messages(thread.thread_id)) == 1


def test_run_events_survive_reopen_and_resume(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.sqlite"
    store = SQLiteProjectThreadStore(database)
    thread = _create_thread(store)

    run, _ = store.start_run_with_message(
        thread_id=thread.thread_id,
        content="分析项目",
    )

    created = store.append_run_event(
        run_id=run.run_id,
        kind="created",
        source="system",
        payload={
            "status": "pending",
        },
    )

    running = store.append_run_event(
        run_id=run.run_id,
        kind="running",
        source="system",
        payload={
            "status": "running",
        },
    )

    token = store.append_run_event(
        run_id=run.run_id,
        kind="token",
        source="main",
        payload={
            "text": "正在分析项目",
        },
    )

    assert created.event_id < running.event_id
    assert running.event_id < token.event_id

    reopened = SQLiteProjectThreadStore(database)

    remaining = reopened.list_run_events(
        run.run_id,
        after_id=created.event_id,
    )

    assert [
        event.kind
        for event in remaining
    ] == [
        "running",
        "token",
    ]

    assert remaining[-1].source == "main"
    assert remaining[-1].payload == {
        "text": "正在分析项目",
    }