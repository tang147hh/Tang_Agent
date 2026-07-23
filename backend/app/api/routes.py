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
    CodeReviewRequest,
    CodeReviewResponse,
    ProjectCreateRequest,
    ProjectResponse,
    RunCreateRequest,
    RunResponse,
    RunPerformanceResponse,
    RunStartResponse,
    ReviewFindingResponse,
    ReviewFindingStatusUpdateRequest,
    ReviewSnapshotResponse,
    GitHubReviewCapabilityResponse,
    GitHubReviewPrepareRequest,
    GitHubReviewPrepareResponse,
    GitHubReviewPublicationResponse,
    GitHubReviewPublishRequest,
    RepositoryBranchRequest,
    RepositoryCommitRequest,
    RepositoryCommitResponse,
    RepositoryCloneRequest,
    RepositoryPushResponse,
    RepositoryResponse,
    ProjectFileChangesResponse,
    PullRequestCreateRequest,
    PullRequestResponse,
    TaskCreateRequest,
    TaskResponse,
    ThreadCreateRequest,
    ThreadResponse,
    SkillDetailResponse,
    SkillSummaryResponse,
    ToolCapabilitiesResponse,
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
from app.core.task_intent import TaskKind, classify_task_kind
from app.core.run_limits import budget_for, network_budget_for
from app.tools.capabilities import (
    TOOL_CAPABILITIES,
    capability_for,
    task_allows_tool,
)
from app.core.review import ReviewFindingStatus, ReviewSeverity
from app.core.review import ReviewOutputError
from app.core.code_review import (
    CodeReviewError,
    CodeReviewErrorCode,
    CodeReviewService,
)
from app.core.review_diff import (
    ReviewDiffError,
    ReviewDiffErrorCode,
    ReviewDiffLimits,
)
from app.core.github_review import (
    GitHubReviewError,
    GitHubReviewErrorCode,
    GitHubReviewService,
)
from app.core.task_runtime import (
    AgentFactory,
    TaskStore,
    run_agent_task,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


def _tool_capabilities_payload(
    request: Request,
    *,
    task_kind: TaskKind,
    network_access: bool,
    run_id: str | None = None,
    network_provider: str | None = None,
) -> ToolCapabilitiesResponse:
    provider = request.app.state.search_provider
    provider_status = provider.availability()
    selected_provider = network_provider or provider.provider_name
    provider_matches = selected_provider == provider.provider_name
    allowed_in_mode = task_allows_tool(task_kind, "web_search")
    web_available = (
        allowed_in_mode
        and network_access
        and provider_status.available
        and provider_matches
    )
    if not allowed_in_mode:
        reason = "当前模式禁止联网搜索。"
    elif not network_access:
        reason = "当前 Run 未允许联网搜索。"
    elif not provider_matches:
        reason = "当前 Run 的搜索提供商配置已经变化。"
    elif not provider_status.available:
        reason = provider_status.reason or "网页搜索提供商不可用。"
    else:
        reason = None

    tools = []
    for name, registered in TOOL_CAPABILITIES.items():
        if name == "web_search":
            current = capability_for(
                name,
                availability=web_available,
                unavailable_reason=reason,
            )
        elif not registered.model_callable:
            current = capability_for(
                name,
                availability=False,
                unavailable_reason="该能力只能通过专用 API 和用户确认执行。",
            )
        else:
            permitted = task_allows_tool(task_kind, name)
            current = capability_for(
                name,
                availability=permitted,
                unavailable_reason=(
                    None if permitted else "当前模式禁止该工具。"
                ),
            )
        tools.append(current.to_dict())

    budget = network_budget_for(task_kind)
    return ToolCapabilitiesResponse.model_validate(
        {
            "task_kind": task_kind,
            "run_id": run_id,
            "network_access": network_access,
            "network_provider": selected_provider,
            "web_search": {
                "available": web_available,
                "provider": selected_provider,
                "configured": provider_status.configured,
                "provider_available": provider_status.available,
                "allowed_in_mode": allowed_in_mode,
                "enabled_for_run": network_access,
                "unavailable_reason": reason,
            },
            "network_budget": {
                "max_searches": budget.max_searches,
                "max_results_per_search": budget.max_results_per_search,
                "request_timeout_seconds": budget.request_timeout_seconds,
                "max_result_chars_per_search": (
                    budget.max_result_chars_per_search
                ),
                "max_total_result_chars": budget.max_total_result_chars,
                "max_bytes_received": budget.max_bytes_received,
            },
            "tools": tools,
        }
    )


@router.get(
    "/api/tool-capabilities",
    response_model=ToolCapabilitiesResponse,
)
def get_tool_capabilities(
    request: Request,
    task_kind: Annotated[TaskKind, Query()] = TaskKind.CODING,
    network_access: Annotated[bool, Query()] = False,
) -> ToolCapabilitiesResponse:
    return _tool_capabilities_payload(
        request,
        task_kind=task_kind,
        network_access=network_access,
    )


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


def _raise_github_review_http_error(error: GitHubReviewError) -> NoReturn:
    if error.code in {
        GitHubReviewErrorCode.GH_NOT_INSTALLED,
        GitHubReviewErrorCode.GITHUB_NOT_AUTHENTICATED,
        GitHubReviewErrorCode.PUBLISHING_DISABLED,
    }:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif error.code is GitHubReviewErrorCode.PERMISSION_DENIED:
        status_code = status.HTTP_403_FORBIDDEN
    elif error.code is GitHubReviewErrorCode.PULL_REQUEST_NOT_FOUND:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is GitHubReviewErrorCode.GITHUB_TIMEOUT:
        status_code = status.HTTP_504_GATEWAY_TIMEOUT
    elif error.code in {
        GitHubReviewErrorCode.REVIEW_NOT_PUBLISHABLE,
        GitHubReviewErrorCode.FINDING_NOT_PUBLISHABLE,
        GitHubReviewErrorCode.UNSUPPORTED_GITHUB_HOST,
        GitHubReviewErrorCode.GITHUB_REMOTE_NOT_FOUND,
    }:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        status_code = status.HTTP_409_CONFLICT
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code.value, "message": error.message},
    ) from error


