from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.backends.command_runner import CommandResult, CommandRunner
from app.backends.workspace import Workspace
from app.repositories import (
    RepositoryCatalog,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryValidationError,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    current = Workspace(tmp_path / "workspace")
    current.ensure_layout()
    return current


def _git(cwd: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _initialize_repository(
    target: Path,
    *,
    remote_url: str | None = None,
) -> None:
    target.mkdir(parents=True)
    _git(target, "init", "-b", "main")
    _git(target, "config", "user.name", "Tang Agent Tests")
    _git(target, "config", "user.email", "tests@example.com")
    target.joinpath("README.md").write_text("# Demo\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "Initial commit")

    if remote_url is not None:
        _git(target, "remote", "add", "origin", remote_url)


class RecordingRunner:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.delegate = CommandRunner(workspace)
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def run(
        self,
        argv: list[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        self.calls.append((tuple(argv), cwd))
        return self.delegate.run(argv, cwd=cwd, timeout=timeout)


class FakeCloneRunner(RecordingRunner):
    def run(
        self,
        argv: list[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        self.calls.append((tuple(argv), cwd))

        if argv[:2] == ["git", "clone"]:
            target = self.workspace.resolve(argv[-1])
            _initialize_repository(target, remote_url=argv[-2])
            return CommandResult(
                argv=tuple(argv),
                cwd=cwd,
                exit_code=0,
                stdout="",
                stderr="",
                timed_out=False,
                truncated=False,
            )

        return self.delegate.run(argv, cwd=cwd, timeout=timeout)


def test_discovers_only_direct_git_repositories(
    workspace: Workspace,
) -> None:
    projects_root = workspace.resolve("/projects")
    _initialize_repository(
        projects_root / "demo",
        remote_url=(
            "https://user:secret-value@github.com/example/demo.git"
        ),
    )
    projects_root.joinpath("notes").mkdir()
    nested = projects_root / "container" / "nested"
    _initialize_repository(nested)
    projects_root.joinpath("demo", "draft.txt").write_text(
        "not committed",
        encoding="utf-8",
    )

    repositories = RepositoryCatalog(workspace).discover()

    assert len(repositories) == 1
    assert repositories[0].name == "demo"
    assert repositories[0].path == "/projects/demo"
    assert repositories[0].current_branch == "main"
    assert repositories[0].branches == ("main",)
    assert repositories[0].dirty is True
    assert repositories[0].remote_url == (
        "https://github.com/example/demo.git"
    )
    assert "secret-value" not in repositories[0].remote_url


def test_hides_absolute_local_remote_path(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    private_remote = tmp_path / "private" / "demo.git"
    _initialize_repository(
        workspace.resolve("/projects/demo"),
        remote_url=str(private_remote),
    )

    snapshot = RepositoryCatalog(workspace).discover()[0]

    assert snapshot.remote_url == ""
    assert str(tmp_path) not in snapshot.remote_url


def test_clones_valid_github_https_url_without_network(
    workspace: Workspace,
) -> None:
    runner = FakeCloneRunner(workspace)
    catalog = RepositoryCatalog(workspace, runner=runner)

    snapshot = catalog.clone(
        "https://github.com/example/course-demo.git"
    )

    assert snapshot.name == "course-demo"
    assert snapshot.path == "/projects/course-demo"
    assert snapshot.remote_url == (
        "https://github.com/example/course-demo"
    )
    assert runner.calls[0] == (
        (
            "git",
            "clone",
            "--origin",
            "origin",
            "https://github.com/example/course-demo",
            "/projects/course-demo",
        ),
        "/projects",
    )


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:example/demo.git",
        "http://github.com/example/demo",
        "https://gitlab.com/example/demo",
        "https://user:secret@github.com/example/demo",
        "https://github.com:8443/example/demo",
        "https://github.com/example/demo?token=secret",
        "https://github.com/example/demo#readme",
        "https://github.com/example/demo/extra",
        "https://github.com/example/bad%20name",
    ],
)
def test_rejects_unsafe_clone_urls(
    workspace: Workspace,
    url: str,
) -> None:
    with pytest.raises(RepositoryValidationError):
        RepositoryCatalog(workspace).clone(url)


def test_clone_rejects_existing_target(
    workspace: Workspace,
) -> None:
    workspace.resolve("/projects/demo").mkdir()

    with pytest.raises(
        RepositoryConflictError,
        match="目标目录已存在",
    ):
        RepositoryCatalog(workspace).clone(
            "https://github.com/example/demo"
        )


def test_fetch_uses_fixed_origin_and_prune_arguments(
    workspace: Workspace,
) -> None:
    _initialize_repository(workspace.resolve("/projects/demo"))
    runner = RecordingRunner(workspace)
    catalog = RepositoryCatalog(workspace, runner=runner)

    original_run = runner.run

    def fake_fetch(
        argv: list[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        if argv == ["git", "fetch", "--prune", "origin"]:
            runner.calls.append((tuple(argv), cwd))
            return CommandResult(
                argv=tuple(argv),
                cwd=cwd,
                exit_code=0,
                stdout="",
                stderr="",
                timed_out=False,
                truncated=False,
            )

        return original_run(argv, cwd=cwd, timeout=timeout)

    runner.run = fake_fetch  # type: ignore[method-assign]

    snapshot = catalog.fetch("demo")

    assert snapshot.name == "demo"
    assert (
        ("git", "fetch", "--prune", "origin"),
        "/projects/demo",
    ) in runner.calls


def test_creates_and_checks_out_local_branches(
    workspace: Workspace,
) -> None:
    _initialize_repository(workspace.resolve("/projects/demo"))
    catalog = RepositoryCatalog(workspace)

    created = catalog.create_branch("demo", "feature/course")
    checked_out = catalog.checkout("demo", "main")

    assert created.current_branch == "feature/course"
    assert created.branches == ("feature/course", "main")
    assert checked_out.current_branch == "main"

    with pytest.raises(
        RepositoryConflictError,
        match="分支已存在",
    ):
        catalog.create_branch("demo", "feature/course")


def test_rejects_invalid_or_missing_branch(
    workspace: Workspace,
) -> None:
    _initialize_repository(workspace.resolve("/projects/demo"))
    catalog = RepositoryCatalog(workspace)

    with pytest.raises(RepositoryValidationError):
        catalog.create_branch("demo", "bad branch")

    with pytest.raises(
        RepositoryNotFoundError,
        match="分支不存在",
    ):
        catalog.checkout("demo", "missing")


def test_rejects_missing_repository(
    workspace: Workspace,
) -> None:
    with pytest.raises(
        RepositoryNotFoundError,
        match="仓库不存在",
    ):
        RepositoryCatalog(workspace).fetch("missing")


def test_commits_all_non_sensitive_changes(
    workspace: Workspace,
) -> None:
    target = workspace.resolve("/projects/demo")
    _initialize_repository(target)
    target.joinpath("feature.txt").write_text(
        "course content\n",
        encoding="utf-8",
    )

    result = RepositoryCatalog(workspace).commit(
        "demo",
        "feat: add course content",
    )

    assert len(result.sha) == 40
    assert result.subject == "feat: add course content"
    assert result.repository.dirty is False
    assert _git(target, "show", "--format=%s", "--no-patch", "HEAD") == (
        "feat: add course content"
    )
    assert _git(target, "ls-files", "feature.txt") == "feature.txt"


def test_commit_rejects_clean_workspace_and_sensitive_files(
    workspace: Workspace,
) -> None:
    target = workspace.resolve("/projects/demo")
    _initialize_repository(target)
    catalog = RepositoryCatalog(workspace)

    with pytest.raises(
        RepositoryConflictError,
        match="没有可提交的修改",
    ):
        catalog.commit("demo", "chore: no changes")

    target.joinpath(".env").write_text(
        "TOKEN=must-not-commit\n",
        encoding="utf-8",
    )

    with pytest.raises(
        RepositoryValidationError,
        match="检测到敏感文件",
    ):
        catalog.commit("demo", "chore: unsafe")

    assert _git(target, "diff", "--cached", "--name-only") == ""


def test_push_uses_current_branch_and_fixed_origin(
    workspace: Workspace,
) -> None:
    _initialize_repository(
        workspace.resolve("/projects/demo"),
        remote_url="https://github.com/example/demo",
    )
    runner = RecordingRunner(workspace)
    catalog = RepositoryCatalog(workspace, runner=runner)
    catalog.create_branch("demo", "feature/course")
    original_run = runner.run

    def fake_push(
        argv: list[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        if argv[:2] == ["git", "push"]:
            runner.calls.append((tuple(argv), cwd))
            return CommandResult(
                argv=tuple(argv),
                cwd=cwd,
                exit_code=0,
                stdout="",
                stderr="",
                timed_out=False,
                truncated=False,
            )

        return original_run(argv, cwd=cwd, timeout=timeout)

    runner.run = fake_push  # type: ignore[method-assign]

    result = catalog.push("demo")

    assert result.branch == "feature/course"
    assert (
        (
            "git",
            "push",
            "--set-upstream",
            "origin",
            "feature/course",
        ),
        "/projects/demo",
    ) in runner.calls


def test_push_rejects_protected_branch(
    workspace: Workspace,
) -> None:
    _initialize_repository(
        workspace.resolve("/projects/demo"),
        remote_url="https://github.com/example/demo",
    )

    with pytest.raises(
        RepositoryConflictError,
        match="禁止直接推送受保护分支",
    ):
        RepositoryCatalog(workspace).push("demo")


def test_prepares_clean_pushed_branch_for_pull_request(
    workspace: Workspace,
) -> None:
    _initialize_repository(
        workspace.resolve("/projects/demo"),
        remote_url="https://github.com/example/demo",
    )
    runner = RecordingRunner(workspace)
    catalog = RepositoryCatalog(workspace, runner=runner)
    catalog.create_branch("demo", "feature/course")
    original_run = runner.run

    def fake_upstream(
        argv: list[str],
        *,
        cwd: str = "/projects",
        timeout: float = 300,
    ) -> CommandResult:
        if "@{upstream}" in argv:
            return CommandResult(
                argv=tuple(argv),
                cwd=cwd,
                exit_code=0,
                stdout="origin/feature/course\n",
                stderr="",
                timed_out=False,
                truncated=False,
            )

        return original_run(argv, cwd=cwd, timeout=timeout)

    runner.run = fake_upstream  # type: ignore[method-assign]

    snapshot = catalog.prepare_pull_request("demo", "main")

    assert snapshot.current_branch == "feature/course"


def test_pull_request_requires_pushed_clean_branch(
    workspace: Workspace,
) -> None:
    target = workspace.resolve("/projects/demo")
    _initialize_repository(
        target,
        remote_url="https://github.com/example/demo",
    )
    catalog = RepositoryCatalog(workspace)
    catalog.create_branch("demo", "feature/course")

    with pytest.raises(
        RepositoryConflictError,
        match="尚未推送",
    ):
        catalog.prepare_pull_request("demo", "main")

    target.joinpath("draft.txt").write_text("draft\n", encoding="utf-8")

    with pytest.raises(
        RepositoryConflictError,
        match="必须先提交",
    ):
        catalog.prepare_pull_request("demo", "main")
