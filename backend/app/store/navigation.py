from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from app.core.conversation import (
    MessageRole,
    MessageSnapshot,
    ProjectSnapshot,
    RunSnapshot,
    RunStatus,
    ThreadSnapshot,
    ThreadStatus,
)


class SQLiteProjectThreadStore:
    """使用 SQLite 保存项目和多轮会话。"""

    def __init__(
        self,
        path: str | Path,
    ) -> None:
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

            connection.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    virtual_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id)
                        REFERENCES projects(project_id)
                        ON DELETE CASCADE
                )
                """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id)
                        REFERENCES threads(thread_id)
                        ON DELETE CASCADE
                )
                """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL,
                    run_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id)
                        REFERENCES threads(thread_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE SET NULL
                )
                """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_threads_project_updated
                ON threads(project_id, updated_at DESC)
                """)

            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_runs_one_active_per_thread
                ON runs(thread_id)
                WHERE status IN ('pending', 'running')
                """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_messages_thread_sequence
                ON messages(thread_id, sequence)
                """)

    def create_project(
        self,
        *,
        name: str,
        virtual_path: str,
    ) -> ProjectSnapshot:
        project_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        project_id,
                        name,
                        virtual_path,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        name,
                        virtual_path,
                        now,
                        now,
                    ),
                )

                row = connection.execute(
                    """
                    SELECT *
                    FROM projects
                    WHERE project_id = ?
                    """,
                    (project_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"项目路径已经存在：{virtual_path}") from exc

        return self._project_snapshot(row)

    def get_project(
        self,
        project_id: str,
    ) -> ProjectSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()

        if row is None:
            return None

        return self._project_snapshot(row)

    def list_projects(
        self,
    ) -> list[ProjectSnapshot]:
        with self._connection() as connection:
            rows = connection.execute("""
                SELECT *
                FROM projects
                ORDER BY updated_at DESC, name ASC
                """).fetchall()

        return [self._project_snapshot(row) for row in rows]

    def create_thread(
        self,
        *,
        project_id: str,
        title: str,
    ) -> ThreadSnapshot:
        thread_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO threads (
                        thread_id,
                        project_id,
                        title,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        project_id,
                        title,
                        ThreadStatus.IDLE.value,
                        now,
                        now,
                    ),
                )

                # 新建会话也意味着这个项目刚刚被使用。
                connection.execute(
                    """
                    UPDATE projects
                    SET updated_at = ?
                    WHERE project_id = ?
                    """,
                    (
                        now,
                        project_id,
                    ),
                )

                row = connection.execute(
                    """
                    SELECT *
                    FROM threads
                    WHERE thread_id = ?
                    """,
                    (thread_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"项目不存在：{project_id}") from exc

        return self._thread_snapshot(row)

    def get_thread(
        self,
        thread_id: str,
    ) -> ThreadSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM threads
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

        if row is None:
            return None

        return self._thread_snapshot(row)

    def list_threads(
        self,
        project_id: str,
    ) -> list[ThreadSnapshot]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM threads
                WHERE project_id = ?
                ORDER BY updated_at DESC
                """,
                (project_id,),
            ).fetchall()

        return [self._thread_snapshot(row) for row in rows]

    def start_run_with_message(
        self,
        *,
        thread_id: str,
        content: str,
    ) -> tuple[RunSnapshot, MessageSnapshot]:
        normalized_content = content.strip()

        if not normalized_content:
            raise ValueError("消息内容不能为空")

        run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            thread = connection.execute(
                """
                SELECT thread_id
                FROM threads
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

            if thread is None:
                raise KeyError(f"会话不存在：{thread_id}")

            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id,
                        thread_id,
                        status,
                        error,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        run_id,
                        thread_id,
                        RunStatus.PENDING.value,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("当前会话已经有正在执行的 Run") from exc

            message_cursor = connection.execute(
                """
                INSERT INTO messages (
                    message_id,
                    thread_id,
                    run_id,
                    role,
                    content,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    run_id,
                    MessageRole.USER.value,
                    normalized_content,
                    now,
                ),
            )

            connection.execute(
                """
                UPDATE threads
                SET status = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    ThreadStatus.RUNNING.value,
                    now,
                    thread_id,
                ),
            )

            run_row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            message_row = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE sequence = ?
                """,
                (message_cursor.lastrowid,),
            ).fetchone()

        return (
            self._run_snapshot(run_row),
            self._message_snapshot(message_row),
        )

    def create_run(
        self,
        *,
        thread_id: str,
    ) -> RunSnapshot:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            thread = connection.execute(
                """
                SELECT thread_id
                FROM threads
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

            if thread is None:
                raise KeyError(f"会话不存在：{thread_id}")

            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id,
                        thread_id,
                        status,
                        error,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        run_id,
                        thread_id,
                        RunStatus.PENDING.value,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("当前会话已经有正在执行的 Run") from exc

            connection.execute(
                """
                UPDATE threads
                SET status = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    ThreadStatus.RUNNING.value,
                    now,
                    thread_id,
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        return self._run_snapshot(row)

    def get_run(
        self,
        run_id: str,
    ) -> RunSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        if row is None:
            return None

        return self._run_snapshot(row)

    def list_runs(
        self,
        thread_id: str,
    ) -> list[RunSnapshot]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE thread_id = ?
                ORDER BY created_at ASC
                """,
                (thread_id,),
            ).fetchall()

        return [self._run_snapshot(row) for row in rows]

    def mark_run_running(
        self,
        run_id: str,
    ) -> RunSnapshot:
        return self._update_run(
            run_id=run_id,
            new_status=RunStatus.RUNNING,
            thread_status=ThreadStatus.RUNNING,
            error=None,
        )

    def complete_run(
        self,
        run_id: str,
    ) -> RunSnapshot:
        return self._update_run(
            run_id=run_id,
            new_status=RunStatus.COMPLETED,
            thread_status=ThreadStatus.IDLE,
            error=None,
        )

    def complete_run_with_message(
        self,
        run_id: str,
        content: str,
    ) -> tuple[RunSnapshot, MessageSnapshot]:
        normalized_content = content.strip()

        if not normalized_content:
            raise ValueError("assistant 消息不能为空")

        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            run_row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            if run_row is None:
                raise KeyError(f"Run 不存在：{run_id}")

            current_status = RunStatus(run_row["status"])

            if current_status is not RunStatus.RUNNING:
                raise ValueError(
                    "只有 running Run 可以完成，"
                    f"当前状态：{current_status.value}"
                )

            thread_id = str(run_row["thread_id"])

            message_cursor = connection.execute(
                """
                INSERT INTO messages (
                    message_id,
                    thread_id,
                    run_id,
                    role,
                    content,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    run_id,
                    MessageRole.ASSISTANT.value,
                    normalized_content,
                    now,
                ),
            )

            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    error = NULL,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    RunStatus.COMPLETED.value,
                    now,
                    run_id,
                ),
            )

            connection.execute(
                """
                UPDATE threads
                SET status = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    ThreadStatus.IDLE.value,
                    now,
                    thread_id,
                ),
            )

            completed_run_row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            message_row = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE sequence = ?
                """,
                (message_cursor.lastrowid,),
            ).fetchone()

        assert completed_run_row is not None
        assert message_row is not None

        return (
            self._run_snapshot(completed_run_row),
            self._message_snapshot(message_row),
        )

    def fail_run(
        self,
        run_id: str,
        error: str,
    ) -> RunSnapshot:
        return self._update_run(
            run_id=run_id,
            new_status=RunStatus.FAILED,
            thread_status=ThreadStatus.ERROR,
            error=error,
        )

    def _update_run(
        self,
        *,
        run_id: str,
        new_status: RunStatus,
        thread_status: ThreadStatus,
        error: str | None,
    ) -> RunSnapshot:
        allowed_transitions = {
            RunStatus.PENDING: {
                RunStatus.RUNNING,
                RunStatus.FAILED,
            },
            RunStatus.RUNNING: {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
            },
        }

        now = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            current = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            if current is None:
                raise KeyError(f"Run 不存在：{run_id}")

            current_status = RunStatus(current["status"])

            allowed_statuses = allowed_transitions.get(
                current_status,
                set(),
            )

            if new_status not in allowed_statuses:
                raise ValueError(
                    "非法 Run 状态转换："
                    f"{current_status.value}"
                    f" → {new_status.value}"
                )

            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    error = ?,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    new_status.value,
                    error,
                    now,
                    run_id,
                ),
            )

            connection.execute(
                """
                UPDATE threads
                SET status = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    thread_status.value,
                    now,
                    current["thread_id"],
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        return self._run_snapshot(row)

    def append_message(
        self,
        *,
        thread_id: str,
        role: MessageRole,
        content: str,
        run_id: str | None = None,
    ) -> MessageSnapshot:
        normalized_content = content.strip()

        if not normalized_content:
            raise ValueError("消息内容不能为空")

        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._connection() as connection:
            thread = connection.execute(
                """
                SELECT thread_id
                FROM threads
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

            if thread is None:
                raise KeyError(f"会话不存在：{thread_id}")

            if run_id is not None:
                run = connection.execute(
                    """
                    SELECT thread_id
                    FROM runs
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()

                if run is None:
                    raise KeyError(f"Run 不存在：{run_id}")

                if run["thread_id"] != thread_id:
                    raise ValueError("消息的 Run 不属于当前会话")

            cursor = connection.execute(
                """
                INSERT INTO messages (
                    message_id,
                    thread_id,
                    run_id,
                    role,
                    content,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    run_id,
                    role.value,
                    normalized_content,
                    now,
                ),
            )

            connection.execute(
                """
                UPDATE threads
                SET updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    now,
                    thread_id,
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE sequence = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        return self._message_snapshot(row)

    def list_messages(
        self,
        thread_id: str,
    ) -> list[MessageSnapshot]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE thread_id = ?
                ORDER BY sequence ASC
                """,
                (thread_id,),
            ).fetchall()

        return [self._message_snapshot(row) for row in rows]

    @staticmethod
    def _project_snapshot(
        row: sqlite3.Row | None,
    ) -> ProjectSnapshot:
        if row is None:
            raise RuntimeError("项目记录读取失败")

        return ProjectSnapshot(
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            virtual_path=str(row["virtual_path"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _thread_snapshot(
        row: sqlite3.Row | None,
    ) -> ThreadSnapshot:
        if row is None:
            raise RuntimeError("会话记录读取失败")

        return ThreadSnapshot(
            thread_id=str(row["thread_id"]),
            project_id=str(row["project_id"]),
            title=str(row["title"]),
            status=ThreadStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _run_snapshot(
        row: sqlite3.Row | None,
    ) -> RunSnapshot:
        if row is None:
            raise RuntimeError("Run 记录读取失败")

        return RunSnapshot(
            run_id=str(row["run_id"]),
            thread_id=str(row["thread_id"]),
            status=RunStatus(row["status"]),
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _message_snapshot(
        row: sqlite3.Row | None,
    ) -> MessageSnapshot:
        if row is None:
            raise RuntimeError("消息记录读取失败")

        return MessageSnapshot(
            sequence=int(row["sequence"]),
            message_id=str(row["message_id"]),
            thread_id=str(row["thread_id"]),
            run_id=row["run_id"],
            role=MessageRole(row["role"]),
            content=str(row["content"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
