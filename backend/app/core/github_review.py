from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse

from app.backends.command_runner import CommandRunner
from app.backends.workspace import Workspace
from app.core.config import Settings
from app.core.review import ReviewFindingSnapshot, ReviewFindingStatus
from app.core.review_diff import (
    ReviewChangeType,
    ReviewDiff,
    ReviewDiffFile,
    ReviewDiffLimits,
    ReviewLineSide,
    ReviewScope,
    ReviewSource,
    ReviewTruncationReason,
    parse_unified_patch,
    redact_sensitive_patch,
    visible_lines_for_file,
)


logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$")
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_HOST_PATH = re.compile(
    r"(?:(?:/Users|/home)/[^\s'\"`]+|[A-Za-z]:\\Users\\[^\s'\"`]+)"
)
_MAX_GH_OUTPUT_CHARS = 4_000_000


class GitHubReviewErrorCode(StrEnum):
    GH_NOT_INSTALLED = "gh_not_installed"
    GITHUB_NOT_AUTHENTICATED = "github_not_authenticated"
    GITHUB_REMOTE_NOT_FOUND = "github_remote_not_found"
    UNSUPPORTED_GITHUB_HOST = "unsupported_github_host"
    PULL_REQUEST_NOT_FOUND = "pull_request_not_found"
    PULL_REQUEST_CLOSED = "pull_request_closed"
    PULL_REQUEST_CHANGED = "pull_request_changed"
    PULL_REQUEST_DRAFT = "pull_request_draft"
    PERMISSION_DENIED = "permission_denied"
    REVIEW_NOT_PUBLISHABLE = "review_not_publishable"
    FINDING_NOT_PUBLISHABLE = "finding_not_publishable"
    PUBLICATION_EXPIRED = "publication_expired"
    PUBLICATION_CHANGED = "publication_changed"
    PUBLICATION_ALREADY_PUBLISHED = "publication_already_published"
    PUBLICATION_IN_PROGRESS = "publication_in_progress"
    PUBLICATION_RESULT_UNKNOWN = "publication_result_unknown"
    GITHUB_TIMEOUT = "github_timeout"
    GITHUB_API_ERROR = "github_api_error"
    PUBLISHING_DISABLED = "publishing_disabled"


class GitHubReviewError(RuntimeError):
    def __init__(
        self,
        code: GitHubReviewErrorCode,
        message: str,
        *,
        result_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.result_unknown = result_unknown


class GitHubReviewEvent(StrEnum):
    COMMENT = "COMMENT"
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class GitHubPublicationStatus(StrEnum):
    PREPARED = "prepared"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class GitHubReviewPublicationSnapshot:
    id: str
    run_id: str
    repository: str
    pr_number: int
    base_sha: str
    head_sha: str
    event: GitHubReviewEvent
    selected_finding_ids: tuple[str, ...]
    finding_state_hash: str
    payload: dict[str, Any]
    preview: dict[str, Any]
    payload_hash: str
    status: GitHubPublicationStatus
    github_review_id: str | None
    github_review_url: str | None
    github_user: str | None
    prepared_at: datetime
    expires_at: datetime
    published_at: datetime | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class GitHubRepositoryIdentity:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class GitHubPullRequest:
    pr_number: int
    title: str
    url: str
    state: str
    is_draft: bool
    base_branch: str
    head_branch: str
    base_sha: str
    head_sha: str
    author: str
    repository: str
    changed_files: int = 0


@dataclass(frozen=True, slots=True)
class GitHubCommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


class GitHubCommandRunner(Protocol):
    def is_installed(self) -> bool: ...

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        input_text: str | None = None,
    ) -> GitHubCommandResult: ...


class GitHubCliRunner:
    """宿主机专用 gh runner；不暴露给 Agent，也不接受前端参数。"""

    def is_installed(self) -> bool:
        return shutil.which("gh") is not None

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        input_text: str | None = None,
    ) -> GitHubCommandResult:
        if not argv or argv[0] != "gh" or isinstance(argv, str):
            raise ValueError("GitHub CLI 必须使用固定参数数组")
        environment = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GH_PROMPT_DISABLED": "1",
                "GH_NO_UPDATE_NOTIFIER": "1",
            }
        )
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                env=environment,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            return GitHubCommandResult(
                exit_code=124,
                stdout=_timeout_text(exc.stdout),
                stderr=_timeout_text(exc.stderr),
                timed_out=True,
            )
        stdout = completed.stdout
        stderr = completed.stderr
        truncated = (
            len(stdout) > _MAX_GH_OUTPUT_CHARS
            or len(stderr) > _MAX_GH_OUTPUT_CHARS
        )
        return GitHubCommandResult(
            exit_code=completed.returncode,
            stdout=stdout[:_MAX_GH_OUTPUT_CHARS],
            stderr=stderr[:_MAX_GH_OUTPUT_CHARS],
            truncated=truncated,
        )


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _validate_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value) or value in {".", ".."}:
        raise GitHubReviewError(
            GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
            f"GitHub {label} 不合法",
        )
    return value


