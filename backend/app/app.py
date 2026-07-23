from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver

from app.backends.workspace import Workspace
from app.backends.local_shell import LocalShellBackend
from app.api.routes import router
from app.core.agent import build_agent
from app.core.conversation import ConversationStore
from app.core.config import Settings, load_settings
from app.core.logging_config import configure_logging
from app.core.model import make_main_model
from app.core.github_review import GitHubCliRunner, GitHubCommandRunner
from app.core.task_runtime import (
    AgentFactory,
    TaskRegistry,
    TaskStore,
)
from app.store import (
    SQLiteProjectThreadStore,
    SQLiteTaskStore,
)
from app.tools.web_search import (
    SearchCache,
    SearchProvider,
    make_search_provider,
)


def create_app(
    *,
    agent_factory: AgentFactory | None = None,
    task_store: TaskStore | None = None,
    navigation_store: ConversationStore | None = None,
    workspace: Workspace | None = None,
    settings: Settings | None = None,
    reviewer: Any | None = None,
    github_runner: GitHubCommandRunner | None = None,
    search_provider: SearchProvider | None = None,
) -> FastAPI:
    """创建 FastAPI 应用并管理持久化资源生命周期。"""

    current_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ):
        configure_logging(current_settings)

        database_path = (
            current_settings.data_dir / "tasks.sqlite"
        )

        active_store = task_store or SQLiteTaskStore(
            database_path
        )

        active_navigation_store = (
            navigation_store
            or SQLiteProjectThreadStore(database_path)
        )

        active_workspace = (
            workspace
            or Workspace.from_settings(current_settings)
        )
        active_workspace.ensure_layout()

        application.state.task_store = active_store
        application.state.navigation_store = (
            active_navigation_store
        )

        application.state.workspace = active_workspace
        application.state.settings = current_settings
        application.state.reviewer = reviewer
        application.state.github_runner = github_runner or GitHubCliRunner()
        application.state.search_provider = (
            search_provider
            or make_search_provider(
                current_settings.web_search_provider,
                zhipu_api_key=current_settings.zhipu_api_key,
            )
        )
        application.state.search_cache = SearchCache(
            ttl_seconds=current_settings.web_search_cache_ttl_seconds,
            empty_ttl_seconds=(
                current_settings.web_search_empty_cache_ttl_seconds
            ),
            max_entries=current_settings.web_search_cache_max_entries,
        )

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
            shared_backend = LocalShellBackend(active_workspace)

            @lru_cache(maxsize=1)
            def shared_model():
                """模型客户端无 Run 状态，可以在进程内安全复用。"""

                return make_main_model(current_settings)

            def persistent_agent_factory(
                task_kind,
                *,
                search_runtime=None,
            ):
                return build_agent(
                    task_kind,
                    backend=shared_backend,
                    model=shared_model(),
                    subagent_model=shared_model(),
                    checkpointer=checkpointer,
                    search_runtime=search_runtime,
                )

            application.state.agent_factory = (
                persistent_agent_factory
            )
            if application.state.reviewer is None:
                application.state.reviewer = (
                    lambda messages: shared_model().invoke(messages)
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
