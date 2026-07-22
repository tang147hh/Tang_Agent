from __future__ import annotations

from pathlib import Path

from app.core.task_intent import TaskKind
from app.core.task_runtime import TaskStatus
from app.store import SQLiteTaskStore


def test_sqlite_task_store_survives_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.sqlite"

    first_store = SQLiteTaskStore(database)

    created = first_store.create(
        prompt="分析项目结构",
        task_kind=TaskKind.ANALYSIS,
    )

    first_store.mark_running(created.thread_id)
    first_store.complete(
        created.thread_id,
        "STORE_PERSISTED",
    )

    # 模拟进程重启：创建全新的 Store 对象。
    second_store = SQLiteTaskStore(database)

    restored = second_store.get(
        created.thread_id
    )

    assert restored is not None
    assert restored.status is TaskStatus.COMPLETED
    assert restored.result == "STORE_PERSISTED"
    assert restored.task_kind is TaskKind.ANALYSIS