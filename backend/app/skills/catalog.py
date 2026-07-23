from __future__ import annotations

import re
from dataclasses import dataclass

from app.backends.workspace import Workspace

MAX_SKILL_FILE_BYTES = 100_000
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillCatalogError(ValueError):
    """Skill 文件结构或元数据不合法。"""


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    path: str


@dataclass(frozen=True, slots=True)
class SkillDetail:
    name: str
    description: str
    path: str
    content: str


def _parse_frontmatter(
    content: str,
    *,
    path: str,
) -> tuple[str, str]:
    """解析课程版 SKILL.md 的 name 和 description。"""

    lines = content.splitlines()

    if not lines or lines[0].strip() != "---":
        raise SkillCatalogError(f"Skill 缺少 YAML frontmatter：{path}")

    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise SkillCatalogError(f"Skill frontmatter 未闭合：{path}") from exc

    values: dict[str, str] = {}

    for line in lines[1:closing_index]:
        key, separator, raw_value = line.partition(":")

        if not separator:
            continue

        normalized_key = key.strip()
        normalized_value = raw_value.strip().strip("\"'")

        if normalized_key in {"name", "description"}:
            values[normalized_key] = normalized_value

    name = values.get("name", "")
    description = values.get("description", "")

    if not SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillCatalogError(f"Skill name 不合法：{name or '<empty>'}")

    if not description:
        raise SkillCatalogError(f"Skill description 不能为空：{path}")

    if len(description) > 1_024:
        raise SkillCatalogError(f"Skill description 超过 1024 字符：{path}")

    return name, description


class SkillCatalog:
    """发现只读 Skill，并生成渐进式元数据提示。"""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def discover(self) -> tuple[SkillMetadata, ...]:
        skills_root = self.workspace.resolve("/skills")
        discovered: list[SkillMetadata] = []

        if not skills_root.exists():
            return ()

        for directory in sorted(
            skills_root.iterdir(),
            key=lambda item: item.name.lower(),
        ):
            if not directory.is_dir():
                continue

            virtual_path = f"/skills/{directory.name}/SKILL.md"
            skill_path = self.workspace.resolve(virtual_path)

            if not skill_path.is_file():
                continue

            raw = skill_path.read_bytes()

            if len(raw) > MAX_SKILL_FILE_BYTES:
                raise SkillCatalogError(f"Skill 文件超过大小限制：{virtual_path}")

            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SkillCatalogError(
                    f"Skill 不是 UTF-8 文本：{virtual_path}"
                ) from exc

            name, description = _parse_frontmatter(
                content,
                path=virtual_path,
            )

            if name != directory.name:
                raise SkillCatalogError(
                    "Skill name 必须与目录名一致：" f"{name} != {directory.name}"
                )

            discovered.append(
                SkillMetadata(
                    name=name,
                    description=description,
                    path=virtual_path,
                )
            )

        return tuple(discovered)

    def get(
        self,
        name: str,
    ) -> SkillDetail | None:
        normalized_name = name.strip()

        if not SKILL_NAME_PATTERN.fullmatch(normalized_name):
            raise SkillCatalogError(
                f"Skill name 不合法：{normalized_name or '<empty>'}"
            )

        metadata = next(
            (skill for skill in self.discover() if skill.name == normalized_name),
            None,
        )

        if metadata is None:
            return None

        content = self.workspace.resolve(metadata.path).read_text(encoding="utf-8")

        return SkillDetail(
            name=metadata.name,
            description=metadata.description,
            path=metadata.path,
            content=content,
        )

    def render_prompt(self) -> str:
        skills = self.discover()

        if not skills:
            return ""

        lines = [
            "可用 Skills：",
            "",
            "这里只提供 Skill 元数据，不包含完整正文。",
            "当用户任务与某个 Skill 匹配时，"
            "必须先用 workspace_read 读取对应 SKILL.md。",
            "",
        ]

        for skill in skills:
            lines.extend(
                [
                    f"- name: {skill.name}",
                    f"  description: {skill.description}",
                    f"  path: {skill.path}",
                ]
            )

        return "\n".join(lines)
