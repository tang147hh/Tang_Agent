from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.backends.command_runner import (
    CommandPolicyError,
    CommandResult,
    CommandRunner,
)
from app.backends.workspace import Workspace, WorkspacePathError


GITHUB_OWNER_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
)
GITHUB_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PROTECTED_BRANCHES = frozenset({"main", "master"})
SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        "id_ed25519",
        "id_rsa",
    }
)
SENSITIVE_FILE_SUFFIXES = frozenset(
    {
        ".key",
        ".p12",
        ".pem",
        ".pfx",
    }
)


class RepositoryError(RuntimeError):
    """仓库操作未能完成。"""


class RepositoryValidationError(RepositoryError):
    """仓库输入不符合安全规则。"""


class RepositoryNotFoundError(RepositoryError):
    """仓库或分支不存在。"""


class RepositoryConflictError(RepositoryError):
    """仓库状态与请求的 Git 操作冲突。"""


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    name: str
    path: str
    remote_url: str
    current_branch: str
    branches: tuple[str, ...]
    dirty: bool


@dataclass(frozen=True, slots=True)
class RepositoryCommitResult:
    repository: RepositorySnapshot
    sha: str
    subject: str


@dataclass(frozen=True, slots=True)
class RepositoryPushResult:
    repository: RepositorySnapshot
    branch: str


def _sanitize_remote_url(value: str) -> str:
    """移除远程地址中可能存在的用户名、密码和查询参数。"""

    normalized = value.strip()

    if not normalized:
        return ""

    if (
        normalized.startswith(("/", "~"))
        or re.match(r"^[A-Za-z]:[\\/]", normalized)
    ):
        return ""

    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return ""

    if parsed.scheme and parsed.netloc:
        hostname = parsed.hostname

        if hostname is None:
            return ""

        safe_host = f"[{hostname}]" if ":" in hostname else hostname

        try:
            if parsed.port is not None:
                safe_host = f"{safe_host}:{parsed.port}"
        except ValueError:
            return ""

        return urlunsplit(
            (
                parsed.scheme,
                safe_host,
                parsed.path,
                "",
                "",
            )
        )

    # 兼容 Git 的 scp 风格远程地址，同时去掉 user@ 前缀。
    if "@" in normalized:
        return normalized.rsplit("@", maxsplit=1)[1]

    return normalized


def _parse_github_clone_url(value: str) -> tuple[str, str]:
    normalized = value.strip()

    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise RepositoryValidationError("GitHub 地址格式不合法") from exc

    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RepositoryValidationError(
            "只支持不含凭据的 GitHub HTTPS 仓库地址"
        )

    parts = parsed.path.strip("/").split("/")

    if len(parts) != 2:
        raise RepositoryValidationError(
            "GitHub 地址必须是 https://github.com/{owner}/{repo}"
        )

    owner, repository = parts

    if repository.endswith(".git"):
        repository = repository[:-4]

    if not GITHUB_OWNER_PATTERN.fullmatch(owner):
        raise RepositoryValidationError("GitHub owner 不合法")

    if (
        not GITHUB_REPOSITORY_PATTERN.fullmatch(repository)
        or repository in {".", ".."}
    ):
        raise RepositoryValidationError("GitHub 仓库名不合法")

    return repository, f"https://github.com/{owner}/{repository}"


