from __future__ import annotations

from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from app.backends.local_shell import LocalShellBackend
from app.backends.task_scoped import TaskScopedBackend
from app.core.model import make_main_model
from app.core.middleware import (
    RunModelCallLimitMiddleware,
    ToolGovernanceMiddleware,
)
from app.core.prompt import get_system_prompt
from app.core.subagents import (
    build_analysis_subagent,
    build_reviewer_subagent,
)
from app.core.task_intent import TaskKind
from app.core.run_limits import budget_for
from app.memory import WorkspaceMemoryLoader
from app.skills import SkillCatalog
from app.tools import (
    SearchRuntime,
    build_web_search_tool,
    build_workspace_tools,
)

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
)

WORKSPACE_TOOL_PROMPT = """
工作区工具规则：
1. 查看目录使用 workspace_list。
2. 不知道文件路径时先用 workspace_glob；定位代码内容时先用 workspace_search。
3. 搜索结果只用于定位，随后只用 workspace_read 读取真正需要的文件和行段。
4. 不要为了定位代码反复调用 workspace_list 或盲目读取多个文件。
5. 只有工具列表中实际存在 workspace_write 时才允许创建文件。
6. 只有工具列表中实际存在 workspace_edit 时才允许修改文件。
7. 只有工具列表中实际存在 workspace_execute 时才允许执行命令。
8. 不要使用 DeepAgents 内置文件工具操作目标项目。
9. 所有项目路径都使用 /projects/... 虚拟路径。
""".strip()


def build_agent(
    task_kind: TaskKind,
    *,
    backend: LocalShellBackend | None = None,
    model: BaseChatModel | None = None,
    subagent_model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    search_runtime: SearchRuntime | None = None,
) -> Any:
    """组装当前任务使用的最小 DeepAgent。"""

    local_backend = backend or LocalShellBackend()
    main_model = model or make_main_model()
    analysis_model = subagent_model or main_model
    budget = budget_for(task_kind)
    model_governance = RunModelCallLimitMiddleware(budget)
    tool_governance = ToolGovernanceMiddleware(budget)

    scoped_backend = TaskScopedBackend.for_task(
        task_kind,
        local_backend,
        network_access=bool(
            search_runtime and search_runtime.network_access
        ),
    )

    memory_prompt = WorkspaceMemoryLoader(
        local_backend.workspace
    ).render_prompt()

    skill_prompt = SkillCatalog(
        local_backend.workspace
    ).render_prompt()

    shared_context = "\n\n".join(
        section
        for section in [
            memory_prompt,
            skill_prompt,
        ]
        if section
    )

    analysis_subagent = build_analysis_subagent(
        local_backend,
        analysis_model,
        shared_context=shared_context,
        middleware=[
            ModelCallLimitMiddleware(
                run_limit=budget.max_model_calls,
                exit_behavior="error",
            ),
            model_governance,
            tool_governance,
        ],
        search_runtime=search_runtime,
    )
    reviewer_subagent = build_reviewer_subagent(
        local_backend,
        analysis_model,
        shared_context=shared_context,
        middleware=[
            ModelCallLimitMiddleware(
                run_limit=budget.max_model_calls,
                exit_behavior="error",
            ),
            model_governance,
            tool_governance,
        ],
    )

    prompt_sections = [
        get_system_prompt(task_kind),
        WORKSPACE_TOOL_PROMPT,
    ]

    if memory_prompt:
        prompt_sections.append(memory_prompt)

    if skill_prompt:
        prompt_sections.append(skill_prompt)

    system_prompt = "\n\n".join(prompt_sections)

    tools = build_workspace_tools(scoped_backend)
    if search_runtime is not None and search_runtime.network_access:
        tools.append(
            build_web_search_tool(
                search_runtime,
                caller_task_kind=task_kind,
            )
        )

    return create_deep_agent(
        model=main_model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=[analysis_subagent, reviewer_subagent],
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],
        middleware=[
            ModelCallLimitMiddleware(
                run_limit=budget.max_model_calls,
                exit_behavior="error",
            ),
            model_governance,
            tool_governance,
        ],
        checkpointer=checkpointer,
    )