def parse_github_remote(remote_url: str) -> GitHubRepositoryIdentity:
    value = remote_url.strip()
    if not value:
        raise GitHubReviewError(
            GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
            "项目没有可用的 origin remote",
        )

    host = ""
    path = ""
    scp_match = re.fullmatch(r"git@([^:]+):(.+)", value)
    if scp_match is not None:
        host, path = scp_match.groups()
    else:
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
                "origin remote 不是有效的 GitHub 仓库地址",
            ) from exc
        if parsed.scheme not in {"https", "ssh"}:
            raise GitHubReviewError(
                GitHubReviewErrorCode.UNSUPPORTED_GITHUB_HOST,
                "只支持 github.com remote",
            )
        host = parsed.hostname or ""
        if host.lower() != "github.com":
            raise GitHubReviewError(
                GitHubReviewErrorCode.UNSUPPORTED_GITHUB_HOST,
                "当前版本只支持 github.com，不支持 GitHub Enterprise",
            )
        expected_authority = (
            "github.com" if parsed.scheme == "https" else "git@github.com"
        )
        if (
            parsed.netloc.casefold() != expected_authority
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
                "origin remote 不是有效的 GitHub 仓库地址",
            )
        path = parsed.path

    if host.lower() != "github.com":
        raise GitHubReviewError(
            GitHubReviewErrorCode.UNSUPPORTED_GITHUB_HOST,
            "当前版本只支持 github.com，不支持 GitHub Enterprise",
        )
    pieces = [piece for piece in path.strip("/").split("/") if piece]
    if len(pieces) != 2:
        raise GitHubReviewError(
            GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
            "origin remote 不是有效的 GitHub 仓库地址",
        )
    owner = _validate_identifier(pieces[0], "owner")
    repo = pieces[1][:-4] if pieces[1].endswith(".git") else pieces[1]
    return GitHubRepositoryIdentity(
        owner=owner,
        repo=_validate_identifier(repo, "repo"),
    )


def sanitize_publication_text(value: str, workspace: Workspace) -> str:
    sanitized = redact_sensitive_patch(value)
    sanitized = sanitized.replace(str(workspace.root), "[REDACTED_PATH]")
    return _HOST_PATH.sub("[REDACTED_PATH]", sanitized)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_sha(value: Any) -> str:
    text = str(value or "")
    if not _SHA.fullmatch(text):
        raise GitHubReviewError(
            GitHubReviewErrorCode.GITHUB_API_ERROR,
            "GitHub 返回了无效的提交 SHA",
        )
    return text.lower()


class GitHubReviewStore(Protocol):
    def get_run(self, run_id: str) -> Any: ...

    def get_thread(self, thread_id: str) -> Any: ...

    def get_project(self, project_id: str) -> Any: ...

    def get_review_diff_snapshot(self, run_id: str) -> Any: ...

    def list_review_findings(self, run_id: str, **kwargs: Any) -> list[Any]: ...

    def create_github_review_publication(self, **kwargs: Any) -> Any: ...

    def get_github_review_publication(self, publication_id: str) -> Any: ...

    def list_github_review_publications(self, run_id: str) -> list[Any]: ...

    def claim_github_review_publication(self, publication_id: str) -> Any: ...

    def finish_github_review_publication(self, **kwargs: Any) -> Any: ...

    def find_published_github_review_payload(self, payload_hash: str) -> Any: ...


class GitHubApiClient:
    def __init__(
        self,
        *,
        runner: GitHubCommandRunner,
        settings: Settings,
    ) -> None:
        self.runner = runner
        self.settings = settings

    def require_authentication(self, cwd: Path) -> None:
        if not self.runner.is_installed():
            raise GitHubReviewError(
                GitHubReviewErrorCode.GH_NOT_INSTALLED,
                "宿主机未安装 GitHub CLI",
            )
        result = self.runner.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            cwd=cwd,
            timeout=self.settings.github_cli_timeout,
        )
        if result.timed_out:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_TIMEOUT,
                "检查 GitHub 登录状态超时",
            )
        if result.exit_code != 0:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_NOT_AUTHENTICATED,
                "GitHub CLI 尚未登录或认证已失效",
            )

    def api(
        self,
        endpoint: str,
        *,
        cwd: Path,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        write: bool = False,
    ) -> Any:
        self.require_authentication(cwd)
        argv = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            endpoint,
        ]
        input_text = None
        if payload is not None:
            argv.extend(["--input", "-"])
            input_text = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        result = self.runner.run(
            argv,
            cwd=cwd,
            timeout=self.settings.github_cli_timeout,
            input_text=input_text,
        )
        if result.timed_out:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_TIMEOUT,
                "GitHub 请求超时",
                result_unknown=write,
            )
        if result.truncated:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub 响应超过安全读取上限",
                result_unknown=write,
            )
        if result.exit_code != 0:
            lowered = result.stderr.lower()
            if "http 403" in lowered or "forbidden" in lowered:
                code = GitHubReviewErrorCode.PERMISSION_DENIED
                message = "当前 GitHub 账号没有发布权限"
            elif "http 404" in lowered:
                code = GitHubReviewErrorCode.PULL_REQUEST_NOT_FOUND
                message = "找不到当前仓库中的 Pull Request"
            else:
                code = GitHubReviewErrorCode.GITHUB_API_ERROR
                message = "GitHub API 请求失败"
            raise GitHubReviewError(code, message)
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub 返回了无效 JSON",
                result_unknown=write,
            ) from exc


