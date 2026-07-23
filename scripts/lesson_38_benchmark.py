from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import tempfile
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.backends.workspace import Workspace
from app.core.code_review import CodeReviewService
from app.core.config import load_settings
from app.core.conversation import MessageRole
from app.core.conversation_runtime import run_conversation_agent
from app.core.github_review import (
    GitHubCommandResult,
    GitHubReviewEvent,
    GitHubReviewService,
)
from app.core.review_diff import ReviewDiffLimits, ReviewSource
from app.core.task_intent import TaskKind
from app.store import SQLiteProjectThreadStore


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


class FixedConversationAgent:
    def __init__(
        self,
        *,
        answer: str,
        tools: Iterable[tuple[str, dict[str, Any]]],
    ) -> None:
        self.answer = answer
        self.tools = tuple(tools)
        self.observed_tools: list[str] = []

    def stream(self, *args: Any, **kwargs: Any):
        del args, kwargs
        for index, (name, arguments) in enumerate(self.tools):
            call_id = f"call-{index}"
            self.observed_tools.append(name)
            yield {
                "type": "messages",
                "ns": (),
                "data": (
                    AIMessage(
                        content="",
                        id=f"model-tool-{index}",
                        tool_calls=[{
                            "name": name,
                            "args": arguments,
                            "id": call_id,
                            "type": "tool_call",
                        }],
                    ),
                    {},
                ),
            }
            yield {
                "type": "messages",
                "ns": (),
                "data": (
                    ToolMessage(
                        content=json.dumps({"status": "completed"}),
                        name=name,
                        tool_call_id=call_id,
                    ),
                    {},
                ),
            }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(
                    content=self.answer,
                    id="model-answer",
                ),
                {},
            ),
        }


class FixedReviewer:
    def invoke(self, messages: list[Any]) -> str:
        del messages
        return json.dumps({
            "findings": [{
                "severity": "medium",
                "category": "correctness",
                "file_path": "/projects/demo/app.py",
                "start_line": 2,
                "end_line": 2,
                "line_side": "new",
                "title": "changed return value",
                "description": "the changed line alters the result",
                "suggestion": "verify the expected return value",
            }],
            "summary": "one fixed finding",
        })


class FixedGitHubRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    def is_installed(self) -> bool:
        return True

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: float,
        input_text: str | None = None,
    ) -> GitHubCommandResult:
        del cwd, timeout
        call = tuple(argv)
        self.calls.append((call, input_text))
        if call[:3] == ("gh", "auth", "status"):
            return self._json({})
        endpoint = next(
            (
                value
                for value in call
                if value == "user" or value.startswith("repos/")
            ),
            "",
        )
        if endpoint == "user":
            return self._json({"login": "fixture-reviewer"})
        if endpoint == "repos/acme/demo":
            return self._json({"permissions": {"pull": True}})
        if endpoint.endswith("pulls?state=open&per_page=20"):
            return self._json([self._pull_request()])
        if endpoint.endswith("pulls/7/files?per_page=100"):
            return self._json([{
                "filename": "app.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+    return 2\n",
            }])
        if endpoint.endswith("pulls/7"):
            return self._json(self._pull_request())
        return GitHubCommandResult(
            exit_code=1,
            stdout="",
            stderr="mock endpoint not found",
        )

    @staticmethod
    def _json(value: Any) -> GitHubCommandResult:
        return GitHubCommandResult(
            exit_code=0,
            stdout=json.dumps(value),
            stderr="",
        )

    @staticmethod
    def _pull_request() -> dict[str, Any]:
        return {
            "number": 7,
            "title": "Lesson 38 fixture",
            "html_url": "https://github.com/acme/demo/pull/7",
            "state": "open",
            "draft": False,
            "base": {
                "ref": "main",
                "sha": BASE_SHA,
                "repo": {"full_name": "acme/demo"},
            },
            "head": {"ref": "lesson-38", "sha": HEAD_SHA},
            "user": {"login": "fixture-author"},
        }


def git(repo: Path, *args: str) -> None:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")


def diff_character_count(review_diff: Any) -> int:
    return sum(len(item.patch or "") for item in review_diff.files)


