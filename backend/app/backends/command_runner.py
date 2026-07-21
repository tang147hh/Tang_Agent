from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from app.backends.workspace import Workspace, WorkspacePathError

ALLOWED_COMMANDS = {
    "git",
    "python",
    "pytest",
    "ruff",
}

SAFE_ENVIRONMENT_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "VIRTUAL_ENV",
}

MAX_TIMEOUT_SECONDS = 600
MAX_OUTPUT_CHARS = 50_000

SENSITIVE_ARGUMENT_MARKERS = (
    "token=",
    "password=",
    "authorization:",
    "api_key=",
    "apikey=",
    "ghp_",
    "github_pat_",
)


class CommandPolicyError(ValueError):
    """命令违反本地执行策略。"""


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


def _truncate(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_OUTPUT_CHARS:
        return value, False

    return (
        value[:MAX_OUTPUT_CHARS]
        + "\n... output truncated ...",
        True,
    )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


class CommandRunner:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        """在工作区内执行一条受控命令。"""

        normalized_argv = self._validate_argv(argv)

        if timeout <= 0:
            raise CommandPolicyError("timeout 必须大于 0")

        if timeout > MAX_TIMEOUT_SECONDS:
            raise CommandPolicyError(
                f"timeout 不能超过 {MAX_TIMEOUT_SECONDS} 秒"
            )

        try:
            cwd_path = self.workspace.resolve(cwd)
        except WorkspacePathError as exc:
            raise CommandPolicyError(
                f"工作目录不符合执行策略：{cwd}"
            ) from exc

        if not cwd_path.exists():
            raise CommandPolicyError(f"工作目录不存在：{cwd}")

        if not cwd_path.is_dir():
            raise CommandPolicyError(f"工作目录不是目录：{cwd}")

        executable = shutil.which(normalized_argv[0])

        if executable is None:
            raise CommandPolicyError(
                f"命令不可用：{normalized_argv[0]}"
            )

        prepared_argv = [
            executable,
            *[
                self._prepare_argument(argument)
                for argument in normalized_argv[1:]
            ],
        ]

        try:
            completed = subprocess.run(
                prepared_argv,
                cwd=cwd_path,
                env=self._safe_environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout, stdout_truncated = _truncate(
                _decode_timeout_output(exc.stdout)
            )
            stderr, stderr_truncated = _truncate(
                _decode_timeout_output(exc.stderr)
            )

            return CommandResult(
                argv=tuple(normalized_argv),
                cwd=cwd,
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                truncated=stdout_truncated or stderr_truncated,
            )

        stdout, stdout_truncated = _truncate(completed.stdout)
        stderr, stderr_truncated = _truncate(completed.stderr)

        return CommandResult(
            argv=tuple(normalized_argv),
            cwd=cwd,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            truncated=stdout_truncated or stderr_truncated,
        )

    def _validate_argv(
        self,
        argv: Sequence[str],
    ) -> list[str]:
        if isinstance(argv, str):
            raise CommandPolicyError(
                "命令必须使用参数数组，不能传入 shell 字符串"
            )

        normalized = [str(argument) for argument in argv]

        if not normalized:
            raise CommandPolicyError("命令不能为空")

        command = normalized[0]

        if command not in ALLOWED_COMMANDS:
            raise CommandPolicyError(
                f"命令不在白名单中：{command}"
            )

        for argument in normalized:
            lowered = argument.lower()

            if "\x00" in argument:
                raise CommandPolicyError("命令参数不能包含空字符")

            if "../" in argument or argument == "..":
                raise CommandPolicyError(
                    f"命令参数禁止路径穿越：{argument}"
                )

            if ".secrets" in argument:
                raise CommandPolicyError(
                    "命令参数禁止访问 .secrets"
                )

            if any(
                marker in lowered
                for marker in SENSITIVE_ARGUMENT_MARKERS
            ):
                raise CommandPolicyError(
                    "命令参数疑似包含凭据或密钥"
                )

        self._validate_command_specific_policy(normalized)

        return normalized

    def _validate_command_specific_policy(
        self,
        argv: list[str],
    ) -> None:
        command = argv[0]
        arguments = [argument.lower() for argument in argv[1:]]

        if command == "python" and "-c" in arguments:
            raise CommandPolicyError(
                "禁止使用 python -c 执行任意内联代码"
            )

        if command != "git":
            return

        if "clean" in arguments:
            raise CommandPolicyError("禁止执行 git clean")

        if "reset" in arguments and "--hard" in arguments:
            raise CommandPolicyError(
                "禁止执行 git reset --hard"
            )

        if "push" in arguments and any(
            argument in {"--force", "--force-with-lease", "-f"}
            for argument in arguments
        ):
            raise CommandPolicyError("禁止强制推送")

        if "config" in arguments and any(
            argument in {"--global", "--system"}
            for argument in arguments
        ):
            raise CommandPolicyError(
                "禁止修改全局或系统 Git 配置"
            )

    def _prepare_argument(self, argument: str) -> str:
        """把虚拟绝对路径转换成真实工作区路径。"""

        if not argument.startswith("/"):
            return argument

        try:
            return str(self.workspace.resolve(argument))
        except WorkspacePathError as exc:
            raise CommandPolicyError(
                f"命令参数路径不符合执行策略：{argument}"
            ) from exc

    def _safe_environment(self) -> dict[str, str]:
        source = os.environ

        environment = {
            key: source[key]
            for key in SAFE_ENVIRONMENT_KEYS
            if key in source
        }

        environment["PYTHONUTF8"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["GIT_TERMINAL_PROMPT"] = "0"

        return environment