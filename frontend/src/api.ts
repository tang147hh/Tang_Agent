export const taskKinds = [
  'qa',
  'planning',
  'analysis',
  'development',
] as const

export const taskStatuses = [
  'pending',
  'running',
  'completed',
  'failed',
] as const

export const taskEventKinds = [
  'created',
  'running',
  'token',
  'tool_started',
  'tool_finished',
  'completed',
  'failed',
] as const

export type TaskKind = (typeof taskKinds)[number]
export type TaskStatus = (typeof taskStatuses)[number]
export type TaskEventKind = (typeof taskEventKinds)[number]

export interface TaskResponse {
  thread_id: string
  prompt: string
  task_kind: TaskKind
  status: TaskStatus
  result: string | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface TaskEventPayload {
  thread_id: string
  source: string
  created_at: string
  status?: TaskStatus
  task_kind?: TaskKind
  text?: string
  name?: string
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

export function createTask(prompt: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>('/api/tasks', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ prompt }),
  })
}

export function getTask(threadId: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>(`/api/tasks/${encodeURIComponent(threadId)}`)
}

export function taskEventsUrl(threadId: string): string {
  return `/api/tasks/${encodeURIComponent(threadId)}/events`
}
