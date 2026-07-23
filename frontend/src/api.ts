export const threadStatuses = ['idle', 'running', 'error'] as const
export const runStatuses = [
  'pending',
  'running',
  'completed',
  'failed',
  'cancelled',
] as const
export const messageRoles = ['user', 'assistant', 'system'] as const
export const runEventKinds = [
  'created',
  'running',
  'token',
  'tool_started',
  'tool_finished',
  'completed',
  'failed',
  'terminated',
  'review_findings_saved',
] as const

export type ThreadStatus = (typeof threadStatuses)[number]
export type RunStatus = (typeof runStatuses)[number]
export type MessageRole = (typeof messageRoles)[number]
export type RunEventKind = (typeof runEventKinds)[number]
export type TaskKind = 'coding' | 'analysis' | 'planning' | 'qa'
export type ReviewSeverity = 'critical' | 'high' | 'medium' | 'low'
export type ReviewCategory =
  | 'correctness'
  | 'security'
  | 'performance'
  | 'maintainability'
  | 'testing'
  | 'documentation'
export type ReviewFindingStatus = 'open' | 'resolved' | 'dismissed'

export interface Project {
  project_id: string
  name: string
  virtual_path: string
  created_at: string
  updated_at: string
}

export interface Thread {
  thread_id: string
  project_id: string
  title: string
  status: ThreadStatus
  created_at: string
  updated_at: string
}

export interface Message {
  sequence: number
  message_id: string
  thread_id: string
  run_id: string | null
  role: MessageRole
  content: string
  created_at: string
}

export interface Run {
  run_id: string
  thread_id: string
  task_kind: TaskKind
  status: RunStatus
  error: string | null
  created_at: string
  updated_at: string
}

export interface RunStartResponse {
  run: Run
  message: Message
}

export interface RunPerformance {
  run_id: string
  task_kind: TaskKind
  max_model_calls: number
  max_tool_calls: number
  max_first_output_seconds: number
  max_seconds: number
  max_identical_tool_calls: number
  model_calls: number
  tool_calls: number
  repeated_tool_calls: number
  tool_errors: number
  safety_rejections: number
  first_output_ms: number | null
  duration_ms: number | null
  termination_reason: string | null
  created_at: string
  updated_at: string
}

export interface ReviewFinding {
  id: string
  run_id: string
  severity: ReviewSeverity
  category: ReviewCategory
  file_path: string | null
  start_line: number | null
  end_line: number | null
  title: string
  description: string
  suggestion: string | null
  status: ReviewFindingStatus
  fingerprint: string
  created_at: string
  updated_at: string
}

export interface SkillSummary {
  name: string
  description: string
  path: string
}

export interface SkillDetail extends SkillSummary {
  content: string
}

export interface Repository {
  name: string
  path: string
  remote_url: string
  current_branch: string
  branches: string[]
  dirty: boolean
}

export interface RepositoryCommitResult {
  repository: Repository
  sha: string
  subject: string
}

export interface RepositoryPushResult {
  repository: Repository
  branch: string
}

export interface PullRequest {
  number: number
  url: string
  title: string
  base: string
  head: string
}

export interface RunEventPayload {
  run_id: string
  source: string
  created_at: string
  status?: RunStatus | 'error'
  task_kind?: TaskKind
  text?: string
  name?: string
  tool_call_id?: string
  subagent?: string
  error?: string
  recoverable?: boolean
  termination_reason?: string
  created_count?: number
  duplicate_count?: number
  rejected_count?: number
  summary?: string
  budget?: Pick<
    RunPerformance,
    | 'max_model_calls'
    | 'max_tool_calls'
    | 'max_first_output_seconds'
    | 'max_seconds'
    | 'max_identical_tool_calls'
  >
}

