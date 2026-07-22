from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.core.task_intent import TaskKind
from app.core.task_runtime import (
    TaskEvent,
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

        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")

        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")

            # 必须先创建主表。
            connection.execute("""
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
                """)

            # 再创建引用 tasks 的事件表。
            connection.execute("""
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id)
                        REFERENCES tasks(thread_id)
                        ON DELETE CASCADE
                )
                """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_task_events_thread_id_id
                ON task_events(thread_id, id)
                """)

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
                raise KeyError(f"任务不存在：{thread_id}")

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
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def append_event(
        self,
        *,
        thread_id: str,
        kind: str,
        source: str,
        payload: dict[str, Any],
    ) -> TaskEvent:
        created_at = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_events (
                    thread_id,
                    kind,
                    source,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    kind,
                    source,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    created_at,
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM task_events
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        return self._event(row)

    def list_events(
        self,
        thread_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[TaskEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM task_events
                WHERE thread_id = ?
                AND id > ?
                ORDER BY id
                LIMIT ?
                """,
                (
                    thread_id,
                    after_id,
                    limit,
                ),
            ).fetchall()

        return [self._event(row) for row in rows]

    @staticmethod
    def _event(
        row: sqlite3.Row | None,
    ) -> TaskEvent:
        if row is None:
            raise RuntimeError("任务事件读取失败")

        payload = json.loads(row["payload_json"])

        if not isinstance(payload, dict):
            raise RuntimeError("任务事件 payload 不是对象")

        return TaskEvent(
            id=int(row["id"]),
            thread_id=str(row["thread_id"]),
            kind=str(row["kind"]),
            source=str(row["source"]),
            payload=payload,
            created_at=datetime.fromisoformat(row["created_at"]),
        )
