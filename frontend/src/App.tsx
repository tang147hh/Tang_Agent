import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Globe2, ScanSearch } from 'lucide-react'
import {
  checkoutRepositoryBranch,
  cloneRepository,
  commitRepository,
  createProject,
  createCodeReviewRun,
  createRepositoryBranch,
  createRepositoryPullRequest,
  createThread,
  fetchRepository,
  getRun,
  getRunPerformance,
  getToolCapabilities,
  getProjectFileChanges,
  getSkill,
  getThread,
  listMessages,
  listProjects,
  listRepositories,
  listRuns,
  listSkills,
  listThreads,
  pushRepository,
  runEventKinds,
  runEventsUrl,
  startRun,
  startCodeReview,
} from './api'
import type {
  Message,
  Project,
  ProjectFileChanges,
  PullRequest,
  Repository,
  Run,
  RunPerformance,
  RunEventKind,
  RunEventPayload,
  SkillDetail,
  SkillSummary,
  TaskKind,
  Thread,
  ToolCapabilities,
  ReviewScope,
  ReviewSource,
} from './api'
import { MarkdownContent } from './MarkdownContent'
import { ReviewWorkspace } from './review/ReviewWorkspace'
import { durationLabel, stepPresentation } from './stepPresentation'
import { acceptRunEvent, reconcileRunStream } from './streamUtils'
import './App.css'

type View = 'chat' | 'skills' | 'repositories' | 'review'
type IconName =
  | 'arrow-left'
  | 'book'
  | 'box'
  | 'check'
  | 'chevron-down'
  | 'chevron-right'
  | 'clock'
  | 'code'
  | 'folder'
  | 'git-branch'
  | 'git-commit'
  | 'history'
  | 'message'
  | 'plus'
  | 'question'
  | 'refresh'
  | 'repository'
  | 'search'
  | 'send'
  | 'settings'
  | 'sparkles'
  | 'terminal'
  | 'upload'
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
  sources?: Array<{ citation_id: string; title: string; url: string }>
}

type PendingRepositoryAction =
  | { kind: 'commit'; message: string }
  | { kind: 'push'; branch: string }
  | { kind: 'pull-request'; title: string; body: string; base: string; head: string }

const quickPrompts = [
  { icon: 'terminal' as const, label: '分析这个项目', prompt: '请分析当前项目结构，并说明主要模块的职责。' },
  { icon: 'folder' as const, label: '梳理目录结构', prompt: '请梳理当前项目的目录结构和关键代码入口。' },
  { icon: 'sparkles' as const, label: '给出优化建议', prompt: '请检查当前项目并给出最值得优先处理的优化建议。' },
  { icon: 'check' as const, label: '检查项目状态', prompt: '请检查当前项目状态、测试情况和未完成事项。' },
]

const taskKinds: Array<{ value: TaskKind; icon: IconName }> = [
  { value: 'coding', icon: 'code' },
  { value: 'analysis', icon: 'search' },
  { value: 'planning', icon: 'message' },
  { value: 'qa', icon: 'question' },
]

const implementPlanPrompt = '请按照上面的方案开始实施，并在完成后运行相关测试。'

