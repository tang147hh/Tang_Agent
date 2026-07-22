import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import {
  createTask,
  getTask,
  taskEventKinds,
  taskEventsUrl,
} from './api'
import type {
  TaskEventKind,
  TaskEventPayload,
  TaskKind,
  TaskResponse,
  TaskStatus,
} from './api'
import './App.css'

interface TimelineItem {
  id: string
  kind: TaskEventKind
  source: string
  detail: string
  createdAt: string
}

const statusLabels: Record<TaskStatus, string> = {
  pending: '等待执行',
  running: '正在执行',
  completed: '执行完成',
  failed: '执行失败',
}

const kindLabels: Record<TaskKind, string> = {
  qa: '问答',
  planning: '规划',
  analysis: '分析',
  development: '开发',
}

const eventLabels: Record<TaskEventKind, string> = {
  created: '任务已创建',
  running: 'Agent 开始执行',
  token: '生成回答',
  tool_started: '调用工具',
  tool_finished: '工具执行完成',
  completed: '任务完成',
  failed: '任务失败',
}

function eventDetail(kind: TaskEventKind, payload: TaskEventPayload): string {
  if (kind === 'tool_started' || kind === 'tool_finished') {
    return payload.name ?? 'unknown'
  }

  if (kind === 'failed') {
    return payload.error ?? '任务执行失败'
  }

  if (kind === 'created' && payload.task_kind) {
    return `识别为${kindLabels[payload.task_kind]}任务`
  }

  if (kind === 'token') {
    return payload.source === 'main' ? '主 Agent 输出片段' : `${payload.source} 输出片段`
  }

  return payload.source === 'system' ? '系统事件' : payload.source
}

function App() {
  const [prompt, setPrompt] = useState('请分析 /projects 目录中的项目结构，并给出清晰总结。')
  const [task, setTask] = useState<TaskResponse | null>(null)
  const [answer, setAnswer] = useState('')
  const [timeline, setTimeline] = useState<TimelineItem[]>([])
  const [notice, setNotice] = useState('等待提交任务')
  const [submitting, setSubmitting] = useState(false)
  const eventSourceRef = useRef<EventSource | null>(null)
  const terminalRef = useRef(false)

  useEffect(() => {
    return () => eventSourceRef.current?.close()
  }, [])

  async function refreshTask(threadId: string): Promise<void> {
    try {
      const latest = await getTask(threadId)
      setTask(latest)

      if (latest.result) {
        setAnswer(latest.result)
      }

      if (latest.error) {
        setNotice(latest.error)
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '读取任务结果失败')
    }
  }

  function connectToEvents(threadId: string): void {
    eventSourceRef.current?.close()
    terminalRef.current = false

    const source = new EventSource(taskEventsUrl(threadId))
    eventSourceRef.current = source

    for (const kind of taskEventKinds) {
      source.addEventListener(kind, (rawEvent) => {
        const message = rawEvent as MessageEvent<string>

        try {
          const payload = JSON.parse(message.data) as TaskEventPayload

          if (kind === 'token' && payload.source === 'main' && payload.text) {
            setAnswer((current) => current + payload.text)
          } else {
            setTimeline((current) => [
              ...current,
              {
                id: `${message.lastEventId}-${kind}`,
                kind,
                source: payload.source,
                detail: eventDetail(kind, payload),
                createdAt: payload.created_at,
              },
            ])
          }

          if (payload.status) {
            setTask((current) =>
              current ? { ...current, status: payload.status as TaskStatus } : current,
            )
          }

          if (kind === 'running') {
            setNotice('连接正常，正在接收 Agent 事件')
          }

          if (kind === 'completed' || kind === 'failed') {
            terminalRef.current = true
            source.close()
            setNotice(kind === 'completed' ? '任务执行完成' : payload.error ?? '任务执行失败')
            void refreshTask(threadId)
          }
        } catch {
          setNotice('收到无法解析的事件数据')
        }
      })
    }

    source.onopen = () => setNotice('事件流已连接')
    source.onerror = () => {
      if (!terminalRef.current) {
        setNotice('事件流暂时中断，浏览器将自动重连')
      }
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const normalizedPrompt = prompt.trim()

    if (!normalizedPrompt || submitting) {
      return
    }

    setSubmitting(true)
    setTask(null)
    setAnswer('')
    setTimeline([])
    setNotice('正在创建任务')

    try {
      const created = await createTask(normalizedPrompt)
      setTask(created)
      setNotice('任务已创建，正在连接事件流')
      connectToEvents(created.thread_id)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '创建任务失败')
    } finally {
      setSubmitting(false)
    }
  }

  const isActive = task?.status === 'pending' || task?.status === 'running'

  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Tang Agent · Local Console</p>
          <h1>观察 Agent，理解每一次调用</h1>
          <p className="hero-copy">
            提交自然语言任务，实时查看任务分类、工具调用、流式回答与最终状态。
          </p>
        </div>
        <div className="system-pill">
          <span className="pulse" aria-hidden="true" />
          macOS · GitHub · DeepSeek
        </div>
      </header>

      <section className="composer panel" aria-labelledby="task-heading">
        <div className="section-heading">
          <div>
            <span className="step">01</span>
            <h2 id="task-heading">提交任务</h2>
          </div>
          <span className="connection-note">{notice}</span>
        </div>

        <form onSubmit={(event) => void handleSubmit(event)}>
          <label htmlFor="prompt">告诉 Agent 你希望它做什么</label>
          <textarea
            id="prompt"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={5}
            maxLength={20_000}
            disabled={submitting || isActive}
          />
          <div className="form-footer">
            <span>{prompt.length.toLocaleString()} / 20,000</span>
            <button type="submit" disabled={!prompt.trim() || submitting || isActive}>
              {isActive ? 'Agent 执行中' : submitting ? '正在提交' : '运行 Agent'}
            </button>
          </div>
        </form>
      </section>

      <section className="workspace-grid">
        <article className="panel answer-panel" aria-labelledby="answer-heading">
          <div className="section-heading">
            <div>
              <span className="step">02</span>
              <h2 id="answer-heading">Agent 回答</h2>
            </div>
            {task && (
              <span className={`status status-${task.status}`}>
                {statusLabels[task.status]}
              </span>
            )}
          </div>

          {task ? (
            <div className="task-meta">
              <span>{kindLabels[task.task_kind]}任务</span>
              <code title={task.thread_id}>{task.thread_id.slice(0, 8)}</code>
            </div>
          ) : null}

          <div className={`answer ${answer ? '' : 'empty'}`} aria-live="polite">
            {answer || '提交任务后，主 Agent 的流式回答会显示在这里。'}
          </div>
        </article>

        <aside className="panel timeline-panel" aria-labelledby="timeline-heading">
          <div className="section-heading">
            <div>
              <span className="step">03</span>
              <h2 id="timeline-heading">执行轨迹</h2>
            </div>
            <span className="event-count">{timeline.length} 个事件</span>
          </div>

          {timeline.length ? (
            <ol className="timeline">
              {timeline.map((item) => (
                <li key={item.id}>
                  <span className={`event-dot event-${item.kind}`} aria-hidden="true" />
                  <div>
                    <strong>{eventLabels[item.kind]}</strong>
                    <p>{item.detail}</p>
                    <time dateTime={item.createdAt}>
                      {new Date(item.createdAt).toLocaleTimeString('zh-CN')}
                    </time>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="timeline-empty">任务生命周期事件会依次出现在这里。</p>
          )}
        </aside>
      </section>
    </main>
  )
}

export default App
