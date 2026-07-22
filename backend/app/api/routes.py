from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from fastapi.sse import (
    EventSourceResponse,
    ServerSentEvent,
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

    task_store.append_event(
        thread_id=snapshot.thread_id,
        kind="created",
        source="system",
        payload={
            "status": snapshot.status.value,
            "task_kind": snapshot.task_kind.value,
        },
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

def require_existing_task_store(
    thread_id: str,
    request: Request,
) -> TaskStore:
    """取得任务存储，并在流式响应开始前验证任务。"""

    task_store: TaskStore = (
        request.app.state.task_store
    )

    if task_store.get(thread_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="task not found",
        )

    return task_store


@router.get(
    "/api/tasks/{thread_id}/events",
    response_class=EventSourceResponse,
)
async def stream_task_events(
    thread_id: str,
    task_store: Annotated[
        TaskStore,
        Depends(require_existing_task_store),
    ],
    last_event_id: Annotated[
        int | None,
        Header(alias="Last-Event-ID"),
    ] = None,
):
    cursor = max(last_event_id or 0, 0)
    terminal_seen = False

    while True:
        events = task_store.list_events(
            thread_id,
            after_id=cursor,
        )

        for event in events:
            cursor = event.id

            if event.kind in {
                "completed",
                "failed",
            }:
                terminal_seen = True

            yield ServerSentEvent(
                id=str(event.id),
                event=event.kind,
                retry=3000,
                data={
                    "thread_id": event.thread_id,
                    "source": event.source,
                    "created_at": (
                        event.created_at.isoformat()
                    ),
                    **event.payload,
                },
            )

        if terminal_seen:
            return

        await asyncio.sleep(0.2)