function actionablePlanningRunId(runs: Run[]): string | null {
  const latestPlanningIndex = runs.findLastIndex((run) => (
    run.task_kind === 'planning' && run.status === 'completed'
  ))

  if (latestPlanningIndex < 0) return null

  const implementationStarted = runs
    .slice(latestPlanningIndex + 1)
    .some((run) => run.task_kind === 'coding')

  return implementationStarted ? null : runs[latestPlanningIndex].run_id
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
    code: <><path d="m8 9-3 3 3 3" /><path d="m16 9 3 3-3 3" /><path d="m14 5-4 14" /></>,
    folder: <><path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /><path d="M3 10h18" /></>,
    'git-branch': <><circle cx="6" cy="5" r="2" /><circle cx="18" cy="6" r="2" /><circle cx="6" cy="19" r="2" /><path d="M6 7v10" /><path d="M8 7c5 0 3 6 8 6h2" /><path d="M18 8v5" /></>,
    'git-commit': <><circle cx="12" cy="12" r="3" /><path d="M3 12h6" /><path d="M15 12h6" /></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l3 2" /></>,
    message: <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" />,
    plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
    question: <><circle cx="12" cy="12" r="9" /><path d="M9.8 9a2.4 2.4 0 1 1 3.1 2.3c-.9.3-.9 1.1-.9 1.7" /><path d="M12 17h.01" /></>,
    refresh: <><path d="M20 6v5h-5" /><path d="M4 18v-5h5" /><path d="M18.5 9A7 7 0 0 0 6 6.5L4 11" /><path d="M5.5 15A7 7 0 0 0 18 17.5l2-4.5" /></>,
    repository: <><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v18H6.5A2.5 2.5 0 0 0 4 22Z" /><path d="M4 4.5v15" /><path d="M9 7h6" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
    sparkles: <><path d="m12 3-1 3-3 1 3 1 1 3 1-3 3-1-3-1Z" /><path d="m19 13-1 2-2 1 2 1 1 2 1-2 2-1-2-1Z" /><path d="m5 14-.7 1.8L2.5 16.5l1.8.7L5 19l.7-1.8 1.8-.7-1.8-.7Z" /></>,
    terminal: <><path d="m4 17 6-6-6-6" /><path d="M12 19h8" /></>,
    upload: <><path d="M12 16V3" /><path d="m7 8 5-5 5 5" /><path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" /></>,
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

const terminationLabels: Record<string, string> = {
  model_call_limit: '模型调用预算',
  tool_call_limit: '工具调用预算',
  first_output_timeout: '首输出超时',
  total_time_limit: '总运行超时',
  repeated_tool_call: '重复工具循环',
  agent_error: 'Agent 执行错误',
}

function stepIdentity(kind: RunEventKind, payload: RunEventPayload, eventId: string): string {
  if ((kind === 'tool_started' || kind === 'tool_finished') && payload.tool_call_id) {
    return `tool:${payload.tool_call_id}`
  }

  if (kind === 'token') return `token:${payload.source}`
  return eventId || `${kind}-${payload.created_at}`
}

function RepositoriesPage({
  search,
  projects,
  onOpenReview,
}: {
  search: string
  projects: Project[]
  onOpenReview: (projectId: string) => void
}) {
  const [repositories, setRepositories] = useState<Repository[]>([])
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [branchName, setBranchName] = useState('')
  const [showCloneDialog, setShowCloneDialog] = useState(false)
  const [cloneUrl, setCloneUrl] = useState('')
  const [cloneError, setCloneError] = useState('')
  const [commitMessage, setCommitMessage] = useState('')
  const [pullRequestTitle, setPullRequestTitle] = useState('')
  const [pullRequestBody, setPullRequestBody] = useState('')
  const [pullRequestBase, setPullRequestBase] = useState('main')
  const [createdPullRequest, setCreatedPullRequest] = useState<PullRequest | null>(null)
  const [pendingAction, setPendingAction] = useState<PendingRepositoryAction | null>(null)

  const loadRepositories = useCallback(async () => {
    setLoading(true)
    setError('')

    try {
      const items = await listRepositories()
      setRepositories(items)
      setSelectedName((current) => (
        items.some((item) => item.name === current)
          ? current
          : items[0]?.name ?? null
      ))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '仓库加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadRepositories()
  }, [loadRepositories])

  const selectedRepository = repositories.find(
    (repository) => repository.name === selectedName,
  ) ?? null
  const registeredProject = selectedRepository
    ? projects.find((project) => project.virtual_path === selectedRepository.path) ?? null
    : null
  const filteredRepositories = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()

    if (!query) return repositories

    return repositories.filter((repository) => (
      `${repository.name} ${repository.path} ${repository.remote_url}`
        .toLocaleLowerCase()
        .includes(query)
    ))
  }, [repositories, search])

  function updateRepository(snapshot: Repository) {
    setRepositories((current) => {
      const exists = current.some((item) => item.name === snapshot.name)
      const updated = exists
        ? current.map((item) => item.name === snapshot.name ? snapshot : item)
        : [...current, snapshot]

      return [...updated].sort((left, right) => left.name.localeCompare(right.name))
    })
    setSelectedName(snapshot.name)
  }

  async function handleFetch() {
    if (!selectedRepository || busy) return
    setBusy('fetch')
    setError('')
    setNotice('')

    try {
      updateRepository(await fetchRepository(selectedRepository.name))
      setNotice('已从 origin 获取最新引用')
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : 'Fetch 失败')
    } finally {
      setBusy('')
    }
  }

  async function handleCreateBranch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedBranch = branchName.trim()

    if (!selectedRepository || !normalizedBranch || busy) return
    setBusy('branch')
    setError('')
    setNotice('')

    try {
      updateRepository(await createRepositoryBranch(
        selectedRepository.name,
        normalizedBranch,
      ))
      setBranchName('')
      setNotice(`已创建并切换到 ${normalizedBranch}`)
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : '创建分支失败')
    } finally {
      setBusy('')
    }
  }

  async function handleCheckout(branch: string) {
    if (!selectedRepository || branch === selectedRepository.current_branch || busy) return
    setBusy(`checkout:${branch}`)
    setError('')
    setNotice('')

    try {
      updateRepository(await checkoutRepositoryBranch(
        selectedRepository.name,
        branch,
      ))
      setNotice(`已切换到 ${branch}`)
    } catch (checkoutError) {
      setError(checkoutError instanceof Error ? checkoutError.message : '切换分支失败')
    } finally {
      setBusy('')
    }
  }

  async function handleClone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedUrl = cloneUrl.trim()

    if (!normalizedUrl || busy) return
    setBusy('clone')
    setCloneError('')

    try {
      const snapshot = await cloneRepository(normalizedUrl)
      updateRepository(snapshot)
      setCloneUrl('')
      setShowCloneDialog(false)
      setNotice(`已克隆 ${snapshot.name}`)
    } catch (cloneFailure) {
      setCloneError(cloneFailure instanceof Error ? cloneFailure.message : '克隆失败')
    } finally {
      setBusy('')
    }
  }

  function requestCommit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = commitMessage.trim()

    if (!selectedRepository || !selectedRepository.dirty || !message || busy) return
    setPendingAction({ kind: 'commit', message })
  }

  function requestPush() {
    if (!selectedRepository || selectedRepository.dirty || busy) return
    setPendingAction({
      kind: 'push',
      branch: selectedRepository.current_branch,
    })
  }

  function requestPullRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const title = pullRequestTitle.trim()
    const base = pullRequestBase.trim()

    if (!selectedRepository || selectedRepository.dirty || !title || !base || busy) return
    setPendingAction({
      kind: 'pull-request',
      title,
      body: pullRequestBody.trim(),
      base,
      head: selectedRepository.current_branch,
    })
  }

  async function executePendingAction() {
    if (!selectedRepository || !pendingAction || busy) return
    const action = pendingAction
    setBusy(action.kind)
    setError('')
    setNotice('')

    try {
      if (action.kind === 'commit') {
        const result = await commitRepository(
          selectedRepository.name,
          action.message,
        )
        updateRepository(result.repository)
        setCommitMessage('')
        setNotice(`已提交 ${result.sha.slice(0, 8)} · ${result.subject}`)
      } else if (action.kind === 'push') {
        const result = await pushRepository(selectedRepository.name)
        updateRepository(result.repository)
        setNotice(`已推送 origin/${result.branch}`)
      } else {
        const result = await createRepositoryPullRequest(
          selectedRepository.name,
          {
            title: action.title,
            body: action.body,
            base: action.base,
          },
        )
        setCreatedPullRequest(result)
        setNotice(`Pull Request #${result.number} 已创建`)
      }

      setPendingAction(null)
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : '仓库操作失败')
      setPendingAction(null)
    } finally {
      setBusy('')
    }
  }

  const protectedBranch = selectedRepository
    ? ['main', 'master'].includes(selectedRepository.current_branch)
    : false
  const confirmation = pendingAction?.kind === 'commit'
    ? {
        title: '确认创建 Commit',
        detail: `将暂存 ${selectedRepository?.name ?? ''} 的全部修改，并提交为“${pendingAction.message}”。`,
        action: '确认提交',
        icon: 'git-commit' as const,
      }
    : pendingAction?.kind === 'push'
      ? {
          title: '确认推送分支',
          detail: `将 ${pendingAction.branch} 推送到固定远程 origin。`,
          action: '确认推送',
          icon: 'upload' as const,
        }
      : pendingAction?.kind === 'pull-request'
        ? {
            title: '确认创建 Pull Request',
            detail: `将在 GitHub 上创建 ${pendingAction.head} → ${pendingAction.base} 的 Pull Request。`,
            action: '确认创建',
            icon: 'git-branch' as const,
          }
        : null

  return (
    <main className="repositories-page">
      <div className="repositories-toolbar">
        <div>
          <h2>Repositories</h2>
          <span>{loading ? '正在扫描…' : `${repositories.length} 个本地 Git 仓库`}</span>
        </div>
        <button className="primary-action" type="button" onClick={() => { setCloneError(''); setShowCloneDialog(true) }}>
          <Icon name="plus" size={17} />克隆仓库
        </button>
      </div>

      {loading ? (
        <div className="repositories-state"><span className="loading-ring" /><p>正在扫描 /projects…</p></div>
      ) : error && repositories.length === 0 ? (
        <div className="repositories-state"><Icon name="repository" size={32} /><h3>仓库加载失败</h3><p>{error}</p><button className="primary-action" type="button" onClick={() => void loadRepositories()}>重新加载</button></div>
      ) : repositories.length === 0 ? (
        <div className="repositories-state"><Icon name="repository" size={32} /><h3>还没有本地 Git 仓库</h3><button className="primary-action" type="button" onClick={() => setShowCloneDialog(true)}>克隆仓库</button></div>
      ) : (
        <div className="repositories-browser">
          <nav className="repository-list" aria-label="仓库列表">
            {filteredRepositories.map((repository) => (
              <button
                key={repository.name}
                className={`repository-item ${selectedName === repository.name ? 'active' : ''}`}
                type="button"
                onClick={() => { setSelectedName(repository.name); setError(''); setNotice(''); setCreatedPullRequest(null) }}
              >
                <span className="repository-item-icon"><Icon name="repository" size={17} /></span>
                <span className="repository-item-copy">
                  <strong>{repository.name}</strong>
                  <small><Icon name="git-branch" size={13} />{repository.current_branch}</small>
                  <code>{repository.path}</code>
                </span>
                <span className={`repository-dirty-dot ${repository.dirty ? 'dirty' : ''}`} title={repository.dirty ? '有未提交修改' : '工作区干净'} />
              </button>
            ))}
            {!filteredRepositories.length ? <p className="repository-list-empty">没有匹配的仓库</p> : null}
          </nav>

          {selectedRepository ? (
            <section className="repository-detail">
              <header className="repository-detail-header">
                <div className="repository-title-row">
                  <span className="repository-detail-icon"><Icon name="repository" size={20} /></span>
                  <div><h2>{selectedRepository.name}</h2><code>{selectedRepository.path}</code></div>
                </div>
                <div className="repository-header-actions">
                  <button className="primary-action repository-review-action" type="button" disabled={!registeredProject || Boolean(busy)} title={registeredProject ? '打开代码审查工作台' : '请先将该仓库登记为 Project'} onClick={() => registeredProject && onOpenReview(registeredProject.project_id)}>
                    <ScanSearch size={16} />代码审查
                  </button>
                  <button className="secondary-action" type="button" disabled={Boolean(busy)} onClick={() => void handleFetch()}>
                    <Icon name="refresh" size={16} />{busy === 'fetch' ? '正在 Fetch…' : 'Fetch'}
                  </button>
                </div>
              </header>

              <div className="repository-facts">
                <div><span>当前分支</span><strong><Icon name="git-branch" size={15} />{selectedRepository.current_branch}</strong></div>
                <div><span>工作区状态</span><strong className={selectedRepository.dirty ? 'dirty-text' : 'clean-text'}>{selectedRepository.dirty ? '有未提交修改' : '干净'}</strong></div>
                <div className="repository-remote"><span>Origin</span>{selectedRepository.remote_url ? <code title={selectedRepository.remote_url}>{selectedRepository.remote_url}</code> : <em>未配置</em>}</div>
              </div>

              {error ? <div className="repository-alert error" role="alert">{error}</div> : null}
              {notice ? <div className="repository-alert success" role="status">{notice}</div> : null}

              <section className="repository-workflow">
                <div className="repository-workflow-heading"><h3>交付工作流</h3><span>{selectedRepository.current_branch}</span></div>

                <div className="workflow-step">
                  <span className="workflow-step-number">1</span>
                  <div className="workflow-step-content">
                    <div className="workflow-step-title"><strong>Commit</strong><span>{selectedRepository.dirty ? '有待提交修改' : '工作区干净'}</span></div>
                    <form className="repository-commit-form" onSubmit={requestCommit}>
                      <label htmlFor="commit-message">提交信息</label>
                      <div><input id="commit-message" value={commitMessage} onChange={(event) => setCommitMessage(event.target.value)} placeholder="feat: implement repository workflow" maxLength={200} /><button className="primary-action" type="submit" disabled={!selectedRepository.dirty || !commitMessage.trim() || Boolean(busy)}><Icon name="git-commit" size={16} />Commit</button></div>
                    </form>
                  </div>
                </div>

                <div className="workflow-step">
                  <span className="workflow-step-number">2</span>
                  <div className="workflow-step-content">
                    <div className="workflow-step-title"><strong>Push</strong><span>origin/{selectedRepository.current_branch}</span></div>
                    <button className="secondary-action repository-push-action" type="button" disabled={selectedRepository.dirty || protectedBranch || !selectedRepository.remote_url || Boolean(busy)} onClick={requestPush}><Icon name="upload" size={16} />{busy === 'push' ? '正在推送…' : protectedBranch ? '请先切换功能分支' : 'Push 当前分支'}</button>
                  </div>
                </div>

                <div className="workflow-step">
                  <span className="workflow-step-number">3</span>
                  <div className="workflow-step-content">
                    <div className="workflow-step-title"><strong>Pull Request</strong><span>{selectedRepository.current_branch} → {pullRequestBase || 'base'}</span></div>
                    <form className="pull-request-form" onSubmit={requestPullRequest}>
                      <div className="pull-request-fields"><label>PR 标题<input value={pullRequestTitle} onChange={(event) => setPullRequestTitle(event.target.value)} placeholder="feat: complete repository workflow" maxLength={256} /></label><label>目标分支<input value={pullRequestBase} onChange={(event) => setPullRequestBase(event.target.value)} placeholder="main" maxLength={255} /></label></div>
                      <label>PR 正文<textarea value={pullRequestBody} onChange={(event) => setPullRequestBody(event.target.value)} placeholder="Summary and verification" maxLength={10000} rows={4} /></label>
                      <button className="primary-action" type="submit" disabled={selectedRepository.dirty || !pullRequestTitle.trim() || !pullRequestBase.trim() || selectedRepository.current_branch === pullRequestBase.trim() || Boolean(busy)}><Icon name="git-branch" size={16} />{busy === 'pull-request' ? '正在创建…' : '创建 Pull Request'}</button>
                    </form>
                    {createdPullRequest ? <a className="pull-request-result" href={createdPullRequest.url} target="_blank" rel="noreferrer"><span><strong>Pull Request #{createdPullRequest.number}</strong><small>{createdPullRequest.head} → {createdPullRequest.base}</small></span><Icon name="chevron-right" size={16} /></a> : null}
                  </div>
                </div>
              </section>

              <section className="branch-section">
                <div className="branch-section-heading"><div><h3>本地分支</h3><span>{selectedRepository.branches.length} 个分支</span></div></div>
                <form className="branch-create-form" onSubmit={(event) => void handleCreateBranch(event)}>
                  <label htmlFor="new-branch-name">新分支名称</label>
                  <div><input id="new-branch-name" value={branchName} onChange={(event) => setBranchName(event.target.value)} placeholder="feature/course" maxLength={255} /><button className="primary-action" type="submit" disabled={!branchName.trim() || Boolean(busy)}><Icon name="plus" size={16} />{busy === 'branch' ? '正在创建…' : '创建并切换'}</button></div>
                </form>
                <div className="branch-list">
                  {selectedRepository.branches.map((branch) => {
                    const active = branch === selectedRepository.current_branch
                    const checkingOut = busy === `checkout:${branch}`

                    return (
                      <div className={`branch-row ${active ? 'active' : ''}`} key={branch}>
                        <span className="branch-icon"><Icon name="git-branch" size={16} /></span>
                        <code>{branch}</code>
                        {active ? <span className="current-branch"><Icon name="check" size={14} />当前</span> : <button type="button" disabled={Boolean(busy)} onClick={() => void handleCheckout(branch)}>{checkingOut ? '切换中…' : '切换'}</button>}
                      </div>
                    )
                  })}
                </div>
              </section>
            </section>
          ) : <div className="repository-detail-placeholder">选择一个仓库查看详情</div>}
        </div>
      )}

      {showCloneDialog ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== 'clone') setShowCloneDialog(false) }}>
          <form className="project-dialog repository-dialog" onSubmit={(event) => void handleClone(event)}>
            <div className="dialog-icon"><Icon name="repository" /></div>
            <h2>克隆 GitHub 仓库</h2>
            <p>仓库将保存到 Agent 工作区的 <code>/projects</code> 目录。</p>
            <label>GitHub HTTPS 地址<input autoFocus value={cloneUrl} onChange={(event) => setCloneUrl(event.target.value)} placeholder="https://github.com/owner/repository.git" maxLength={2048} /></label>
            {cloneError ? <p className="dialog-error">{cloneError}</p> : null}
            <div className="dialog-actions"><button type="button" disabled={busy === 'clone'} onClick={() => setShowCloneDialog(false)}>取消</button><button className="primary-action" type="submit" disabled={!cloneUrl.trim() || Boolean(busy)}>{busy === 'clone' ? '正在克隆…' : '开始克隆'}</button></div>
          </form>
        </div>
      ) : null}

      {pendingAction && confirmation ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) setPendingAction(null) }}>
          <section className="project-dialog confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="repository-confirmation-title">
            <div className="dialog-icon"><Icon name={confirmation.icon} /></div>
            <h2 id="repository-confirmation-title">{confirmation.title}</h2>
            <p>{confirmation.detail}</p>
            <div className="dialog-actions"><button type="button" disabled={Boolean(busy)} onClick={() => setPendingAction(null)}>取消</button><button className="primary-action" type="button" disabled={Boolean(busy)} onClick={() => void executePendingAction()}>{busy ? '正在执行…' : confirmation.action}</button></div>
          </section>
        </div>
      ) : null}
    </main>
  )
}