class RepositoryCatalog:
    """发现并管理 Agent 工作区中的本地 Git 仓库。"""

    def __init__(
        self,
        workspace: Workspace,
        runner: CommandRunner | None = None,
    ) -> None:
        self.workspace = workspace
        self.runner = runner or CommandRunner(workspace)

    def discover(self) -> tuple[RepositorySnapshot, ...]:
        projects_root = self.workspace.resolve("/projects")
        discovered: list[RepositorySnapshot] = []

        if not projects_root.exists():
            return ()

        for candidate in sorted(
            projects_root.iterdir(),
            key=lambda item: item.name.lower(),
        ):
            if not candidate.is_dir() or not candidate.joinpath(".git").exists():
                continue

            try:
                virtual_path = self.workspace.to_virtual(candidate)
            except WorkspacePathError:
                continue

            discovered.append(
                self._snapshot(candidate.name, virtual_path)
            )

        return tuple(discovered)

    def clone(self, url: str) -> RepositorySnapshot:
        name, canonical_url = _parse_github_clone_url(url)
        virtual_path = f"/projects/{name}"
        target = self.workspace.resolve(virtual_path)

        if target.exists():
            raise RepositoryConflictError(
                f"目标目录已存在：{virtual_path}"
            )

        self._run(
            [
                "git",
                "clone",
                "--origin",
                "origin",
                canonical_url,
                virtual_path,
            ],
            cwd="/projects",
            error=RepositoryConflictError("GitHub 仓库克隆失败"),
        )

        if not target.joinpath(".git").exists():
            raise RepositoryConflictError("Git 克隆完成后未找到有效仓库")

        return self._snapshot(name, virtual_path)

    def fetch(self, name: str) -> RepositorySnapshot:
        virtual_path = self._repository_path(name)
        self._run(
            ["git", "fetch", "--prune", "origin"],
            cwd=virtual_path,
            error=RepositoryConflictError("无法从 origin 获取更新"),
        )
        return self._snapshot(name, virtual_path)

    def commit(
        self,
        name: str,
        message: str,
    ) -> RepositoryCommitResult:
        virtual_path = self._repository_path(name)
        normalized_message = self._validated_commit_message(message)
        snapshot = self._snapshot(name, virtual_path)

        if not snapshot.dirty:
            raise RepositoryConflictError("工作区没有可提交的修改")

        changed_paths = self._changed_paths(virtual_path)
        sensitive_paths = [
            path for path in changed_paths if self._is_sensitive_path(path)
        ]

        if sensitive_paths:
            raise RepositoryValidationError(
                "检测到敏感文件，拒绝提交："
                + ", ".join(sorted(sensitive_paths))
            )

        self._run(
            ["git", "add", "--all"],
            cwd=virtual_path,
            error=RepositoryConflictError("无法暂存仓库修改"),
        )
        self._run(
            ["git", "commit", "-m", normalized_message],
            cwd=virtual_path,
            error=RepositoryConflictError(
                "Git commit 失败，请检查提交钩子和用户配置"
            ),
        )
        sha = self._run(
            ["git", "rev-parse", "HEAD"],
            cwd=virtual_path,
            error=RepositoryConflictError("无法读取新提交 ID"),
        ).stdout.strip()
        subject = self._run(
            ["git", "log", "-1", "--format=%s"],
            cwd=virtual_path,
            error=RepositoryConflictError("无法读取新提交信息"),
        ).stdout.strip()

        return RepositoryCommitResult(
            repository=self._snapshot(name, virtual_path),
            sha=sha,
            subject=subject,
        )

    def push(self, name: str) -> RepositoryPushResult:
        virtual_path = self._repository_path(name)
        snapshot = self._snapshot(name, virtual_path)
        branch = snapshot.current_branch

        if branch == "DETACHED":
            raise RepositoryConflictError("detached HEAD 不能执行 push")

        if branch in PROTECTED_BRANCHES:
            raise RepositoryConflictError(
                f"禁止直接推送受保护分支：{branch}"
            )

        if not snapshot.remote_url:
            raise RepositoryConflictError("仓库没有可用的 origin")

        self._run(
            ["git", "push", "--set-upstream", "origin", branch],
            cwd=virtual_path,
            error=RepositoryConflictError(
                "Git push 失败，请检查网络、认证和远程分支状态"
            ),
        )

        return RepositoryPushResult(
            repository=self._snapshot(name, virtual_path),
            branch=branch,
        )

    def prepare_pull_request(
        self,
        name: str,
        base: str,
    ) -> RepositorySnapshot:
        virtual_path = self._repository_path(name)
        normalized_base = self._validated_branch(base, virtual_path)
        snapshot = self._snapshot(name, virtual_path)

        if snapshot.current_branch == "DETACHED":
            raise RepositoryConflictError(
                "detached HEAD 不能创建 Pull Request"
            )

        if snapshot.current_branch == normalized_base:
            raise RepositoryConflictError("PR 的 head 与 base 分支不能相同")

        if snapshot.dirty:
            raise RepositoryConflictError(
                "创建 Pull Request 前必须先提交全部修改"
            )

        upstream = self._run_optional(
            [
                "git",
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ],
            cwd=virtual_path,
        )
        expected_upstream = f"origin/{snapshot.current_branch}"

        if upstream is None or upstream.stdout.strip() != expected_upstream:
            raise RepositoryConflictError(
                "当前分支尚未推送到 origin，请先执行 push"
            )

        return snapshot

    def create_branch(
        self,
        name: str,
        branch: str,
    ) -> RepositorySnapshot:
        virtual_path = self._repository_path(name)
        normalized_branch = self._validated_branch(branch, virtual_path)
        snapshot = self._snapshot(name, virtual_path)

        if normalized_branch in snapshot.branches:
            raise RepositoryConflictError(
                f"分支已存在：{normalized_branch}"
            )

        self._run(
            ["git", "switch", "-c", normalized_branch],
            cwd=virtual_path,
            error=RepositoryConflictError("无法创建并切换到新分支"),
        )
        return self._snapshot(name, virtual_path)

    def checkout(
        self,
        name: str,
        branch: str,
    ) -> RepositorySnapshot:
        virtual_path = self._repository_path(name)
        normalized_branch = self._validated_branch(branch, virtual_path)
        snapshot = self._snapshot(name, virtual_path)

        if normalized_branch not in snapshot.branches:
            raise RepositoryNotFoundError(
                f"分支不存在：{normalized_branch}"
            )

        self._run(
            ["git", "switch", normalized_branch],
            cwd=virtual_path,
            error=RepositoryConflictError(
                "无法切换分支，请先处理工作区中的冲突"
            ),
        )
        return self._snapshot(name, virtual_path)

    def _repository_path(self, name: str) -> str:
        normalized_name = name.strip()

        if (
            not normalized_name
            or normalized_name in {".", ".."}
            or "/" in normalized_name
            or "\\" in normalized_name
            or "\x00" in normalized_name
            or len(normalized_name) > 255
        ):
            raise RepositoryValidationError("仓库名不合法")

        virtual_path = f"/projects/{normalized_name}"

        try:
            repository_path = self.workspace.resolve(virtual_path)
        except WorkspacePathError as exc:
            raise RepositoryValidationError("仓库路径不合法") from exc

        if (
            not repository_path.is_dir()
            or not repository_path.joinpath(".git").exists()
        ):
            raise RepositoryNotFoundError(
                f"仓库不存在：{normalized_name}"
            )

        return virtual_path

    def _validated_branch(
        self,
        branch: str,
        virtual_path: str,
    ) -> str:
        normalized = branch.strip()

        if not normalized or len(normalized) > 255 or "\x00" in normalized:
            raise RepositoryValidationError("分支名不合法")

        result = self._run(
            ["git", "check-ref-format", "--branch", normalized],
            cwd=virtual_path,
            error=RepositoryValidationError("分支名不符合 Git 规则"),
        )

        if result.stdout.strip() != normalized:
            raise RepositoryValidationError("分支名不符合 Git 规则")

        return normalized

    def _validated_commit_message(self, message: str) -> str:
        normalized = message.strip()

        if (
            not normalized
            or len(normalized) > 200
            or any(ord(character) < 32 for character in normalized)
        ):
            raise RepositoryValidationError(
                "提交信息必须是 1 到 200 个字符的单行文本"
            )

        return normalized

    def _changed_paths(self, virtual_path: str) -> tuple[str, ...]:
        output = self._run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            cwd=virtual_path,
            error=RepositoryConflictError("无法读取待提交文件列表"),
        ).stdout
        paths: list[str] = []

        for item in output.split("\0"):
            if not item:
                continue

            path = item[3:] if len(item) > 3 and item[2] == " " else item

            if path:
                paths.append(path)

        return tuple(paths)

    def _is_sensitive_path(self, value: str) -> bool:
        normalized = value.replace("\\", "/").strip("/")
        parts = tuple(part.lower() for part in normalized.split("/"))

        if not parts:
            return False

        filename = parts[-1]

        if ".secrets" in parts:
            return True

        if filename in SENSITIVE_FILE_NAMES:
            return True

        if filename.startswith(".env.") and filename != ".env.example":
            return True

        return any(
            filename.endswith(suffix)
            for suffix in SENSITIVE_FILE_SUFFIXES
        )

    def _snapshot(
        self,
        name: str,
        virtual_path: str,
    ) -> RepositorySnapshot:
        current_branch = self._run(
            ["git", "branch", "--show-current"],
            cwd=virtual_path,
            error=RepositoryConflictError("无法读取当前 Git 分支"),
        ).stdout.strip()

        branches_output = self._run(
            [
                "git",
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads",
            ],
            cwd=virtual_path,
            error=RepositoryConflictError("无法读取本地 Git 分支"),
        ).stdout
        branches = sorted(
            {
                branch.strip()
                for branch in branches_output.splitlines()
                if branch.strip()
            }
        )

        if current_branch and current_branch not in branches:
            branches.append(current_branch)
            branches.sort()

        status_output = self._run(
            ["git", "status", "--porcelain"],
            cwd=virtual_path,
            error=RepositoryConflictError("无法读取 Git 工作区状态"),
        ).stdout

        remote_result = self._run_optional(
            ["git", "remote", "get-url", "origin"],
            cwd=virtual_path,
        )
        remote_url = (
            _sanitize_remote_url(remote_result.stdout)
            if remote_result is not None
            else ""
        )

        return RepositorySnapshot(
            name=name,
            path=virtual_path,
            remote_url=remote_url,
            current_branch=current_branch or "DETACHED",
            branches=tuple(branches),
            dirty=bool(status_output.strip()),
        )

    def _run(
        self,
        argv: list[str],
        *,
        cwd: str,
        error: RepositoryError,
    ) -> CommandResult:
        try:
            result = self.runner.run(argv, cwd=cwd)
        except (CommandPolicyError, OSError) as exc:
            raise error from exc

        if result.exit_code != 0 or result.timed_out:
            raise error

        return result

    def _run_optional(
        self,
        argv: list[str],
        *,
        cwd: str,
    ) -> CommandResult | None:
        try:
            result = self.runner.run(argv, cwd=cwd)
        except (CommandPolicyError, OSError):
            return None

        if result.exit_code != 0 or result.timed_out:
            return None

        return result
