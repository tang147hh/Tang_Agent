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
from app.core.review_diff import (
    ReviewDiff,
    ReviewDiffSnapshot,
    ReviewLineSide,
    ReviewScope,
    ReviewSnapshotStatus,
    review_diff_from_dict,
    review_diff_to_dict,
)
from app.core.github_review import (
    GitHubPublicationStatus,
    GitHubReviewEvent,
    GitHubReviewPublicationSnapshot,
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
                    network_access INTEGER NOT NULL DEFAULT 0,
                    network_provider TEXT NOT NULL DEFAULT 'disabled',
                    network_request_count INTEGER NOT NULL DEFAULT 0,
                    network_result_count INTEGER NOT NULL DEFAULT 0,
                    network_bytes_received INTEGER NOT NULL DEFAULT 0,
                    network_cache_hit_count INTEGER NOT NULL DEFAULT 0,
                    network_limit_reached INTEGER NOT NULL DEFAULT 0,
                    network_limit_reason TEXT,
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
            run_column_migrations = {
                "network_access": "INTEGER NOT NULL DEFAULT 0",
                "network_provider": "TEXT NOT NULL DEFAULT 'disabled'",
                "network_request_count": "INTEGER NOT NULL DEFAULT 0",
                "network_result_count": "INTEGER NOT NULL DEFAULT 0",
                "network_bytes_received": "INTEGER NOT NULL DEFAULT 0",
                "network_cache_hit_count": "INTEGER NOT NULL DEFAULT 0",
                "network_limit_reached": "INTEGER NOT NULL DEFAULT 0",
                "network_limit_reason": "TEXT",
            }
            for column, definition in run_column_migrations.items():
                if column not in run_columns:
                    connection.execute(
                        f"ALTER TABLE runs ADD COLUMN {column} {definition}"
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
                    line_side TEXT CHECK (
                        line_side IS NULL OR line_side IN ('old', 'new')
                    ),
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    suggestion TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('open', 'resolved', 'dismissed')
                    ),
                    fingerprint TEXT NOT NULL,
                    review_diff_hash TEXT,
                    review_scope TEXT CHECK (
                        review_scope IS NULL OR
                        review_scope IN ('staged', 'unstaged', 'all')
                    ),
                    base_revision TEXT,
                    head_revision TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE CASCADE,
                    CHECK (
                        (file_path IS NULL AND start_line IS NULL
                            AND end_line IS NULL AND line_side IS NULL)
                        OR
                        (file_path IS NOT NULL
                            AND start_line IS NULL
                            AND end_line IS NULL
                            AND line_side IS NULL)
                        OR
                        (file_path IS NOT NULL
                            AND start_line IS NOT NULL
                            AND end_line IS NOT NULL
                            AND start_line > 0
                            AND end_line >= start_line
                            AND line_side IN ('old', 'new'))
                    ),
                    UNIQUE (run_id, fingerprint)
                )
                """)

            self._upgrade_review_findings_schema(connection)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS review_diff_snapshots (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN ('collected', 'completed', 'failed')
                    ),
                    payload_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE CASCADE
                )
                """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS github_review_publications (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    base_sha TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    event TEXT NOT NULL CHECK (
                        event IN ('COMMENT', 'APPROVE', 'REQUEST_CHANGES')
                    ),
                    selected_finding_ids_json TEXT NOT NULL,
                    finding_state_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'prepared', 'publishing', 'published',
                            'failed', 'unknown'
                        )
                    ),
                    github_review_id TEXT,
                    github_review_url TEXT,
                    github_user TEXT,
                    prepared_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    published_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE CASCADE
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

            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_github_review_publications_run
                ON github_review_publications(run_id, prepared_at DESC)
                """)

            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_github_review_publications_published_payload
                ON github_review_publications(payload_hash)
                WHERE status = 'published'
                """)

            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_github_review_publications_active_payload
                ON github_review_publications(payload_hash)
                WHERE status IN ('publishing', 'published', 'unknown')
                """)

    @staticmethod
    def _upgrade_review_findings_schema(
        connection: sqlite3.Connection,
    ) -> None:
        """把第 34 课表无损升级为支持 Diff 定位的第 35 课结构。"""

        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(review_findings)"
            ).fetchall()
        }
        required = {
            "line_side",
            "review_diff_hash",
            "review_scope",
            "base_revision",
            "head_revision",
        }
        if required <= columns:
            return

        connection.execute("DROP TABLE IF EXISTS review_findings_v35")
        connection.execute("""
            CREATE TABLE review_findings_v35 (
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
                line_side TEXT CHECK (
                    line_side IS NULL OR line_side IN ('old', 'new')
                ),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                suggestion TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('open', 'resolved', 'dismissed')
                ),
                fingerprint TEXT NOT NULL,
                review_diff_hash TEXT,
                review_scope TEXT CHECK (
                    review_scope IS NULL OR
                    review_scope IN ('staged', 'unstaged', 'all')
                ),
                base_revision TEXT,
                head_revision TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (run_id)
                    REFERENCES runs(run_id)
                    ON DELETE CASCADE,
                CHECK (
                    (file_path IS NULL AND start_line IS NULL
                        AND end_line IS NULL AND line_side IS NULL)
                    OR
                    (file_path IS NOT NULL AND start_line IS NULL
                        AND end_line IS NULL AND line_side IS NULL)
                    OR
                    (file_path IS NOT NULL AND start_line IS NOT NULL
                        AND end_line IS NOT NULL AND start_line > 0
                        AND end_line >= start_line
                        AND line_side IN ('old', 'new'))
                ),
                UNIQUE (run_id, fingerprint)
            )
            """)
        connection.execute("""
            INSERT INTO review_findings_v35 (
                id, run_id, severity, category, file_path,
                start_line, end_line, line_side, title, description,
                suggestion, status, fingerprint, review_diff_hash,
                review_scope, base_revision, head_revision,
                created_at, updated_at
            )
            SELECT
                id, run_id, severity, category, file_path,
                start_line, end_line,
                CASE WHEN start_line IS NULL THEN NULL ELSE 'new' END,
                title, description, suggestion, status, fingerprint,
                NULL, NULL, NULL, NULL, created_at, updated_at
            FROM review_findings
            """)
        connection.execute("DROP TABLE review_findings")
        connection.execute(
            "ALTER TABLE review_findings_v35 RENAME TO review_findings"
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
        network_access: bool = False,
        network_provider: str = "disabled",
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
                        network_access,
                        network_provider,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        thread_id,
                        task_kind.value,
                        RunStatus.PENDING.value,
                        int(network_access),
                        network_provider,
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
        network_access: bool = False,
        network_provider: str = "disabled",
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
                        network_access,
                        network_provider,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        thread_id,
                        task_kind.value,
                        RunStatus.PENDING.value,
                        int(network_access),
                        network_provider,
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

    def update_run_network_metrics(
        self,
        *,
        run_id: str,
        request_count: int,
        result_count: int,
        bytes_received: int,
        cache_hit_count: int,
        limit_reached: bool,
        limit_reason: str | None,
    ) -> RunSnapshot:
        values = (
            request_count,
            result_count,
            bytes_received,
            cache_hit_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("Run 网络指标不能小于 0")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET network_request_count = ?,
                    network_result_count = ?,
                    network_bytes_received = ?,
                    network_cache_hit_count = ?,
                    network_limit_reached = ?,
                    network_limit_reason = ?,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    request_count,
                    result_count,
                    bytes_received,
                    cache_hit_count,
                    int(limit_reached),
                    limit_reason,
                    now,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Run 不存在：{run_id}")
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._run_snapshot(row)

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
                        start_line, end_line, line_side, title, description,
                        suggestion, status, fingerprint, review_diff_hash,
                        review_scope, base_revision, head_revision,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        finding.severity.value,
                        finding.category.value,
                        finding.file_path,
                        finding.start_line,
                        finding.end_line,
                        (
                            finding.line_side.value
                            if finding.line_side is not None
                            else None
                        ),
                        finding.title,
                        finding.description,
                        finding.suggestion,
                        ReviewFindingStatus.OPEN.value,
                        finding.fingerprint,
                        finding.review_diff_hash,
                        (
                            finding.review_scope.value
                            if finding.review_scope is not None
                            else None
                        ),
                        finding.base_revision,
                        finding.head_revision,
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

    def save_review_diff_snapshot(
        self,
        *,
        run_id: str,
        review_diff: ReviewDiff,
        summary: str,
    ) -> ReviewDiffSnapshot:
        """首次保存 Reviewer 实际看到的受控 Diff；同一 Run 不允许覆盖。"""

        now = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(
            review_diff_to_dict(review_diff),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO review_diff_snapshots (
                        run_id, status, payload_json, summary,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        ReviewSnapshotStatus.COLLECTED.value,
                        payload_json,
                        summary,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM review_diff_snapshots WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("当前 Run 已经存在代码审查快照") from exc
        return self._review_diff_snapshot(row)

    def get_review_diff_snapshot(
        self,
        run_id: str,
    ) -> ReviewDiffSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM review_diff_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._review_diff_snapshot(row)

    def create_github_review_publication(
        self,
        *,
        publication_id: str,
        run_id: str,
        repository: str,
        pr_number: int,
        base_sha: str,
        head_sha: str,
        event: GitHubReviewEvent,
        selected_finding_ids: tuple[str, ...],
        finding_state_hash: str,
        payload: dict[str, Any],
        preview: dict[str, Any],
        payload_hash: str,
        prepared_at: datetime,
        expires_at: datetime,
    ) -> GitHubReviewPublicationSnapshot:
        with self._connection() as connection:
            if connection.execute(
                "SELECT run_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone() is None:
                raise KeyError(f"Run 不存在：{run_id}")
            connection.execute(
                """
                INSERT INTO github_review_publications (
                    id, run_id, repository, pr_number, base_sha, head_sha,
                    event, selected_finding_ids_json, finding_state_hash,
                    payload_json, preview_json, payload_hash, status,
                    prepared_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication_id,
                    run_id,
                    repository,
                    pr_number,
                    base_sha,
                    head_sha,
                    event.value,
                    json.dumps(list(selected_finding_ids), ensure_ascii=False),
                    finding_state_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(preview, ensure_ascii=False, sort_keys=True),
                    payload_hash,
                    GitHubPublicationStatus.PREPARED.value,
                    prepared_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM github_review_publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
            return self._github_review_publication(row)

    def get_github_review_publication(
        self,
        publication_id: str,
    ) -> GitHubReviewPublicationSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM github_review_publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
        if row is None:
            return None
        return self._github_review_publication(row)

    def list_github_review_publications(
        self,
        run_id: str,
    ) -> list[GitHubReviewPublicationSnapshot]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM github_review_publications
                WHERE run_id = ?
                ORDER BY prepared_at DESC, id DESC
                """,
                (run_id,),
            ).fetchall()
        return [self._github_review_publication(row) for row in rows]

    def find_published_github_review_payload(
        self,
        payload_hash: str,
    ) -> GitHubReviewPublicationSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM github_review_publications
                WHERE payload_hash = ? AND status = 'published'
                LIMIT 1
                """,
                (payload_hash,),
            ).fetchone()
        if row is None:
            return None
        return self._github_review_publication(row)

    def claim_github_review_publication(
        self,
        publication_id: str,
    ) -> GitHubReviewPublicationSnapshot:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM github_review_publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
            if row is None:
                raise KeyError("publication not found")
            current = GitHubPublicationStatus(str(row["status"]))
            if current is GitHubPublicationStatus.PUBLISHED:
                raise ValueError("publication_already_published")
            if current is GitHubPublicationStatus.PUBLISHING:
                raise ValueError("publication_in_progress")
            if current is GitHubPublicationStatus.UNKNOWN:
                raise ValueError("publication_result_unknown")
            conflict = connection.execute(
                """
                SELECT status FROM github_review_publications
                WHERE payload_hash = ? AND id != ?
                  AND status IN ('publishing', 'published', 'unknown')
                LIMIT 1
                """,
                (row["payload_hash"], publication_id),
            ).fetchone()
            if conflict is not None:
                conflict_status = GitHubPublicationStatus(
                    str(conflict["status"])
                )
                if conflict_status is GitHubPublicationStatus.PUBLISHED:
                    raise ValueError("publication_already_published")
                if conflict_status is GitHubPublicationStatus.UNKNOWN:
                    raise ValueError("publication_result_unknown")
                raise ValueError("publication_in_progress")
            cursor = connection.execute(
                """
                UPDATE github_review_publications
                SET status = 'publishing', error_code = NULL,
                    error_message = NULL
                WHERE id = ? AND status IN ('prepared', 'failed')
                """,
                (publication_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("publication_in_progress")
            claimed = connection.execute(
                "SELECT * FROM github_review_publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
            return self._github_review_publication(claimed)

    def finish_github_review_publication(
        self,
        *,
        publication_id: str,
        status: GitHubPublicationStatus,
        github_review_id: str | None,
        github_review_url: str | None,
        github_user: str | None,
        published_at: datetime | None,
        error_code: str | None,
        error_message: str | None,
    ) -> GitHubReviewPublicationSnapshot:
        if status not in {
            GitHubPublicationStatus.PUBLISHED,
            GitHubPublicationStatus.FAILED,
            GitHubPublicationStatus.UNKNOWN,
        }:
            raise ValueError("publication final status is invalid")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE github_review_publications
                SET status = ?, github_review_id = ?, github_review_url = ?,
                    github_user = ?, published_at = ?, error_code = ?,
                    error_message = ?
                WHERE id = ? AND status = 'publishing'
                """,
                (
                    status.value,
                    github_review_id,
                    github_review_url,
                    github_user,
                    published_at.isoformat() if published_at else None,
                    error_code,
                    error_message,
                    publication_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("publication is not publishing")
            row = connection.execute(
                "SELECT * FROM github_review_publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
            return self._github_review_publication(row)

    def update_review_diff_snapshot(
        self,
        *,
        run_id: str,
        status: ReviewSnapshotStatus,
        summary: str,
    ) -> ReviewDiffSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE review_diff_snapshots
                SET status = ?, summary = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status.value, summary, now, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Review Diff 快照不存在：{run_id}")
            row = connection.execute(
                "SELECT * FROM review_diff_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._review_diff_snapshot(row)

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
            network_access=bool(row["network_access"]),
            network_provider=str(row["network_provider"]),
            network_request_count=int(row["network_request_count"]),
            network_result_count=int(row["network_result_count"]),
            network_bytes_received=int(row["network_bytes_received"]),
            network_cache_hit_count=int(row["network_cache_hit_count"]),
            network_limit_reached=bool(row["network_limit_reached"]),
            network_limit_reason=row["network_limit_reason"],
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
            line_side=(
                ReviewLineSide(row["line_side"])
                if row["line_side"] is not None
                else None
            ),
            title=str(row["title"]),
            description=str(row["description"]),
            suggestion=row["suggestion"],
            status=ReviewFindingStatus(row["status"]),
            fingerprint=str(row["fingerprint"]),
            review_diff_hash=row["review_diff_hash"],
            review_scope=(
                ReviewScope(row["review_scope"])
                if row["review_scope"] is not None
                else None
            ),
            base_revision=row["base_revision"],
            head_revision=row["head_revision"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _review_diff_snapshot(
        row: sqlite3.Row | None,
    ) -> ReviewDiffSnapshot:
        if row is None:
            raise RuntimeError("Review Diff 快照读取失败")
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise RuntimeError("Review Diff 快照格式无效")
        return ReviewDiffSnapshot(
            run_id=str(row["run_id"]),
            status=ReviewSnapshotStatus(row["status"]),
            diff=review_diff_from_dict(payload),
            summary=str(row["summary"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _github_review_publication(
        row: sqlite3.Row | None,
    ) -> GitHubReviewPublicationSnapshot:
        if row is None:
            raise RuntimeError("GitHub Review publication 读取失败")
        return GitHubReviewPublicationSnapshot(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            repository=str(row["repository"]),
            pr_number=int(row["pr_number"]),
            base_sha=str(row["base_sha"]),
            head_sha=str(row["head_sha"]),
            event=GitHubReviewEvent(str(row["event"])),
            selected_finding_ids=tuple(
                str(value)
                for value in json.loads(str(row["selected_finding_ids_json"]))
            ),
            finding_state_hash=str(row["finding_state_hash"]),
            payload=dict(json.loads(str(row["payload_json"]))),
            preview=dict(json.loads(str(row["preview_json"]))),
            payload_hash=str(row["payload_hash"]),
            status=GitHubPublicationStatus(str(row["status"])),
            github_review_id=row["github_review_id"],
            github_review_url=row["github_review_url"],
            github_user=row["github_user"],
            prepared_at=datetime.fromisoformat(str(row["prepared_at"])),
            expires_at=datetime.fromisoformat(str(row["expires_at"])),
            published_at=(
                datetime.fromisoformat(str(row["published_at"]))
                if row["published_at"] is not None
                else None
            ),
            error_code=row["error_code"],
            error_message=row["error_message"],
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
