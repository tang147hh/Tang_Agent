from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.app import create_app
from app.backends.workspace import Workspace
from app.core.conversation import MessageRole, RunStatus
from app.core.conversation_runtime import run_conversation_agent
from app.core.review import (
    ReviewCategory,
    ReviewFindingService,
    ReviewFindingStatus,
    ReviewOutputError,
    ReviewSeverity,
    parse_reviewer_output,
)
from app.core.task_intent import TaskKind
from app.core.task_runtime import TaskRegistry
from app.store import SQLiteProjectThreadStore


def _payload(**overrides):
    finding = {
        "severity": "high",
        "category": "correctness",
        "file_path": "backend/app.py",
        "start_line": 10,
        "end_line": 12,
        "title": "错误分支不会返回",
        "description": "输入为空时继续执行，随后会访问不存在的值。",
        "suggestion": "在访问前返回校验错误。",
    }
    finding.update(overrides)
    return {"findings": [finding], "summary": "发现一个问题。"}


@pytest.fixture
def review_context(tmp_path: Path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_layout()
    (workspace.root / "projects" / "demo" / "backend").mkdir(parents=True)
    store = SQLiteProjectThreadStore(tmp_path / "tasks.sqlite")
    project = store.create_project(
        name="Demo",
        virtual_path="/projects/demo",
    )
    thread = store.create_thread(
        project_id=project.project_id,
        title="Review",
    )
    run = store.create_run(
        thread_id=thread.thread_id,
        task_kind=TaskKind.ANALYSIS,
    )
    return workspace, store, thread, run


def test_valid_model_output_becomes_system_owned_finding(review_context) -> None:
    workspace, store, _, run = review_context
    result = ReviewFindingService(store, workspace).save_model_output(
        run_id=run.run_id,
        raw_output=json.dumps(_payload(), ensure_ascii=False),
    )

    assert result.created_count == 1
    finding = result.findings[0]
    assert finding.id
    assert finding.run_id == run.run_id
    assert finding.fingerprint
    assert finding.status is ReviewFindingStatus.OPEN
    assert finding.file_path == "/projects/demo/backend/app.py"
    assert finding.created_at == finding.updated_at


def test_empty_findings_and_markdown_json_are_accepted(review_context) -> None:
    workspace, store, _, run = review_context
    result = ReviewFindingService(store, workspace).save_model_output(
        run_id=run.run_id,
        raw_output='```json\n{"findings": [], "summary": "没有问题。"}\n```',
    )
    assert result.findings == ()
    assert result.created_count == 0


@pytest.mark.parametrize(
    ("raw_output", "message"),
    [
        ("not json", "不是合法 JSON"),
        (_payload(severity="urgent"), "severity"),
        (_payload(category="style"), "category"),
        (_payload(title="   "), "title"),
        (_payload(description=" "), "description"),
        (_payload(start_line=0), "start_line"),
        (_payload(start_line=20, end_line=19), "end_line"),
        (
            _payload(file_path=None, start_line=1, end_line=1),
            "必须同时提供",
        ),
        (_payload(id="model-id"), "Extra inputs"),
        (_payload(title="x" * 201), "at most 200"),
        (_payload(description="x" * 10_001), "at most 10000"),
    ],
)
def test_invalid_model_output_is_rejected_as_a_batch(
    raw_output,
    message: str,
) -> None:
    with pytest.raises(ReviewOutputError, match=message):
        parse_reviewer_output(raw_output)


@pytest.mark.parametrize(
    "file_path",
    [
        "../outside.py",
        "/Users/tang/private.py",
        r"C:\Users\tang\private.py",
        "C:/Users/tang/private.py",
        "/projects/other/file.py",
    ],
)
def test_unsafe_or_cross_project_path_is_never_saved(
    review_context,
    file_path: str,
) -> None:
    workspace, store, _, run = review_context
    with pytest.raises(ReviewOutputError):
        ReviewFindingService(store, workspace).save_model_output(
            run_id=run.run_id,
            raw_output=_payload(file_path=file_path),
        )
    assert store.list_review_findings(run.run_id) == []


def test_same_run_deduplicates_but_different_runs_do_not(review_context) -> None:
    workspace, store, thread, run = review_context
    service = ReviewFindingService(store, workspace)
    duplicated = _payload()
    duplicated["findings"].append(dict(duplicated["findings"][0]))

    first = service.save_model_output(
        run_id=run.run_id,
        raw_output=duplicated,
    )
    second = service.save_model_output(
        run_id=run.run_id,
        raw_output=_payload(),
    )
    store.mark_run_running(run.run_id)
    store.complete_run(run.run_id)
    other_run = store.create_run(thread_id=thread.thread_id)
    third = service.save_model_output(
        run_id=other_run.run_id,
        raw_output=_payload(),
    )

    assert first.created_count == 1
    assert first.duplicate_count == 1
    assert second.created_count == 0
    assert len(store.list_review_findings(run.run_id)) == 1
    assert third.created_count == 1
    assert third.findings[0].run_id == other_run.run_id


def test_missing_run_cannot_create_finding(review_context) -> None:
    workspace, store, _, _ = review_context
    with pytest.raises(KeyError, match="Run 不存在"):
        ReviewFindingService(store, workspace).save_model_output(
            run_id="missing-run",
            raw_output=_payload(),
        )


def test_filters_stable_sort_and_status_only_update(review_context) -> None:
    workspace, store, _, run = review_context
    payload = _payload()
    payload["findings"] = [
        _payload(
            severity="low",
            category="testing",
            file_path="z.py",
            start_line=9,
            end_line=9,
            title="缺少回归测试",
        )["findings"][0],
        _payload(
            severity="critical",
            category="security",
            file_path="a.py",
            start_line=2,
            end_line=2,
            title="认证可以绕过",
        )["findings"][0],
        _payload(
            severity="high",
            category="correctness",
            file_path="b.py",
            start_line=3,
            end_line=3,
            title="返回值错误",
        )["findings"][0],
    ]
    saved = ReviewFindingService(store, workspace).save_model_output(
        run_id=run.run_id,
        raw_output=payload,
    )

    assert [item.severity for item in saved.findings] == [
        ReviewSeverity.CRITICAL,
        ReviewSeverity.HIGH,
        ReviewSeverity.LOW,
    ]
    original = saved.findings[0]
    updated = store.update_review_finding_status(
        run_id=run.run_id,
        finding_id=original.id,
        status=ReviewFindingStatus.RESOLVED,
    )
    assert updated.status is ReviewFindingStatus.RESOLVED
    assert updated.title == original.title
    assert updated.file_path == original.file_path
    assert updated.fingerprint == original.fingerprint
    assert store.list_review_findings(
        run.run_id,
        severity=ReviewSeverity.CRITICAL,
        status=ReviewFindingStatus.RESOLVED,
    ) == [updated]


def _review_app(review_context):
    workspace, store, _, run = review_context
    ReviewFindingService(store, workspace).save_model_output(
        run_id=run.run_id,
        raw_output=_payload(),
    )
    app = create_app(
        agent_factory=lambda task_kind: None,
        task_store=TaskRegistry(),
        navigation_store=store,
        workspace=workspace,
    )
    return app, store, run


def test_review_finding_api_filters_and_updates(review_context) -> None:
    app, store, run = _review_app(review_context)
    finding = store.list_review_findings(run.run_id)[0]
    with TestClient(app) as client:
        listed = client.get(
            f"/api/runs/{run.run_id}/review-findings",
            params={"severity": "high", "status": "open"},
        )
        updated = client.patch(
            f"/api/runs/{run.run_id}/review-findings/{finding.id}",
            json={"status": "dismissed"},
        )
        invalid = client.patch(
            f"/api/runs/{run.run_id}/review-findings/{finding.id}",
            json={"status": "invalid"},
        )

    assert listed.status_code == 200
    assert listed.json()[0]["file_path"] == "/projects/demo/backend/app.py"
    assert updated.status_code == 200
    assert updated.json()["status"] == "dismissed"
    assert invalid.status_code == 422
    assert store.list_review_findings(run.run_id)[0].status is ReviewFindingStatus.DISMISSED


def test_review_finding_api_enforces_run_ownership_and_404(review_context) -> None:
    app, store, run = _review_app(review_context)
    finding = store.list_review_findings(run.run_id)[0]
    store.mark_run_running(run.run_id)
    store.complete_run(run.run_id)
    thread = store.get_thread(run.thread_id)
    assert thread is not None
    other_run = store.create_run(thread_id=thread.thread_id)

    with TestClient(app) as client:
        wrong_run = client.patch(
            f"/api/runs/{other_run.run_id}/review-findings/{finding.id}",
            json={"status": "resolved"},
        )
        missing = client.get("/api/runs/missing/review-findings")
        invalid_filter = client.get(
            f"/api/runs/{run.run_id}/review-findings?severity=urgent"
        )

    assert wrong_run.status_code == 404
    assert missing.status_code == 404
    assert invalid_filter.status_code == 422
    assert store.list_review_findings(run.run_id)[0].status is ReviewFindingStatus.OPEN


def test_existing_sqlite_database_is_upgraded_without_data_loss(
    review_context,
) -> None:
    _, store, _, run = review_context
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE review_findings")

    reopened = SQLiteProjectThreadStore(store.path)
    assert reopened.get_run(run.run_id) is not None
    with sqlite3.connect(store.path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(review_findings)"
            ).fetchall()
        }
    assert {"run_id", "fingerprint", "status"} <= columns


