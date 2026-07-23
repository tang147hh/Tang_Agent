from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.app import create_app
from app.backends.command_runner import CommandPolicyError, CommandRunner
from app.backends.workspace import Workspace
from app.core.config import load_settings
from app.core.github_review import (
    GitHubCommandResult,
    GitHubPublicationStatus,
    GitHubReviewError,
    GitHubReviewErrorCode,
    GitHubReviewEvent,
    GitHubReviewService,
    parse_github_remote,
)
from app.core.review import ReviewFindingService, ReviewFindingStatus
from app.core.review_diff import ReviewDiffLimits
from app.core.task_intent import TaskKind
from app.core.task_runtime import TaskRegistry
from app.store import SQLiteProjectThreadStore


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def _git(repo: Path, *args: str) -> None:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


class FakeGitHubRunner:
    def __init__(self) -> None:
        self.installed = True
        self.authenticated = True
        self.head_sha = HEAD_SHA
        self.state = "open"
        self.draft = False
        self.post_mode = "success"
        self.repository = "acme/demo"
        self.review_url = (
            "https://github.com/acme/demo/pull/7#pullrequestreview-91"
        )
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.files: list[dict[str, Any]] = [
            {
                "filename": "src/app.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1 +1 @@\n-old value\n+new value\n",
            },
            {
                "filename": "old.py",
                "status": "removed",
                "additions": 0,
                "deletions": 1,
                "patch": "@@ -2 +1,0 @@\n-old line\n",
            },
            {
                "filename": "assets/logo.bin",
                "status": "added",
                "additions": 0,
                "deletions": 0,
            },
        ]

    def is_installed(self) -> bool:
        return self.installed

    def run(
        self,
        argv,
        *,
        cwd: Path,
        timeout: float,
        input_text: str | None = None,
    ) -> GitHubCommandResult:
        del cwd, timeout
        call = tuple(argv)
        self.calls.append((call, input_text))
        if call[:3] == ("gh", "auth", "status"):
            return GitHubCommandResult(
                exit_code=0 if self.authenticated else 1,
                stdout="",
                stderr="" if self.authenticated else "not logged in",
            )
        endpoint = next(
            (
                value
                for value in call
                if value == "user" or value.startswith("repos/")
            ),
            "",
        )
        if endpoint == "user":
            return self._json({"login": "reviewer"})
        if endpoint == "repos/acme/demo":
            return self._json({"permissions": {"pull": True}})
        if endpoint.endswith("pulls?state=open&per_page=20"):
            return self._json([self._pr()])
        if endpoint.endswith("pulls/7/files?per_page=100"):
            return self._json(self.files)
        if endpoint.endswith("pulls/7/reviews"):
            if self.post_mode == "timeout":
                return GitHubCommandResult(
                    exit_code=124,
                    stdout="",
                    stderr="",
                    timed_out=True,
                )
            if self.post_mode == "failed":
                return GitHubCommandResult(
                    exit_code=1,
                    stdout="",
                    stderr="HTTP 500",
                )
            return self._json(
                {
                    "id": 91,
                    "html_url": self.review_url,
                    "user": {"login": "reviewer"},
                }
            )
        if endpoint.endswith("pulls/7"):
            return self._json(self._pr())
        return GitHubCommandResult(exit_code=1, stdout="", stderr="HTTP 404")

    def _pr(self) -> dict[str, Any]:
        return {
            "number": 7,
            "title": "Safe review",
            "html_url": "https://github.com/acme/demo/pull/7",
            "state": self.state,
            "draft": self.draft,
            "base": {
                "ref": "main",
                "sha": BASE_SHA,
                "repo": {"full_name": self.repository},
            },
            "head": {"ref": "feature", "sha": self.head_sha},
            "user": {"login": "author"},
        }

    @staticmethod
    def _json(value: Any) -> GitHubCommandResult:
        return GitHubCommandResult(
            exit_code=0,
            stdout=json.dumps(value),
            stderr="",
        )

    @property
    def writes(self) -> list[tuple[tuple[str, ...], str | None]]:
        return [
            call
            for call in self.calls
            if "--method" in call[0]
            and call[0][call[0].index("--method") + 1] == "POST"
        ]


