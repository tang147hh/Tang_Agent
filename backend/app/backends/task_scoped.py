from __future__ import annotations

from collections.abc import Sequence

from app.backends.command_runner import CommandResult
from app.backends.local_shell import (
    FileEntry,
    LocalShellBackend,
    WorkspaceGlobResult,
    WorkspaceSearchResult,
)
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
        *,
        network_access: bool = False,
    ) -> None:
        self._backend = backend
        self.policy = policy
        self.network_access = network_access

    @classmethod
    def for_task(
        cls,
        task_kind: TaskKind,
        backend: LocalShellBackend | None = None,
        *,
        network_access: bool = False,
    ) -> "TaskScopedBackend":
        return cls(
            backend or LocalShellBackend(),
            policy_for(task_kind),
            network_access=network_access,
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

    def glob_paths(
        self,
        virtual_path: str = "/projects",
        *,
        pattern: str,
        max_results: int = 100,
        include_directories: bool = False,
    ) -> WorkspaceGlobResult:
        return self._backend.glob_paths(
            virtual_path,
            pattern=pattern,
            max_results=max_results,
            include_directories=include_directories,
        )

    def search_text(
        self,
        virtual_path: str = "/projects",
        *,
        query: str,
        file_pattern: str = "**/*",
        max_results: int = 100,
        case_sensitive: bool = True,
    ) -> WorkspaceSearchResult:
        return self._backend.search_text(
            virtual_path,
            query=query,
            file_pattern=file_pattern,
            max_results=max_results,
            case_sensitive=case_sensitive,
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
        self._require_controlled_network(argv)

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

    def _require_controlled_network(self, argv: Sequence[str]) -> None:
        """网络关闭时阻止命令工具代替受控 web_search。"""

        if self.network_access or not argv:
            return
        command = str(argv[0]).lower()
        arguments = {str(argument).lower() for argument in argv[1:]}
        if any(
            argument.startswith(("http://", "https://", "ftp://"))
            for argument in arguments
        ):
            raise TaskPermissionError(
                "当前 Run 未允许联网，命令参数不能包含网络 URL"
            )
        if command in {"curl", "wget"}:
            raise TaskPermissionError(
                "当前 Run 未允许联网，禁止执行网络客户端"
            )
        if command == "git" and arguments.intersection(
            {"clone", "fetch", "pull", "ls-remote", "archive", "submodule"}
        ):
            raise TaskPermissionError(
                "当前 Run 未允许联网，禁止执行 Git 网络读取命令"
            )