class InvalidReviewerAgent:
    def stream(self, *args, **kwargs):
        del args, kwargs
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "subagent_type": "reviewer",
                                "description": "审查代码",
                            },
                            "id": "review-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                ToolMessage(
                    content="not json",
                    tool_call_id="review-call",
                    name="task",
                ),
                {},
            ),
        }


def test_invalid_reviewer_result_fails_run_instead_of_leaving_running(
    review_context,
) -> None:
    workspace, store, _, run = review_context
    store.append_message(
        thread_id=run.thread_id,
        run_id=run.run_id,
        role=MessageRole.USER,
        content="审查代码",
    )
    run_conversation_agent(
        run_id=run.run_id,
        conversation_store=store,
        agent_factory=lambda task_kind: InvalidReviewerAgent(),
        workspace=workspace,
    )

    failed = store.get_run(run.run_id)
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.error == "Reviewer 输出不是合法 JSON"
    assert store.list_review_findings(run.run_id) == []


class ReviewerBudgetAgent:
    def stream(self, *args, **kwargs):
        del args, kwargs
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(content="调用 Reviewer", id="main-1"),
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": ("tools:review-call",),
            "data": (
                AIMessageChunk(content="审查中", id="reviewer-1"),
                {},
            ),
        }


def test_reviewer_stream_counts_toward_run_model_budget(
    review_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, store, _, run = review_context
    monkeypatch.setenv("TANG_AGENT_ANALYSIS_MAX_MODEL_CALLS", "1")
    store.append_message(
        thread_id=run.thread_id,
        run_id=run.run_id,
        role=MessageRole.USER,
        content="审查代码",
    )
    run_conversation_agent(
        run_id=run.run_id,
        conversation_store=store,
        agent_factory=lambda task_kind: ReviewerBudgetAgent(),
        workspace=workspace,
    )

    failed = store.get_run(run.run_id)
    performance = store.get_run_performance(run.run_id)
    assert failed is not None and failed.status is RunStatus.FAILED
    assert performance is not None
    assert performance.model_calls == 2
    assert performance.termination_reason == "model_call_limit"
