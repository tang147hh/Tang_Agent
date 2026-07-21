from __future__ import annotations

from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel

from app.backends.local_shell import LocalShellBackend
from app.backends.task_scoped import TaskScopedBackend
from app.core.model import make_main_model
from app.core.prompt import get_system_prompt
from app.core.task_intent import TaskKind
from app.skills import SkillCatalog
from app.tools import build_workspace_tools
from app.memory import WorkspaceMemoryLoader

WORKSPACE_TOOL_PROMPT = """
工作区工具规则：
1. 查看目录使用 workspace_list。
2. 读取文件使用 workspace_read。
3. 只有工具列表中实际存在 workspace_write 时才允许创建文件。
4. 只有工具列表中实际存在 workspace_edit 时才允许修改文件。
5. 只有工具列表中实际存在 workspace_execute 时才允许执行命令。
6. 不要使用 DeepAgents 内置文件工具操作目标项目。
7. 所有项目路径都使用 /projects/... 虚拟路径。
""".strip()


def build_agent(
    task_kind: TaskKind,
    *,
    backend: LocalShellBackend | None = None,
    model: BaseChatModel | None = None,
) -> Any:
    """组装当前任务使用的最小 DeepAgent。"""

    local_backend = backend or LocalShellBackend()

    scoped_backend = TaskScopedBackend.for_task(
        task_kind,
        local_backend,
    )

    memory_prompt = WorkspaceMemoryLoader(
        local_backend.workspace
    ).render_prompt()

    skill_prompt = SkillCatalog(
        local_backend.workspace
    ).render_prompt()

    prompt_sections = [
        get_system_prompt(task_kind),
        WORKSPACE_TOOL_PROMPT,
    ]

    if memory_prompt:
        prompt_sections.append(memory_prompt)

    if skill_prompt:
        prompt_sections.append(skill_prompt)

    system_prompt = "\n\n".join(prompt_sections)

    return create_deep_agent(
        model=model or make_main_model(),
        tools=build_workspace_tools(scoped_backend),
        system_prompt=system_prompt,
        permissions=[
            # 本课不让 DeepAgents 内置文件工具操作任何路径。
            # 项目访问统一经过 workspace_* 自定义工具。
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],
    )
