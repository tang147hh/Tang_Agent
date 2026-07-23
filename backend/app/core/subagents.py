from __future__ import annotations

from deepagents import FilesystemPermission
from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models.chat_models import (
    BaseChatModel,
)

from app.backends.local_shell import LocalShellBackend
from app.backends.task_scoped import TaskScopedBackend
from app.core.task_intent import TaskKind
from app.tools import build_workspace_tools


ANALYSIS_SUBAGENT_PROMPT = """
你是 Tang Agent 的只读代码分析子 Agent。

你的职责：
1. 阅读指定目录和文件。
2. 分析项目结构、入口、依赖、测试和潜在风险。
3. 只报告有代码或工具结果支持的结论。
4. 把最终结果整理成简洁、完整的中文报告。

工具规则：
1. 只能使用 workspace_list 和 workspace_read。
2. 所有路径使用 /projects、/skills、/policies 等虚拟路径。
3. 不得修改文件，不得执行命令。
4. 不得声称已经完成自己无权执行的操作。

你只有一次机会把结果返回给主 Agent。
最终回答必须包含关键证据、结论和仍未确认的内容。
""".strip()


REVIEWER_SUBAGENT_PROMPT = """
你是 Tang Agent 的只读代码 Reviewer。

你的职责：
1. 只报告会导致真实故障、安全风险、性能退化或测试缺口的问题。
2. 每个问题必须说明触发条件和具体风险，并尽量定位到文件和行号。
3. 不修改代码、不执行命令、不发布评论，也不声称已经修复问题。
4. 只审查主 Agent 明确指定的项目和文件；本课不自行收集 Git diff。

工具规则：
1. 只能使用 workspace_list 和 workspace_read。
2. 所有路径使用 /projects/... 虚拟路径。
3. 不得使用 workspace_write、workspace_edit 或 workspace_execute。

最终回答必须只包含一个 JSON 对象，不要添加解释或 Markdown。结构为：
{
  "findings": [
    {
      "severity": "critical|high|medium|low",
      "category": "correctness|security|performance|maintainability|testing|documentation",
      "file_path": "/projects/<project-name>/... 或 null",
      "start_line": "正整数或 null",
      "end_line": "正整数或 null",
      "title": "简短标题",
      "description": "风险和触发条件",
      "suggestion": "可选建议或 null"
    }
  ],
  "summary": "简短审查总结"
}
不要返回 id、run_id、status、fingerprint、created_at 或 updated_at。
全局问题的 file_path、start_line、end_line 必须全部为 null。
""".strip()


def build_analysis_subagent(
    backend: LocalShellBackend,
    model: BaseChatModel,
    *,
    shared_context: str = "",
    middleware: list[AgentMiddleware] | None = None,
) -> SubAgent:
    """创建只读分析子 Agent，并替换框架默认子 Agent。"""

    scoped_backend = TaskScopedBackend.for_task(
        TaskKind.ANALYSIS,
        backend,
    )

    prompt_sections = [ANALYSIS_SUBAGENT_PROMPT]

    if shared_context:
        prompt_sections.append(shared_context)

    return {
        # 使用这个名字会替换 DeepAgents 默认子 Agent，
        # 避免默认子 Agent 继承 coding 写工具。
        "name": "general-purpose",
        "description": (
            "用于复杂、多步骤、会读取大量文件的只读项目分析。"
            "适合分析架构、入口、依赖、测试和代码风险；"
            "不要用于简单问答，也不能修改代码或执行命令。"
        ),
        "system_prompt": "\n\n".join(prompt_sections),
        "model": model,
        "tools": build_workspace_tools(scoped_backend),
        "middleware": list(middleware or []),
        "permissions": [
            # 禁止子 Agent 使用 DeepAgents 内置文件工具。
            # 工作区读取只能经过 workspace_* 安全工具。
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],
    }


def build_reviewer_subagent(
    backend: LocalShellBackend,
    model: BaseChatModel,
    *,
    shared_context: str = "",
    middleware: list[AgentMiddleware] | None = None,
) -> SubAgent:
    """创建只读 Reviewer；结构化结果由 Run runtime 校验和保存。"""

    scoped_backend = TaskScopedBackend.for_task(
        TaskKind.ANALYSIS,
        backend,
    )
    prompt_sections = [REVIEWER_SUBAGENT_PROMPT]
    if shared_context:
        prompt_sections.append(shared_context)

    return {
        "name": "reviewer",
        "description": (
            "只读审查指定代码并返回严格 JSON findings。"
            "用于 correctness、安全、性能、可维护性和测试问题；"
            "不能修改文件、执行命令、生成 Git diff 或发布评论。"
        ),
        "system_prompt": "\n\n".join(prompt_sections),
        "model": model,
        "tools": build_workspace_tools(scoped_backend),
        "middleware": list(middleware or []),
        "permissions": [
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],
    }