def _context(tmp_path: Path, *, remote: str = "https://github.com/acme/demo.git"):
    settings = replace(
        load_settings(),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspace",
        github_review_publish_enabled=True,
        github_cli_timeout=2,
        github_publication_ttl_seconds=60,
    )
    workspace = Workspace.from_settings(settings)
    workspace.ensure_layout()
    repo = workspace.resolve("/projects/demo")
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", remote)
    store = SQLiteProjectThreadStore(tmp_path / "navigation.sqlite")
    project = store.create_project(name="Demo", virtual_path="/projects/demo")
    thread = store.create_thread(project_id=project.project_id, title="Review")
    run = store.create_run(thread_id=thread.thread_id, task_kind=TaskKind.ANALYSIS)
    fake = FakeGitHubRunner()
    service = GitHubReviewService(
        store=store,
        workspace=workspace,
        settings=settings,
        runner=fake,
    )
    return settings, workspace, store, run, fake, service


def _save_pr_review(tmp_path: Path):
    settings, workspace, store, run, fake, service = _context(tmp_path)
    review_diff = service.collect_pull_request_diff(
        run_id=run.run_id,
        pr_number=7,
        limits=ReviewDiffLimits.from_settings(settings),
    )
    store.save_review_diff_snapshot(
        run_id=run.run_id,
        review_diff=review_diff,
        summary="snapshot",
    )
    output = {
        "findings": [
            {
                "severity": "high",
                "category": "correctness",
                "file_path": "/projects/demo/src/app.py",
                "start_line": 1,
                "end_line": 1,
                "line_side": "new",
                "title": "new issue",
                "description": "new behavior is wrong",
                "suggestion": "restore the check",
            },
            {
                "severity": "medium",
                "category": "correctness",
                "file_path": "/projects/demo/old.py",
                "start_line": 2,
                "end_line": 2,
                "line_side": "old",
                "title": "deleted check",
                "description": "the deleted line is required",
                "suggestion": None,
            },
            {
                "severity": "low",
                "category": "maintainability",
                "file_path": "/projects/demo/assets/logo.bin",
                "start_line": None,
                "end_line": None,
                "line_side": None,
                "title": "binary review",
                "description": "verify this asset manually",
                "suggestion": None,
            },
            {
                "severity": "low",
                "category": "testing",
                "file_path": None,
                "start_line": None,
                "end_line": None,
                "line_side": None,
                "title": "global coverage",
                "description": "coverage is missing",
                "suggestion": None,
            },
        ],
        "summary": "four findings",
    }
    findings = ReviewFindingService(store, workspace).save_model_output(
        run_id=run.run_id,
        raw_output=output,
        review_diff=review_diff,
    ).findings
    return settings, workspace, store, run, fake, service, review_diff, findings


@pytest.mark.parametrize(
    ("remote", "full_name"),
    [
        ("git@github.com:acme/demo.git", "acme/demo"),
        ("https://github.com/acme/demo.git", "acme/demo"),
        ("ssh://git@github.com/acme/demo.git", "acme/demo"),
    ],
)
def test_parse_github_remote_formats(remote: str, full_name: str) -> None:
    assert parse_github_remote(remote).full_name == full_name


def test_parse_github_remote_rejects_other_hosts_and_path_escape() -> None:
    with pytest.raises(GitHubReviewError) as unsupported:
        parse_github_remote("https://git.example.com/acme/demo.git")
    assert unsupported.value.code is GitHubReviewErrorCode.UNSUPPORTED_GITHUB_HOST
    with pytest.raises(GitHubReviewError):
        parse_github_remote("https://github.com/acme/../demo.git")
    with pytest.raises(GitHubReviewError):
        parse_github_remote("https://token@github.com/acme/demo.git")
    with pytest.raises(GitHubReviewError):
        parse_github_remote("https://github.com/acme/demo.git?token=secret")


def test_capability_reports_missing_gh_and_invalid_auth(tmp_path: Path) -> None:
    _, _, _, run, fake, service = _context(tmp_path)
    fake.installed = False
    capability = service.capability(run.run_id)
    assert capability["reason"] == "gh_not_installed"
    fake.installed = True
    fake.authenticated = False
    capability = service.capability(run.run_id)
    assert capability["reason"] == "github_not_authenticated"
    assert capability["current_user"] is None


def test_capability_returns_only_registered_repository_prs(tmp_path: Path) -> None:
    _, _, _, run, fake, service = _context(tmp_path)
    capability = service.capability(run.run_id)
    assert capability["repository"] == "acme/demo"
    assert capability["current_user"] == "reviewer"
    assert capability["can_publish"] is True
    assert capability["pull_requests"][0]["pr_number"] == 7
    assert fake.writes == []