def _github_review_service(request: Request) -> GitHubReviewService:
    return GitHubReviewService(
        store=request.app.state.navigation_store,
        workspace=request.app.state.workspace,
        settings=request.app.state.settings,
        runner=request.app.state.github_runner,
    )


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


@router.get(
    "/api/projects/{project_id}/file-changes",
    response_model=ProjectFileChangesResponse,
)
def get_project_file_changes(
    project_id: str,
    request: Request,
) -> ProjectFileChangesResponse:
    store: ConversationStore = request.app.state.navigation_store
    workspace: Workspace = request.app.state.workspace
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project not found",
        )

    repository_name = PurePosixPath(project.virtual_path).name
    try:
        snapshot = RepositoryCatalog(workspace).file_changes(
            repository_name
        )
    except RepositoryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "repository_not_found",
                "message": "当前项目不是 Git 仓库",
            },
        ) from error
    except RepositoryError as error:
        _raise_repository_http_error(error)

    if snapshot.project_path != project.virtual_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="repository not found",
        )
    return ProjectFileChangesResponse.model_validate(snapshot)


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
    "/api/runs/{run_id}/tool-capabilities",
    response_model=ToolCapabilitiesResponse,
)
def get_run_tool_capabilities(
    run_id: str,
    request: Request,
) -> ToolCapabilitiesResponse:
    navigation_store: ConversationStore = request.app.state.navigation_store
    run = navigation_store.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    return _tool_capabilities_payload(
        request,
        task_kind=run.task_kind,
        network_access=run.network_access,
        run_id=run.run_id,
        network_provider=run.network_provider,
    )


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


