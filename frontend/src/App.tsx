import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import {
  createProject,
  createThread,
  getRun,
  getThread,
  listMessages,
  listProjects,
  listRuns,
  listThreads,
  runEventKinds,
  runEventsUrl,
  startRun,
} from './api'
import type {
  Message,
  Project,
  Run,
  RunEventKind,
  RunEventPayload,
  Thread,
} from './api'
import { MarkdownContent } from './MarkdownContent'
import './App.css'

type View = 'chat' | 'skills'
type IconName =
  | 'arrow-left'
  | 'book'
  | 'box'
  | 'check'
  | 'chevron-down'
  | 'chevron-right'
  | 'clock'
  | 'folder'
  | 'history'
  | 'message'
  | 'plus'
  | 'search'
  | 'send'
  | 'settings'
  | 'sparkles'
  | 'terminal'
  | 'user'

interface AgentStep {
  id: string
  kind: RunEventKind
  title: string
  detail: string
  status: 'running' | 'completed' | 'failed'
  createdAt: string
  source: string
  toolCallId?: string
}

const quickPrompts = [
  { icon: 'terminal' as const, label: '分析这个项目', prompt: '请分析当前项目结构，并说明主要模块的职责。' },
  { icon: 'folder' as const, label: '梳理目录结构', prompt: '请梳理当前项目的目录结构和关键代码入口。' },
  { icon: 'sparkles' as const, label: '给出优化建议', prompt: '请检查当前项目并给出最值得优先处理的优化建议。' },
  { icon: 'check' as const, label: '检查项目状态', prompt: '请检查当前项目状态、测试情况和未完成事项。' },
]

const stepCopy: Record<RunEventKind, { title: string; detail: string }> = {
  created: { title: '任务已创建', detail: '已接收用户请求并创建执行记录' },
  running: { title: '分析项目', detail: '正在读取上下文并执行任务' },
  token: { title: '生成回答', detail: '正在整理结果并生成回复' },
  tool_started: { title: '调用工具', detail: '正在执行项目工具' },
  tool_finished: { title: '工具执行完成', detail: '工具结果已返回 Agent' },
  completed: { title: '完成', detail: '任务执行完成，等待下一条指令' },
  failed: { title: '执行失败', detail: '任务未能完成，请查看错误信息' },
}

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    'arrow-left': <><path d="m15 18-6-6 6-6" /><path d="M9 12h12" /></>,
    book: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" /></>,
    box: <><path d="m21 8-9 5-9-5" /><path d="m3 8 9-5 9 5v8l-9 5-9-5Z" /><path d="M12 13v8" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    'chevron-down': <path d="m6 9 6 6 6-6" />,
    'chevron-right': <path d="m9 18 6-6-6-6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    folder: <><path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /><path d="M3 10h18" /></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l3 2" /></>,
    message: <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" />,
    plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
    sparkles: <><path d="m12 3-1 3-3 1 3 1 1 3 1-3 3-1-3-1Z" /><path d="m19 13-1 2-2 1 2 1 1 2 1-2 2-1-2-1Z" /><path d="m5 14-.7 1.8L2.5 16.5l1.8.7L5 19l.7-1.8 1.8-.7-1.8-.7Z" /></>,
    terminal: <><path d="m4 17 6-6-6-6" /><path d="M12 19h8" /></>,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  }

  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  )
}

function Logo({ small = false }: { small?: boolean }) {
  return <span className={`brand-mark ${small ? 'brand-mark-small' : ''}`}><span /></span>
}

