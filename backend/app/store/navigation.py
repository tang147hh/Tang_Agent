from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.core.conversation import (
    ProjectSnapshot,
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

        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )
        connection.execute(
            "PRAGMA foreign_keys = ON"
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
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    virtual_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
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
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_threads_project_updated
                ON threads(project_id, updated_at DESC)
                """
            )

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
            raise ValueError(
                f"项目路径已经存在：{virtual_path}"
            ) from exc

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
            rows = connection.execute(
                """
                SELECT *
                FROM projects
                ORDER BY updated_at DESC, name ASC
                """
            ).fetchall()

        return [
            self._project_snapshot(row)
            for row in rows
        ]

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
            raise ValueError(
                f"项目不存在：{project_id}"
            ) from exc

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

        return [
            self._thread_snapshot(row)
            for row in rows
        ]

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
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
            updated_at=datetime.fromisoformat(
                row["updated_at"]
            ),
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
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
            updated_at=datetime.fromisoformat(
                row["updated_at"]
            ),
        )