@router.post(
    "/api/runs/{run_id}/reviews",
    response_model=CodeReviewResponse,
)
def review_run_diff(
    run_id: str,
    body: CodeReviewRequest,
    request: Request,
) -> CodeReviewResponse:
    conversation_store: ConversationStore = (
        request.app.state.navigation_store
    )
    workspace: Workspace = request.app.state.workspace
    reviewer = request.app.state.reviewer
    try:
        result = CodeReviewService(
            store=conversation_store,
            workspace=workspace,
            reviewer=reviewer,
            limits=ReviewDiffLimits.from_settings(
                request.app.state.settings
            ),
            github_review_service=_github_review_service(request),
        ).review_run(
            run_id=run_id,
            scope=body.scope,
            source=body.source,
            pr_number=body.pr_number,
        )
    except GitHubReviewError as exc:
        _raise_github_review_http_error(exc)
    except ReviewDiffError as exc:
        if exc.code in {
            ReviewDiffErrorCode.RUN_NOT_FOUND,
            ReviewDiffErrorCode.PROJECT_NOT_FOUND,
            ReviewDiffErrorCode.REPOSITORY_NOT_FOUND,
        }:
            status_code = status.HTTP_404_NOT_FOUND
        elif exc.code is ReviewDiffErrorCode.GIT_COMMAND_TIMEOUT:
            status_code = status.HTTP_504_GATEWAY_TIMEOUT
        elif exc.code is ReviewDiffErrorCode.RUN_TIME_LIMIT:
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        elif exc.code is ReviewDiffErrorCode.REPOSITORY_OUTSIDE_WORKSPACE:
            status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        else:
            status_code = status.HTTP_409_CONFLICT
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code.value, "message": exc.message},
        ) from exc
    except CodeReviewError as exc:
        if exc.code is CodeReviewErrorCode.REVIEWER_UNAVAILABLE:
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif exc.code is CodeReviewErrorCode.BUDGET_EXCEEDED:
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        elif exc.code is CodeReviewErrorCode.ALREADY_EXISTS:
            status_code = status.HTTP_409_CONFLICT
        else:
            status_code = status.HTTP_502_BAD_GATEWAY
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code.value, "message": exc.message},
        ) from exc
    except ReviewOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "reviewer_output_invalid",
                "message": str(exc),
            },
        ) from exc
    return CodeReviewResponse.model_validate(result)


@router.post(
    "/api/threads/{thread_id}/review-runs",
    response_model=CodeReviewResponse,
)
def start_thread_review_run(
    thread_id: str,
    body: CodeReviewRequest,
    request: Request,
) -> CodeReviewResponse:
    """创建独立 analysis Run，保证重新审查不覆盖历史结果。"""

    conversation_store: ConversationStore = (
        request.app.state.navigation_store
    )
    if conversation_store.get_thread(thread_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="thread not found",
        )
    budget = budget_for(TaskKind.ANALYSIS)
    try:
        run = conversation_store.create_run(
            thread_id=thread_id,
            task_kind=TaskKind.ANALYSIS,
        )
        conversation_store.initialize_run_performance(
            run_id=run.run_id,
            task_kind=TaskKind.ANALYSIS,
            max_model_calls=budget.max_model_calls,
            max_tool_calls=budget.max_tool_calls,
            max_first_output_seconds=budget.max_first_output_seconds,
            max_seconds=budget.max_seconds,
            max_identical_tool_calls=budget.max_identical_tool_calls,
        )
        conversation_store.mark_run_running(run.run_id)
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

    try:
        response = review_run_diff(run.run_id, body, request)
    except HTTPException as exc:
        current = conversation_store.get_run(run.run_id)
        if current is not None and current.status in {
            RunStatus.PENDING,
            RunStatus.RUNNING,
        }:
            message = (
                str(exc.detail.get("message", "代码审查失败"))
                if isinstance(exc.detail, dict)
                else str(exc.detail)
            )
            conversation_store.fail_run(run.run_id, message)
        detail = (
            {**exc.detail, "run_id": run.run_id}
            if isinstance(exc.detail, dict)
            else {"message": str(exc.detail), "run_id": run.run_id}
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=detail,
            headers=exc.headers,
        ) from exc

    conversation_store.complete_run(run.run_id)
    return response


