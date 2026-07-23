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
] as const

export type ThreadStatus = (typeof threadStatuses)[number]
export type RunStatus = (typeof runStatuses)[number]
export type MessageRole = (typeof messageRoles)[number]
export type RunEventKind = (typeof runEventKinds)[number]

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
  status: RunStatus
  error: string | null
  created_at: string
  updated_at: string
}

export interface RunStartResponse {
  run: Run
  message: Message
}

export interface RunEventPayload {
  run_id: string
  source: string
  created_at: string
  status?: RunStatus
  text?: string
  name?: string
  tool_call_id?: string
  subagent?: string
  error?: string
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

export function startRun(threadId: string, content: string): Promise<RunStartResponse> {
  return requestJson<RunStartResponse>(
    `/api/threads/${encodeURIComponent(threadId)}/runs`,
    jsonRequest('POST', { content }),
  )
}

export function runEventsUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/events`
}
