from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.command_runner import CommandPolicyError
from app.backends.local_shell import LocalShellBackend
from app.backends.workspace import Workspace


@pytest.fixture
def backend(tmp_path: Path) -> LocalShellBackend:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    return LocalShellBackend(workspace)


def test_runs_python_file_through_virtual_path(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/tmp/hello.py",
        "print('hello from workspace')\n",
    )

    result = backend.run_command(
        ["python", "/tmp/hello.py"],
        cwd="/tmp",
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello from workspace"
    assert result.timed_out is False


def test_rejects_shell_string(
    backend: LocalShellBackend,
) -> None:
    with pytest.raises(CommandPolicyError):
        backend.run_command("git status")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", "echo unsafe"],
        ["git", "clean", "-fd"],
        ["git", "reset", "--hard"],
        ["git", "push", "--force"],
        ["git", "push", "--force-with-lease"],
        ["git", "config", "--global", "user.name", "agent"],
        ["python", "-c", "print('unsafe')"],
        ["git", "-C", "../outside", "status"],
        ["python", "/etc/passwd"],
    ],
)
def test_rejects_unsafe_commands(
    backend: LocalShellBackend,
    argv: list[str],
) -> None:
    with pytest.raises(CommandPolicyError):
        backend.run_command(argv, cwd="/tmp")


def test_rejects_sensitive_arguments(
    backend: LocalShellBackend,
) -> None:
    with pytest.raises(CommandPolicyError):
        backend.run_command(
            ["git", "clone", "https://token=secret@example.com/repo"],
            cwd="/tmp",
        )


def test_command_timeout(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/tmp/sleep.py",
        "import time\ntime.sleep(2)\n",
    )

    result = backend.run_command(
        ["python", "/tmp/sleep.py"],
        cwd="/tmp",
        timeout=0.01,
    )

    assert result.exit_code == 124
    assert result.timed_out is True


def test_command_output_is_truncated(
    backend: LocalShellBackend,
) -> None:
    backend.write_text(
        "/tmp/large_output.py",
        "print('x' * 60000)\n",
    )

    result = backend.run_command(
        ["python", "/tmp/large_output.py"],
        cwd="/tmp",
    )

    assert result.exit_code == 0
    assert result.truncated is True
    assert "output truncated" in result.stdout


def test_sensitive_environment_is_not_inherited(
    backend: LocalShellBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TANG_AGENT_TEST_SECRET",
        "must-not-leak",
    )

    backend.write_text(
        "/tmp/read_env.py",
        (
            "import os\n"
            "print(os.getenv('TANG_AGENT_TEST_SECRET', 'missing'))\n"
        ),
    )

    result = backend.run_command(
        ["python", "/tmp/read_env.py"],
        cwd="/tmp",
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "missing"