class GitHubReviewService:
    def __init__(
        self,
        *,
        store: GitHubReviewStore,
        workspace: Workspace,
        settings: Settings,
        runner: GitHubCommandRunner | None = None,
        clock: Any = _utc_now,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.settings = settings
        self.runner = runner or GitHubCliRunner()
        self.api_client = GitHubApiClient(
            runner=self.runner,
            settings=settings,
        )
        self.clock = clock

    def capability(
        self,
        run_id: str | None = None,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if run_id is not None:
            context = self._context(run_id)
        elif project_id is not None:
            context = self._project_context(project_id)
        else:
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "缺少已注册项目上下文",
            )
        response: dict[str, Any] = {
            "gh_installed": self.runner.is_installed(),
            "authenticated": False,
            "remote_found": False,
            "publish_enabled": self.settings.github_review_publish_enabled,
            "can_publish": False,
            "reason": None,
            "repository": None,
            "current_user": None,
            "pull_requests": [],
        }
        if not response["gh_installed"]:
            response["reason"] = GitHubReviewErrorCode.GH_NOT_INSTALLED.value
            return response
        try:
            identity = self._repository_identity(context)
            response["remote_found"] = True
            response["repository"] = identity.full_name
            self.api_client.require_authentication(context["cwd"])
            response["authenticated"] = True
            user = self.api_client.api("user", cwd=context["cwd"])
            if not isinstance(user, dict):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub 用户响应格式无效",
                )
            response["current_user"] = str(user.get("login") or "") or None
            repository = self.api_client.api(
                f"repos/{identity.owner}/{identity.repo}",
                cwd=context["cwd"],
            )
            if not isinstance(repository, dict):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub 仓库响应格式无效",
                )
            permissions = repository.get("permissions") or {}
            if not isinstance(permissions, dict):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub 仓库权限响应格式无效",
                )
            has_access = bool(
                permissions.get("pull")
                or permissions.get("triage")
                or permissions.get("push")
                or permissions.get("maintain")
                or permissions.get("admin")
            )
            raw_prs = self.api_client.api(
                f"repos/{identity.owner}/{identity.repo}/pulls?state=open&per_page=20",
                cwd=context["cwd"],
            )
            if not isinstance(raw_prs, list):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub Pull Request 列表格式无效",
                )
            response["pull_requests"] = [
                asdict(self._parse_pr(item, identity))
                for item in raw_prs
                if isinstance(item, dict)
            ]
            if len(response["pull_requests"]) != len(raw_prs):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub Pull Request 列表包含无效项目",
                )
            if not self.settings.github_review_publish_enabled:
                response["reason"] = (
                    GitHubReviewErrorCode.PUBLISHING_DISABLED.value
                )
            elif not has_access:
                response["reason"] = GitHubReviewErrorCode.PERMISSION_DENIED.value
            else:
                response["can_publish"] = True
        except GitHubReviewError as exc:
            response["reason"] = exc.code.value
        return response

    def collect_pull_request_diff(
        self,
        *,
        run_id: str,
        pr_number: int,
        limits: ReviewDiffLimits,
    ) -> ReviewDiff:
        context = self._context(run_id)
        identity = self._repository_identity(context)
        pull_request = self._get_pull_request(identity, pr_number, context["cwd"])
        raw_files = self.api_client.api(
            f"repos/{identity.owner}/{identity.repo}/pulls/{pr_number}/files?per_page=100",
            cwd=context["cwd"],
        )
        if not isinstance(raw_files, list):
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub PR 文件列表格式无效",
            )
        if any(not isinstance(item, dict) for item in raw_files):
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub PR 文件列表包含无效项目",
            )
        return self._build_pull_request_diff(
            context=context,
            identity=identity,
            pull_request=pull_request,
            raw_files=raw_files,
            limits=limits,
        )

    def prepare(
        self,
        *,
        run_id: str,
        pr_number: int,
        selected_finding_ids: Sequence[str],
        event: GitHubReviewEvent,
        summary: str | None,
    ) -> dict[str, Any]:
        if not self.settings.github_review_publish_enabled:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PUBLISHING_DISABLED,
                "GitHub Review 发布功能未启用",
            )
        context = self._context(run_id)
        snapshot = self.store.get_review_diff_snapshot(run_id)
        if snapshot is None or snapshot.diff.source is not ReviewSource.PULL_REQUEST:
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "本地工作树 Review 不能发布到 GitHub，请先创建 PR 并重新审查",
            )
        review_diff = snapshot.diff
        if review_diff.pr_number != pr_number:
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "Pull Request 与审查快照不一致",
            )
        identity = self._repository_identity(context)
        if review_diff.repository != identity.full_name:
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "GitHub 仓库身份与审查快照不一致",
            )
        pull_request = self._get_pull_request(identity, pr_number, context["cwd"])
        self._validate_publishable_pr(pull_request, review_diff.head_revision)

        identifiers = list(dict.fromkeys(selected_finding_ids))
        findings_by_id = {
            finding.id: finding
            for finding in self.store.list_review_findings(run_id)
        }
        missing = [identifier for identifier in identifiers if identifier not in findings_by_id]
        if missing:
            raise GitHubReviewError(
                GitHubReviewErrorCode.FINDING_NOT_PUBLISHABLE,
                "选中的 Finding 不属于当前 Review",
            )

        inline_comments: list[dict[str, Any]] = []
        summary_findings: list[dict[str, Any]] = []
        skipped_findings: list[dict[str, Any]] = []
        warnings: list[str] = []
        selected: list[ReviewFindingSnapshot] = []
        for identifier in identifiers:
            finding = findings_by_id[identifier]
            if finding.status is not ReviewFindingStatus.OPEN:
                skipped_findings.append(
                    {
                        "finding_id": finding.id,
                        "title": finding.title,
                        "reason": "仅发布 open 状态的问题",
                    }
                )
                continue
            if (
                finding.review_diff_hash != review_diff.content_hash
                or finding.base_revision != review_diff.base_revision
                or finding.head_revision != review_diff.head_revision
            ):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.FINDING_NOT_PUBLISHABLE,
                    "Finding 与当前 PR Diff 快照不一致",
                )
            selected.append(finding)
            mapped = self._map_finding(finding, review_diff)
            if mapped is None:
                summary_findings.append(
                    {
                        "finding_id": finding.id,
                        "title": finding.title,
                        "reason": "文件级或全局问题将进入 Review 总结",
                    }
                )
                continue
            if len(inline_comments) >= self.settings.github_review_max_inline_comments:
                skipped_findings.append(
                    {
                        "finding_id": finding.id,
                        "title": finding.title,
                        "reason": "超过单次行内评论上限",
                    }
                )
                continue
            inline_comments.append(mapped)

        if skipped_findings:
            warnings.append("部分 Finding 不会作为行内评论发布，请检查跳过原因。")
        if review_diff.truncated:
            warnings.append("PR Diff 快照已截断，只允许发布 Reviewer 实际看到的行。")

        body = self._summary_body(
            selected=selected,
            summary_finding_ids={item["finding_id"] for item in summary_findings},
            user_summary=summary,
            inline_count=len(inline_comments),
        )
        github_payload = {
            "commit_id": review_diff.head_revision,
            "body": body,
            "event": event.value,
            "comments": [
                {key: value for key, value in item.items() if key != "finding_id"}
                for item in inline_comments
            ],
        }
        payload_hash = _json_hash(
            {
                "repository": identity.full_name,
                "pr_number": pr_number,
                "payload": github_payload,
            }
        )
        if self.store.find_published_github_review_payload(payload_hash) is not None:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PUBLICATION_ALREADY_PUBLISHED,
                "相同内容已经成功发布，不能重复发布",
            )
        finding_state_hash = self._finding_state_hash(selected)
        prepared_at = self.clock()
        expires_at = prepared_at + timedelta(
            seconds=self.settings.github_publication_ttl_seconds
        )
        publication_id = str(uuid.uuid4())
        preview = {
            "publication_id": publication_id,
            "repository": identity.full_name,
            "pr_number": pr_number,
            "pr_title": pull_request.title,
            "pr_url": pull_request.url,
            "base_sha": pull_request.base_sha,
            "head_sha": pull_request.head_sha,
            "event": event.value,
            "inline_comments": inline_comments,
            "summary_comments": summary_findings,
            "summary_body": body,
            "skipped_findings": skipped_findings,
            "warnings": warnings,
            "payload_hash": payload_hash,
            "expires_at": expires_at.isoformat(),
        }
        self.store.create_github_review_publication(
            publication_id=publication_id,
            run_id=run_id,
            repository=identity.full_name,
            pr_number=pr_number,
            base_sha=pull_request.base_sha,
            head_sha=pull_request.head_sha,
            event=event,
            selected_finding_ids=tuple(finding.id for finding in selected),
            finding_state_hash=finding_state_hash,
            payload=github_payload,
            preview=preview,
            payload_hash=payload_hash,
            prepared_at=prepared_at,
            expires_at=expires_at,
        )
        logger.info(
            "GitHub Review publication prepared: publication_id=%s run_id=%s "
            "repository=%s pr_number=%s head_sha=%s payload_hash=%s status=prepared",
            publication_id,
            run_id,
            identity.full_name,
            pr_number,
            pull_request.head_sha,
            payload_hash,
        )
        return preview

    def publish(self, *, run_id: str, publication_id: str) -> Any:
        if not self.settings.github_review_publish_enabled:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PUBLISHING_DISABLED,
                "GitHub Review 发布功能未启用",
            )
        publication = self.store.get_github_review_publication(publication_id)
        if publication is None or publication.run_id != run_id:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PUBLICATION_CHANGED,
                "发布预览不存在或不属于当前 Run",
            )
        if publication.status is GitHubPublicationStatus.PUBLISHED:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PUBLICATION_ALREADY_PUBLISHED,
                "当前 Review 已经发布",
            )
        if publication.status is GitHubPublicationStatus.UNKNOWN:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN,
                "上次发布结果不确定，禁止自动重试",
            )
        if publication.expires_at <= self.clock():
            raise GitHubReviewError(
                GitHubReviewErrorCode.PUBLICATION_EXPIRED,
                "发布预览已过期，请重新预览",
            )
        try:
            publication = self.store.claim_github_review_publication(
                publication_id
            )
        except ValueError as exc:
            codes = {
                "publication_already_published": (
                    GitHubReviewErrorCode.PUBLICATION_ALREADY_PUBLISHED
                ),
                "publication_in_progress": (
                    GitHubReviewErrorCode.PUBLICATION_IN_PROGRESS
                ),
                "publication_result_unknown": (
                    GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN
                ),
            }
            code = codes.get(str(exc), GitHubReviewErrorCode.PUBLICATION_CHANGED)
            raise GitHubReviewError(code, "当前 publication 状态不允许发布") from exc
        try:
            context = self._context(run_id)
            snapshot = self.store.get_review_diff_snapshot(run_id)
            if snapshot is None or snapshot.diff.source is not ReviewSource.PULL_REQUEST:
                raise GitHubReviewError(
                    GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                    "Review 快照不再允许发布",
                )
            review_diff = snapshot.diff
            if (
                review_diff.repository != publication.repository
                or review_diff.pr_number != publication.pr_number
                or review_diff.base_revision != publication.base_sha
                or review_diff.head_revision != publication.head_sha
            ):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.PUBLICATION_CHANGED,
                    "Review 快照与发布预览不再一致",
                )
            identity = self._repository_identity(context)
            if identity.full_name != publication.repository:
                raise GitHubReviewError(
                    GitHubReviewErrorCode.PUBLICATION_CHANGED,
                    "GitHub 仓库身份已经变化",
                )
            findings_by_id = {
                finding.id: finding
                for finding in self.store.list_review_findings(run_id)
            }
            selected = [
                findings_by_id[identifier]
                for identifier in publication.selected_finding_ids
                if identifier in findings_by_id
            ]
            if (
                len(selected) != len(publication.selected_finding_ids)
                or self._finding_state_hash(selected)
                != publication.finding_state_hash
            ):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.PUBLICATION_CHANGED,
                    "Finding 内容或状态已变化，请重新预览",
                )
            for finding in selected:
                self._map_finding(finding, review_diff)
            expected_hash = _json_hash(
                {
                    "repository": publication.repository,
                    "pr_number": publication.pr_number,
                    "payload": publication.payload,
                }
            )
            if expected_hash != publication.payload_hash:
                raise GitHubReviewError(
                    GitHubReviewErrorCode.PUBLICATION_CHANGED,
                    "发布载荷已经变化，请重新预览",
                )
            pull_request = self._get_pull_request(
                identity,
                publication.pr_number,
                context["cwd"],
            )
            self._validate_publishable_pr(pull_request, publication.head_sha)
            response = self.api_client.api(
                f"repos/{identity.owner}/{identity.repo}/pulls/"
                f"{publication.pr_number}/reviews",
                cwd=context["cwd"],
                method="POST",
                payload=publication.payload,
                write=True,
            )
            if not isinstance(response, dict):
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub Review 返回格式无效",
                    result_unknown=True,
                )
            review_id = str(response.get("id") or "")
            if not review_id.isdigit():
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub Review 响应缺少有效 ID",
                    result_unknown=True,
                )
            links = response.get("_links") or {}
            if not isinstance(links, dict):
                links = {}
            html_link = links.get("html") or {}
            if not isinstance(html_link, dict):
                html_link = {}
            raw_review_url = str(
                response.get("html_url") or html_link.get("href") or ""
            )
            review_url = self._validated_review_url(
                raw_review_url,
                identity,
                publication.pr_number,
            )
            if review_url is None:
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub Review 响应缺少可信 URL",
                    result_unknown=True,
                )
            raw_user = response.get("user") or {}
            if not isinstance(raw_user, dict):
                raw_user = {}
            github_user = sanitize_publication_text(
                str(raw_user.get("login") or "")[:100],
                self.workspace,
            )
            if not github_user:
                raise GitHubReviewError(
                    GitHubReviewErrorCode.GITHUB_API_ERROR,
                    "GitHub Review 响应缺少发布用户",
                    result_unknown=True,
                )
            result = self.store.finish_github_review_publication(
                publication_id=publication_id,
                status=GitHubPublicationStatus.PUBLISHED,
                github_review_id=review_id,
                github_review_url=review_url,
                github_user=github_user,
                published_at=self.clock(),
                error_code=None,
                error_message=None,
            )
            logger.info(
                "GitHub Review publication completed: publication_id=%s run_id=%s "
                "repository=%s pr_number=%s head_sha=%s payload_hash=%s status=published",
                publication_id,
                run_id,
                publication.repository,
                publication.pr_number,
                publication.head_sha,
                publication.payload_hash,
            )
            return result
        except GitHubReviewError as exc:
            next_status = (
                GitHubPublicationStatus.UNKNOWN
                if exc.result_unknown
                else GitHubPublicationStatus.FAILED
            )
            self.store.finish_github_review_publication(
                publication_id=publication_id,
                status=next_status,
                github_review_id=None,
                github_review_url=None,
                github_user=None,
                published_at=None,
                error_code=(
                    GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN.value
                    if exc.result_unknown
                    else exc.code.value
                ),
                error_message=(
                    "GitHub 返回结果不确定，请人工检查 PR"
                    if exc.result_unknown
                    else exc.message
                ),
            )
            logger.warning(
                "GitHub Review publication ended: publication_id=%s run_id=%s "
                "repository=%s pr_number=%s head_sha=%s payload_hash=%s status=%s",
                publication_id,
                run_id,
                publication.repository,
                publication.pr_number,
                publication.head_sha,
                publication.payload_hash,
                next_status.value,
            )
            if exc.result_unknown:
                raise GitHubReviewError(
                    GitHubReviewErrorCode.PUBLICATION_RESULT_UNKNOWN,
                    "GitHub 返回结果不确定，请人工检查 PR，禁止自动重试",
                ) from exc
            raise

    def _context(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "Run 不存在",
            )
        thread = self.store.get_thread(run.thread_id)
        project = self.store.get_project(thread.project_id) if thread else None
        if project is None:
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "Run 没有关联的已注册项目",
            )
        return self._project_context(
            project.project_id,
            run=run,
            thread=thread,
        )

    def _project_context(
        self,
        project_id: str,
        *,
        run: Any = None,
        thread: Any = None,
    ) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if project is None:
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "已注册项目不存在",
            )
        cwd = self.workspace.resolve(project.virtual_path)
        if not cwd.is_dir():
            raise GitHubReviewError(
                GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
                "已注册项目目录不存在",
            )
        return {"run": run, "thread": thread, "project": project, "cwd": cwd}

    def _repository_identity(
        self,
        context: dict[str, Any],
    ) -> GitHubRepositoryIdentity:
        result = CommandRunner(
            self.workspace,
            allowed_commands={"git"},
        ).run(
            ["git", "config", "--get-all", "remote.origin.url"],
            cwd=context["project"].virtual_path,
            timeout=min(self.settings.github_cli_timeout, 30.0),
        )
        if result.timed_out:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_TIMEOUT,
                "读取 GitHub remote 超时",
            )
        urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.exit_code != 0 or not urls:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
                "已注册项目没有 origin remote",
            )
        identities = [parse_github_remote(url) for url in urls]
        first = identities[0]
        if any(identity != first for identity in identities[1:]):
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
                "origin 包含多个不一致的 GitHub 仓库地址",
            )
        return first

    def _get_pull_request(
        self,
        identity: GitHubRepositoryIdentity,
        pr_number: int,
        cwd: Path,
    ) -> GitHubPullRequest:
        raw = self.api_client.api(
            f"repos/{identity.owner}/{identity.repo}/pulls/{pr_number}",
            cwd=cwd,
        )
        if not isinstance(raw, dict):
            raise GitHubReviewError(
                GitHubReviewErrorCode.PULL_REQUEST_NOT_FOUND,
                "找不到当前仓库中的 Pull Request",
            )
        pull_request = self._parse_pr(raw, identity)
        if pull_request.pr_number != pr_number:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PULL_REQUEST_NOT_FOUND,
                "GitHub 返回的 Pull Request 编号与请求不一致",
            )
        return pull_request

    def _parse_pr(
        self,
        raw: dict[str, Any],
        identity: GitHubRepositoryIdentity,
    ) -> GitHubPullRequest:
        base = raw.get("base") or {}
        head = raw.get("head") or {}
        user = raw.get("user") or {}
        if (
            not isinstance(base, dict)
            or not isinstance(head, dict)
            or not isinstance(user, dict)
        ):
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub Pull Request 响应格式无效",
            )
        base_repository = base.get("repo") or {}
        if not isinstance(base_repository, dict):
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub Pull Request 仓库响应格式无效",
            )
        repository = str(base_repository.get("full_name") or "")
        if repository.casefold() != identity.full_name.casefold():
            raise GitHubReviewError(
                GitHubReviewErrorCode.PULL_REQUEST_NOT_FOUND,
                "Pull Request 不属于当前已注册项目",
            )
        try:
            number = int(raw["number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub Pull Request 编号无效",
            ) from exc
        try:
            changed_files = max(int(raw.get("changed_files") or 0), 0)
        except (TypeError, ValueError):
            changed_files = 0
        return GitHubPullRequest(
            pr_number=number,
            title=sanitize_publication_text(
                str(raw.get("title") or "")[:500],
                self.workspace,
            ),
            url=f"https://github.com/{identity.full_name}/pull/{number}",
            state=str(raw.get("state") or "").lower(),
            is_draft=bool(raw.get("draft")),
            base_branch=sanitize_publication_text(
                str(base.get("ref") or "")[:255],
                self.workspace,
            ),
            head_branch=sanitize_publication_text(
                str(head.get("ref") or "")[:255],
                self.workspace,
            ),
            base_sha=_validated_sha(base.get("sha")),
            head_sha=_validated_sha(head.get("sha")),
            author=sanitize_publication_text(
                str(user.get("login") or "")[:100],
                self.workspace,
            ),
            repository=identity.full_name,
            changed_files=changed_files,
        )

    @staticmethod
    def _validated_review_url(
        value: str,
        identity: GitHubRepositoryIdentity,
        pr_number: int,
    ) -> str | None:
        if not value:
            return None
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        expected_path = f"/{identity.owner}/{identity.repo}/pull/{pr_number}"
        fragment_match = re.fullmatch(
            r"pullrequestreview-(?P<review_id>[1-9][0-9]*)",
            parsed.fragment,
        )
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "github.com"
            or parsed.netloc.casefold() != "github.com"
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or fragment_match is None
        ):
            return None
        review_id = fragment_match.group("review_id")
        return (
            f"https://github.com/{identity.owner}/{identity.repo}/pull/"
            f"{pr_number}#pullrequestreview-{review_id}"
        )

    @staticmethod
    def _validate_publishable_pr(
        pull_request: GitHubPullRequest,
        expected_head_sha: str | None,
    ) -> None:
        if pull_request.state != "open":
            raise GitHubReviewError(
                GitHubReviewErrorCode.PULL_REQUEST_CLOSED,
                "Pull Request 已关闭，不能发布 Review",
            )
        if pull_request.is_draft:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PULL_REQUEST_DRAFT,
                "Draft Pull Request 暂不允许发布 Review",
            )
        if pull_request.head_sha != expected_head_sha:
            raise GitHubReviewError(
                GitHubReviewErrorCode.PULL_REQUEST_CHANGED,
                "Pull Request 已发生变化，请重新审查最新提交后再发布。",
            )

    def _build_pull_request_diff(
        self,
        *,
        context: dict[str, Any],
        identity: GitHubRepositoryIdentity,
        pull_request: GitHubPullRequest,
        raw_files: list[dict[str, Any]],
        limits: ReviewDiffLimits,
    ) -> ReviewDiff:
        reasons: list[ReviewTruncationReason] = []
        if pull_request.changed_files > len(raw_files):
            reasons.append(ReviewTruncationReason.MAX_FILES)
        if len(raw_files) > limits.max_files:
            raw_files = raw_files[:limits.max_files]
            reasons.append(ReviewTruncationReason.MAX_FILES)
        files: list[ReviewDiffFile] = []
        total_chars = 0
        total_changed = 0
        project_path = context["project"].virtual_path
        for item in raw_files:
            file = self._github_file(
                item,
                project_path=project_path,
                limits=limits,
                remaining_chars=max(limits.max_total_patch_chars - total_chars, 0),
                remaining_changed=max(
                    limits.max_total_changed_lines - total_changed,
                    0,
                ),
            )
            files.append(file)
            total_chars += len(file.patch or "")
            total_changed += file.additions + file.deletions
            if file.truncation_reason and file.truncation_reason not in reasons:
                reasons.append(file.truncation_reason)
        created_at = self.clock()
        hash_payload = {
            "source": ReviewSource.PULL_REQUEST.value,
            "repository": identity.full_name,
            "pr_number": pull_request.pr_number,
            "base_sha": pull_request.base_sha,
            "head_sha": pull_request.head_sha,
            "files": [
                {
                    "old_path": file.old_path,
                    "new_path": file.new_path,
                    "change_type": file.change_type.value,
                    "binary": file.binary,
                    "submodule": file.submodule,
                    "patch": file.patch,
                    "truncated": file.truncated,
                    "reason": (
                        file.truncation_reason.value
                        if file.truncation_reason
                        else None
                    ),
                    "redacted": file.redacted,
                }
                for file in files
            ],
        }
        return ReviewDiff(
            scope=ReviewScope.ALL,
            repository_virtual_path=project_path,
            base_revision=pull_request.base_sha,
            head_revision=pull_request.head_sha,
            files=tuple(files),
            file_count=len(files),
            total_additions=sum(file.additions for file in files),
            total_deletions=sum(file.deletions for file in files),
            truncated=bool(reasons),
            truncation_reasons=tuple(reasons),
            content_hash=_json_hash(hash_payload),
            created_at=created_at,
            redacted=any(file.redacted for file in files),
            source=ReviewSource.PULL_REQUEST,
            repository=identity.full_name,
            pr_number=pull_request.pr_number,
        )

    def _github_file(
        self,
        item: dict[str, Any],
        *,
        project_path: str,
        limits: ReviewDiffLimits,
        remaining_chars: int,
        remaining_changed: int,
    ) -> ReviewDiffFile:
        filename = self._relative_path(str(item.get("filename") or ""))
        previous = item.get("previous_filename")
        previous_path = (
            self._relative_path(str(previous)) if previous is not None else filename
        )
        status = str(item.get("status") or "modified")
        change_type = {
            "added": ReviewChangeType.ADDED,
            "removed": ReviewChangeType.DELETED,
            "renamed": ReviewChangeType.RENAMED,
            "copied": ReviewChangeType.COPIED,
            "changed": ReviewChangeType.MODIFIED,
            "modified": ReviewChangeType.MODIFIED,
        }.get(status, ReviewChangeType.MODIFIED)
        old_relative = None if change_type is ReviewChangeType.ADDED else previous_path
        new_relative = None if change_type is ReviewChangeType.DELETED else filename
        raw_patch = item.get("patch")
        try:
            api_additions = max(int(item.get("additions") or 0), 0)
            api_deletions = max(int(item.get("deletions") or 0), 0)
        except (TypeError, ValueError) as exc:
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub PR 文件统计格式无效",
            ) from exc
        binary = raw_patch is None and api_additions == 0 and api_deletions == 0
        submodule = False
        patch: str | None = None
        reason: ReviewTruncationReason | None = None
        redacted = False
        if isinstance(raw_patch, str) and raw_patch.startswith("@@ "):
            prefix = (
                f"diff --git a/{old_relative or filename} b/{new_relative or filename}\n"
                f"--- {'/dev/null' if old_relative is None else f'a/{old_relative}'}\n"
                f"+++ {'/dev/null' if new_relative is None else f'b/{new_relative}'}\n"
            )
            untrusted_patch = prefix + raw_patch + ("" if raw_patch.endswith("\n") else "\n")
            cleaned = sanitize_publication_text(untrusted_patch, self.workspace)
            redacted = cleaned != untrusted_patch
            patch, reason = self._limit_patch(
                cleaned,
                limits=limits,
                remaining_chars=remaining_chars,
                remaining_changed=remaining_changed,
            )
            submodule = "Subproject commit " in patch or " 160000" in patch
        elif not binary:
            reason = ReviewTruncationReason.GITHUB_PATCH_UNAVAILABLE
        parsed = parse_unified_patch(patch or "")
        return ReviewDiffFile(
            old_path=(
                str(PurePosixPath(project_path) / old_relative)
                if old_relative is not None
                else None
            ),
            new_path=(
                str(PurePosixPath(project_path) / new_relative)
                if new_relative is not None
                else None
            ),
            change_type=change_type,
            binary=binary,
            submodule=submodule,
            additions=(parsed.additions if patch is not None else api_additions),
            deletions=(parsed.deletions if patch is not None else api_deletions),
            patch=None if binary else patch,
            truncated=reason is not None,
            truncation_reason=reason,
            changed_new_lines=parsed.changed_new_lines,
            changed_old_lines=parsed.changed_old_lines,
            redacted=redacted,
        )

    @staticmethod
    def _relative_path(path: str) -> str:
        pure = PurePosixPath(path)
        if (
            not path
            or "\x00" in path
            or "\\" in path
            or pure.is_absolute()
            or ".." in pure.parts
        ):
            raise GitHubReviewError(
                GitHubReviewErrorCode.GITHUB_API_ERROR,
                "GitHub 返回了不安全的文件路径",
            )
        return str(pure)

    @staticmethod
    def _limit_patch(
        patch: str,
        *,
        limits: ReviewDiffLimits,
        remaining_chars: int,
        remaining_changed: int,
    ) -> tuple[str, ReviewTruncationReason | None]:
        output: list[str] = []
        chars = 0
        changed = 0
        for line in patch.splitlines(keepends=True):
            is_changed = (
                line.startswith("+") and not line.startswith("+++")
            ) or (line.startswith("-") and not line.startswith("---"))
            next_chars = chars + len(line)
            next_changed = changed + int(is_changed)
            if next_chars > limits.max_file_patch_chars:
                return "".join(output), ReviewTruncationReason.FILE_PATCH_CHARS
            if next_changed > limits.max_file_changed_lines:
                return "".join(output), ReviewTruncationReason.FILE_CHANGED_LINES
            if next_chars > remaining_chars:
                return "".join(output), ReviewTruncationReason.TOTAL_PATCH_CHARS
            if next_changed > remaining_changed:
                return "".join(output), ReviewTruncationReason.TOTAL_CHANGED_LINES
            output.append(line)
            chars = next_chars
            changed = next_changed
        return "".join(output), None

    def _map_finding(
        self,
        finding: ReviewFindingSnapshot,
        review_diff: ReviewDiff,
    ) -> dict[str, Any] | None:
        if finding.file_path is None or finding.start_line is None:
            return None
        if finding.end_line is None or finding.line_side is None:
            return None
        candidates = [
            file
            for file in review_diff.files
            if (
                finding.line_side is ReviewLineSide.NEW
                and file.new_path == finding.file_path
            )
            or (
                finding.line_side is ReviewLineSide.OLD
                and file.old_path == finding.file_path
            )
        ]
        if len(candidates) != 1:
            raise GitHubReviewError(
                GitHubReviewErrorCode.FINDING_NOT_PUBLISHABLE,
                "Finding 文件不属于当前 PR Diff",
            )
        file = candidates[0]
        if file.binary or file.submodule:
            return None
        requested = set(range(finding.start_line, finding.end_line + 1))
        visible = visible_lines_for_file(file, finding.line_side)
        changed = set(
            file.changed_new_lines
            if finding.line_side is ReviewLineSide.NEW
            else file.changed_old_lines
        )
        if not requested <= visible or not requested & changed:
            raise GitHubReviewError(
                GitHubReviewErrorCode.FINDING_NOT_PUBLISHABLE,
                "Finding 行号不在 Reviewer 实际看到的 PR Diff 范围内",
            )
        github_path = file.new_path or file.old_path
        if github_path is None:
            raise GitHubReviewError(
                GitHubReviewErrorCode.FINDING_NOT_PUBLISHABLE,
                "Finding 文件没有可发布的 GitHub 路径",
            )
        relative = self._relative_from_virtual(
            github_path,
            review_diff.repository_virtual_path,
        )
        body = self._finding_body(finding)
        mapped: dict[str, Any] = {
            "finding_id": finding.id,
            "path": relative,
            "line": finding.end_line,
            "side": "RIGHT" if finding.line_side is ReviewLineSide.NEW else "LEFT",
            "body": body,
        }
        if finding.start_line != finding.end_line:
            mapped["start_line"] = finding.start_line
            mapped["start_side"] = mapped["side"]
        return mapped

    @staticmethod
    def _relative_from_virtual(path: str, project_path: str) -> str:
        pure = PurePosixPath(path)
        project = PurePosixPath(project_path)
        try:
            relative = pure.relative_to(project)
        except ValueError as exc:
            raise GitHubReviewError(
                GitHubReviewErrorCode.FINDING_NOT_PUBLISHABLE,
                "Finding 路径不属于当前项目",
            ) from exc
        return GitHubReviewService._relative_path(str(relative))

    def _finding_body(self, finding: ReviewFindingSnapshot) -> str:
        lines = [
            f"**[{finding.severity.value.upper()}] {finding.title}**",
            "",
            f"类别：`{finding.category.value}`",
            "",
            finding.description,
        ]
        if finding.suggestion:
            lines.extend(["", f"建议：{finding.suggestion}"])
        body = sanitize_publication_text("\n".join(lines), self.workspace)
        return self._limited(body, self.settings.github_review_max_comment_chars)

    def _summary_body(
        self,
        *,
        selected: list[ReviewFindingSnapshot],
        summary_finding_ids: set[str],
        user_summary: str | None,
        inline_count: int,
    ) -> str:
        lines = [
            f"Tang Agent Review：{inline_count} 条行内评论，"
            f"{len(summary_finding_ids)} 条总结问题。"
        ]
        if user_summary:
            lines.extend(["", sanitize_publication_text(user_summary, self.workspace)])
        for finding in selected:
            if finding.id not in summary_finding_ids:
                continue
            location = "全局" if finding.file_path is None else PurePosixPath(finding.file_path).name
            lines.extend(
                [
                    "",
                    f"- [{finding.severity.value.upper()}] {finding.title} ({location})",
                    f"  {finding.description}",
                ]
            )
        return self._limited(
            sanitize_publication_text("\n".join(lines), self.workspace),
            self.settings.github_review_max_summary_chars,
        )

    @staticmethod
    def _limited(value: str, maximum: int) -> str:
        if len(value) <= maximum:
            return value
        return value[: max(maximum - 3, 0)] + "..."

    @staticmethod
    def _finding_state_hash(findings: Sequence[ReviewFindingSnapshot]) -> str:
        return _json_hash(
            [
                {
                    "id": finding.id,
                    "status": finding.status.value,
                    "severity": finding.severity.value,
                    "category": finding.category.value,
                    "file_path": finding.file_path,
                    "start_line": finding.start_line,
                    "end_line": finding.end_line,
                    "line_side": (
                        finding.line_side.value if finding.line_side else None
                    ),
                    "title": finding.title,
                    "description": finding.description,
                    "suggestion": finding.suggestion,
                    "fingerprint": finding.fingerprint,
                    "review_diff_hash": finding.review_diff_hash,
                    "base_revision": finding.base_revision,
                    "head_revision": finding.head_revision,
                }
                for finding in findings
            ]
        )
