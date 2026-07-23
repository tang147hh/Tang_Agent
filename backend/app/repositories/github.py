from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.backends.command_runner import (
    ALLOWED_COMMANDS,
    CommandPolicyError,
    CommandResult,
    CommandRunner,
)
from app.backends.workspace import Workspace


GITHUB_SLUG_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$"
)


class GitHubError(RuntimeError):
    """GitHub Pull Request 操作未能完成。"""


class GitHubValidationError(GitHubError):
    """GitHub 请求参数不合法。"""


class GitHubConfigurationError(GitHubError):
    """GitHub CLI 不可用或尚未认证。"""


class GitHubConflictError(GitHubError):
    """GitHub 拒绝了 Pull Request 操作。"""


@dataclass(frozen=True, slots=True)
class PullRequestResult:
    number: int
    url: str
    title: str
    base: str
    head: str


def github_repository_slug(remote_url: str) -> str:
    normalized = remote_url.strip()

    if not normalized:
        raise GitHubValidationError("origin 不是有效的 GitHub 仓库地址")

    if normalized.lower().startswith("github.com:"):
        path = normalized.split(":", maxsplit=1)[1].strip("/")
    else:
        try:
            parsed = urlsplit(normalized)
        except ValueError as exc:
            raise GitHubValidationError(
                "origin 不是有效的 GitHub 仓库地址"
            ) from exc

        if (
            not parsed.scheme
            or parsed.hostname is None
            or parsed.hostname.lower() != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise GitHubValidationError(
                "origin 不是有效的 GitHub 仓库地址"
            )

        path = parsed.path.strip("/")

    if path.endswith(".git"):
        path = path[:-4]

    if not GITHUB_SLUG_PATTERN.fullmatch(path):
        raise GitHubValidationError("origin 中的 GitHub 仓库名称不合法")

    return path


class GitHubClient:
    """通过严格受限的 GitHub CLI 创建 Pull Request。"""

    def __init__(
        self,
        workspace: Workspace,
        runner: CommandRunner | None = None,
    ) -> None:
        self.workspace = workspace
        self.runner = runner or CommandRunner(
            workspace,
            allowed_commands=ALLOWED_COMMANDS | {"gh"},
        )

    def create_pull_request(
        self,
        *,
        repository_name: str,
        remote_url: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PullRequestResult:
        slug = github_repository_slug(remote_url)
        normalized_title = self._validated_title(title)
        normalized_body = self._validated_body(body)
        virtual_path = f"/projects/{repository_name}"

        authentication = self._run_gh(
            [
                "gh",
                "auth",
                "status",
                "--hostname",
                "github.com",
            ],
            cwd=virtual_path,
        )

        if authentication.exit_code != 0 or authentication.timed_out:
            raise GitHubConfigurationError(
                "GitHub CLI 尚未登录，请先在后端主机完成 gh auth login"
            )

        created = self._run_gh(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                slug,
                "--base",
                base,
                "--head",
                head,
                "--title",
                normalized_title,
                "--body",
                normalized_body,
            ],
            cwd=virtual_path,
        )

        if created.exit_code != 0 or created.timed_out:
            raise GitHubConflictError(
                "GitHub 未能创建 Pull Request，请检查分支和现有 PR"
            )

        url = next(
            (
                line.strip()
                for line in reversed(created.stdout.splitlines())
                if line.strip().startswith("https://github.com/")
            ),
            "",
        )
        number = self._pull_request_number(url, slug)

        return PullRequestResult(
            number=number,
            url=url,
            title=normalized_title,
            base=base,
            head=head,
        )

    def _run_gh(
        self,
        argv: list[str],
        *,
        cwd: str,
    ) -> CommandResult:
        try:
            return self.runner.run(argv, cwd=cwd)
        except CommandPolicyError as exc:
            if "命令不可用：gh" in str(exc):
                raise GitHubConfigurationError(
                    "后端主机尚未安装 GitHub CLI"
                ) from exc

            raise GitHubValidationError(
                "Pull Request 内容不符合命令安全策略"
            ) from exc

    def _validated_title(self, value: str) -> str:
        normalized = value.strip()

        if (
            not normalized
            or len(normalized) > 256
            or any(ord(character) < 32 for character in normalized)
        ):
            raise GitHubValidationError(
                "PR 标题必须是 1 到 256 个字符的单行文本"
            )

        return normalized

    def _validated_body(self, value: str) -> str:
        normalized = value.strip()

        if len(normalized) > 10_000 or any(
            ord(character) < 32 and character not in {"\n", "\t"}
            for character in normalized
        ):
            raise GitHubValidationError("PR 正文不能超过 10000 个字符")

        return normalized

    def _pull_request_number(
        self,
        url: str,
        slug: str,
    ) -> int:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise GitHubConflictError(
                "GitHub CLI 未返回有效的 Pull Request 地址"
            ) from exc

        parts = parsed.path.strip("/").split("/")
        expected_slug = "/".join(parts[:2])

        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or len(parts) != 4
            or expected_slug.lower() != slug.lower()
            or parts[2] != "pull"
            or not parts[3].isdigit()
        ):
            raise GitHubConflictError(
                "GitHub CLI 未返回有效的 Pull Request 地址"
            )

        return int(parts[3])
