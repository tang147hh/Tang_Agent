from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field

from app.backends.command_runner import CommandPolicyError
from app.backends.local_shell import (
    MAX_WORKSPACE_GLOB_PATTERN_CHARS,
    MAX_WORKSPACE_SEARCH_QUERY_CHARS,
    MAX_WORKSPACE_SEARCH_RESULTS,
)
from app.backends.task_scoped import TaskScopedBackend
from app.tools.capabilities import capability_for


def build_workspace_tools(
    backend: TaskScopedBackend,
) -> list[BaseTool]:
    """根据任务权限创建模型可见的工作区工具。"""

    def workspace_list(
        path: str = "/projects",
    ) -> list[dict[str, object]]:
        """列出虚拟工作区目录中的下一层内容。"""

        return [
            asdict(entry)
            for entry in backend.list_dir(path)
        ]

    def workspace_read(
        path: str,
        offset: int = 0,
        limit: int = 2_000,
    ) -> str:
        """读取虚拟工作区中的 UTF-8 文本文件。"""

        return backend.read_text(
            path,
            offset=offset,
            limit=limit,
        )

    def workspace_glob(
        pattern: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_WORKSPACE_GLOB_PATTERN_CHARS,
            ),
        ],
        path: str = "/projects",
        max_results: Annotated[
            int,
            Field(ge=1, le=MAX_WORKSPACE_SEARCH_RESULTS),
        ] = 100,
        include_directories: bool = False,
    ) -> dict[str, object]:
        """按相对 Glob 模式定位受控工作区路径。"""

        return asdict(
            backend.glob_paths(
                path,
                pattern=pattern,
                max_results=max_results,
                include_directories=include_directories,
            )
        )

    def workspace_search(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_WORKSPACE_SEARCH_QUERY_CHARS,
            ),
        ],
        path: str = "/projects",
        file_pattern: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_WORKSPACE_GLOB_PATTERN_CHARS,
            ),
        ] = "**/*",
        max_results: Annotated[
            int,
            Field(ge=1, le=MAX_WORKSPACE_SEARCH_RESULTS),
        ] = 100,
        case_sensitive: bool = True,
    ) -> dict[str, object]:
        """在受控 UTF-8 文本文件中执行字面量代码搜索。"""

        return asdict(
            backend.search_text(
                path,
                query=query,
                file_pattern=file_pattern,
                max_results=max_results,
                case_sensitive=case_sensitive,
            )
        )

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            func=workspace_list,
            name="workspace_list",
            description=(
                "列出 Agent 虚拟工作区目录的下一层内容。"
                "项目仓库位于 /projects。"
            ),
            metadata=capability_for("workspace_list").to_dict(),
        ),
        StructuredTool.from_function(
            func=workspace_glob,
            name="workspace_glob",
            description=(
                "按相对于 path 的 Glob 模式定位工作区文件。"
                "支持 **/*.py 等模式，返回稳定排序的虚拟路径、类型和大小；"
                "不会扫描依赖、敏感目录或符号链接。"
            ),
            metadata=capability_for("workspace_glob").to_dict(),
        ),
        StructuredTool.from_function(
            func=workspace_search,
            name="workspace_search",
            description=(
                "在工作区代码文件中搜索字面量文本，返回虚拟路径、"
                "行号、列号和受限单行片段。先搜索定位，再用 workspace_read "
                "读取需要的文件；query 不是正则表达式。"
            ),
            metadata=capability_for("workspace_search").to_dict(),
        ),
        StructuredTool.from_function(
            func=workspace_read,
            name="workspace_read",
            description=(
                "读取 Agent 虚拟工作区中的 UTF-8 文本文件。"
                "只能传入 /projects、/tmp 等虚拟路径。"
            ),
            metadata=capability_for("workspace_read").to_dict(),
        ),
    ]

    if backend.policy.allow_file_write:

        def workspace_write(
            path: str,
            content: str,
        ) -> dict[str, str]:
            """在虚拟工作区中创建新文件，不覆盖已有文件。"""

            written_path = backend.write_text(path, content)

            return {
                "status": "created",
                "path": written_path,
            }

        def workspace_edit(
            path: str,
            old_text: str,
            new_text: str,
            replace_all: bool = False,
        ) -> dict[str, object]:
            """通过精确文本匹配修改已有文件。"""

            replacements = backend.edit_text(
                path,
                old_text,
                new_text,
                replace_all=replace_all,
            )

            return {
                "status": "updated",
                "path": path,
                "replacements": replacements,
            }

        tools.extend(
            [
                StructuredTool.from_function(
                    func=workspace_write,
                    name="workspace_write",
                    description=(
                        "在虚拟工作区中创建新的 UTF-8 文件。"
                        "不能覆盖已有文件。"
                    ),
                    metadata=capability_for("workspace_write").to_dict(),
                ),
                StructuredTool.from_function(
                    func=workspace_edit,
                    name="workspace_edit",
                    description=(
                        "使用精确的 old_text 和 new_text "
                        "修改已有 UTF-8 文件。"
                    ),
                    metadata=capability_for("workspace_edit").to_dict(),
                ),
            ]
        )

    if backend.policy.allow_command_execution:

        def workspace_execute(
            argv: list[str],
            cwd: str = "/projects",
            timeout: float = 300,
        ) -> dict[str, object]:
            """在工作区中执行受控的参数数组命令。"""

            try:
                result = backend.run_command(
                    argv,
                    cwd=cwd,
                    timeout=timeout,
                )
            except CommandPolicyError as exc:
                return {
                    "status": "rejected",
                    "error": str(exc),
                    "recoverable": True,
                    "hint": (
                        "命令未执行。请改用符合策略的命令；"
                        "验证文件内容时优先使用 workspace_read。"
                    ),
                }

            return asdict(result)

        tools.append(
            StructuredTool.from_function(
                func=workspace_execute,
                name="workspace_execute",
                description=(
                    "在 Agent 工作区内执行受控命令。"
                    "argv 必须是参数数组，不能传 shell 字符串。"
                ),
                metadata=capability_for("workspace_execute").to_dict(),
            )
        )

    return tools