async function requestJson<T>(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(input, init)

  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`

    try {
      const body: unknown = await response.json()
      if (
        typeof body === 'object' &&
        body !== null &&
        'detail' in body &&
        typeof body.detail === 'string'
      ) {
        message = body.detail
      }
    } catch {
      // 非 JSON 错误响应保留 HTTP 状态提示。
    }

    throw new Error(message)
  }

  return response.json() as Promise<T>
}

function jsonRequest(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  }
}

export function listProjects(): Promise<Project[]> {
  return requestJson<Project[]>('/api/projects')
}

export function createProject(input: {
  name: string
  virtual_path: string
}): Promise<Project> {
  return requestJson<Project>('/api/projects', jsonRequest('POST', input))
}

export function listThreads(projectId: string): Promise<Thread[]> {
  return requestJson<Thread[]>(
    `/api/projects/${encodeURIComponent(projectId)}/threads`,
  )
}

export function createThread(projectId: string, title = '新对话'): Promise<Thread> {
  return requestJson<Thread>(
    `/api/projects/${encodeURIComponent(projectId)}/threads`,
    jsonRequest('POST', { title }),
  )
}

export function getThread(threadId: string): Promise<Thread> {
  return requestJson<Thread>(`/api/threads/${encodeURIComponent(threadId)}`)
}

export function listMessages(threadId: string): Promise<Message[]> {
  return requestJson<Message[]>(
    `/api/threads/${encodeURIComponent(threadId)}/messages`,
  )
}

export function listRuns(threadId: string): Promise<Run[]> {
  return requestJson<Run[]>(`/api/threads/${encodeURIComponent(threadId)}/runs`)
}

export function getRun(runId: string): Promise<Run> {
  return requestJson<Run>(`/api/runs/${encodeURIComponent(runId)}`)
}

export function getRunPerformance(runId: string): Promise<RunPerformance | null> {
  return requestJson<RunPerformance | null>(
    `/api/runs/${encodeURIComponent(runId)}/performance`,
  )
}

export function listReviewFindings(
  runId: string,
  filters: { severity?: ReviewSeverity; status?: ReviewFindingStatus } = {},
): Promise<ReviewFinding[]> {
  const query = new URLSearchParams()
  if (filters.severity) query.set('severity', filters.severity)
  if (filters.status) query.set('status', filters.status)
  const suffix = query.size ? `?${query.toString()}` : ''
  return requestJson<ReviewFinding[]>(
    `/api/runs/${encodeURIComponent(runId)}/review-findings${suffix}`,
  )
}

export function updateReviewFindingStatus(
  runId: string,
  findingId: string,
  status: ReviewFindingStatus,
): Promise<ReviewFinding> {
  return requestJson<ReviewFinding>(
    `/api/runs/${encodeURIComponent(runId)}/review-findings/${encodeURIComponent(findingId)}`,
    jsonRequest('PATCH', { status }),
  )
}

export function startRun(
  threadId: string,
  content: string,
  taskKind: TaskKind,
): Promise<RunStartResponse> {
  return requestJson<RunStartResponse>(
    `/api/threads/${encodeURIComponent(threadId)}/runs`,
    jsonRequest('POST', { content, task_kind: taskKind }),
  )
}

export function runEventsUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/events`
}

export function listSkills(): Promise<SkillSummary[]> {
  return requestJson<SkillSummary[]>('/api/skills')
}

export function getSkill(skillName: string): Promise<SkillDetail> {
  return requestJson<SkillDetail>(
    `/api/skills/${encodeURIComponent(skillName)}`,
  )
}

export function listRepositories(): Promise<Repository[]> {
  return requestJson<Repository[]>('/api/repositories')
}

export function cloneRepository(url: string): Promise<Repository> {
  return requestJson<Repository>(
    '/api/repositories/clone',
    jsonRequest('POST', { url }),
  )
}

export function fetchRepository(name: string): Promise<Repository> {
  return requestJson<Repository>(
    `/api/repositories/${encodeURIComponent(name)}/fetch`,
    jsonRequest('POST', {}),
  )
}

export function createRepositoryBranch(
  repositoryName: string,
  branchName: string,
): Promise<Repository> {
  return requestJson<Repository>(
    `/api/repositories/${encodeURIComponent(repositoryName)}/branches`,
    jsonRequest('POST', { name: branchName }),
  )
}

export function checkoutRepositoryBranch(
  repositoryName: string,
  branchName: string,
): Promise<Repository> {
  return requestJson<Repository>(
    `/api/repositories/${encodeURIComponent(repositoryName)}/checkout`,
    jsonRequest('POST', { name: branchName }),
  )
}

export function commitRepository(
  repositoryName: string,
  message: string,
): Promise<RepositoryCommitResult> {
  return requestJson<RepositoryCommitResult>(
    `/api/repositories/${encodeURIComponent(repositoryName)}/commit`,
    jsonRequest('POST', { message }),
  )
}

export function pushRepository(
  repositoryName: string,
): Promise<RepositoryPushResult> {
  return requestJson<RepositoryPushResult>(
    `/api/repositories/${encodeURIComponent(repositoryName)}/push`,
    jsonRequest('POST', {}),
  )
}

export function createRepositoryPullRequest(
  repositoryName: string,
  input: {
    title: string
    body: string
    base: string
  },
): Promise<PullRequest> {
  return requestJson<PullRequest>(
    `/api/repositories/${encodeURIComponent(repositoryName)}/pull-requests`,
    jsonRequest('POST', input),
  )
}