def test_project_capability_api_uses_registered_project_without_writes(
    tmp_path: Path,
) -> None:
    settings, workspace, store, run, fake, _ = _context(tmp_path)
    thread = store.get_thread(run.thread_id)
    assert thread is not None
    app = create_app(
        agent_factory=lambda task_kind: None,
        reviewer=None,
        task_store=TaskRegistry(),
        navigation_store=store,
        workspace=workspace,
        settings=settings,
        github_runner=fake,
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/projects/{thread.project_id}/github-review/capability"
        )

    assert response.status_code == 200
    assert response.json()["repository"] == "acme/demo"
    assert str(tmp_path) not in response.text
    assert fake.writes == []


def test_pr_from_another_repository_is_rejected(tmp_path: Path) -> None:
    settings, _, _, run, fake, service = _context(tmp_path)
    fake.repository = "attacker/demo"

    with pytest.raises(GitHubReviewError) as rejected:
        service.collect_pull_request_diff(
            run_id=run.run_id,
            pr_number=7,
            limits=ReviewDiffLimits.from_settings(settings),
        )

    assert rejected.value.code is GitHubReviewErrorCode.PULL_REQUEST_NOT_FOUND
    assert fake.writes == []


def test_pull_request_snapshot_records_identity_lines_and_binary(tmp_path: Path) -> None:
    _, _, _, _, _, _, review_diff, _ = _save_pr_review(tmp_path)
    assert review_diff.source.value == "pull_request"
    assert review_diff.repository == "acme/demo"
    assert review_diff.pr_number == 7
    assert review_diff.base_revision == BASE_SHA
    assert review_diff.head_revision == HEAD_SHA
    assert review_diff.files[0].changed_new_lines == (1,)
    assert review_diff.files[1].changed_old_lines == (2,)
    assert review_diff.files[2].binary is True


def test_missing_github_patch_is_marked_truncated(tmp_path: Path) -> None:
    settings, _, _, run, fake, service = _context(tmp_path)
    fake.files = [{
        "filename": "large.txt",
        "status": "modified",
        "additions": 100,
        "deletions": 100,
    }]
    review_diff = service.collect_pull_request_diff(
        run_id=run.run_id,
        pr_number=7,
        limits=ReviewDiffLimits.from_settings(settings),
    )
    assert review_diff.truncated is True
    assert review_diff.files[0].truncation_reason.value == "github_patch_unavailable"
    assert review_diff.files[0].changed_new_lines == ()


def test_prepare_maps_right_left_and_moves_file_findings_to_summary(
    tmp_path: Path,
) -> None:
    _, _, _, run, fake, service, _, findings = _save_pr_review(tmp_path)
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[finding.id for finding in findings],
        event=GitHubReviewEvent.REQUEST_CHANGES,
        summary="Please address the verified findings.",
    )
    assert [item["side"] for item in preview["inline_comments"]] == [
        "RIGHT",
        "LEFT",
    ]
    assert preview["inline_comments"][0]["path"] == "src/app.py"
    assert preview["inline_comments"][1]["path"] == "old.py"
    assert len(preview["summary_comments"]) == 2
    assert preview["event"] == "REQUEST_CHANGES"
    assert fake.writes == []


@pytest.mark.parametrize(
    "finding_status",
    [ReviewFindingStatus.DISMISSED, ReviewFindingStatus.RESOLVED],
)
def test_prepare_skips_non_open_findings(
    tmp_path: Path,
    finding_status: ReviewFindingStatus,
) -> None:
    _, _, store, run, _, service, _, findings = _save_pr_review(tmp_path)
    store.update_review_finding_status(
        run_id=run.run_id,
        finding_id=findings[0].id,
        status=finding_status,
    )
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[finding.id for finding in findings],
        event=GitHubReviewEvent.COMMENT,
        summary=None,
    )
    assert preview["skipped_findings"][0]["finding_id"] == findings[0].id
    assert all(
        item["finding_id"] != findings[0].id
        for item in preview["inline_comments"]
    )


def test_working_tree_review_cannot_prepare(tmp_path: Path) -> None:
    settings, workspace, store, run, _, service = _context(tmp_path)
    repo = workspace.resolve("/projects/demo")
    (repo / "local.txt").write_text("local\n", encoding="utf-8")
    from app.core.review_diff import ReviewDiffCollector

    review_diff = ReviewDiffCollector(
        workspace=workspace,
        store=store,
        limits=ReviewDiffLimits.from_settings(settings),
    ).collect_for_run(run_id=run.run_id)
    store.save_review_diff_snapshot(
        run_id=run.run_id,
        review_diff=review_diff,
        summary="local",
    )
    with pytest.raises(GitHubReviewError) as rejected:
        service.prepare(
            run_id=run.run_id,
            pr_number=7,
            selected_finding_ids=[],
            event=GitHubReviewEvent.COMMENT,
            summary=None,
        )
    assert rejected.value.code is GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE


