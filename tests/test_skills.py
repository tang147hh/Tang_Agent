from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, ToolMessage

from app.backends.local_shell import LocalShellBackend
from app.backends.workspace import Workspace
from app.core.agent import build_agent
from app.core.task_intent import TaskKind
from app.skills import SkillCatalog, SkillCatalogError


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    current = Workspace(tmp_path / "workspace")
    current.ensure_layout()
    return current


def write_skill(
    workspace: Workspace,
    *,
    name: str = "repo-analysis",
    body: str = "SKILL_BODY_LOADED",
) -> None:
    skill_dir = workspace.resolve(f"/skills/{name}")
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_dir.joinpath("SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            "description: 分析陌生代码仓库\n"
            "---\n\n"
            "# Repository Analysis\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )


def test_discovers_skill_metadata(
    workspace: Workspace,
) -> None:
    write_skill(workspace)

    skills = SkillCatalog(workspace).discover()

    assert len(skills) == 1
    assert skills[0].name == "repo-analysis"
    assert skills[0].path == "/skills/repo-analysis/SKILL.md"


def test_gets_skill_detail(
    workspace: Workspace,
) -> None:
    write_skill(
        workspace,
        body="DETAIL_BODY_LOADED",
    )

    detail = SkillCatalog(workspace).get("repo-analysis")

    assert detail is not None
    assert detail.name == "repo-analysis"
    assert detail.description == "分析陌生代码仓库"
    assert detail.path == "/skills/repo-analysis/SKILL.md"
    assert "DETAIL_BODY_LOADED" in detail.content


def test_get_returns_none_for_missing_skill(
    workspace: Workspace,
) -> None:
    detail = SkillCatalog(workspace).get("missing-skill")

    assert detail is None


def test_get_rejects_invalid_skill_name(
    workspace: Workspace,
) -> None:
    with pytest.raises(
        SkillCatalogError,
        match="Skill name 不合法",
    ):
        SkillCatalog(workspace).get("../secret")


def test_prompt_uses_progressive_disclosure(
    workspace: Workspace,
) -> None:
    write_skill(
        workspace,
        body="FULL_SKILL_BODY_MUST_NOT_BE_PRELOADED",
    )

    prompt = SkillCatalog(workspace).render_prompt()

    assert "repo-analysis" in prompt
    assert "分析陌生代码仓库" in prompt
    assert "/skills/repo-analysis/SKILL.md" in prompt
    assert "FULL_SKILL_BODY_MUST_NOT_BE_PRELOADED" not in prompt


def test_rejects_invalid_skill(
    workspace: Workspace,
) -> None:
    skill_dir = workspace.resolve("/skills/broken")
    skill_dir.mkdir(parents=True)

    skill_dir.joinpath("SKILL.md").write_text(
        "# Missing frontmatter\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SkillCatalogError,
        match="缺少 YAML frontmatter",
    ):
        SkillCatalog(workspace).discover()


def test_agent_can_load_skill_on_demand(
    workspace: Workspace,
) -> None:
    write_skill(workspace)
    backend = LocalShellBackend(workspace)

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {
                            "path": ("/skills/repo-analysis/" "SKILL.md"),
                            "offset": 0,
                            "limit": 200,
                        },
                        "id": "call_skill",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="已加载 repo-analysis Skill",
            ),
        ]
    )

    agent = build_agent(
        TaskKind.ANALYSIS,
        backend=backend,
        model=model,
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "分析一个陌生项目",
                }
            ]
        }
    )

    assert any(
        isinstance(message, ToolMessage) and "SKILL_BODY_LOADED" in str(message.content)
        for message in result["messages"]
    )

    assert result["messages"][-1].content == "已加载 repo-analysis Skill"