def sample_from_performance(
    *,
    scenario: str,
    run_id: str,
    performance: Any,
    review_diff: Any | None = None,
    finding_count: int = 0,
    observed_tools: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "run_id": run_id,
        "task_kind": performance.task_kind.value,
        "total_duration_ms": round(performance.duration_ms or 0.0, 3),
        "first_output_latency_ms": (
            round(performance.first_output_ms, 3)
            if performance.first_output_ms is not None
            else None
        ),
        "model_call_count": performance.model_calls,
        "tool_call_count": performance.tool_calls,
        "rejected_tool_count": performance.safety_rejections,
        "limit_reached": performance.termination_reason is not None,
        "limit_reason": performance.termination_reason,
        "review_file_count": len(review_diff.files) if review_diff else 0,
        "diff_character_count": (
            diff_character_count(review_diff) if review_diff else 0
        ),
        "finding_count": finding_count,
        "observed_tools": list(observed_tools),
    }


def conversation_samples(
    store: SQLiteProjectThreadStore,
    project_id: str,
) -> list[dict[str, Any]]:
    cases = (
        ("ordinary_greeting", TaskKind.QA, "你好", "你好，请问需要什么帮助？", ()),
        ("simple_qa", TaskKind.QA, "2+2 是多少？", "2+2 等于 4。", ()),
        (
            "single_file_read",
            TaskKind.ANALYSIS,
            "读取 app.py",
            "已读取单个文件。",
            (("workspace_read", {"path": "/projects/demo/app.py"}),),
        ),
        (
            "multi_file_analysis",
            TaskKind.ANALYSIS,
            "分析多个文件",
            "已完成多文件分析。",
            (
                ("workspace_list", {"path": "/projects/demo"}),
                ("workspace_read", {"path": "/projects/demo/app.py"}),
                ("workspace_read", {"path": "/projects/demo/test_app.py"}),
            ),
        ),
        (
            "small_coding_run",
            TaskKind.CODING,
            "修改并测试 app.py",
            "已完成小型代码修改。",
            (
                ("workspace_read", {"path": "/projects/demo/app.py"}),
                ("workspace_write", {"path": "/projects/demo/app.py"}),
                ("workspace_execute", {"argv": ["pytest", "-q"]}),
            ),
        ),
    )
    samples: list[dict[str, Any]] = []
    for scenario, task_kind, prompt, answer, tools in cases:
        for repetition in range(3):
            thread = store.create_thread(
                project_id=project_id,
                title=f"{scenario}-{repetition + 1}",
            )
            run = store.create_run(
                thread_id=thread.thread_id,
                task_kind=task_kind,
            )
            store.append_message(
                thread_id=thread.thread_id,
                run_id=run.run_id,
                role=MessageRole.USER,
                content=prompt,
            )
            agent = FixedConversationAgent(answer=answer, tools=tools)
            run_conversation_agent(
                run_id=run.run_id,
                conversation_store=store,
                agent_factory=lambda _: agent,
            )
            performance = store.get_run_performance(run.run_id)
            if performance is None:
                raise RuntimeError("run performance was not persisted")
            if scenario == "ordinary_greeting":
                forbidden = {
                    "workspace_list",
                    "workspace_read",
                    "web_search",
                    "todo_write",
                }
                if forbidden.intersection(agent.observed_tools):
                    raise RuntimeError("greeting performed meaningless work")
            samples.append(sample_from_performance(
                scenario=scenario,
                run_id=run.run_id,
                performance=performance,
                observed_tools=agent.observed_tools,
            ))
    return samples