def test_publish_uses_server_payload_and_saves_audit_result(tmp_path: Path) -> None:
    _, _, store, run, fake, service, _, findings = _save_pr_review(tmp_path)
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[0].id],
        event=GitHubReviewEvent.APPROVE,
        summary=None,
    )
    publication = service.publish(
        run_id=run.run_id,
        publication_id=preview["publication_id"],
    )
    assert publication.status is GitHubPublicationStatus.PUBLISHED
    assert publication.github_review_id == "91"
    assert publication.github_user == "reviewer"
    assert publication.github_review_url == (
        "https://github.com/acme/demo/pull/7#pullrequestreview-91"
    )
    assert len(fake.writes) == 1
    payload = json.loads(fake.writes[0][1] or "{}")
    assert payload["commit_id"] == HEAD_SHA
    assert payload["event"] == "APPROVE"
    assert payload["comments"][0]["path"] == "src/app.py"
    assert store.get_github_review_publication(publication.id) == publication


def test_secrets_and_host_paths_never_reach_github_payload(tmp_path: Path) -> None:
    _, workspace, _, run, fake, service, review_diff, _ = _save_pr_review(tmp_path)
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    host_path = "/Users/alice/private/config.py"
    [finding] = ReviewFindingService(
        service.store,
        workspace,
    ).save_model_output(
        run_id=run.run_id,
        raw_output={
            "findings": [
                {
                    "severity": "critical",
                    "category": "security",
                    "file_path": "/projects/demo/src/app.py",
                    "start_line": 1,
                    "end_line": 1,
                    "line_side": "new",
                    "title": "credential exposure",
                    "description": f"Do not publish {secret} from {host_path}",
                    "suggestion": f"remove {secret} at {host_path}",
                }
            ],
            "summary": "sensitive finding",
        },
        review_diff=review_diff,
    ).findings
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[finding.id],
        event=GitHubReviewEvent.COMMENT,
        summary=f"Inspect {host_path}; token={secret}",
    )
    service.publish(run_id=run.run_id, publication_id=preview["publication_id"])

    payload_text = fake.writes[0][1] or ""
    assert secret not in payload_text
    assert host_path not in payload_text
    assert "[REDACTED" in payload_text


def test_untrusted_review_url_becomes_unknown_and_is_not_persisted(
    tmp_path: Path,
) -> None:
    _, _, store, run, fake, service, _, findings = _save_pr_review(tmp_path)
    fake.review_url = (
        "https://token@github.com/acme/demo/pull/70"
        "?access_token=secret#pullrequestreview-91"
    )
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[0].id],
        event=GitHubReviewEvent.COMMENT,
        summary=None,
    )

    with pytest.raises(GitHubReviewError) as unknown:
        service.publish(
            run_id=run.run_id,
            publication_id=preview["publication_id"],
        )

    assert unknown.value.code is GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN
    publication = store.get_github_review_publication(preview["publication_id"])
    assert publication is not None
    assert publication.status is GitHubPublicationStatus.UNKNOWN
    assert publication.github_review_url is None


def test_publish_rejects_changed_head_and_changed_finding(tmp_path: Path) -> None:
    _, _, store, run, fake, service, _, findings = _save_pr_review(tmp_path)
    first = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[0].id],
        event=GitHubReviewEvent.COMMENT,
        summary=None,
    )
    fake.head_sha = "c" * 40
    with pytest.raises(GitHubReviewError) as changed:
        service.publish(run_id=run.run_id, publication_id=first["publication_id"])
    assert changed.value.code is GitHubReviewErrorCode.PULL_REQUEST_CHANGED
    assert fake.writes == []

    fake.head_sha = HEAD_SHA
    second = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[1].id],
        event=GitHubReviewEvent.COMMENT,
        summary=None,
    )
    store.update_review_finding_status(
        run_id=run.run_id,
        finding_id=findings[1].id,
        status=ReviewFindingStatus.RESOLVED,
    )
    with pytest.raises(GitHubReviewError) as finding_changed:
        service.publish(run_id=run.run_id, publication_id=second["publication_id"])
    assert finding_changed.value.code is GitHubReviewErrorCode.PUBLICATION_CHANGED


