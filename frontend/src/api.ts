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
export type ReviewScope = 'staged' | 'unstaged' | 'all'
export type ReviewSource = 'working_tree' | 'pull_request'
export type ReviewLineSide = 'old' | 'new'
export type ReviewStatus = 'collected' | 'completed' | 'failed'
export type DiffLineType = 'context' | 'addition' | 'deletion' | 'no_newline'
export type ReviewChangeType =
  | 'modified'
  | 'added'
  | 'deleted'
  | 'renamed'
  | 'copied'
  | 'untracked'
export type ReviewTruncationReason =
  | 'max_files'
  | 'file_patch_chars'
  | 'file_changed_lines'
  | 'total_patch_chars'
  | 'total_changed_lines'
  | 'git_output'
  | 'github_patch_unavailable'

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
  network_access: boolean
  network_provider: string
  network_request_count: number
  network_result_count: number
  network_bytes_received: number
  network_cache_hit_count: number
  network_limit_reached: boolean
  network_limit_reason: string | null
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

export interface NetworkBudget {
  max_searches: number
  max_results_per_search: number
  request_timeout_seconds: number
  max_result_chars_per_search: number
  max_total_result_chars: number
  max_bytes_received: number
}

export interface ToolCapability {
  name: string
  category: string
  risk_level: string
  allowed_task_kinds: TaskKind[]
  requires_network_access: boolean
  model_callable: boolean
  description: string
  availability: boolean
  unavailable_reason: string | null
}

export interface ToolCapabilities {
  task_kind: TaskKind
  run_id: string | null
  network_access: boolean
  network_provider: string
  web_search: {
    available: boolean
    provider: string
    configured: boolean
    provider_available: boolean
    allowed_in_mode: boolean
    enabled_for_run: boolean
    unavailable_reason: string | null
  }
  network_budget: NetworkBudget
  tools: ToolCapability[]
}

export interface ReviewFinding {
  id: string
  run_id: string
  severity: ReviewSeverity
  category: ReviewCategory
  file_path: string | null
  start_line: number | null
  end_line: number | null
  line_side: ReviewLineSide | null
  title: string
  description: string
  suggestion: string | null
  status: ReviewFindingStatus
  fingerprint: string
  review_diff_hash: string | null
  review_scope: ReviewScope | null
  base_revision: string | null
  head_revision: string | null
  created_at: string
  updated_at: string
}

export interface ReviewDiffFile {
  old_path: string | null
  new_path: string | null
  change_type: ReviewChangeType
  binary: boolean
  submodule: boolean
  additions: number
  deletions: number
  truncated: boolean
  truncation_reason: ReviewTruncationReason | null
  changed_new_lines: number[]
  changed_old_lines: number[]
  redacted: boolean
  hunks: DiffHunk[]
}

export interface DiffLine {
  type: DiffLineType
  old_line_number: number | null
  new_line_number: number | null
  content: string
}

export interface DiffHunk {
  header: string
  old_start: number
  old_count: number
  new_start: number
  new_count: number
  lines: DiffLine[]
}

export interface ReviewDiff {
  scope: ReviewScope
  source: ReviewSource
  repository: string | null
  pr_number: number | null
  repository_virtual_path: string
  base_revision: string | null
  head_revision: string | null
  files: ReviewDiffFile[]
  file_count: number
  total_additions: number
  total_deletions: number
  truncated: boolean
  truncation_reasons: ReviewTruncationReason[]
  content_hash: string
  created_at: string
  redacted: boolean
}

export interface CodeReviewResult {
  run_id: string
  status: 'completed'
  scope: ReviewScope
  diff: ReviewDiff
  findings: ReviewFinding[]
  finding_count: number
  created_count: number
  duplicate_count: number
  summary: string
}

export interface ReviewSnapshot {
  run_id: string
  status: ReviewStatus
  scope: ReviewScope
  diff: ReviewDiff
  findings: ReviewFinding[]
  finding_count: number
  summary: string
  created_at: string
  updated_at: string
}

