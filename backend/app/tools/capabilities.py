from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from app.core.task_intent import TaskKind


class ToolCategory(StrEnum):
    LOCAL_READ = "local_read"
    LOCAL_WRITE = "local_write"
    COMMAND_EXECUTION = "command_execution"
    NETWORK_READ = "network_read"
    EXTERNAL_WRITE = "external_write"


class ToolRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ToolCapability:
    name: str
    category: ToolCategory
    risk_level: ToolRiskLevel
    allowed_task_kinds: tuple[TaskKind, ...]
    requires_network_access: bool
    model_callable: bool
    description: str
    availability: bool = True
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["category"] = self.category.value
        payload["risk_level"] = self.risk_level.value
        payload["allowed_task_kinds"] = [
            kind.value for kind in self.allowed_task_kinds
        ]
        return payload


ALL_TASK_KINDS = tuple(TaskKind)
READ_ONLY_TASK_KINDS = (
    TaskKind.QA,
    TaskKind.PLANNING,
    TaskKind.ANALYSIS,
    TaskKind.CODING,
)


TOOL_CAPABILITIES: dict[str, ToolCapability] = {
    "workspace_list": ToolCapability(
        name="workspace_list",
        category=ToolCategory.LOCAL_READ,
        risk_level=ToolRiskLevel.LOW,
        allowed_task_kinds=ALL_TASK_KINDS,
        requires_network_access=False,
        model_callable=True,
        description="列出受控虚拟工作区目录。",
    ),
    "workspace_read": ToolCapability(
        name="workspace_read",
        category=ToolCategory.LOCAL_READ,
        risk_level=ToolRiskLevel.LOW,
        allowed_task_kinds=ALL_TASK_KINDS,
        requires_network_access=False,
        model_callable=True,
        description="读取受控虚拟工作区文本文件。",
    ),
    "workspace_glob": ToolCapability(
        name="workspace_glob",
        category=ToolCategory.LOCAL_READ,
        risk_level=ToolRiskLevel.LOW,
        allowed_task_kinds=READ_ONLY_TASK_KINDS,
        requires_network_access=False,
        model_callable=True,
        description="按受控 Glob 模式定位虚拟工作区路径。",
    ),
    "workspace_search": ToolCapability(
        name="workspace_search",
        category=ToolCategory.LOCAL_READ,
        risk_level=ToolRiskLevel.LOW,
        allowed_task_kinds=READ_ONLY_TASK_KINDS,
        requires_network_access=False,
        model_callable=True,
        description="在受控虚拟工作区文本文件中搜索代码内容。",
    ),
    "workspace_write": ToolCapability(
        name="workspace_write",
        category=ToolCategory.LOCAL_WRITE,
        risk_level=ToolRiskLevel.MEDIUM,
        allowed_task_kinds=(TaskKind.CODING,),
        requires_network_access=False,
        model_callable=True,
        description="在受控虚拟工作区创建文本文件。",
    ),
    "workspace_edit": ToolCapability(
        name="workspace_edit",
        category=ToolCategory.LOCAL_WRITE,
        risk_level=ToolRiskLevel.MEDIUM,
        allowed_task_kinds=(TaskKind.CODING,),
        requires_network_access=False,
        model_callable=True,
        description="精确修改受控虚拟工作区文本文件。",
    ),
    "workspace_execute": ToolCapability(
        name="workspace_execute",
        category=ToolCategory.COMMAND_EXECUTION,
        risk_level=ToolRiskLevel.HIGH,
        allowed_task_kinds=(TaskKind.CODING,),
        requires_network_access=False,
        model_callable=True,
        description="执行参数数组形式的本地白名单命令。",
    ),
    "web_search": ToolCapability(
        name="web_search",
        category=ToolCategory.NETWORK_READ,
        risk_level=ToolRiskLevel.MEDIUM,
        allowed_task_kinds=READ_ONLY_TASK_KINDS,
        requires_network_access=True,
        model_callable=True,
        description="通过固定提供商执行只读结构化网页搜索。",
    ),
    # GitHub Review 发布刻意不注册成 Agent 工具。它只能通过专用的
    # prepare/publish API 和用户确认执行。
    "github_review_publish": ToolCapability(
        name="github_review_publish",
        category=ToolCategory.EXTERNAL_WRITE,
        risk_level=ToolRiskLevel.CRITICAL,
        allowed_task_kinds=(),
        requires_network_access=True,
        model_callable=False,
        description="经用户确认发布 GitHub Review。",
    ),
}


def capability_for(
    name: str,
    *,
    availability: bool | None = None,
    unavailable_reason: str | None = None,
) -> ToolCapability:
    """读取固定注册能力；不接受模块路径或用户工具定义。"""

    capability = TOOL_CAPABILITIES[name]
    if availability is None:
        return capability
    return ToolCapability(
        name=capability.name,
        category=capability.category,
        risk_level=capability.risk_level,
        allowed_task_kinds=capability.allowed_task_kinds,
        requires_network_access=capability.requires_network_access,
        model_callable=capability.model_callable,
        description=capability.description,
        availability=availability,
        unavailable_reason=unavailable_reason,
    )


def task_allows_tool(task_kind: TaskKind, name: str) -> bool:
    capability = TOOL_CAPABILITIES[name]
    return (
        capability.model_callable
        and task_kind in capability.allowed_task_kinds
    )
