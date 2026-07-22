from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.core.agent import build_agent
from app.core.logging_config import configure_logging
from app.core.task_runtime import (
    AgentFactory,
    TaskRegistry,
)


def create_app(
    *,
    agent_factory: AgentFactory = build_agent,
    task_registry: TaskRegistry | None = None,
) -> FastAPI:
    """创建应用，并允许测试替换 Agent 和任务注册表。"""

    configure_logging()

    application = FastAPI(
        title="Tang Agent API",
        version="0.1.0",
    )

    application.state.agent_factory = agent_factory
    application.state.task_registry = (
        task_registry or TaskRegistry()
    )

    application.include_router(router)

    return application


app = create_app()