def review_samples(
    *,
    store: SQLiteProjectThreadStore,
    project_id: str,
    workspace: Workspace,
    github_service: GitHubReviewService,
    github_runner: FixedGitHubRunner,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for scenario, source in (
        ("working_tree_review", ReviewSource.WORKING_TREE),
        ("pull_request_review", ReviewSource.PULL_REQUEST),
    ):
        for repetition in range(3):
            thread = store.create_thread(
                project_id=project_id,
                title=f"{scenario}-{repetition + 1}",
            )
            run = store.create_run(
                thread_id=thread.thread_id,
                task_kind=TaskKind.ANALYSIS,
            )
            result = CodeReviewService(
                store=store,
                workspace=workspace,
                reviewer=FixedReviewer(),
                github_review_service=github_service,
            ).review_run(
                run_id=run.run_id,
                source=source,
                pr_number=7 if source is ReviewSource.PULL_REQUEST else None,
            )
            performance = store.get_run_performance(run.run_id)
            if performance is None:
                raise RuntimeError("review performance was not persisted")
            samples.append(sample_from_performance(
                scenario=scenario,
                run_id=run.run_id,
                performance=performance,
                review_diff=result.diff,
                finding_count=result.finding_count,
            ))

            if source is not ReviewSource.PULL_REQUEST:
                continue
            calls_before = len(github_runner.calls)
            started = time.perf_counter()
            github_service.prepare(
                run_id=run.run_id,
                pr_number=7,
                selected_finding_ids=[item.id for item in result.findings],
                event=GitHubReviewEvent.COMMENT,
                summary="fixed acceptance preview",
            )
            duration_ms = (time.perf_counter() - started) * 1000
            samples.append({
                "scenario": "github_prepare",
                "run_id": run.run_id,
                "task_kind": "github_prepare",
                "total_duration_ms": round(duration_ms, 3),
                "first_output_latency_ms": None,
                "model_call_count": 0,
                "tool_call_count": len(github_runner.calls) - calls_before,
                "rejected_tool_count": 0,
                "limit_reached": False,
                "limit_reason": None,
                "review_file_count": len(result.diff.files),
                "diff_character_count": diff_character_count(result.diff),
                "finding_count": result.finding_count,
                "observed_tools": [],
            })
    return samples


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for scenario in sorted({item["scenario"] for item in samples}):
        group = [item for item in samples if item["scenario"] == scenario]
        durations = sorted(item["total_duration_ms"] for item in group)
        first_outputs = sorted(
            item["first_output_latency_ms"]
            for item in group
            if item["first_output_latency_ms"] is not None
        )
        result[scenario] = {
            "runs": len(group),
            "total_duration_ms": {
                "min": durations[0],
                "median": round(statistics.median(durations), 3),
                "max": durations[-1],
            },
            "first_output_latency_ms": (
                {
                    "min": first_outputs[0],
                    "median": round(statistics.median(first_outputs), 3),
                    "max": first_outputs[-1],
                }
                if first_outputs
                else None
            ),
            "model_call_counts": [item["model_call_count"] for item in group],
            "tool_call_counts": [item["tool_call_count"] for item in group],
        }
    return result


def benchmark(output_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="tang-agent-lesson-38-") as temp:
        root = Path(temp)
        settings = replace(
            load_settings(),
            data_dir=root / "data",
            log_dir=root / "logs",
            workspace_root=root / "workspace",
            github_review_publish_enabled=True,
            github_cli_timeout=2,
        )
        workspace = Workspace.from_settings(settings)
        workspace.ensure_layout()
        repo = workspace.resolve("/projects/demo")
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Lesson 38")
        git(repo, "config", "user.email", "lesson-38@example.invalid")
        git(repo, "remote", "add", "origin", "https://github.com/acme/demo.git")
        (repo / "app.py").write_text(
            "def value():\n    return 1\n",
            encoding="utf-8",
        )
        (repo / "test_app.py").write_text(
            "from app import value\n\ndef test_value():\n    assert value() == 1\n",
            encoding="utf-8",
        )
        git(repo, "add", "--", "app.py", "test_app.py")
        git(repo, "commit", "-m", "fixture")
        (repo / "app.py").write_text(
            "def value():\n    return 2\n",
            encoding="utf-8",
        )

        store = SQLiteProjectThreadStore(root / "tasks.sqlite")
        project = store.create_project(
            name="Demo",
            virtual_path="/projects/demo",
        )
        github_runner = FixedGitHubRunner()
        github_service = GitHubReviewService(
            store=store,
            workspace=workspace,
            settings=settings,
            runner=github_runner,
        )
        samples = conversation_samples(store, project.project_id)
        samples.extend(review_samples(
            store=store,
            project_id=project.project_id,
            workspace=workspace,
            github_service=github_service,
            github_runner=github_runner,
        ))

    report = {
        "measurement": "fake_model_and_fake_github_fixed_cases",
        "real_model_latency": {
            "status": "blocked",
            "reason": "external model cost was not explicitly authorized",
        },
        "real_github_publish": {
            "status": "blocked",
            "reason": "GitHub authentication and dedicated test PR are unavailable",
        },
        "historical_reference": {
            "complex_run_duration_ms": 152_460,
            "complex_run_tool_calls": 28,
            "pre_lesson_33_average_duration_ms": 34_540,
            "comparison_status": "blocked",
            "reason": "fake-model latency is not comparable to historical real-model latency",
        },
        "samples": samples,
        "summary": summarize(samples),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/tang-agent-lesson-38/performance.json"),
    )
    args = parser.parse_args()
    report = benchmark(args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