export type GitHubReviewEvent = 'COMMENT' | 'APPROVE' | 'REQUEST_CHANGES'
export type GitHubPublicationStatus = 'prepared' | 'publishing' | 'published' | 'failed' | 'unknown'

export interface GitHubPullRequest {
  pr_number: number
  title: string
  url: string
  state: string
  is_draft: boolean
  base_branch: string
  head_branch: string
  base_sha: string
  head_sha: string
  author: string
  repository: string
}

export interface GitHubReviewCapability {
  gh_installed: boolean
  authenticated: boolean
  remote_found: boolean
  publish_enabled: boolean
  can_publish: boolean
  reason: string | null
  repository: string | null
  current_user: string | null
  pull_requests: GitHubPullRequest[]
}

export interface GitHubInlineCommentPreview {
  finding_id: string
  path: string
  line: number
  side: 'LEFT' | 'RIGHT'
  body: string
  start_line?: number | null
  start_side?: 'LEFT' | 'RIGHT' | null
}

export interface GitHubFindingPublicationNote {
  finding_id: string
  title: string
  reason: string
}

export interface GitHubReviewPreview {
  publication_id: string
  repository: string
  pr_number: number
  pr_title: string
  pr_url: string
  base_sha: string
  head_sha: string
  event: GitHubReviewEvent
  inline_comments: GitHubInlineCommentPreview[]
  summary_comments: GitHubFindingPublicationNote[]
  summary_body: string
  skipped_findings: GitHubFindingPublicationNote[]
  warnings: string[]
  payload_hash: string
  expires_at: string
}

export interface GitHubReviewPublication {
  id: string
  run_id: string
  repository: string
  pr_number: number
  base_sha: string
  head_sha: string
  event: GitHubReviewEvent
  selected_finding_ids: string[]
  payload_hash: string
  status: GitHubPublicationStatus
  github_review_id: string | null
  github_review_url: string | null
  github_user: string | null
  prepared_at: string
  expires_at: string
  published_at: string | null
  error_code: string | null
  error_message: string | null
}

export interface ApiErrorDetail {
  code: string
  message: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly runId: string | null

  constructor(
    message: string,
    options: { status: number; code?: string | null; runId?: string | null },
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code ?? null
    this.runId = options.runId ?? null
  }
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

export type FileChangeStatus = 'modified' | 'added' | 'deleted' | 'untracked'

export interface FileChange {
  path: string
  additions: number | null
  deletions: number | null
  binary: boolean
  status: FileChangeStatus
}

export interface ProjectFileChanges {
  project_path: string
  changed_files: number
  additions: number
  deletions: number
  binary_files: number
  hidden_files: number
  files: FileChange[]
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
  query?: string
  provider?: string
  path?: string
  pattern?: string
  file_pattern?: string
  max_results?: number
  result_count?: number
  match_count?: number
  files_searched?: number
  skipped_file_count?: number
  scanned_entry_count?: number
  scanned_bytes?: number
  duration_ms?: number
  cached?: boolean
  truncated?: boolean
  error_code?: string
  sources?: Array<{
    citation_id: string
    title: string
    url: string
  }>
  network_access?: boolean
  network_provider?: string
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
    let code: string | null = null
    let runId: string | null = null

    try {
      const body: unknown = await response.json()
      if (
        typeof body === 'object' &&
        body !== null &&
        'detail' in body &&
        typeof body.detail === 'string'
      ) {
        message = body.detail
      } else if (
        typeof body === 'object' &&
        body !== null &&
        'detail' in body &&
        typeof body.detail === 'object' &&
        body.detail !== null &&
        'message' in body.detail &&
        typeof body.detail.message === 'string'
      ) {
        message = body.detail.message
        if ('code' in body.detail && typeof body.detail.code === 'string') {
          code = body.detail.code
        }
        if ('run_id' in body.detail && typeof body.detail.run_id === 'string') {
          runId = body.detail.run_id
        }
      }
    } catch {
      // 非 JSON 错误响应保留 HTTP 状态提示。
    }

