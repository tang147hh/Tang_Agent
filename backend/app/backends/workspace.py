from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.config import Settings, load_settings


VIRTUAL_ROOTS = (
    "projects",
    "skills",
    "policies",
    "reviews",
    "runtimes",
    "tmp",
    "logs",
)

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class WorkspacePathError(ValueError):
    """路径不满足 Agent 工作区安全规则。"""


@dataclass(frozen=True, slots=True)
class Workspace:
    root: Path

    def __post_init__(self) -> None:
        resolved = self.root.expanduser().resolve()

        if resolved == Path("/"):
            raise WorkspacePathError("工作区不能是文件系统根目录")

        if resolved == Path.home().resolve():
            raise WorkspacePathError("工作区不能直接使用用户主目录")

        object.__setattr__(self, "root", resolved)

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
    ) -> "Workspace":
        current = settings or load_settings()
        workspace = cls(current.workspace_root)
        project_root = current.project_root.resolve()

        if (
            workspace.root == project_root
            or workspace.root.is_relative_to(project_root)
            or project_root.is_relative_to(workspace.root)
        ):
            raise WorkspacePathError(
                "Agent 工作区必须与 Tang Agent 平台源码目录分离"
            )

        return workspace

    def ensure_layout(self) -> None:
        """显式创建工作区目录，不在配置加载时产生副作用。"""

        self.root.mkdir(parents=True, exist_ok=True)

        for name in VIRTUAL_ROOTS:
            (self.root / name).mkdir(parents=True, exist_ok=True)

        # 后续用于保存 askpass 等敏感运行文件，不对 Agent 暴露。
        (self.root / ".secrets").mkdir(parents=True, exist_ok=True)

    def resolve(self, virtual_path: str = "/") -> Path:
        """把 Agent 虚拟路径转换成工作区内的真实路径。"""

        raw = str(virtual_path).strip()

        if raw in {"", ".", "/"}:
            return self.root

        if "\x00" in raw:
            raise WorkspacePathError("路径中不能包含空字符")

        if "\\" in raw or _WINDOWS_ABSOLUTE_PATH.match(raw):
            raise WorkspacePathError(
                f"macOS 工作区不接受 Windows 路径：{virtual_path}"
            )

        normalized = raw.lstrip("/")
        parts = PurePosixPath(normalized).parts

        if not parts:
            return self.root

        if ".." in parts:
            raise WorkspacePathError(
                f"禁止使用 '..' 跳出工作区：{virtual_path}"
            )

        if ".secrets" in parts:
            raise WorkspacePathError("禁止访问受保护目录 .secrets")

        if parts[0] not in VIRTUAL_ROOTS:
            raise WorkspacePathError(
                f"未知虚拟目录：{parts[0]}"
            )

        candidate = self.root.joinpath(*parts).resolve()
        self._assert_inside(candidate)

        return candidate

    def to_virtual(self, real_path: str | Path) -> str:
        """把工作区真实路径转换回 Agent 虚拟路径。"""

        candidate = Path(real_path).expanduser().resolve()
        self._assert_inside(candidate)

        relative = candidate.relative_to(self.root)

        if relative == Path("."):
            return "/"

        if ".secrets" in relative.parts:
            raise WorkspacePathError("禁止暴露受保护目录 .secrets")

        if relative.parts[0] not in VIRTUAL_ROOTS:
            raise WorkspacePathError(
                f"真实路径不属于公开虚拟目录：{candidate}"
            )

        return f"/{relative.as_posix()}"

    def _assert_inside(self, candidate: Path) -> None:
        """确认解析后的路径仍位于 workspace root 内。"""

        if candidate == self.root:
            return

        if not candidate.is_relative_to(self.root):
            raise WorkspacePathError(
                f"路径越过 Agent 工作区边界：{candidate}"
            )