function timeLabel(value: string): string {
  return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function stepIdentity(kind: RunEventKind, payload: RunEventPayload, eventId: string): string {
  if ((kind === 'tool_started' || kind === 'tool_finished') && payload.tool_call_id) {
    return `tool:${payload.tool_call_id}`
  }

  if (kind === 'token') return `token:${payload.source}`
  return eventId || `${kind}-${payload.created_at}`
}

function stepPresentation(kind: RunEventKind, payload: RunEventPayload) {
  const copy = stepCopy[kind]
  const isSubagent = payload.source.startsWith('subagent')
  const subagent = payload.subagent ?? 'general-purpose'

  if (kind === 'token' && isSubagent) {
    return {
      title: '子 Agent 分析',
      detail: `${subagent} 正在整理分析结果`,
    }
  }

  if (kind === 'tool_started' || kind === 'tool_finished') {
    const finished = kind === 'tool_finished'

    if (payload.name === 'task') {
      return {
        title: '委派子 Agent',
        detail: finished
          ? `${subagent} 已返回分析结果`
          : `正在调用 ${subagent}`,
      }
    }

    const toolName = payload.name ?? '项目工具'
    return {
      title: isSubagent ? '子 Agent 调用工具' : copy.title,
      detail: `${isSubagent ? `${subagent} · ` : ''}${toolName}${finished ? ' 已完成' : ''}`,
    }
  }

  return {
    title: copy.title,
    detail: kind === 'failed' ? payload.error ?? copy.detail : copy.detail,
  }
}

function App() {
  const [view, setView] = useState<View>('chat')
  const [projects, setProjects] = useState<Project[]>([])
  const [threads, setThreads] = useState<Thread[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null)
  const [activeRun, setActiveRun] = useState<Run | null>(null)
  const [streamText, setStreamText] = useState('')
  const [steps, setSteps] = useState<AgentStep[]>([])
  const [prompt, setPrompt] = useState('')
  const [search, setSearch] = useState('')
  const [notice, setNotice] = useState('准备就绪')
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [loadingConversation, setLoadingConversation] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [creatingThread, setCreatingThread] = useState(false)
  const [showProjectDialog, setShowProjectDialog] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectPath, setProjectPath] = useState('/projects/')
  const [projectError, setProjectError] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [showRunPanel, setShowRunPanel] = useState(() => window.innerWidth > 1100)
  const eventSourceRef = useRef<EventSource | null>(null)
  const terminalRef = useRef(false)
  const messageEndRef = useRef<HTMLDivElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)

  const selectedProject = projects.find((item) => item.project_id === selectedProjectId) ?? null
  const selectedThread = threads.find((item) => item.thread_id === selectedThreadId) ?? null
  const filteredProjects = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return projects
    return projects.filter((project) => project.name.toLocaleLowerCase().includes(query))
  }, [projects, search])

  const filteredThreads = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return threads
    return threads.filter((thread) => thread.title.toLocaleLowerCase().includes(query))
  }, [threads, search])
  const orderedMessages = useMemo(
    () => [...messages].sort((left, right) => left.sequence - right.sequence),
    [messages],
  )

  const refreshConversation = useCallback(async (threadId: string, runId?: string) => {
    const [latestThread, latestMessages, latestRuns, latestRun] = await Promise.all([
      getThread(threadId),
      listMessages(threadId),
      listRuns(threadId),
      runId ? getRun(runId) : Promise.resolve(null),
    ])
    const authoritativeRuns = latestRun
      ? latestRuns.some((run) => run.run_id === latestRun.run_id)
        ? latestRuns.map((run) => run.run_id === latestRun.run_id ? latestRun : run)
        : [...latestRuns, latestRun]
      : latestRuns
    setThreads((current) => current.map((item) => item.thread_id === latestThread.thread_id ? latestThread : item))
    setMessages(latestMessages)
    setRuns(authoritativeRuns)
    const running = [...authoritativeRuns].reverse().find((run) => run.status === 'pending' || run.status === 'running') ?? null
    setActiveRun(running)
  }, [])

  const closeEventSource = useCallback(() => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
  }, [])

  const updateSteps = useCallback((kind: RunEventKind, payload: RunEventPayload, eventId: string) => {
    setSteps((current) => {
      const id = stepIdentity(kind, payload, eventId)
      const presentation = stepPresentation(kind, payload)
      const status = kind === 'failed'
        ? 'failed' as const
        : kind === 'created' || kind === 'completed' || kind === 'tool_finished'
          ? 'completed' as const
          : 'running' as const
      const previousStatus = kind === 'failed' ? 'failed' as const : 'completed' as const
      const completedPrevious = current.map((step) => {
        if (step.status !== 'running' || step.id === id) return step
        if (step.toolCallId && payload.source === `subagent:${step.toolCallId}`) return step
        return { ...step, status: previousStatus }
      })
      const existingIndex = completedPrevious.findIndex((step) => step.id === id)

      if (existingIndex >= 0) {
        return completedPrevious.map((step, index) => index === existingIndex
          ? {
              ...step,
              title: presentation.title,
              detail: presentation.detail,
              status,
              createdAt: payload.created_at,
            }
          : step)
      }

      return [
        ...completedPrevious,
        {
          id,
          kind,
          title: presentation.title,
          detail: presentation.detail,
          status,
          createdAt: payload.created_at,
          source: payload.source,
          toolCallId: payload.tool_call_id,
        },
      ]
    })
  }, [])

  const connectToRun = useCallback((runId: string, threadId: string) => {
    closeEventSource()
    terminalRef.current = false
    const source = new EventSource(runEventsUrl(runId))
    eventSourceRef.current = source

    for (const kind of runEventKinds) {
      source.addEventListener(kind, (rawEvent) => {
        const event = rawEvent as MessageEvent<string>
        try {
          const payload = JSON.parse(event.data) as RunEventPayload
          updateSteps(kind, payload, event.lastEventId)

          if (kind === 'token' && payload.source === 'main' && payload.text) {
            setStreamText((current) => current + payload.text)
          }

          if (payload.status) {
            setActiveRun((current) => current ? { ...current, status: payload.status! } : current)
          }

          if (kind === 'running') setNotice('Agent 正在执行')

          if (kind === 'completed' || kind === 'failed') {
            terminalRef.current = true
            source.close()
            setNotice(kind === 'completed' ? '任务执行完成' : payload.error ?? '任务执行失败')
            void refreshConversation(threadId, runId).catch((error: unknown) => {
              setNotice(error instanceof Error ? error.message : '刷新会话失败')
            })
          }
        } catch {
          setNotice('收到无法解析的事件数据')
        }
      })
    }

    source.onopen = () => setNotice('已连接 Agent 事件流')
    source.onerror = () => {
      if (!terminalRef.current) setNotice('事件流中断，正在自动重连')
    }
  }, [closeEventSource, refreshConversation, updateSteps])

  useEffect(() => {
    let cancelled = false
    setLoadingProjects(true)
    listProjects()
      .then((items) => {
        if (cancelled) return
        setProjects(items)
        setSelectedProjectId((current) => current ?? items[0]?.project_id ?? null)
      })
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : '项目加载失败'))
      .finally(() => !cancelled && setLoadingProjects(false))
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!selectedProjectId) {
      setThreads([])
      setSelectedThreadId(null)
      return
    }
    let cancelled = false
    listThreads(selectedProjectId)
      .then((items) => {
        if (cancelled) return
        setThreads(items)
        setSelectedThreadId((current) => items.some((item) => item.thread_id === current) ? current : items[0]?.thread_id ?? null)
      })
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : '会话加载失败'))
    return () => { cancelled = true }
  }, [selectedProjectId])

  useEffect(() => {
    closeEventSource()
    setMessages([])
    setRuns([])
    setSteps([])
    setStreamText('')
    setActiveRun(null)
    if (!selectedThreadId) return

    let cancelled = false
    setLoadingConversation(true)
    Promise.all([getThread(selectedThreadId), listMessages(selectedThreadId), listRuns(selectedThreadId)])
      .then(([latestThread, latestMessages, latestRuns]) => {
        if (cancelled) return
        setThreads((current) => current.map((item) => item.thread_id === latestThread.thread_id ? latestThread : item))
        setMessages(latestMessages)
        setRuns(latestRuns)
        const running = [...latestRuns].reverse().find((run) => run.status === 'pending' || run.status === 'running') ?? null
        setActiveRun(running)
        if (running) connectToRun(running.run_id, selectedThreadId)
      })
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : '会话加载失败'))
      .finally(() => !cancelled && setLoadingConversation(false))

    return () => {
      cancelled = true
      closeEventSource()
    }
  }, [closeEventSource, connectToRun, selectedThreadId])

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: streamText ? 'auto' : 'smooth' })
  }, [orderedMessages, streamText])

  useEffect(() => {
    function focusSearch(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        searchInputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', focusSearch)
    return () => window.removeEventListener('keydown', focusSearch)
  }, [])

  async function handleCreateThread() {
    if (!selectedProjectId || creatingThread) return
    setCreatingThread(true)
    try {
      const thread = await createThread(selectedProjectId)
      setThreads((current) => [thread, ...current])
      setSelectedThreadId(thread.thread_id)
      setView('chat')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '新建会话失败')
    } finally {
      setCreatingThread(false)
    }
  }

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setProjectError('')
    try {
      const project = await createProject({ name: projectName.trim(), virtual_path: projectPath.trim() })
      setProjects((current) => [project, ...current])
      setSelectedProjectId(project.project_id)
      setProjectName('')
      setProjectPath('/projects/')
      setShowProjectDialog(false)
      setView('chat')
    } catch (error) {
      setProjectError(error instanceof Error ? error.message : '项目登记失败')
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = prompt.trim()
    if (!content || !selectedThreadId || submitting || activeRun?.status === 'pending' || activeRun?.status === 'running') return
    setSubmitting(true)
    setSteps([])
    setStreamText('')
    try {
      const created = await startRun(selectedThreadId, content)
      setMessages((current) => [...current, created.message])
      setActiveRun(created.run)
      setRuns((current) => [...current, created.run])
      setPrompt('')
      setNotice('任务已创建，正在连接 Agent')
      connectToRun(created.run.run_id, selectedThreadId)
      void getThread(selectedThreadId).then((thread) => {
        setThreads((current) => [thread, ...current.filter((item) => item.thread_id !== thread.thread_id)])
      }).catch(() => undefined)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '发送失败')
    } finally {
      setSubmitting(false)
    }
  }

  function selectProject(projectId: string) {
    setSelectedProjectId(projectId)
    setView('chat')
    setMobileSidebarOpen(false)
  }

  const isRunning = activeRun?.status === 'pending' || activeRun?.status === 'running'
  const lastRun = activeRun ?? runs.at(-1) ?? null

  return (
    <div className={`workbench ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      {mobileSidebarOpen ? <button className="sidebar-backdrop" type="button" aria-label="关闭导航" onClick={() => setMobileSidebarOpen(false)} /> : null}
      <aside className={`sidebar ${mobileSidebarOpen ? 'mobile-open' : ''}`}>
        <div className="brand-row">
          <div className="brand"><Logo /><strong>Tang Agent</strong></div>
          <button className="icon-button collapse-button" type="button" title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'} onClick={() => { if (window.innerWidth <= 760) setMobileSidebarOpen(false); else setSidebarCollapsed((value) => !value) }}><Icon name="arrow-left" /></button>
        </div>

        <div className="sidebar-scroll">
          <div className="sidebar-heading"><span>PROJECTS</span><button type="button" onClick={() => setShowProjectDialog(true)} title="登记项目"><Icon name="plus" size={16} /></button></div>
          <nav className="project-list" aria-label="项目列表">
            {loadingProjects ? <p className="sidebar-empty">正在加载项目…</p> : null}
            {!loadingProjects && !filteredProjects.length ? <p className="sidebar-empty">还没有登记项目</p> : null}
            {filteredProjects.map((project, index) => (
              <button key={project.project_id} className={`project-item ${selectedProjectId === project.project_id && view === 'chat' ? 'active' : ''}`} type="button" onClick={() => selectProject(project.project_id)}>
                <span className={`project-icon project-color-${index % 4}`}><Icon name="folder" size={16} /></span>
                <span>{project.name}</span>
              </button>
            ))}
          </nav>

          <div className="sidebar-divider" />
          <div className="sidebar-heading"><span>对话</span><button type="button" onClick={() => void handleCreateThread()} disabled={!selectedProjectId || creatingThread} title="新建对话"><Icon name="plus" size={17} /></button></div>
          <nav className="thread-list" aria-label="会话列表">
            {selectedProjectId && !filteredThreads.length ? <p className="sidebar-empty">这个项目还没有会话</p> : null}
            {filteredThreads.map((thread) => (
              <button key={thread.thread_id} className={`thread-item ${selectedThreadId === thread.thread_id && view === 'chat' ? 'active' : ''}`} type="button" onClick={() => { setSelectedThreadId(thread.thread_id); setView('chat'); setMobileSidebarOpen(false) }}>
                <span className={`thread-status-dot ${thread.status}`} />
                <span className="thread-title">{thread.title}</span>
              </button>
            ))}
          </nav>
        </div>

        <div className="sidebar-footer">
          <button className={`skills-link ${view === 'skills' ? 'active' : ''}`} type="button" onClick={() => { setView('skills'); setMobileSidebarOpen(false) }}><span><Icon name="box" />Skills</span><Icon name="chevron-right" size={16} /></button>
          <div className="account-row"><span className="avatar">T</span><span><strong>tang</strong><small>本地工作区</small></span><Icon name="chevron-down" size={16} /></div>
        </div>
      </aside>

      <section className="content-shell">
        <header className="topbar">
          <div className="topbar-leading"><button className="icon-button mobile-menu" type="button" aria-label="打开导航" onClick={() => setMobileSidebarOpen(true)}><Icon name="message" /></button><div className="breadcrumbs"><strong>{selectedProject?.name ?? 'Tang Agent'}</strong><span>/</span><span>{view === 'skills' ? 'Skills' : selectedThread?.title ?? '新对话'}</span></div></div>
          <div className="topbar-actions">
            <label className="search-box"><Icon name="search" size={17} /><input ref={searchInputRef} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索" /><kbd>⌘ K</kbd></label>
            <span className="topbar-separator" />
            <button className={`icon-button ${showRunPanel ? 'active' : ''}`} type="button" title="执行状态" onClick={() => setShowRunPanel((value) => !value)}><Icon name="terminal" /></button>
            <button className="icon-button" type="button" title="历史记录"><Icon name="history" /></button>
            <button className="icon-button" type="button" title="文档"><Icon name="book" /></button>
            <button className="icon-button" type="button" title="设置"><Icon name="settings" /></button>
          </div>
        </header>

        {view === 'skills' ? (
          <main className="skills-page">
            <div className="skills-hero"><span className="skills-hero-icon"><Icon name="box" size={26} /></span><div><p className="eyebrow">CAPABILITIES</p><h1>Skills 管理</h1><p>集中查看 Agent 可以按需加载的专业能力。</p></div></div>
            <div className="skills-toolbar"><div><h2>已安装 Skills</h2><p>Skills API 将在后续课程接入</p></div><button type="button" disabled><Icon name="plus" size={16} />添加 Skill</button></div>
            <div className="skills-empty"><Icon name="box" size={34} /><h3>Skills 后端尚未接入</h3><p>页面结构已经准备好。完成 <code>GET /api/skills</code> 和详情接口后，这里会展示名称、来源、状态与 SKILL.md 内容。</p></div>
          </main>
        ) : (
          <div className={`chat-layout ${showRunPanel ? '' : 'panel-hidden'}`}>
            <main className="conversation-column">
              <section className="conversation-card">
                {loadingConversation ? (
                  <div className="center-state"><span className="loading-ring" /><p>正在加载会话…</p></div>
                ) : !selectedProject ? (
                  <div className="center-state"><Logo /><h1>添加你的第一个项目</h1><p>登记工作区中已有的项目目录，然后开始与 Agent 协作。</p><button className="primary-action" type="button" onClick={() => setShowProjectDialog(true)}>登记项目</button></div>
                ) : !selectedThread ? (
                  <div className="center-state"><Logo /><h1>开始一个新的 Agent 会话</h1><p>当前项目是 {selectedProject.name}。创建会话后即可分析代码、生成实现并持续讨论。</p><button className="primary-action" type="button" onClick={() => void handleCreateThread()} disabled={creatingThread}>{creatingThread ? '正在创建…' : '新建对话'}</button></div>
                ) : messages.length === 0 && !streamText ? (
                  <div className="welcome-state"><Logo /><h1>开始一个新的 Agent 会话</h1><p>我可以帮你理解项目结构、生成代码、优化性能、解决问题等。</p><strong>你可以试试：</strong><div className="quick-grid">{quickPrompts.map((item) => <button type="button" key={item.label} onClick={() => setPrompt(item.prompt)}><span><Icon name={item.icon} /></span>{item.label}</button>)}</div></div>
                ) : (
                  <div className="message-list">
                    {orderedMessages.map((message) => (
                      <article key={message.message_id} className={`message message-${message.role}`}>
                        <div className="message-avatar">{message.role === 'user' ? <Icon name="user" size={16} /> : <Logo small />}</div>
                        <div className="message-body"><div className="message-meta"><strong>{message.role === 'user' ? '你' : message.role === 'assistant' ? 'Tang Agent' : '系统'}</strong><time>{timeLabel(message.created_at)}</time></div><div className="message-content">{message.role === 'user' ? message.content : <MarkdownContent content={message.content} />}</div></div>
                      </article>
                    ))}
                    {streamText ? <article className="message message-assistant streaming"><div className="message-avatar"><Logo small /></div><div className="message-body"><div className="message-meta"><strong>Tang Agent</strong><span className="typing-dot" /></div><div className="message-content"><MarkdownContent content={streamText} /></div></div></article> : null}
                    <div ref={messageEndRef} />
                  </div>
                )}
              </section>

              <form className="composer" onSubmit={(event) => void handleSubmit(event)}>
                <div className="composer-main"><button className="attach-button" type="button" title="附件功能尚未接入">+</button><textarea value={prompt} onChange={(event) => { setPrompt(event.target.value); event.currentTarget.style.height = 'auto'; event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 160)}px` }} placeholder={selectedThread ? '输入你的需求，按 Enter 发送' : '请先选择或创建会话'} rows={1} disabled={!selectedThread || isRunning} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} /><span className="model-label">DeepSeek V4<Icon name="chevron-down" size={14} /></span><button className="send-button" type="submit" disabled={!prompt.trim() || !selectedThread || submitting || isRunning}><Icon name="send" size={18} /></button></div>
                <div className="composer-hint"><span>Enter 发送，Shift + Enter 换行</span><span>{notice}</span></div>
              </form>
            </main>

            <aside className={`run-panel ${showRunPanel ? 'open' : ''}`}>
              <div className="run-panel-header"><strong>Agent 执行状态</strong><span className={`run-state ${isRunning ? 'running' : lastRun?.status === 'failed' ? 'failed' : ''}`}><i />{isRunning ? '运行中' : lastRun?.status === 'failed' ? '失败' : lastRun?.status === 'completed' ? '已完成' : '空闲'}</span></div>
              <div className="run-panel-section"><h2>执行步骤</h2>{steps.length ? <ol className="step-list">{steps.map((step, index) => <li key={step.id} className={`step-${step.status} ${step.source.startsWith('subagent') ? 'step-subagent' : ''}`}><span className="step-number">{step.status === 'completed' ? <Icon name="check" size={14} /> : index + 1}</span><div><div className="step-title"><strong>{step.title}</strong><time>{timeLabel(step.createdAt)}</time></div><p>{step.detail}</p></div></li>)}</ol> : <div className="steps-empty"><Icon name="clock" /><p>发送任务后，这里会实时显示 Agent 的执行过程。</p></div>}</div>
              <div className="run-details"><div className="details-heading"><strong>任务详情</strong><Icon name="chevron-down" size={16} /></div><dl><div><dt>任务 ID</dt><dd title={lastRun?.run_id}>{lastRun?.run_id.slice(0, 10) ?? '—'}</dd></div><div><dt>创建时间</dt><dd>{lastRun ? new Date(lastRun.created_at).toLocaleString('zh-CN') : '—'}</dd></div><div><dt>模型</dt><dd>DeepSeek V4</dd></div><div><dt>项目路径</dt><dd title={selectedProject?.virtual_path}>{selectedProject?.virtual_path ?? '—'}</dd></div></dl></div>
            </aside>
          </div>
        )}
      </section>

      {showProjectDialog ? <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowProjectDialog(false) }}><form className="project-dialog" onSubmit={(event) => void handleCreateProject(event)}><div className="dialog-icon"><Icon name="folder" /></div><h2>登记工作区项目</h2><p>项目目录必须已经存在于 Agent 工作区的 <code>/projects</code> 下。</p><label>项目名称<input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="例如 Tang Agent" maxLength={100} /></label><label>虚拟路径<input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="/projects/tang-agent" maxLength={1000} /></label>{projectError ? <p className="dialog-error">{projectError}</p> : null}<div className="dialog-actions"><button type="button" onClick={() => setShowProjectDialog(false)}>取消</button><button className="primary-action" type="submit" disabled={!projectName.trim() || !projectPath.trim()}>登记项目</button></div></form></div> : null}
    </div>
  )
}

export default App