    throw new ApiError(message, { status: response.status, code, runId })
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

export function getToolCapabilities(
  taskKind: TaskKind,
  networkAccess: boolean,
): Promise<ToolCapabilities> {
  const params = new URLSearchParams({
    task_kind: taskKind,
    network_access: String(networkAccess),
  })
  return requestJson<ToolCapabilities>(`/api/tool-capabilities?${params.toString()}`)
}

export function getRunToolCapabilities(runId: string): Promise<ToolCapabilities> {
  return requestJson<ToolCapabilities>(
    `/api/runs/${encodeURIComponent(runId)}/tool-capabilities`,
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

export function startCodeReview(
  runId: string,
  scope: ReviewScope = 'all',
  source: ReviewSource = 'working_tree',
  prNumber: number | null = null,
): Promise<CodeReviewResult> {
  return requestJson<CodeReviewResult>(
    `/api/runs/${encodeURIComponent(runId)}/reviews`,
    jsonRequest('POST', {
      scope,
      source,
      ...(prNumber === null ? {} : { pr_number: prNumber }),
    }),
  )
}

export function createCodeReviewRun(
  threadId: string,
  scope: ReviewScope = 'all',
  source: ReviewSource = 'working_tree',
  prNumber: number | null = null,
): Promise<CodeReviewResult> {
  return requestJson<CodeReviewResult>(
    `/api/threads/${encodeURIComponent(threadId)}/review-runs`,
    jsonRequest('POST', {
      scope,
      source,
      ...(prNumber === null ? {} : { pr_number: prNumber }),
    }),
  )
}

export function getReviewSnapshot(runId: string): Promise<ReviewSnapshot> {
  return requestJson<ReviewSnapshot>(
    `/api/runs/${encodeURIComponent(runId)}/review`,
  )
}

export function getGitHubReviewCapability(projectId: string): Promise<GitHubReviewCapability> {
  return requestJson<GitHubReviewCapability>(
    `/api/projects/${encodeURIComponent(projectId)}/github-review/capability`,
  )
}

export function prepareGitHubReview(
  runId: string,
  options: {
    pr_number: number
    selected_finding_ids: string[]
    event: GitHubReviewEvent
    summary?: string | null
  },
): Promise<GitHubReviewPreview> {
  return requestJson<GitHubReviewPreview>(
    `/api/runs/${encodeURIComponent(runId)}/github-review/prepare`,
    jsonRequest('POST', options),
  )
}

export function publishGitHubReview(
  runId: string,
  publicationId: string,
): Promise<GitHubReviewPublication> {
  return requestJson<GitHubReviewPublication>(
    `/api/runs/${encodeURIComponent(runId)}/github-review/publish`,
    jsonRequest('POST', { publication_id: publicationId }),
  )
}

export function listGitHubReviewPublications(
  runId: string,
): Promise<GitHubReviewPublication[]> {
  return requestJson<GitHubReviewPublication[]>(
    `/api/runs/${encodeURIComponent(runId)}/github-review/publications`,
  )
}

export function startRun(
  threadId: string,
  content: string,
  taskKind: TaskKind,
  networkAccess = false,
): Promise<RunStartResponse> {
  return requestJson<RunStartResponse>(
    `/api/threads/${encodeURIComponent(threadId)}/runs`,
    jsonRequest('POST', {
      content,
      task_kind: taskKind,
      network_access: networkAccess,
    }),
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

export function getProjectFileChanges(
  projectId: string,
): Promise<ProjectFileChanges> {
  return requestJson<ProjectFileChanges>(
    `/api/projects/${encodeURIComponent(projectId)}/file-changes`,
  )
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
