from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
    status,
)

from app.api.schemas import (
    TaskCreateRequest,
    TaskResponse,
)
from app.core.task_intent import classify_task_kind
from app.core.task_runtime import (
    AgentFactory,
    TaskStore,
    run_agent_task,
)


router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.post(
    "/api/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_task(
    body: TaskCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> TaskResponse:
    task_store: TaskStore = (
        request.app.state.task_store
    )
    agent_factory: AgentFactory = (
        request.app.state.agent_factory
    )

    task_kind = classify_task_kind(body.prompt)

    snapshot = task_store.create(
        prompt=body.prompt,
        task_kind=task_kind,
    )

    background_tasks.add_task(
        run_agent_task,
        thread_id=snapshot.thread_id,
        task_store=task_store,
        agent_factory=agent_factory,
    )

    return TaskResponse.model_validate(snapshot)


@router.get(
    "/api/tasks/{thread_id}",
    response_model=TaskResponse,
)
def get_task(
    thread_id: str,
    request: Request,
) -> TaskResponse:
    task_store: TaskStore = (
        request.app.state.task_store
    )

    snapshot = task_store.get(thread_id)

    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="task not found",
        )

    return TaskResponse.model_validate(snapshot)