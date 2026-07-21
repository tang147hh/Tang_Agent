from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskKind(StrEnum):
    CODING = "coding"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    QA = "qa"


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    kind: TaskKind
    allow_file_write: bool
    allow_command_execution: bool


def _normalize_prompt(prompt: str) -> str:
    """规整用户输入，便于进行确定性的关键词判断。"""

    return " ".join((prompt or "").lower().split())


def classify_task_kind(prompt: str) -> TaskKind:
    """使用本地规则判断任务类型，不调用模型。"""

    normalized = _normalize_prompt(prompt)

    planning_directives = (
        "先给方案",
        "先列方案",
        "只给方案",
        "仅给方案",
        "不要修改",
        "不要改代码",
        "确认后再",
        "由我确认",
    )

    coding_keywords = (
        "修改",
        "修复",
        "新增",
        "增加",
        "实现",
        "开发",
        "改成",
        "改为",
        "重构",
        "迁移",
        "升级",
        "删除功能",
        "运行测试",
    )

    analysis_keywords = (
        "分析",
        "解析",
        "解释",
        "梳理",
        "目录结构",
        "代码结构",
        "帮我看看",
        "检查一下",
    )

    planning_keywords = (
        "方案",
        "计划",
        "设计",
        "步骤",
        "怎么做",
        "如何实现",
    )

    # “不要修改，只给方案”里虽然出现“修改”，
    # 但它表达的是禁止修改，因此优先归入 planning。
    if any(
        directive in normalized
        for directive in planning_directives
    ):
        return TaskKind.PLANNING

    # 只要用户明确要求产生改动，就按 coding 处理。
    if any(
        keyword in normalized
        for keyword in coding_keywords
    ):
        return TaskKind.CODING

    if any(
        keyword in normalized
        for keyword in analysis_keywords
    ):
        return TaskKind.ANALYSIS

    if any(
        keyword in normalized
        for keyword in planning_keywords
    ):
        return TaskKind.PLANNING

    return TaskKind.QA


def policy_for(task_kind: TaskKind) -> TaskPolicy:
    """把任务类型转换成可执行的权限策略。"""

    can_mutate = task_kind is TaskKind.CODING

    return TaskPolicy(
        kind=task_kind,
        allow_file_write=can_mutate,
        allow_command_execution=can_mutate,
    )


def is_read_only_task(task_kind: TaskKind) -> bool:
    return not policy_for(task_kind).allow_file_write