def test_expired_publication_never_writes(tmp_path: Path) -> None:
    settings, workspace, store, run, fake, _, review_diff, findings = _save_pr_review(
        tmp_path
    )
    del review_diff
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    clock_value = [now]
    service = GitHubReviewService(
        store=store,
        workspace=workspace,
        settings=replace(settings, github_publication_ttl_seconds=1),
        runner=fake,
        clock=lambda: clock_value[0],
    )
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[0].id],
        event=GitHubReviewEvent.COMMENT,
        summary=None,
    )
    clock_value[0] = now + timedelta(seconds=2)
    with pytest.raises(GitHubReviewError) as expired:
        service.publish(run_id=run.run_id, publication_id=preview["publication_id"])
    assert expired.value.code is GitHubReviewErrorCode.PUBLICATION_EXPIRED
    assert fake.writes == []


def test_duplicate_payload_and_timeout_unknown_block_retry(tmp_path: Path) -> None:
    _, _, store, run, fake, service, _, findings = _save_pr_review(tmp_path)
    kwargs = {
        "run_id": run.run_id,
        "pr_number": 7,
        "selected_finding_ids": [findings[0].id],
        "event": GitHubReviewEvent.COMMENT,
        "summary": None,
    }
    first = service.prepare(**kwargs)
    second = service.prepare(**kwargs)
    service.publish(run_id=run.run_id, publication_id=first["publication_id"])
    with pytest.raises(GitHubReviewError) as duplicate:
        service.publish(run_id=run.run_id, publication_id=second["publication_id"])
    assert duplicate.value.code is GitHubReviewErrorCode.PUBLICATION_ALREADY_PUBLISHED
    assert len(fake.writes) == 1

    third = service.prepare(
        **{**kwargs, "selected_finding_ids": [findings[1].id]}
    )
    fake.post_mode = "timeout"
    with pytest.raises(GitHubReviewError) as unknown:
        service.publish(run_id=run.run_id, publication_id=third["publication_id"])
    assert unknown.value.code is GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN
    saved = store.get_github_review_publication(third["publication_id"])
    assert saved is not None and saved.status is GitHubPublicationStatus.UNKNOWN
    with pytest.raises(GitHubReviewError):
        service.publish(run_id=run.run_id, publication_id=third["publication_id"])
    assert len(fake.writes) == 2


def test_normal_api_failure_can_retry_same_publication(tmp_path: Path) -> None:
    _, _, store, run, fake, service, _, findings = _save_pr_review(tmp_path)
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[0].id],
        event=GitHubReviewEvent.COMMENT,
        summary=None,
    )
    fake.post_mode = "failed"
    with pytest.raises(GitHubReviewError):
        service.publish(run_id=run.run_id, publication_id=preview["publication_id"])
    failed = store.get_github_review_publication(preview["publication_id"])
    assert failed is not None and failed.status is GitHubPublicationStatus.FAILED
    fake.post_mode = "success"
    published = service.publish(
        run_id=run.run_id,
        publication_id=preview["publication_id"],
    )
    assert published.status is GitHubPublicationStatus.PUBLISHED


def test_claimed_publication_rejects_concurrent_publish(tmp_path: Path) -> None:
    _, _, store, run, fake, service, _, findings = _save_pr_review(tmp_path)
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[0].id],
        event=GitHubReviewEvent.COMMENT,
        summary=None,
    )
    store.claim_github_review_publication(preview["publication_id"])

    with pytest.raises(GitHubReviewError) as concurrent:
        service.publish(
            run_id=run.run_id,
            publication_id=preview["publication_id"],
        )

    assert concurrent.value.code is GitHubReviewErrorCode.PUBLICATION_IN_PROGRESS
    assert fake.writes == []


def test_api_rejects_owner_override_and_invalid_event(tmp_path: Path) -> None:
    settings, workspace, store, run, fake, _, _, findings = _save_pr_review(tmp_path)
    app = create_app(
        agent_factory=lambda task_kind: None,
        reviewer=None,
        task_store=TaskRegistry(),
        navigation_store=store,
        workspace=workspace,
        settings=settings,
        github_runner=fake,
    )
    with TestClient(app) as client:
        owner = client.post(
            f"/api/runs/{run.run_id}/github-review/prepare",
            json={
                "pr_number": 7,
                "selected_finding_ids": [findings[0].id],
                "event": "COMMENT",
                "owner": "attacker",
            },
        )
        event = client.post(
            f"/api/runs/{run.run_id}/github-review/prepare",
            json={
                "pr_number": 7,
                "selected_finding_ids": [findings[0].id],
                "event": "MERGE",
            },
        )
    assert owner.status_code == 422
    assert event.status_code == 422
    assert fake.writes == []


