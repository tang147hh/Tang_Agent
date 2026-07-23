from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.command_runner import CommandPolicyError, CommandResult
from app.backends.workspace import Workspace
from app.repositories import (
    GitHubClient,
    GitHubConfigurationError,
    GitHubConflictError,
    GitHubValidationError,
    github_repository_slug,
)


class FakeGitHubRunner:
    def __init__(
        self,
        *,
        authenticated: bool = True,
        pull_request_url: str = (
            "https://github.com/example/demo/pull/42"
        ),
    ) -> None:
        self.authenticated = authenticated
        self.pull_request_url = pull_request_url
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def run(
        self,
        argv: list[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        self.calls.append((tuple(argv), cwd))
        is_authentication = argv[1:3] == ["auth", "status"]
        exit_code = 0 if self.authenticated or not is_authentication else 1
        stdout = (
            f"{self.pull_request_url}\n"
            if argv[1:3] == ["pr", "create"]
            else ""
        )
        return CommandResult(
            argv=tuple(argv),
            cwd=cwd,
            exit_code=exit_code,
            stdout=stdout,
            stderr="",
            timed_out=False,
            truncated=False,
        )


class MissingGitHubRunner:
    def run(self, argv, *, cwd="/projects", timeout=300):
        raise CommandPolicyError("命令不可用：gh")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    current = Workspace(tmp_path / "workspace")
    current.ensure_layout()
    current.resolve("/projects/demo").mkdir()
    return current


@pytest.mark.parametrize(
    ("remote_url", "slug"),
    [
        ("https://github.com/example/demo.git", "example/demo"),
        ("ssh://github.com/example/demo.git", "example/demo"),
        ("github.com:example/demo.git", "example/demo"),
    ],
)
def test_parses_github_repository_slug(
    remote_url: str,
    slug: str,
) -> None:
    assert github_repository_slug(remote_url) == slug


def test_rejects_non_github_remote() -> None:
    with pytest.raises(GitHubValidationError):
        github_repository_slug("https://gitlab.com/example/demo")


def test_creates_pull_request_with_fixed_cli_shape(
    workspace: Workspace,
) -> None:
    runner = FakeGitHubRunner()
    client = GitHubClient(workspace, runner=runner)  # type: ignore[arg-type]

    result = client.create_pull_request(
        repository_name="demo",
        remote_url="https://github.com/example/demo.git",
        head="feature/course",
        base="main",
        title="feat: finish course",
        body="## Summary\n\nCourse implementation.",
    )

    assert result.number == 42
    assert result.url == "https://github.com/example/demo/pull/42"
    assert runner.calls[0][0] == (
        "gh",
        "auth",
        "status",
        "--hostname",
        "github.com",
    )
    assert runner.calls[1] == (
        (
            "gh",
            "pr",
            "create",
            "--repo",
            "example/demo",
            "--base",
            "main",
            "--head",
            "feature/course",
            "--title",
            "feat: finish course",
            "--body",
            "## Summary\n\nCourse implementation.",
        ),
        "/projects/demo",
    )


def test_reports_missing_or_unauthenticated_github_cli(
    workspace: Workspace,
) -> None:
    missing = GitHubClient(
        workspace,
        runner=MissingGitHubRunner(),  # type: ignore[arg-type]
    )
    unauthenticated = GitHubClient(
        workspace,
        runner=FakeGitHubRunner(authenticated=False),  # type: ignore[arg-type]
    )

    with pytest.raises(
        GitHubConfigurationError,
        match="尚未安装",
    ):
        missing.create_pull_request(
            repository_name="demo",
            remote_url="https://github.com/example/demo",
            head="feature/course",
            base="main",
            title="Course",
            body="",
        )

    with pytest.raises(
        GitHubConfigurationError,
        match="尚未登录",
    ):
        unauthenticated.create_pull_request(
            repository_name="demo",
            remote_url="https://github.com/example/demo",
            head="feature/course",
            base="main",
            title="Course",
            body="",
        )


def test_rejects_invalid_pull_request_url(
    workspace: Workspace,
) -> None:
    client = GitHubClient(
        workspace,
        runner=FakeGitHubRunner(
            pull_request_url="https://example.com/not-a-pr"
        ),  # type: ignore[arg-type]
    )

    with pytest.raises(GitHubConflictError):
        client.create_pull_request(
            repository_name="demo",
            remote_url="https://github.com/example/demo",
            head="feature/course",
            base="main",
            title="Course",
            body="",
        )
