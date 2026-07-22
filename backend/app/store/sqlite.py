from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.core.task_intent import TaskKind
from app.core.task_runtime import (
    TaskSnapshot,
    TaskStatus,
)


class SQLiteTaskStore:
    """使用 SQLite 持久化 API 可查询的业务任务。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._initialize()

    @contextmanager
    def _connection(
        self,
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )

        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    thread_id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    task_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create(
        self,
        *,
        prompt: str,
        task_kind: TaskKind,
    ) -> TaskSnapshot:
        thread_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    thread_id,
                    prompt,
                    task_kind,
                    status,
                    result,
                    error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    thread_id,
                    prompt,
                    task_kind.value,
                    TaskStatus.PENDING.value,
                    now,
                    now,
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM tasks
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

        return self._snapshot(row)

    def get(
        self,
        thread_id: str,
    ) -> TaskSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM tasks
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

        if row is None:
            return None

        return self._snapshot(row)

    def mark_running(
        self,
        thread_id: str,
    ) -> TaskSnapshot:
        return self._update(
            thread_id,
            status=TaskStatus.RUNNING,
            result=None,
            error=None,
        )

    def complete(
        self,
        thread_id: str,
        result: str,
    ) -> TaskSnapshot:
        return self._update(
            thread_id,
            status=TaskStatus.COMPLETED,
            result=result,
            error=None,
        )

    def fail(
        self,
        thread_id: str,
        error: str,
    ) -> TaskSnapshot:
        return self._update(
            thread_id,
            status=TaskStatus.FAILED,
            result=None,
            error=error,
        )

    def _update(
        self,
        thread_id: str,
        *,
        status: TaskStatus,
        result: str | None,
        error: str | None,
    ) -> TaskSnapshot:
        now = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?,
                    result = ?,
                    error = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    status.value,
                    result,
                    error,
                    now,
                    thread_id,
                ),
            )

            if cursor.rowcount == 0:
                raise KeyError(
                    f"任务不存在：{thread_id}"
                )

            row = connection.execute(
                """
                SELECT *
                FROM tasks
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

        return self._snapshot(row)

    @staticmethod
    def _snapshot(
        row: sqlite3.Row | None,
    ) -> TaskSnapshot:
        if row is None:
            raise RuntimeError("任务记录读取失败")

        return TaskSnapshot(
            thread_id=str(row["thread_id"]),
            prompt=str(row["prompt"]),
            task_kind=TaskKind(row["task_kind"]),
            status=TaskStatus(row["status"]),
            result=row["result"],
            error=row["error"],
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
            updated_at=datetime.fromisoformat(
                row["updated_at"]
            ),
        )