def test_agent_command_policy_rejects_gh_api(tmp_path: Path) -> None:
    settings = replace(load_settings(), workspace_root=tmp_path / "workspace")
    workspace = Workspace.from_settings(settings)
    workspace.ensure_layout()
    runner = CommandRunner(workspace, allowed_commands={"gh"})
    with pytest.raises(CommandPolicyError):
        runner.run(
            ["gh", "api", "--method", "POST", "repos/acme/demo/pulls/7/reviews"],
            cwd="/projects",
        )


def test_existing_sqlite_database_adds_publication_table_without_data_loss(
    tmp_path: Path,
) -> None:
    _, _, store, run, _, _ = _context(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE github_review_publications")

    reopened = SQLiteProjectThreadStore(store.path)

    assert reopened.get_run(run.run_id) is not None
    with sqlite3.connect(store.path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(github_review_publications)"
            ).fetchall()
        }
    assert {"id", "run_id", "payload_hash", "status", "expires_at"} <= columns


def test_publication_audit_and_retry_locks_survive_store_reopen(
    tmp_path: Path,
) -> None:
    published_root = tmp_path / "published"
    (
        settings,
        workspace,
        store,
        run,
        fake,
        service,
        _,
        findings,
    ) = _save_pr_review(published_root)
    preview = service.prepare(
        run_id=run.run_id,
        pr_number=7,
        selected_finding_ids=[findings[0].id],
        event=GitHubReviewEvent.COMMENT,
        summary="restart audit",
    )
    service.publish(
        run_id=run.run_id,
        publication_id=preview["publication_id"],
    )

    reopened = SQLiteProjectThreadStore(store.path)
    restarted_service = GitHubReviewService(
        store=reopened,
        workspace=workspace,
        settings=settings,
        runner=fake,
    )
    assert reopened.get_review_diff_snapshot(run.run_id) is not None
    assert reopened.list_review_findings(run.run_id)
    saved_publication = reopened.get_github_review_publication(
        preview["publication_id"]
    )
    assert saved_publication is not None
    assert saved_publication.status is GitHubPublicationStatus.PUBLISHED
    writes_before_retry = len(fake.writes)
    with pytest.raises(GitHubReviewError) as duplicate:
        restarted_service.publish(
            run_id=run.run_id,
            publication_id=preview["publication_id"],
        )
    assert duplicate.value.code is (
        GitHubReviewErrorCode.PUBLICATION_ALREADY_PUBLISHED
    )
    assert len(fake.writes) == writes_before_retry

    unknown_root = tmp_path / "unknown"
    (
        unknown_settings,
        unknown_workspace,
        unknown_store,
        unknown_run,
        unknown_fake,
        unknown_service,
        _,
        unknown_findings,
    ) = _save_pr_review(unknown_root)
    unknown_preview = unknown_service.prepare(
        run_id=unknown_run.run_id,
        pr_number=7,
        selected_finding_ids=[unknown_findings[0].id],
        event=GitHubReviewEvent.COMMENT,
        summary="unknown restart audit",
    )
    unknown_fake.post_mode = "timeout"
    with pytest.raises(GitHubReviewError) as timed_out:
        unknown_service.publish(
            run_id=unknown_run.run_id,
            publication_id=unknown_preview["publication_id"],
        )
    assert timed_out.value.code is (
        GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN
    )

    reopened_unknown = SQLiteProjectThreadStore(unknown_store.path)
    restarted_unknown_service = GitHubReviewService(
        store=reopened_unknown,
        workspace=unknown_workspace,
        settings=unknown_settings,
        runner=unknown_fake,
    )
    saved_unknown = reopened_unknown.get_github_review_publication(
        unknown_preview["publication_id"]
    )
    assert saved_unknown is not None
    assert saved_unknown.status is GitHubPublicationStatus.UNKNOWN
    unknown_writes_before_retry = len(unknown_fake.writes)
    with pytest.raises(GitHubReviewError) as locked:
        restarted_unknown_service.publish(
            run_id=unknown_run.run_id,
            publication_id=unknown_preview["publication_id"],
        )
    assert locked.value.code is (
        GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN
    )
    assert len(unknown_fake.writes) == unknown_writes_before_retry
