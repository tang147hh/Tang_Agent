from __future__ import annotations

from dataclasses import asdict

from langchain_core.tools import BaseTool, StructuredTool

from app.backends.task_scoped import TaskScopedBackend


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

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            func=workspace_list,
            name="workspace_list",
            description=(
                "列出 Agent 虚拟工作区目录的下一层内容。"
                "项目仓库位于 /projects。"
            ),
        ),
        StructuredTool.from_function(
            func=workspace_read,
            name="workspace_read",
            description=(
                "读取 Agent 虚拟工作区中的 UTF-8 文本文件。"
                "只能传入 /projects、/tmp 等虚拟路径。"
            ),
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
                ),
                StructuredTool.from_function(
                    func=workspace_edit,
                    name="workspace_edit",
                    description=(
                        "使用精确的 old_text 和 new_text "
                        "修改已有 UTF-8 文件。"
                    ),
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

            result = backend.run_command(
                argv,
                cwd=cwd,
                timeout=timeout,
            )

            return asdict(result)

        tools.append(
            StructuredTool.from_function(
                func=workspace_execute,
                name="workspace_execute",
                description=(
                    "在 Agent 工作区内执行受控命令。"
                    "argv 必须是参数数组，不能传 shell 字符串。"
                ),
            )
        )

    return tools