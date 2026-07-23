from __future__ import annotations
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from app.core.conversation import (
    MessageRole,
    MessageSnapshot,
    ProjectSnapshot,
    RunSnapshot,
    RunPerformanceSnapshot,
    RunStatus,
    ThreadSnapshot,
    ThreadStatus,
    RunEventSnapshot,
)
from app.core.task_intent import TaskKind
from app.core.review import (
    ReviewCategory,
    ReviewFindingDraft,
    ReviewFindingSnapshot,
    ReviewFindingStatus,
    ReviewSeverity,
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
                    task_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id)
                        REFERENCES threads(thread_id)
                        ON DELETE CASCADE
                )
                """)

            run_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(runs)"
                ).fetchall()
            }
            if "task_kind" not in run_columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN task_kind TEXT "
                    "NOT NULL DEFAULT 'qa'"
                )

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
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE CASCADE
                )
                """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS run_performance (
                    run_id TEXT PRIMARY KEY,
                    task_kind TEXT NOT NULL,
                    max_model_calls INTEGER NOT NULL,
                    max_tool_calls INTEGER NOT NULL,
                    max_first_output_seconds REAL NOT NULL,
                    max_seconds REAL NOT NULL,
                    max_identical_tool_calls INTEGER NOT NULL,
                    model_calls INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    repeated_tool_calls INTEGER NOT NULL DEFAULT 0,
                    tool_errors INTEGER NOT NULL DEFAULT 0,
                    safety_rejections INTEGER NOT NULL DEFAULT 0,
                    first_output_ms REAL,
                    duration_ms REAL,
                    termination_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE CASCADE
                )
                """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS review_findings (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    severity TEXT NOT NULL CHECK (
                        severity IN ('critical', 'high', 'medium', 'low')
                    ),
                    category TEXT NOT NULL CHECK (
                        category IN (
                            'correctness', 'security', 'performance',
                            'maintainability', 'testing', 'documentation'
                        )
                    ),
                    file_path TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    suggestion TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('open', 'resolved', 'dismissed')
                    ),
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE CASCADE,
                    CHECK (
                        (file_path IS NULL AND start_line IS NULL
                            AND end_line IS NULL)
                        OR
                        (file_path IS NOT NULL AND start_line > 0
                            AND end_line >= start_line)
                    ),
                    UNIQUE (run_id, fingerprint)
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

            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_run_events_run_id_event_id
                ON run_events(run_id, event_id)
                """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_review_findings_run_filters
                ON review_findings(run_id, severity, status)
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
        task_kind: TaskKind = TaskKind.QA,
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
                SELECT thread_id, title
                FROM threads
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()

            if thread is None:
                raise KeyError(f"会话不存在：{thread_id}")

            title = str(thread["title"])
            if title == "新对话":
                title = self._title_from_first_message(
                    normalized_content,
                )

            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id,
                        thread_id,
                        task_kind,
                        status,
                        error,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        run_id,
                        thread_id,
                        task_kind.value,
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
                    title = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    ThreadStatus.RUNNING.value,
                    title,
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

    @staticmethod
    def _title_from_first_message(content: str) -> str:
        """从首条消息生成稳定、可检索的会话标题。"""
        first_line = content.splitlines()[0]
        title = re.sub(r"[`#>*_~\[\]()]+", " ", first_line)
        title = re.sub(r"\s+", " ", title).strip(" ，。！？!?：:；;-—")
        for prefix in ("请帮我", "麻烦帮我", "帮我", "请", "我想要", "我想"):
            if title.startswith(prefix) and len(title) > len(prefix) + 2:
                title = title[len(prefix):].lstrip(" ，。:：")
                break
        if len(title) > 28:
            title = title[:28].rstrip() + "…"
        return title or "新对话"

    def create_run(
        self,
        *,
        thread_id: str,
        task_kind: TaskKind = TaskKind.QA,
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
                        task_kind,
                        status,
                        error,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        run_id,
                        thread_id,
                        task_kind.value,
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

    def initialize_run_performance(
        self,
        *,
        run_id: str,
        task_kind: TaskKind,
        max_model_calls: int,
        max_tool_calls: int,
        max_first_output_seconds: float,
        max_seconds: float,
        max_identical_tool_calls: int,
    ) -> RunPerformanceSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            run = connection.execute(
                "SELECT run_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Run 不存在：{run_id}")
            connection.execute(
                """
                INSERT INTO run_performance (
                    run_id, task_kind, max_model_calls, max_tool_calls,
                    max_first_output_seconds, max_seconds,
                    max_identical_tool_calls, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    task_kind = excluded.task_kind,
                    max_model_calls = excluded.max_model_calls,
                    max_tool_calls = excluded.max_tool_calls,
                    max_first_output_seconds = excluded.max_first_output_seconds,
                    max_seconds = excluded.max_seconds,
                    max_identical_tool_calls = excluded.max_identical_tool_calls,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    task_kind.value,
                    max_model_calls,
                    max_tool_calls,
                    max_first_output_seconds,
                    max_seconds,
                    max_identical_tool_calls,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM run_performance WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._run_performance_snapshot(row)

    def update_run_performance(
        self,
        *,
        run_id: str,
        model_calls: int,
        tool_calls: int,
        repeated_tool_calls: int,
        tool_errors: int,
        safety_rejections: int,
        first_output_ms: float | None,
        duration_ms: float,
        termination_reason: str | None,
    ) -> RunPerformanceSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE run_performance
                SET model_calls = ?,
                    tool_calls = ?,
                    repeated_tool_calls = ?,
                    tool_errors = ?,
                    safety_rejections = ?,
                    first_output_ms = ?,
                    duration_ms = ?,
                    termination_reason = ?,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    model_calls,
                    tool_calls,
                    repeated_tool_calls,
                    tool_errors,
                    safety_rejections,
                    first_output_ms,
                    duration_ms,
                    termination_reason,
                    now,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Run 性能记录不存在：{run_id}")
            row = connection.execute(
                "SELECT * FROM run_performance WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._run_performance_snapshot(row)

    def get_run_performance(
        self,
        run_id: str,
    ) -> RunPerformanceSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM run_performance WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._run_performance_snapshot(row)

    def add_review_findings(
        self,
        *,
        run_id: str,
        findings: list[ReviewFindingDraft],
    ) -> tuple[list[ReviewFindingSnapshot], int]:
        """原子写入一批已校验 Finding，重复指纹保持现有记录。"""

        now = datetime.now(timezone.utc).isoformat()
        created_count = 0
        fingerprints = [finding.fingerprint for finding in findings]

        with self._connection() as connection:
            run = connection.execute(
                "SELECT run_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Run 不存在：{run_id}")

            for finding in findings:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO review_findings (
                        id, run_id, severity, category, file_path,
                        start_line, end_line, title, description,
                        suggestion, status, fingerprint, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        finding.severity.value,
                        finding.category.value,
                        finding.file_path,
                        finding.start_line,
                        finding.end_line,
                        finding.title,
                        finding.description,
                        finding.suggestion,
                        ReviewFindingStatus.OPEN.value,
                        finding.fingerprint,
                        now,
                        now,
                    ),
                )
                created_count += cursor.rowcount

            if not fingerprints:
                rows: list[sqlite3.Row] = []
            else:
                placeholders = ",".join("?" for _ in fingerprints)
                rows = connection.execute(
                    f"""
                    SELECT * FROM review_findings
                    WHERE run_id = ?
                    AND fingerprint IN ({placeholders})
                    """,
                    (run_id, *fingerprints),
                ).fetchall()

        snapshots = [self._review_finding_snapshot(row) for row in rows]
        return self._sort_review_findings(snapshots), created_count

    def list_review_findings(
        self,
        run_id: str,
        *,
        severity: ReviewSeverity | None = None,
        status: ReviewFindingStatus | None = None,
    ) -> list[ReviewFindingSnapshot]:
        clauses = ["run_id = ?"]
        parameters: list[str] = [run_id]
        if severity is not None:
            clauses.append("severity = ?")
            parameters.append(severity.value)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)

        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM review_findings
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE severity
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        WHEN 'low' THEN 4
                    END,
                    COALESCE(file_path, ''),
                    COALESCE(start_line, 0),
                    created_at,
                    id
                """,
                parameters,
            ).fetchall()
        return [self._review_finding_snapshot(row) for row in rows]

    def update_review_finding_status(
        self,
        *,
        run_id: str,
        finding_id: str,
        status: ReviewFindingStatus,
    ) -> ReviewFindingSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE review_findings
                SET status = ?, updated_at = ?
                WHERE run_id = ? AND id = ?
                """,
                (status.value, now, run_id, finding_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Review Finding 不存在：{finding_id}")
            row = connection.execute(
                "SELECT * FROM review_findings WHERE id = ?",
                (finding_id,),
            ).fetchone()
        return self._review_finding_snapshot(row)

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

    def append_run_event(
        self,
        *,
        run_id: str,
        kind: str,
        source: str,
        payload: dict[str, Any],
    ) -> RunEventSnapshot:
        normalized_kind = kind.strip()
        normalized_source = source.strip()

        if not normalized_kind:
            raise ValueError("事件类型不能为空")

        if not normalized_source:
            raise ValueError("事件来源不能为空")

        created_at = datetime.now(
            timezone.utc
        ).isoformat()

        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        with self._connection() as connection:
            run_row = connection.execute(
                """
                SELECT run_id
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            if run_row is None:
                raise KeyError(f"Run 不存在：{run_id}")

            cursor = connection.execute(
                """
                INSERT INTO run_events (
                    run_id,
                    kind,
                    source,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    normalized_kind,
                    normalized_source,
                    payload_json,
                    created_at,
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM run_events
                WHERE event_id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        return self._run_event_snapshot(row)

    def list_run_events(
        self,
        run_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[RunEventSnapshot]:
        normalized_after_id = max(after_id, 0)
        normalized_limit = min(max(limit, 1), 1000)

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM run_events
                WHERE run_id = ?
                AND event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (
                    run_id,
                    normalized_after_id,
                    normalized_limit,
                ),
            ).fetchall()

        return [
            self._run_event_snapshot(row)
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
            task_kind=TaskKind(row["task_kind"]),
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

    @staticmethod
    def _run_event_snapshot(
        row: sqlite3.Row | None,
    ) -> RunEventSnapshot:
        if row is None:
            raise RuntimeError("Run 事件读取失败")

        payload = json.loads(row["payload_json"])

        if not isinstance(payload, dict):
            raise RuntimeError("Run 事件 payload 必须是对象")

        return RunEventSnapshot(
            event_id=int(row["event_id"]),
            run_id=str(row["run_id"]),
            kind=str(row["kind"]),
            source=str(row["source"]),
            payload=payload,
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
        )

    @staticmethod
    def _review_finding_snapshot(
        row: sqlite3.Row | None,
    ) -> ReviewFindingSnapshot:
        if row is None:
            raise RuntimeError("Review Finding 记录读取失败")
        return ReviewFindingSnapshot(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            severity=ReviewSeverity(row["severity"]),
            category=ReviewCategory(row["category"]),
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            title=str(row["title"]),
            description=str(row["description"]),
            suggestion=row["suggestion"],
            status=ReviewFindingStatus(row["status"]),
            fingerprint=str(row["fingerprint"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _sort_review_findings(
        findings: list[ReviewFindingSnapshot],
    ) -> list[ReviewFindingSnapshot]:
        severity_order = {
            ReviewSeverity.CRITICAL: 1,
            ReviewSeverity.HIGH: 2,
            ReviewSeverity.MEDIUM: 3,
            ReviewSeverity.LOW: 4,
        }
        return sorted(
            findings,
            key=lambda finding: (
                severity_order[finding.severity],
                finding.file_path or "",
                finding.start_line or 0,
                finding.created_at,
                finding.id,
            ),
        )

    @staticmethod
    def _run_performance_snapshot(
        row: sqlite3.Row | None,
    ) -> RunPerformanceSnapshot:
        if row is None:
            raise RuntimeError("Run 性能记录读取失败")
        return RunPerformanceSnapshot(
            run_id=str(row["run_id"]),
            task_kind=TaskKind(row["task_kind"]),
            max_model_calls=int(row["max_model_calls"]),
            max_tool_calls=int(row["max_tool_calls"]),
            max_first_output_seconds=float(
                row["max_first_output_seconds"]
            ),
            max_seconds=float(row["max_seconds"]),
            max_identical_tool_calls=int(
                row["max_identical_tool_calls"]
            ),
            model_calls=int(row["model_calls"]),
            tool_calls=int(row["tool_calls"]),
            repeated_tool_calls=int(row["repeated_tool_calls"]),
            tool_errors=int(row["tool_errors"]),
            safety_rejections=int(row["safety_rejections"]),
            first_output_ms=(
                float(row["first_output_ms"])
                if row["first_output_ms"] is not None
                else None
            ),
            duration_ms=(
                float(row["duration_ms"])
                if row["duration_ms"] is not None
                else None
            ),
            termination_reason=row["termination_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
