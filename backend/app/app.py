from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver

from app.api.routes import router
from app.core.agent import build_agent
from app.core.config import Settings, load_settings
from app.core.logging_config import configure_logging
from app.core.task_runtime import (
    AgentFactory,
    TaskRegistry,
    TaskStore,
)
from app.store import SQLiteTaskStore


def create_app(
    *,
    agent_factory: AgentFactory | None = None,
    task_store: TaskStore | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """创建 FastAPI 应用并管理持久化资源生命周期。"""

    current_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ):
        configure_logging(current_settings)

        active_store = task_store or SQLiteTaskStore(
            current_settings.data_dir / "tasks.sqlite"
        )

        application.state.task_store = active_store

        checkpoint_connection = None

        if agent_factory is not None:
            # 单元测试注入假 Agent 时不创建 checkpoint 数据库。
            application.state.agent_factory = agent_factory
        else:
            current_settings.data_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            checkpoint_connection = sqlite3.connect(
                current_settings.data_dir
                / "checkpoints.sqlite",
                check_same_thread=False,
            )

            checkpointer = SqliteSaver(
                checkpoint_connection
            )

            def persistent_agent_factory(
                task_kind,
            ):
                return build_agent(
                    task_kind,
                    checkpointer=checkpointer,
                )

            application.state.agent_factory = (
                persistent_agent_factory
            )
            application.state.checkpointer = checkpointer

        try:
            yield
        finally:
            if checkpoint_connection is not None:
                checkpoint_connection.close()

    application = FastAPI(
        title="Tang Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.include_router(router)

    return application


app = create_app()