@router.get(
    "/api/runs/{run_id}/review",
    response_model=ReviewSnapshotResponse,
)
def get_run_review_snapshot(
    run_id: str,
    request: Request,
) -> ReviewSnapshotResponse:
    """只从持久化快照读取，不重新执行 Git Diff。"""

    conversation_store: ConversationStore = (
        request.app.state.navigation_store
    )
    if conversation_store.get_run(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    snapshot = conversation_store.get_review_diff_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="review snapshot not found",
        )
    findings = conversation_store.list_review_findings(run_id)
    return ReviewSnapshotResponse.model_validate(
        {
            "run_id": run_id,
            "status": snapshot.status,
            "scope": snapshot.diff.scope,
            "diff": snapshot.diff,
            "findings": findings,
            "finding_count": len(findings),
            "summary": snapshot.summary,
            "created_at": snapshot.created_at,
            "updated_at": snapshot.updated_at,
        }
    )


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


@router.get(
    "/api/projects/{project_id}/github-review/capability",
    response_model=GitHubReviewCapabilityResponse,
)
def get_project_github_review_capability(
    project_id: str,
    request: Request,
) -> GitHubReviewCapabilityResponse:
    try:
        capability = _github_review_service(request).capability(
            project_id=project_id
        )
    except GitHubReviewError as exc:
        _raise_github_review_http_error(exc)
    return GitHubReviewCapabilityResponse.model_validate(capability)


@router.get(
    "/api/runs/{run_id}/github-review/capability",
    response_model=GitHubReviewCapabilityResponse,
)
def get_github_review_capability(
    run_id: str,
    request: Request,
) -> GitHubReviewCapabilityResponse:
    try:
        capability = _github_review_service(request).capability(run_id)
    except GitHubReviewError as exc:
        _raise_github_review_http_error(exc)
    return GitHubReviewCapabilityResponse.model_validate(capability)


@router.post(
    "/api/runs/{run_id}/github-review/prepare",
    response_model=GitHubReviewPrepareResponse,
)
def prepare_github_review(
    run_id: str,
    body: GitHubReviewPrepareRequest,
    request: Request,
) -> GitHubReviewPrepareResponse:
    try:
        preview = _github_review_service(request).prepare(
            run_id=run_id,
            pr_number=body.pr_number,
            selected_finding_ids=body.selected_finding_ids,
            event=body.event,
            summary=body.summary,
        )
    except GitHubReviewError as exc:
        _raise_github_review_http_error(exc)
    return GitHubReviewPrepareResponse.model_validate(preview)


@router.post(
    "/api/runs/{run_id}/github-review/publish",
    response_model=GitHubReviewPublicationResponse,
)
def publish_github_review(
    run_id: str,
    body: GitHubReviewPublishRequest,
    request: Request,
) -> GitHubReviewPublicationResponse:
    try:
        publication = _github_review_service(request).publish(
            run_id=run_id,
            publication_id=body.publication_id,
        )
    except GitHubReviewError as exc:
        _raise_github_review_http_error(exc)
    return GitHubReviewPublicationResponse.model_validate(publication)


@router.get(
    "/api/runs/{run_id}/github-review/publications",
    response_model=list[GitHubReviewPublicationResponse],
)
def list_github_review_publications(
    run_id: str,
    request: Request,
) -> list[GitHubReviewPublicationResponse]:
    store: ConversationStore = request.app.state.navigation_store
    if store.get_run(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    return [
        GitHubReviewPublicationResponse.model_validate(publication)
        for publication in store.list_github_review_publications(run_id)
    ]


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
                network_access=body.network_access,
                network_provider=(
                    request.app.state.search_provider.provider_name
                ),
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
        search_provider=request.app.state.search_provider,
        search_cache=request.app.state.search_cache,
    )

    return RunStartResponse(
        run=RunResponse.model_validate(run),
        message=MessageResponse.model_validate(
            message
        ),
    )