function App() {
  const [view, setView] = useState<View>('chat')
  const [projects, setProjects] = useState<Project[]>([])
  const [threads, setThreads] = useState<Thread[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null)
  const [selectedSkillName, setSelectedSkillName] = useState<string | null>(null)
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null)
  const [activeRun, setActiveRun] = useState<Run | null>(null)
  const [runPerformance, setRunPerformance] = useState<RunPerformance | null>(null)
  const [fileChanges, setFileChanges] = useState<ProjectFileChanges | null>(null)
  const [fileChangesError, setFileChangesError] = useState('')
  const [loadingFileChanges, setLoadingFileChanges] = useState(false)
  const [runStream, setRunStream] = useState({ runId: null as string | null, text: '' })
  const [steps, setSteps] = useState<AgentStep[]>([])
  const [prompt, setPrompt] = useState('')
  const [taskKind, setTaskKind] = useState<TaskKind>('coding')
  const [showTaskKindMenu, setShowTaskKindMenu] = useState(false)
  const [networkAccess, setNetworkAccess] = useState(false)
  const [showNetworkMenu, setShowNetworkMenu] = useState(false)
  const [toolCapabilities, setToolCapabilities] = useState<ToolCapabilities | null>(null)
  const [capabilityError, setCapabilityError] = useState('')
  const [search, setSearch] = useState('')
  const [notice, setNotice] = useState('准备就绪')
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [loadingConversation, setLoadingConversation] = useState(false)
  const [loadingSkills, setLoadingSkills] = useState(false)
  const [loadingSkillDetail, setLoadingSkillDetail] = useState(false)
  const [skillsLoaded, setSkillsLoaded] = useState(false)
  const [skillsReloadToken, setSkillsReloadToken] = useState(0)
  const [skillsError, setSkillsError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [creatingThread, setCreatingThread] = useState(false)
  const [showProjectDialog, setShowProjectDialog] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectPath, setProjectPath] = useState('/projects/')
  const [projectError, setProjectError] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [showRunPanel, setShowRunPanel] = useState(() => window.innerWidth > 1100)
  const [reviewRunId, setReviewRunId] = useState<string | null>(null)
  const [reviewReturnView, setReviewReturnView] = useState<'chat' | 'repositories'>('repositories')
  const eventSourceRef = useRef<EventSource | null>(null)
  const terminalRef = useRef(false)
  const messageEndRef = useRef<HTMLDivElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const taskKindMenuRef = useRef<HTMLDivElement | null>(null)
  const networkMenuRef = useRef<HTMLDivElement | null>(null)
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null)
  const fileChangesRequestRef = useRef(0)

  const selectedProject = projects.find((item) => item.project_id === selectedProjectId) ?? null
  const selectedThread = threads.find((item) => item.thread_id === selectedThreadId) ?? null
  const selectedTaskKind = taskKinds.find((item) => item.value === taskKind) ?? taskKinds[0]
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
  const filteredSkills = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return skills
    return skills.filter((skill) => (
      `${skill.name} ${skill.description}`.toLocaleLowerCase().includes(query)
    ))
  }, [search, skills])
  const orderedMessages = useMemo(
    () => [...messages].sort((left, right) => left.sequence - right.sequence),
    [messages],
  )
  const planningRunId = useMemo(
    () => actionablePlanningRunId(runs),
    [runs],
  )
  const streamText = runStream.text

  useEffect(() => {
    if (!showTaskKindMenu) return

    function closeTaskKindMenu(event: MouseEvent) {
      if (!taskKindMenuRef.current?.contains(event.target as Node)) {
        setShowTaskKindMenu(false)
      }
    }

    function closeTaskKindMenuWithEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setShowTaskKindMenu(false)
    }

    document.addEventListener('mousedown', closeTaskKindMenu)
    document.addEventListener('keydown', closeTaskKindMenuWithEscape)
    return () => {
      document.removeEventListener('mousedown', closeTaskKindMenu)
      document.removeEventListener('keydown', closeTaskKindMenuWithEscape)
    }
  }, [showTaskKindMenu])

  useEffect(() => {
    if (!showNetworkMenu) return

    function closeNetworkMenu(event: MouseEvent) {
      if (!networkMenuRef.current?.contains(event.target as Node)) {
        setShowNetworkMenu(false)
      }
    }

    function closeNetworkMenuWithEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setShowNetworkMenu(false)
    }

    document.addEventListener('mousedown', closeNetworkMenu)
    document.addEventListener('keydown', closeNetworkMenuWithEscape)
    return () => {
      document.removeEventListener('mousedown', closeNetworkMenu)
      document.removeEventListener('keydown', closeNetworkMenuWithEscape)
    }
  }, [showNetworkMenu])

  useEffect(() => {
    let active = true
    setCapabilityError('')
    void getToolCapabilities(taskKind, networkAccess)
      .then((capabilities) => {
        if (active) setToolCapabilities(capabilities)
      })
      .catch((error: unknown) => {
        if (!active) return
        setToolCapabilities(null)
        setCapabilityError(error instanceof Error ? error.message : '无法读取联网能力')
      })
    return () => {
      active = false
    }
  }, [networkAccess, taskKind])

  const refreshFileChanges = useCallback(async (projectId: string) => {
    const requestId = ++fileChangesRequestRef.current
    setLoadingFileChanges(true)
    setFileChangesError('')
    try {
      const summary = await getProjectFileChanges(projectId)
      if (requestId === fileChangesRequestRef.current) setFileChanges(summary)
    } catch (error) {
      if (requestId !== fileChangesRequestRef.current) return
      setFileChanges(null)
      setFileChangesError(error instanceof Error ? error.message : '无法读取文件更改')
    } finally {
      if (requestId === fileChangesRequestRef.current) setLoadingFileChanges(false)
    }
  }, [])

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
    const performanceRunId = runId ?? authoritativeRuns.at(-1)?.run_id
    const latestPerformance = performanceRunId
      ? await getRunPerformance(performanceRunId)
      : null
    setThreads((current) => current.map((item) => item.thread_id === latestThread.thread_id ? latestThread : item))
    setMessages(latestMessages)
    setRunStream((current) => reconcileRunStream(current, latestMessages, runId))
    setRuns(authoritativeRuns)
    setRunPerformance(latestPerformance)
    if (selectedProjectId) void refreshFileChanges(selectedProjectId)
    const running = [...authoritativeRuns].reverse().find((run) => run.status === 'pending' || run.status === 'running') ?? null
    setActiveRun(running)
  }, [refreshFileChanges, selectedProjectId])

  const closeEventSource = useCallback(() => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
  }, [])

  const updateSteps = useCallback((kind: RunEventKind, payload: RunEventPayload, eventId: string) => {
    setSteps((current) => {
      const id = stepIdentity(kind, payload, eventId)
      const presentation = stepPresentation(kind, payload)
      const status = kind === 'failed' || kind === 'terminated' || payload.status === 'failed' || payload.status === 'error'
        ? 'failed' as const
        : kind === 'created' || kind === 'completed' || kind === 'tool_finished'
          ? 'completed' as const
          : 'running' as const
      const previousStatus = kind === 'failed' || kind === 'terminated' ? 'failed' as const : 'completed' as const
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
              sources: payload.sources ?? step.sources,
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
          sources: payload.sources,
        },
      ]
    })
  }, [])

  const connectToRun = useCallback((runId: string, threadId: string) => {
    closeEventSource()
    terminalRef.current = false
    setRunStream((current) => current.runId === runId ? current : { runId, text: '' })
    const source = new EventSource(runEventsUrl(runId))
    const processedEventIds = new Set<string>()
    eventSourceRef.current = source

    for (const kind of runEventKinds) {
      source.addEventListener(kind, (rawEvent) => {
        if (source !== eventSourceRef.current || terminalRef.current) return
        const event = rawEvent as MessageEvent<string>
        if (!acceptRunEvent(event.lastEventId, processedEventIds)) return
        try {
          const payload = JSON.parse(event.data) as RunEventPayload
          updateSteps(kind, payload, event.lastEventId)

          if (kind === 'token' && payload.source === 'main' && payload.text) {
            setRunStream((current) => current.runId === runId
              ? { ...current, text: current.text + payload.text }
              : current)
          }

          if (
            payload.status
            && payload.status !== 'error'
            && ['created', 'running', 'completed', 'failed', 'terminated'].includes(kind)
          ) {
            const runStatus: Run['status'] = payload.status
            setActiveRun((current) => current ? { ...current, status: runStatus } : current)
          }

          if (kind === 'running') setNotice('Agent 正在执行')

          if (kind === 'completed' || kind === 'failed' || kind === 'terminated') {
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

    source.onopen = () => {
      if (source === eventSourceRef.current) setNotice('已连接 Agent 事件流')
    }
    source.onerror = () => {
      if (source === eventSourceRef.current && !terminalRef.current) {
        setNotice('事件流中断，正在自动重连')
      }
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
    if (view !== 'skills' || skillsLoaded) return

    let cancelled = false
    setLoadingSkills(true)
    setSkillsError('')

    listSkills()
      .then((items) => {
        if (cancelled) return
        setSkills(items)
        setSelectedSkillName((current) => (
          items.some((item) => item.name === current)
            ? current
            : items[0]?.name ?? null
        ))
        setSkillsLoaded(true)
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setSkillsError(error instanceof Error ? error.message : 'Skills 加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingSkills(false)
      })

    return () => {
      cancelled = true
    }
  }, [skillsLoaded, skillsReloadToken, view])

  useEffect(() => {
    if ((view !== 'chat' && view !== 'review') || !selectedProjectId) {
      fileChangesRequestRef.current += 1
      setFileChanges(null)
      setFileChangesError('')
      setLoadingFileChanges(false)
      return
    }
    void refreshFileChanges(selectedProjectId)
  }, [refreshFileChanges, selectedProjectId, view])

  useEffect(() => {
    if (view !== 'skills' || !selectedSkillName) return

    let cancelled = false
    setSkillDetail(null)
    setLoadingSkillDetail(true)
    setSkillsError('')

    getSkill(selectedSkillName)
      .then((detail) => {
        if (!cancelled) setSkillDetail(detail)
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setSkillsError(error instanceof Error ? error.message : 'Skill 详情加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingSkillDetail(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedSkillName, skillsReloadToken, view])

  useEffect(() => {
    closeEventSource()
    setMessages([])
    setRuns([])
    setSteps([])
    setRunStream({ runId: null, text: '' })
    setActiveRun(null)
    setRunPerformance(null)
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
        const latestRun = running ?? latestRuns.at(-1) ?? null
        if (latestRun) {
          void getRunPerformance(latestRun.run_id)
            .then((performance) => {
              if (!cancelled) setRunPerformance(performance)
            })
            .catch(() => undefined)
        }
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

  async function handleStartReview(
    scope: ReviewScope,
    existingRunId: string | null,
    source: ReviewSource,
    prNumber: number | null,
  ) {
    if (existingRunId) {
      return startCodeReview(existingRunId, scope, source, prNumber)
    }
    if (!selectedProjectId) throw new Error('找不到当前项目。')

    let threadId = selectedThreadId
    if (!threadId) {
      const thread = await createThread(selectedProjectId, '代码审查')
      setThreads((current) => [thread, ...current])
      setSelectedThreadId(thread.thread_id)
      threadId = thread.thread_id
    }
    const result = await createCodeReviewRun(
      threadId,
      scope,
      source,
      prNumber,
    )
    try {
      const [reviewRun, performance] = await Promise.all([
        getRun(result.run_id),
        getRunPerformance(result.run_id),
      ])
      setRuns((current) => (
        current.some((run) => run.run_id === reviewRun.run_id)
          ? current.map((run) => run.run_id === reviewRun.run_id ? reviewRun : run)
          : [...current, reviewRun]
      ))
      setRunPerformance(performance)
      setActiveRun(null)
    } catch {
      setNotice('审查已完成，Run 历史将在返回聊天后刷新')
    }
    return result
  }

  function openRepositoryReview(projectId: string) {
    setSelectedProjectId(projectId)
    setSelectedThreadId(null)
    setReviewRunId(null)
    setReviewReturnView('repositories')
    setView('review')
    setMobileSidebarOpen(false)
  }

  function openRunReview(runId: string) {
    setReviewRunId(runId)
    setReviewReturnView('chat')
    setView('review')
    setMobileSidebarOpen(false)
  }

  function closeReview() {
    setView(reviewReturnView)
    if (reviewReturnView === 'chat' && selectedThreadId) {
      void refreshConversation(selectedThreadId, reviewRunId ?? undefined)
        .catch((error: unknown) => {
          setNotice(error instanceof Error ? error.message : 'Run 历史刷新失败')
        })
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
    setRunStream({ runId: null, text: '' })
    setRunPerformance(null)
    try {
      const created = await startRun(selectedThreadId, content, taskKind, networkAccess)
      setMessages((current) => [...current, created.message])
      setActiveRun(created.run)
      setRuns((current) => [...current, created.run])
      setPrompt('')
      setNotice('任务已创建，正在连接 Agent')
      connectToRun(created.run.run_id, selectedThreadId)
      void getRunPerformance(created.run.run_id)
        .then(setRunPerformance)
        .catch(() => undefined)
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

  function preparePlanImplementation() {
    if (isRunning) return

    setTaskKind('coding')
    setShowTaskKindMenu(false)
    setPrompt(implementPlanPrompt)
    setNotice('已切换到 coding，请确认实施要求后发送')
    requestAnimationFrame(() => composerInputRef.current?.focus())
  }

  const isRunning = activeRun?.status === 'pending' || activeRun?.status === 'running'
  const lastRun = activeRun ?? runs.at(-1) ?? null

  return (
    <div className={`workbench view-${view} ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
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
              <button key={project.project_id} className={`project-item ${selectedProjectId === project.project_id && (view === 'chat' || view === 'review') ? 'active' : ''}`} type="button" onClick={() => selectProject(project.project_id)}>
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
          <button className={`skills-link ${view === 'repositories' ? 'active' : ''}`} type="button" onClick={() => { setView('repositories'); setMobileSidebarOpen(false) }}><span><Icon name="repository" />Repositories</span><Icon name="chevron-right" size={16} /></button>
          <button className={`skills-link ${view === 'skills' ? 'active' : ''}`} type="button" onClick={() => { setView('skills'); setMobileSidebarOpen(false) }}><span><Icon name="box" />Skills</span><Icon name="chevron-right" size={16} /></button>
          <div className="account-row"><span className="avatar">T</span><span><strong>tang</strong><small>本地工作区</small></span><Icon name="chevron-down" size={16} /></div>
        </div>
      </aside>

      <section className="content-shell">
        {view !== 'review' ? <header className="topbar">
          <div className="topbar-leading"><button className="icon-button mobile-menu" type="button" aria-label="打开导航" onClick={() => setMobileSidebarOpen(true)}><Icon name="message" /></button><div className="breadcrumbs"><strong>{view === 'chat' ? selectedProject?.name ?? 'Tang Agent' : 'Tang Agent'}</strong><span>/</span><span>{view === 'skills' ? 'Skills' : view === 'repositories' ? 'Repositories' : selectedThread?.title ?? '新对话'}</span></div></div>
          <div className="topbar-actions">
            <label className="search-box"><Icon name="search" size={17} /><input ref={searchInputRef} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={view === 'repositories' ? '搜索仓库' : view === 'skills' ? '搜索 Skill' : '搜索'} /><kbd>⌘ K</kbd></label>
            {view === 'chat' ? <><span className="topbar-separator" /><button className={`icon-button ${showRunPanel ? 'active' : ''}`} type="button" title="执行状态" onClick={() => setShowRunPanel((value) => !value)}><Icon name="terminal" /></button><button className="icon-button" type="button" title="历史记录"><Icon name="history" /></button><button className="icon-button" type="button" title="文档"><Icon name="book" /></button><button className="icon-button" type="button" title="设置"><Icon name="settings" /></button></> : null}
          </div>
        </header> : null}

        {view === 'review' && selectedProject ? (
          <ReviewWorkspace
            project={selectedProject}
            threadId={selectedThreadId}
            runId={reviewRunId}
            hasLocalChanges={fileChanges ? fileChanges.changed_files > 0 : null}
            onBack={closeReview}
            onStartReview={handleStartReview}
            onRunChanged={setReviewRunId}
          />
        ) : view === 'skills' ? (
          <main className="skills-page">
            <div className="skills-toolbar">
              <div>
                <h2>已安装 Skills</h2>
                <p>{loadingSkills ? '正在加载…' : `${skills.length} 个可用 Skill`}</p>
              </div>
            </div>

            {loadingSkills ? (
              <div className="skills-empty"><span className="loading-ring" /><p>正在加载 Skills…</p></div>
            ) : skillsError && skills.length === 0 ? (
              <div className="skills-empty skills-error"><Icon name="box" size={34} /><h3>Skills 加载失败</h3><p>{skillsError}</p><button className="primary-action" type="button" onClick={() => setSkillsReloadToken((value) => value + 1)}>重新加载</button></div>
            ) : skills.length === 0 ? (
              <div className="skills-empty"><Icon name="box" size={34} /><h3>还没有安装 Skill</h3></div>
            ) : (
              <div className="skills-browser">
                <nav className="skill-list" aria-label="Skill 列表">
                  {filteredSkills.map((skill) => (
                    <button
                      key={skill.name}
                      className={`skill-item ${selectedSkillName === skill.name ? 'active' : ''}`}
                      type="button"
                      onClick={() => setSelectedSkillName(skill.name)}
                    >
                      <span className="skill-item-icon"><Icon name="box" size={17} /></span>
                      <span className="skill-item-copy"><strong>{skill.name}</strong><small>{skill.description}</small><code>{skill.path}</code></span>
                      <Icon name="chevron-right" size={16} />
                    </button>
                  ))}
                  {!filteredSkills.length ? <p className="skill-list-empty">没有匹配的 Skill</p> : null}
                </nav>

                <section className="skill-preview" aria-live="polite">
                  {loadingSkillDetail ? (
                    <div className="skill-preview-state"><span className="loading-ring" /><p>正在加载详情…</p></div>
                  ) : skillsError ? (
                    <div className="skill-preview-state"><Icon name="box" size={30} /><p>{skillsError}</p><button className="primary-action" type="button" onClick={() => setSkillsReloadToken((value) => value + 1)}>重新加载</button></div>
                  ) : skillDetail ? (
                    <>
                      <header className="skill-preview-header"><span>SKILL.md</span><h2>{skillDetail.name}</h2><p>{skillDetail.description}</p><code>{skillDetail.path}</code></header>
                      <div className="skill-document"><MarkdownContent content={skillDetail.content} /></div>
                    </>
                  ) : (
                    <div className="skill-preview-state"><p>选择一个 Skill 查看详情</p></div>
                  )}
                </section>
              </div>
            )}
          </main>
        ) : view === 'repositories' ? (
          <RepositoriesPage search={search} projects={projects} onOpenReview={openRepositoryReview} />
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
                    {orderedMessages.map((message) => {
                      const canImplementPlan = !isRunning
                        && message.role === 'assistant'
                        && message.run_id === planningRunId

                      return (
                        <article key={message.message_id} className={`message message-${message.role}`}>
                          <div className="message-avatar">{message.role === 'user' ? <Icon name="user" size={16} /> : <Logo small />}</div>
                          <div className="message-body">
                            <div className="message-meta"><strong>{message.role === 'user' ? '你' : message.role === 'assistant' ? 'Tang Agent' : '系统'}</strong><time>{timeLabel(message.created_at)}</time></div>
                            <div className="message-content">{message.role === 'user' ? message.content : <MarkdownContent content={message.content} />}</div>
                            {canImplementPlan ? (
                              <div className="plan-action">
                                <button type="button" onClick={preparePlanImplementation}>
                                  <Icon name="code" size={16} />
                                  按此方案实施
                                </button>
                              </div>
                            ) : null}
                          </div>
                        </article>
                      )
                    })}
                    {streamText ? <article className="message message-assistant streaming"><div className="message-avatar"><Logo small /></div><div className="message-body"><div className="message-meta"><strong>Tang Agent</strong><span className="typing-dot" /></div><div className="message-content"><MarkdownContent content={streamText} /></div></div></article> : null}
                    <div ref={messageEndRef} />
                  </div>
                )}
              </section>

              <form className="composer" onSubmit={(event) => void handleSubmit(event)}>
                <div className="composer-main"><button className="attach-button" type="button" title="附件功能尚未接入">+</button><textarea ref={composerInputRef} value={prompt} onChange={(event) => { setPrompt(event.target.value); event.currentTarget.style.height = 'auto'; event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 160)}px` }} placeholder={selectedThread ? '输入你的需求，按 Enter 发送' : '请先选择或创建会话'} rows={1} disabled={!selectedThread || isRunning} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} /><span className="model-label">DeepSeek V4<Icon name="chevron-down" size={14} /></span><button className="send-button" type="submit" disabled={!prompt.trim() || !selectedThread || submitting || isRunning}><Icon name="send" size={18} /></button></div>
                <div className="composer-footer">
                  <div className="task-kind-picker" ref={taskKindMenuRef}>
                    {showTaskKindMenu ? <div className="task-kind-menu" role="listbox" aria-label="Agent 模式">{taskKinds.map((item) => <button className={item.value === taskKind ? 'selected' : ''} type="button" role="option" aria-selected={item.value === taskKind} key={item.value} onClick={() => { setTaskKind(item.value); setShowTaskKindMenu(false) }}><Icon name={item.icon} size={17} /><span>{item.value}</span>{item.value === taskKind ? <Icon name="check" size={16} /> : null}</button>)}</div> : null}
                    <button className="task-kind-trigger" type="button" aria-haspopup="listbox" aria-expanded={showTaskKindMenu} onClick={() => setShowTaskKindMenu((value) => !value)}><Icon name={selectedTaskKind.icon} size={17} /><span>{taskKind}</span><Icon name="chevron-down" size={14} /></button>
                  </div>
                  <div className="network-access-picker" ref={networkMenuRef}>
                    {showNetworkMenu ? <div className="network-access-menu" role="menu" aria-label="联网设置"><button type="button" role="menuitemradio" aria-checked={!networkAccess} className={!networkAccess ? 'selected' : ''} onClick={() => { setNetworkAccess(false); setShowNetworkMenu(false) }}><Globe2 size={16} /><span><strong>禁止联网</strong><small>仅使用本地信息</small></span>{!networkAccess ? <Icon name="check" size={15} /> : null}</button><button type="button" role="menuitemradio" aria-checked={networkAccess} className={networkAccess ? 'selected' : ''} onClick={() => { setNetworkAccess(true); setShowNetworkMenu(false) }}><Globe2 size={16} /><span><strong>允许联网</strong><small>{toolCapabilities?.web_search.provider ?? '固定搜索提供商'}</small></span>{networkAccess ? <Icon name="check" size={15} /> : null}</button>{networkAccess && (capabilityError || toolCapabilities?.web_search.unavailable_reason) ? <p role="status">{capabilityError || toolCapabilities?.web_search.unavailable_reason}</p> : null}</div> : null}
                    <button className={`network-access-trigger ${networkAccess ? 'enabled' : ''} ${networkAccess && toolCapabilities && !toolCapabilities.web_search.available ? 'unavailable' : ''}`} type="button" aria-haspopup="menu" aria-expanded={showNetworkMenu} disabled={isRunning} title={capabilityError || toolCapabilities?.web_search.unavailable_reason || (networkAccess ? '下一次 Run 允许结构化网页搜索' : '下一次 Run 禁止联网搜索')} onClick={() => setShowNetworkMenu((value) => !value)}><Globe2 size={16} /><span>联网</span><small>{networkAccess ? '开启' : '关闭'}</small><Icon name="chevron-down" size={13} /></button>
                  </div>
                  <div className="composer-hint"><span>Enter 发送，Shift + Enter 换行</span><span>{notice}</span></div>
                </div>
              </form>
            </main>

            <aside className={`run-panel ${showRunPanel ? 'open' : ''}`}>
              <div className="run-panel-header"><strong>Agent 执行状态</strong><div className="run-panel-header-actions">{lastRun ? <button className="run-review-entry" type="button" disabled={isRunning} title="打开此 Run 的代码审查" onClick={() => openRunReview(lastRun.run_id)}><ScanSearch size={14} />审查</button> : null}<span className={`run-state ${isRunning ? 'running' : lastRun?.status === 'failed' ? 'failed' : ''}`}><i />{isRunning ? '运行中' : lastRun?.status === 'failed' ? '失败' : lastRun?.status === 'completed' ? '已完成' : '空闲'}</span></div></div>
              <div className="run-panel-section"><h2>执行步骤</h2>{steps.length ? <ol className="step-list">{steps.map((step, index) => <li key={step.id} className={`step-${step.status} ${step.source.startsWith('subagent') ? 'step-subagent' : ''}`}><span className="step-number">{step.status === 'completed' ? <Icon name="check" size={14} /> : index + 1}</span><div><div className="step-title"><strong>{step.title}</strong><time>{timeLabel(step.createdAt)}</time></div><p>{step.detail}</p>{step.sources?.length ? <div className="step-sources">{step.sources.map((source) => <a key={`${source.citation_id}:${source.url}`} href={source.url} target="_blank" rel="noopener noreferrer" title={source.url}><span>{source.citation_id}</span>{source.title}</a>)}</div> : null}</div></li>)}</ol> : <div className="steps-empty"><Icon name="clock" /><p>发送任务后，这里会实时显示 Agent 的执行过程。</p></div>}</div>
              <section className="file-changes-section" aria-live="polite">
                <div className="file-changes-heading">
                  <strong>文件更改</strong>
                  {fileChanges?.changed_files ? <span>{fileChanges.changed_files} 个文件</span> : null}
                </div>
                {loadingFileChanges ? (
                  <div className="file-changes-state"><span className="loading-ring" /><span>正在统计…</span></div>
                ) : fileChangesError ? (
                  <div className="file-changes-state error" role="alert">{fileChangesError}</div>
                ) : fileChanges && fileChanges.changed_files > 0 ? (
                  <>
                    <div className="file-changes-totals"><span className="addition">+{fileChanges.additions}</span><span className="deletion">-{fileChanges.deletions}</span>{fileChanges.binary_files ? <span>{fileChanges.binary_files} 个二进制文件</span> : null}</div>
                    <ul className="file-change-list">
                      {fileChanges.files.map((file) => {
                        const relativePath = file.path.startsWith(`${fileChanges.project_path}/`)
                          ? file.path.slice(fileChanges.project_path.length + 1)
                          : file.path
                        return (
                          <li key={file.path} className={`file-change-${file.status}`}>
                            <span className="file-change-icon"><Icon name="code" size={14} /></span>
                            <code title={file.path}>{relativePath}</code>
                            <span className="file-change-counts">
                              {file.binary ? <span className="binary-change">BIN</span> : <><span className="addition">+{file.additions ?? 0}</span><span className="deletion">-{file.deletions ?? 0}</span></>}
                            </span>
                          </li>
                        )
                      })}
                    </ul>
                    {fileChanges.hidden_files ? <p className="hidden-file-note">另有 {fileChanges.hidden_files} 个敏感文件未显示</p> : null}
                  </>
                ) : (
                  <div className="file-changes-state">工作区没有未提交更改</div>
                )}
              </section>
              <div className="run-details"><div className="details-heading"><strong>任务详情</strong><Icon name="chevron-down" size={16} /></div><dl><div><dt>任务 ID</dt><dd title={lastRun?.run_id}>{lastRun?.run_id.slice(0, 10) ?? '—'}</dd></div><div><dt>创建时间</dt><dd>{lastRun ? new Date(lastRun.created_at).toLocaleString('zh-CN') : '—'}</dd></div><div><dt>执行模式</dt><dd>{lastRun?.task_kind ?? '—'}</dd></div><div><dt>联网</dt><dd>{lastRun ? lastRun.network_access ? `${lastRun.network_provider} · ${lastRun.network_request_count} 次请求` : '禁止' : '—'}</dd></div><div><dt>搜索结果</dt><dd>{lastRun ? `${lastRun.network_result_count} 条${lastRun.network_cache_hit_count ? ` · 缓存 ${lastRun.network_cache_hit_count}` : ''}` : '—'}</dd></div><div><dt>首个输出</dt><dd>{durationLabel(runPerformance?.first_output_ms)}</dd></div><div><dt>运行耗时</dt><dd>{durationLabel(runPerformance?.duration_ms)}</dd></div><div><dt>模型调用</dt><dd>{runPerformance ? `${runPerformance.model_calls} / ${runPerformance.max_model_calls}` : '—'}</dd></div><div><dt>工具调用</dt><dd>{runPerformance ? `${runPerformance.tool_calls} / ${runPerformance.max_tool_calls}` : '—'}</dd></div><div><dt>重复调用</dt><dd>{runPerformance?.repeated_tool_calls ?? '—'}</dd></div><div><dt>工具错误</dt><dd>{runPerformance ? `${runPerformance.tool_errors}（拒绝 ${runPerformance.safety_rejections}）` : '—'}</dd></div>{lastRun?.network_limit_reached ? <div className="termination-detail"><dt>联网限制</dt><dd>{lastRun.network_limit_reason ?? '已达到预算'}</dd></div> : null}{runPerformance?.termination_reason ? <div className="termination-detail"><dt>终止原因</dt><dd title={lastRun?.error ?? undefined}>{terminationLabels[runPerformance.termination_reason] ?? runPerformance.termination_reason}</dd></div> : null}<div><dt>模型</dt><dd>DeepSeek V4</dd></div><div><dt>项目路径</dt><dd title={selectedProject?.virtual_path}>{selectedProject?.virtual_path ?? '—'}</dd></div></dl></div>
            </aside>
          </div>
        )}
      </section>

      {showProjectDialog ? <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowProjectDialog(false) }}><form className="project-dialog" onSubmit={(event) => void handleCreateProject(event)}><div className="dialog-icon"><Icon name="folder" /></div><h2>登记工作区项目</h2><p>项目目录必须已经存在于 Agent 工作区的 <code>/projects</code> 下。</p><label>项目名称<input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="例如 Tang Agent" maxLength={100} /></label><label>虚拟路径<input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="/projects/tang-agent" maxLength={1000} /></label>{projectError ? <p className="dialog-error">{projectError}</p> : null}<div className="dialog-actions"><button type="button" onClick={() => setShowProjectDialog(false)}>取消</button><button className="primary-action" type="submit" disabled={!projectName.trim() || !projectPath.trim()}>登记项目</button></div></form></div> : null}
    </div>
  )
}

export default App
