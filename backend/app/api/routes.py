from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Annotated, NoReturn

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
    Path as ApiPath,
    Query,
)
from fastapi.sse import (
    EventSourceResponse,
    ServerSentEvent,
)

from app.backends.workspace import (
    Workspace,
    WorkspacePathError,
)

from app.api.schemas import (
    MessageResponse,
    ProjectCreateRequest,
    ProjectResponse,
    RunCreateRequest,
    RunResponse,
    RunPerformanceResponse,
    RunStartResponse,
    ReviewFindingResponse,
    ReviewFindingStatusUpdateRequest,
    RepositoryBranchRequest,
    RepositoryCommitRequest,
    RepositoryCommitResponse,
    RepositoryCloneRequest,
    RepositoryPushResponse,
    RepositoryResponse,
    PullRequestCreateRequest,
    PullRequestResponse,
    TaskCreateRequest,
    TaskResponse,
    ThreadCreateRequest,
    ThreadResponse,
    SkillDetailResponse,
    SkillSummaryResponse,
)
from app.skills import SkillCatalog
from app.repositories import (
    GitHubClient,
    GitHubConfigurationError,
    GitHubConflictError,
    GitHubError,
    GitHubValidationError,
    RepositoryCatalog,
    RepositoryConflictError,
    RepositoryError,
    RepositoryNotFoundError,
    RepositoryValidationError,
)
from app.core.conversation_runtime import run_conversation_agent
from app.core.conversation import (
    ConversationStore,
    RunStatus,
)
from app.core.task_intent import classify_task_kind
from app.core.run_limits import budget_for
from app.core.review import ReviewFindingStatus, ReviewSeverity
from app.core.task_runtime import (
    AgentFactory,
    TaskStore,
    run_agent_task,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


def _raise_repository_http_error(
    error: RepositoryError,
) -> NoReturn:
    if isinstance(error, RepositoryValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(error, RepositoryNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, RepositoryConflictError):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_409_CONFLICT

    raise HTTPException(
        status_code=status_code,
        detail=str(error),
    ) from error


def _raise_github_http_error(error: GitHubError) -> NoReturn:
    if isinstance(error, GitHubValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(error, GitHubConfigurationError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(error, GitHubConflictError):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_409_CONFLICT

    raise HTTPException(
        status_code=status_code,
        detail=str(error),
    ) from error


@router.get(
    "/api/repositories",
    response_model=list[RepositoryResponse],
)
def list_repositories(
    request: Request,
) -> list[RepositoryResponse]:
    workspace: Workspace = request.app.state.workspace

    try:
        snapshots = RepositoryCatalog(workspace).discover()
    except RepositoryError as error:
        _raise_repository_http_error(error)

    return [
        RepositoryResponse.model_validate(snapshot)
        for snapshot in snapshots
    ]


@router.post(
    "/api/repositories/clone",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def clone_repository(
    body: RepositoryCloneRequest,
    request: Request,
) -> RepositoryResponse:
    workspace: Workspace = request.app.state.workspace

    try:
        snapshot = RepositoryCatalog(workspace).clone(body.url)
    except RepositoryError as error:
        _raise_repository_http_error(error)

    return RepositoryResponse.model_validate(snapshot)


@router.post(
    "/api/repositories/{repository_name}/fetch",
    response_model=RepositoryResponse,
)
def fetch_repository(
    repository_name: str,
    request: Request,
) -> RepositoryResponse:
    workspace: Workspace = request.app.state.workspace

    try:
        snapshot = RepositoryCatalog(workspace).fetch(repository_name)
    except RepositoryError as error:
        _raise_repository_http_error(error)

    return RepositoryResponse.model_validate(snapshot)


@router.post(
    "/api/repositories/{repository_name}/commit",
    response_model=RepositoryCommitResponse,
)
def commit_repository(
    repository_name: str,
    body: RepositoryCommitRequest,
    request: Request,
) -> RepositoryCommitResponse:
    workspace: Workspace = request.app.state.workspace

    try:
        result = RepositoryCatalog(workspace).commit(
            repository_name,
            body.message,
        )
    except RepositoryError as error:
        _raise_repository_http_error(error)

    return RepositoryCommitResponse.model_validate(result)


@router.post(
    "/api/repositories/{repository_name}/push",
    response_model=RepositoryPushResponse,
)
def push_repository(
    repository_name: str,
    request: Request,
) -> RepositoryPushResponse:
    workspace: Workspace = request.app.state.workspace

    try:
        result = RepositoryCatalog(workspace).push(repository_name)
    except RepositoryError as error:
        _raise_repository_http_error(error)

    return RepositoryPushResponse.model_validate(result)


@router.post(
    "/api/repositories/{repository_name}/pull-requests",
    response_model=PullRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_repository_pull_request(
    repository_name: str,
    body: PullRequestCreateRequest,
    request: Request,
) -> PullRequestResponse:
    workspace: Workspace = request.app.state.workspace
    catalog = RepositoryCatalog(workspace)

    try:
        repository = catalog.prepare_pull_request(
            repository_name,
            body.base,
        )
        result = GitHubClient(workspace).create_pull_request(
            repository_name=repository.name,
            remote_url=repository.remote_url,
            head=repository.current_branch,
            base=body.base,
            title=body.title,
            body=body.body,
        )
    except RepositoryError as error:
        _raise_repository_http_error(error)
    except GitHubError as error:
        _raise_github_http_error(error)

    return PullRequestResponse.model_validate(result)


@router.post(
    "/api/repositories/{repository_name}/branches",
    response_model=RepositoryResponse,
)
def create_repository_branch(
    repository_name: str,
    body: RepositoryBranchRequest,
    request: Request,
) -> RepositoryResponse:
    workspace: Workspace = request.app.state.workspace

    try:
        snapshot = RepositoryCatalog(workspace).create_branch(
            repository_name,
            body.name,
        )
    except RepositoryError as error:
        _raise_repository_http_error(error)

    return RepositoryResponse.model_validate(snapshot)


@router.post(
    "/api/repositories/{repository_name}/checkout",
    response_model=RepositoryResponse,
)
def checkout_repository_branch(
    repository_name: str,
    body: RepositoryBranchRequest,
    request: Request,
) -> RepositoryResponse:
    workspace: Workspace = request.app.state.workspace

    try:
        snapshot = RepositoryCatalog(workspace).checkout(
            repository_name,
            body.name,
        )
    except RepositoryError as error:
        _raise_repository_http_error(error)

    return RepositoryResponse.model_validate(snapshot)

@router.get(
    "/api/skills",
    response_model=list[SkillSummaryResponse],
)
def list_skills(
    request: Request,
) -> list[SkillSummaryResponse]:
    workspace: Workspace = request.app.state.workspace

    return [
        SkillSummaryResponse(
            name=skill.name,
            description=skill.description,
            path=skill.path,
        )
        for skill in SkillCatalog(workspace).discover()
    ]


@router.get(
    "/api/skills/{skill_name}",
    response_model=SkillDetailResponse,
)
def get_skill(
    skill_name: Annotated[
        str,
        ApiPath(
            pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        ),
    ],
    request: Request,
) -> SkillDetailResponse:
    workspace: Workspace = request.app.state.workspace
    skill = SkillCatalog(workspace).get(skill_name)

    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="skill not found",
        )

    return SkillDetailResponse(
        name=skill.name,
        description=skill.description,
        path=skill.path,
        content=skill.content,
    )

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
    task_store: TaskStore = request.app.state.task_store
    agent_factory: AgentFactory = request.app.state.agent_factory

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
    task_store: TaskStore = request.app.state.task_store

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

    task_store: TaskStore = request.app.state.task_store

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
                "terminated",
            }:
                terminal_seen = True

            yield ServerSentEvent(
                id=str(event.id),
                event=event.kind,
                retry=3000,
                data={
                    "thread_id": event.thread_id,
                    "source": event.source,
                    "created_at": (event.created_at.isoformat()),
                    **event.payload,
                },
            )

        if terminal_seen:
            return

        await asyncio.sleep(0.2)


def _validated_project_path(
    workspace: Workspace,
    virtual_path: str,
) -> str:
    """验证项目路径是 /projects 下的直接子目录。"""

    try:
        real_path = workspace.resolve(virtual_path)
        canonical_path = workspace.to_virtual(real_path)
    except WorkspacePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    parts = PurePosixPath(canonical_path).parts

    if len(parts) != 3 or parts[0] != "/" or parts[1] != "projects":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=("项目必须是 /projects 下的直接子目录"),
        )

    if not real_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="项目目录不存在",
        )

    return canonical_path


@router.post(
    "/api/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    body: ProjectCreateRequest,
    request: Request,
) -> ProjectResponse:
    navigation_store: ConversationStore = request.app.state.navigation_store
    workspace: Workspace = request.app.state.workspace

    virtual_path = _validated_project_path(
        workspace,
        body.virtual_path,
    )

    try:
        project = navigation_store.create_project(
            name=body.name,
            virtual_path=virtual_path,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return ProjectResponse.model_validate(project)


@router.get(
    "/api/projects",
    response_model=list[ProjectResponse],
)
def list_projects(
    request: Request,
) -> list[ProjectResponse]:
    navigation_store: ConversationStore = request.app.state.navigation_store

    return [
        ProjectResponse.model_validate(project)
        for project in navigation_store.list_projects()
    ]


@router.post(
    "/api/projects/{project_id}/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_thread(
    project_id: str,
    body: ThreadCreateRequest,
    request: Request,
) -> ThreadResponse:
    navigation_store: ConversationStore = request.app.state.navigation_store

    if navigation_store.get_project(project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project not found",
        )

    thread = navigation_store.create_thread(
        project_id=project_id,
        title=body.title,
    )

    return ThreadResponse.model_validate(thread)


@router.get(
    "/api/projects/{project_id}/threads",
    response_model=list[ThreadResponse],
)
def list_threads(
    project_id: str,
    request: Request,
) -> list[ThreadResponse]:
    navigation_store: ConversationStore = request.app.state.navigation_store

    if navigation_store.get_project(project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project not found",
        )

    return [
        ThreadResponse.model_validate(thread)
        for thread in navigation_store.list_threads(project_id)
    ]


@router.get(
    "/api/threads/{thread_id}",
    response_model=ThreadResponse,
)
def get_thread(
    thread_id: str,
    request: Request,
) -> ThreadResponse:
    navigation_store: ConversationStore = request.app.state.navigation_store

    thread = navigation_store.get_thread(thread_id)

    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="thread not found",
        )

    return ThreadResponse.model_validate(thread)


@router.get(
    "/api/threads/{thread_id}/messages",
    response_model=list[MessageResponse],
)
def list_thread_messages(
    thread_id: str,
    request: Request,
) -> list[MessageResponse]:
    navigation_store: ConversationStore = request.app.state.navigation_store

    if navigation_store.get_thread(thread_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="thread not found",
        )

    return [
        MessageResponse.model_validate(message)
        for message in navigation_store.list_messages(thread_id)
    ]


@router.get(
    "/api/threads/{thread_id}/runs",
    response_model=list[RunResponse],
)
def list_thread_runs(
    thread_id: str,
    request: Request,
) -> list[RunResponse]:
    navigation_store: ConversationStore = request.app.state.navigation_store

    if navigation_store.get_thread(thread_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="thread not found",
        )

    return [
        RunResponse.model_validate(run) for run in navigation_store.list_runs(thread_id)
    ]


@router.get(
    "/api/runs/{run_id}",
    response_model=RunResponse,
)
def get_run(
    run_id: str,
    request: Request,
) -> RunResponse:
    navigation_store: ConversationStore = request.app.state.navigation_store

    run = navigation_store.get_run(run_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )

    return RunResponse.model_validate(run)


@router.get(
    "/api/runs/{run_id}/performance",
    response_model=RunPerformanceResponse | None,
)
def get_run_performance(
    run_id: str,
    request: Request,
) -> RunPerformanceResponse | None:
    conversation_store: ConversationStore = (
        request.app.state.navigation_store
    )
    if conversation_store.get_run(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    performance = conversation_store.get_run_performance(run_id)
    if performance is None:
        return None
    return RunPerformanceResponse.model_validate(performance)


@router.get(
    "/api/runs/{run_id}/review-findings",
    response_model=list[ReviewFindingResponse],
)
def list_run_review_findings(
    run_id: str,
    request: Request,
    severity: ReviewSeverity | None = None,
    finding_status: Annotated[
        ReviewFindingStatus | None,
        Query(alias="status"),
    ] = None,
) -> list[ReviewFindingResponse]:
    conversation_store: ConversationStore = (
        request.app.state.navigation_store
    )
    if conversation_store.get_run(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    return [
        ReviewFindingResponse.model_validate(finding)
        for finding in conversation_store.list_review_findings(
            run_id,
            severity=severity,
            status=finding_status,
        )
    ]


@router.patch(
    "/api/runs/{run_id}/review-findings/{finding_id}",
    response_model=ReviewFindingResponse,
)
def update_run_review_finding(
    run_id: str,
    finding_id: str,
    body: ReviewFindingStatusUpdateRequest,
    request: Request,
) -> ReviewFindingResponse:
    conversation_store: ConversationStore = (
        request.app.state.navigation_store
    )
    if conversation_store.get_run(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    try:
        finding = conversation_store.update_review_finding_status(
            run_id=run_id,
            finding_id=finding_id,
            status=body.status,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="review finding not found",
        ) from exc
    return ReviewFindingResponse.model_validate(finding)


def require_existing_run_store(
    run_id: str,
    request: Request,
) -> ConversationStore:
    """取得 ConversationStore，并在响应开始前验证 Run。"""

    conversation_store: ConversationStore = (
        request.app.state.navigation_store
    )

    if conversation_store.get_run(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )

    return conversation_store


@router.get(
    "/api/runs/{run_id}/events",
    response_class=EventSourceResponse,
)
async def stream_run_events(
    run_id: str,
    conversation_store: Annotated[
        ConversationStore,
        Depends(require_existing_run_store),
    ],
    last_event_id: Annotated[
        int | None,
        Header(alias="Last-Event-ID"),
    ] = None,
):
    cursor = max(last_event_id or 0, 0)
    terminal_seen = False

    while True:
        events = conversation_store.list_run_events(
            run_id,
            after_id=cursor,
        )

        for event in events:
            cursor = event.event_id

            if event.kind in {
                "completed",
                "failed",
                "terminated",
            }:
                terminal_seen = True

            yield ServerSentEvent(
                id=str(event.event_id),
                event=event.kind,
                retry=3000,
                data={
                    "run_id": event.run_id,
                    "source": event.source,
                    "created_at": (
                        event.created_at.isoformat()
                    ),
                    **event.payload,
                },
            )

        if terminal_seen:
            return

        run = conversation_store.get_run(run_id)

        if (
            run is not None
            and run.status
            in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }
            and not events
        ):
            return

        await asyncio.sleep(0.2)


@router.post(
    "/api/threads/{thread_id}/runs",
    response_model=RunStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_thread_run(
    thread_id: str,
    body: RunCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> RunStartResponse:
    navigation_store: ConversationStore = (
        request.app.state.navigation_store
    )

    try:
        task_kind = body.task_kind or classify_task_kind(
            body.content
        )
        budget = budget_for(task_kind)
        run, message = (
            navigation_store.start_run_with_message(
                thread_id=thread_id,
                content=body.content,
                task_kind=task_kind,
            )
        )
        try:
            navigation_store.initialize_run_performance(
                run_id=run.run_id,
                task_kind=task_kind,
                max_model_calls=budget.max_model_calls,
                max_tool_calls=budget.max_tool_calls,
                max_first_output_seconds=(
                    budget.max_first_output_seconds
                ),
                max_seconds=budget.max_seconds,
                max_identical_tool_calls=(
                    budget.max_identical_tool_calls
                ),
            )
        except Exception as exc:
            navigation_store.fail_run(
                run.run_id,
                "Run 性能指标初始化失败",
            )
            raise RuntimeError(
                "failed to initialize run performance"
            ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="thread not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    background_tasks.add_task(
        run_conversation_agent,
        run_id=run.run_id,
        conversation_store=navigation_store,
        agent_factory=request.app.state.agent_factory,
        workspace=request.app.state.workspace,
    )

    return RunStartResponse(
        run=RunResponse.model_validate(run),
        message=MessageResponse.model_validate(
            message
        ),
    )
