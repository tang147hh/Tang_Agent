from __future__ import annotations

from collections.abc import Sequence

from app.backends.command_runner import CommandResult
from app.backends.local_shell import FileEntry, LocalShellBackend
from app.core.task_intent import (
    TaskKind,
    TaskPolicy,
    policy_for,
)


class TaskPermissionError(PermissionError):
    """当前任务类型无权使用某项能力。"""


class TaskScopedBackend:
    """根据当前任务策略限制 LocalShellBackend。"""

    def __init__(
        self,
        backend: LocalShellBackend,
        policy: TaskPolicy,
    ) -> None:
        self._backend = backend
        self.policy = policy

    @classmethod
    def for_task(
        cls,
        task_kind: TaskKind,
        backend: LocalShellBackend | None = None,
    ) -> "TaskScopedBackend":
        return cls(
            backend or LocalShellBackend(),
            policy_for(task_kind),
        )

    def list_dir(
        self,
        virtual_path: str = "/",
    ) -> list[FileEntry]:
        return self._backend.list_dir(virtual_path)

    def read_text(
        self,
        virtual_path: str,
        *,
        offset: int = 0,
        limit: int = 2_000,
    ) -> str:
        return self._backend.read_text(
            virtual_path,
            offset=offset,
            limit=limit,
        )

    def write_text(
        self,
        virtual_path: str,
        content: str,
    ) -> str:
        self._require_file_write()

        return self._backend.write_text(
            virtual_path,
            content,
        )

    def edit_text(
        self,
        virtual_path: str,
        old_text: str,
        new_text: str,
        *,
        replace_all: bool = False,
    ) -> int:
        self._require_file_write()

        return self._backend.edit_text(
            virtual_path,
            old_text,
            new_text,
            replace_all=replace_all,
        )

    def run_command(
        self,
        argv: Sequence[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        self._require_command_execution()

        return self._backend.run_command(
            argv,
            cwd=cwd,
            timeout=timeout,
        )

    def _require_file_write(self) -> None:
        if not self.policy.allow_file_write:
            raise TaskPermissionError(
                f"{self.policy.kind.value} 任务禁止修改文件"
            )

    def _require_command_execution(self) -> None:
        if not self.policy.allow_command_execution:
            raise TaskPermissionError(
                f"{self.policy.kind.value} 任务禁止执行命令"
            )