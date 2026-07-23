import {
  AlertTriangle,
  ArrowLeft,
  Binary,
  CheckCircle2,
  EyeOff,
  FileCode2,
  Files,
  GitCompareArrows,
  GitPullRequest,
  ExternalLink,
  LoaderCircle,
  LocateFixed,
  PanelLeftClose,
  PanelRightClose,
  Play,
  RefreshCw,
  Search,
  Send,
  ShieldAlert,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  getGitHubReviewCapability,
  getReviewSnapshot,
  listGitHubReviewPublications,
  prepareGitHubReview,
  publishGitHubReview,
  updateReviewFindingStatus,
} from '../api'
import type {
  CodeReviewResult,
  DiffLine,
  Project,
  ReviewCategory,
  ReviewDiff,
  ReviewDiffFile,
  ReviewFinding,
  ReviewFindingStatus,
  ReviewSource,
  ReviewScope,
  ReviewSeverity,
  ReviewSnapshot,
  GitHubPullRequest,
  GitHubReviewCapability,
  GitHubReviewEvent,
  GitHubReviewPreview,
  GitHubReviewPublication,
} from '../api'
import {
  aggregateFileFindings,
  categoryLabels,
  defaultPublicationFindingIds,
  diffLineDomId,
  filterAndSortFindings,
  filterReviewFiles,
  findingPublicationTarget,
  findingStatusLabels,
  relativeReviewPath,
  resolveFindingLocation,
  reviewErrorMessage,
  reviewFileDomId,
  reviewFileKey,
  reviewFilePath,
  severityLabels,
  shouldSuppressPublishKey,
  updateFindingStatusOptimistically,
} from './reviewUtils'
import type { FindingFilters, FindingLocation } from './reviewUtils'
import './ReviewWorkspace.css'

type MobileTab = 'files' | 'diff' | 'findings'

interface ReviewWorkspaceProps {
  project: Project
  threadId: string | null
  runId: string | null
  hasLocalChanges: boolean | null
  onBack: () => void
  onStartReview: (
    scope: ReviewScope,
    existingRunId: string | null,
    source: ReviewSource,
    prNumber: number | null,
  ) => Promise<CodeReviewResult>
  onRunChanged: (runId: string) => void
}

const scopeLabels: Record<ReviewScope, string> = {
  all: '全部变更',
  staged: '已暂存',
  unstaged: '未暂存',
}

const reviewSourceLabels: Record<ReviewSource, string> = {
  working_tree: '本地工作树',
  pull_request: 'Pull Request',
}

const githubEventLabels: Record<GitHubReviewEvent, string> = {
  COMMENT: '评论',
  APPROVE: '批准',
  REQUEST_CHANGES: '请求修改',
}

const capabilityReasonLabels: Record<string, string> = {
  gh_not_installed: '宿主机未安装 GitHub CLI',
  github_not_authenticated: 'GitHub CLI 认证已失效',
  github_remote_not_found: '项目没有 GitHub origin',
  unsupported_github_host: '当前只支持 github.com',
  permission_denied: '当前账号没有发布权限',
  publishing_disabled: 'GitHub 发布功能未启用',
  github_timeout: 'GitHub 查询超时',
  github_api_error: 'GitHub 暂时不可用',
}

const changeTypeLabels: Record<ReviewDiffFile['change_type'], { short: string; label: string }> = {
  modified: { short: 'M', label: '修改' },
  added: { short: 'A', label: '新增' },
  deleted: { short: 'D', label: '删除' },
  renamed: { short: 'R', label: '重命名' },
  copied: { short: 'C', label: '复制' },
  untracked: { short: 'U', label: '未跟踪' },
}

function reviewStatusLabel(snapshot: ReviewSnapshot | null): string {
  if (!snapshot) return '尚未审查'
  if (snapshot.status === 'collected') return '已保存快照'
  if (snapshot.status === 'failed') return '未完整完成'
  return '审查完成'
}

function ReviewToolbar(props: {
  project: Project
  snapshot: ReviewSnapshot | null
  scope: ReviewScope
  starting: boolean
  filesCollapsed: boolean
  findingsCollapsed: boolean
  onBack: () => void
  onReview: () => void
  onToggleFiles: () => void
  onToggleFindings: () => void
}) {
  const diff = props.snapshot?.diff
  return (
    <header className="review-toolbar">
      <div className="review-toolbar-leading">
        <button className="review-icon-button" type="button" aria-label="返回" title="返回仓库或聊天" onClick={props.onBack}>
          <ArrowLeft size={18} />
        </button>
        <div className="review-toolbar-title">
          <strong>{props.project.name}</strong>
          <span>代码审查</span>
        </div>
        <span className={`review-status review-status-${props.snapshot?.status ?? 'idle'}`}>
          {reviewStatusLabel(props.snapshot)}
        </span>
      </div>
      <div className="review-toolbar-metrics" aria-label="审查统计">
        <span>{diff?.source === 'pull_request' ? 'Pull Request' : scopeLabels[diff?.scope ?? props.scope]}</span>
        <span><Files size={14} />{diff?.file_count ?? 0}</span>
        <span className="review-additions">+{diff?.total_additions ?? 0}</span>
        <span className="review-deletions">-{diff?.total_deletions ?? 0}</span>
        <span>{props.snapshot?.finding_count ?? 0} 个问题</span>
      </div>
      <div className="review-toolbar-actions">
        <button className="review-icon-button desktop-panel-toggle" type="button" aria-label={props.filesCollapsed ? '展开文件列表' : '收起文件列表'} title={props.filesCollapsed ? '展开文件列表' : '收起文件列表'} onClick={props.onToggleFiles}>
          <PanelLeftClose size={18} />
        </button>
        <button className="review-icon-button desktop-panel-toggle" type="button" aria-label={props.findingsCollapsed ? '展开问题列表' : '收起问题列表'} title={props.findingsCollapsed ? '展开问题列表' : '收起问题列表'} onClick={props.onToggleFindings}>
          <PanelRightClose size={18} />
        </button>
        {props.snapshot ? (
          <button className="review-icon-button" type="button" aria-label="重新审查" title="创建新的审查 Run" disabled={props.starting} onClick={props.onReview}>
            {props.starting ? <LoaderCircle className="review-spin" size={18} /> : <RefreshCw size={18} />}
          </button>
        ) : null}
      </div>
    </header>
  )
}

function ReviewFileList(props: {
  diff: ReviewDiff
  findings: ReviewFinding[]
  selectedKey: string | null
  onSelect: (file: ReviewDiffFile) => void
}) {
  const [query, setQuery] = useState('')
  const files = useMemo(
    () => filterReviewFiles(props.diff.files, query),
    [props.diff.files, query],
  )
  const summaries = useMemo(
    () => aggregateFileFindings(props.diff.files, props.findings),
    [props.diff.files, props.findings],
  )

  return (
    <aside className="review-file-panel" aria-label="变更文件">
      <div className="review-panel-heading"><strong>变更文件</strong><span>{props.diff.file_count}</span></div>
      <label className="review-search">
        <Search size={15} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索路径" aria-label="搜索变更文件" />
      </label>
      <nav className="review-file-list">
        {files.map((file) => {
          const key = reviewFileKey(file)
          const path = relativeReviewPath(reviewFilePath(file), props.diff.repository_virtual_path)
          const pieces = path.split('/')
          const fileName = pieces.pop() ?? path
          const directory = pieces.join('/')
          const status = changeTypeLabels[file.change_type]
          const summary = summaries.get(key) ?? { count: 0, urgent: 0 }
          const oldPath = file.old_path
            ? relativeReviewPath(file.old_path, props.diff.repository_virtual_path)
            : null
          return (
            <button key={key} className={`review-file-item ${props.selectedKey === key ? 'active' : ''}`} type="button" onClick={() => props.onSelect(file)}>
              <span className={`review-file-status status-${file.change_type}`} title={status.label}>{status.short}</span>
              <span className="review-file-copy">
                <strong title={path}>{fileName}</strong>
                <small title={directory}>{directory || '项目根目录'}</small>
                {file.change_type === 'renamed' && oldPath && oldPath !== path ? <em title={`${oldPath} → ${path}`}>{oldPath} → {path}</em> : null}
                <span className="review-file-flags">
                  {file.binary ? <span title="二进制文件"><Binary size={12} />BIN</span> : null}
                  {file.submodule ? <span title="子模块"><GitCompareArrows size={12} />SUB</span> : null}
                  {file.truncated ? <span title="Diff 已截断"><AlertTriangle size={12} />部分</span> : null}
                </span>
              </span>
              <span className="review-file-counts">
                {summary.count ? <b className={summary.urgent ? 'urgent' : ''} title={`${summary.count} 个问题`}>{summary.count}</b> : null}
                {!file.binary ? <><i>+{file.additions}</i><i>-{file.deletions}</i></> : null}
              </span>
            </button>
          )
        })}
        {!files.length ? <p className="review-list-empty">没有匹配的文件</p> : null}
      </nav>
    </aside>
  )
}

function lineIsHighlighted(line: DiffLine, location: FindingLocation | null): boolean {
  if (!location || location.kind !== 'line') return false
  const number = location.side === 'old' ? line.old_line_number : line.new_line_number
  return number !== null && number >= location.startLine && number <= location.endLine
}

export function DiffViewer(props: {
  diff: ReviewDiff
  file: ReviewDiffFile | null
  location: FindingLocation | null
}) {
  if (!props.file) {
    return <main className="review-diff-panel"><div className="review-compact-empty">选择一个文件查看 Diff。</div></main>
  }
  const file = props.file
  const key = reviewFileKey(file)
  const path = relativeReviewPath(reviewFilePath(file), props.diff.repository_virtual_path)
  const oldPath = file.old_path ? relativeReviewPath(file.old_path, props.diff.repository_virtual_path) : null
  const newPath = file.new_path ? relativeReviewPath(file.new_path, props.diff.repository_virtual_path) : null

  return (
    <main className="review-diff-panel" aria-label="Diff 查看器">
      <header className="review-diff-header" id={reviewFileDomId(key)}>
        <div><FileCode2 size={17} /><strong>{path}</strong></div>
        <span><i>+{file.additions}</i><i>-{file.deletions}</i></span>
        {oldPath && newPath && oldPath !== newPath ? <small>{oldPath} → {newPath}</small> : null}
      </header>
      {file.truncated ? <div className="review-inline-warning"><AlertTriangle size={15} />该文件只展示本次审查实际覆盖的部分 Diff。</div> : null}
      {file.binary ? (
        <div className="review-file-message"><Binary size={24} /><strong>二进制文件不展示内容</strong><span>Reviewer 仅收到文件级元数据。</span></div>
      ) : !file.hunks.length ? (
        <div className="review-file-message"><FileCode2 size={24} /><strong>{file.submodule ? '子模块引用变化' : '没有可展示的文本行'}</strong></div>
      ) : (
        <div className="review-diff-scroll">
          <div className="review-diff-content">
            {file.hunks.map((hunk) => (
              <section className="review-hunk" key={`${hunk.header}-${hunk.old_start}-${hunk.new_start}`}>
                <div className="review-hunk-header">{hunk.header}</div>
                {hunk.lines.map((line, index) => {
                  if (line.type === 'no_newline') {
                    return <div className="review-no-newline" key={`${hunk.header}-no-newline-${index}`}>\ {line.content}</div>
                  }
                  const highlighted = lineIsHighlighted(line, props.location)
                  return (
                    <div className={`review-diff-line line-${line.type} ${highlighted ? 'finding-highlight' : ''}`} key={`${hunk.header}-${line.old_line_number ?? 'x'}-${line.new_line_number ?? 'x'}-${index}`}>
                      <span className="review-line-number" id={line.old_line_number === null ? undefined : diffLineDomId(key, 'old', line.old_line_number)}>{line.old_line_number ?? ''}</span>
                      <span className="review-line-number" id={line.new_line_number === null ? undefined : diffLineDomId(key, 'new', line.new_line_number)}>{line.new_line_number ?? ''}</span>
                      <span className="review-line-marker">{line.type === 'addition' ? '+' : line.type === 'deletion' ? '-' : ' '}</span>
                      <code>{line.content || ' '}</code>
                    </div>
                  )
                })}
              </section>
            ))}
          </div>
        </div>
      )}
    </main>
  )
}

function FindingPanel(props: {
  findings: ReviewFinding[]
  diff: ReviewDiff
  selectedId: string | null
  busyId: string | null
  onLocate: (finding: ReviewFinding) => void
  onStatus: (finding: ReviewFinding, status: ReviewFindingStatus) => void
  publicationMode: boolean
  selectedPublicationIds: ReadonlySet<string>
  publicationLocked: boolean
  onTogglePublication: (finding: ReviewFinding) => void
}) {
  const [filters, setFilters] = useState<FindingFilters>({
    severity: 'all',
    category: 'all',
    status: 'all',
  })
  const visible = useMemo(
    () => filterAndSortFindings(props.findings, filters),
    [filters, props.findings],
  )

  return (
    <aside className="review-finding-panel" aria-label="审查问题">
      <div className="review-panel-heading"><strong>审查问题</strong><span>{visible.length}/{props.findings.length}</span></div>
      <div className="review-filters">
        <select value={filters.severity} aria-label="按严重级别筛选" onChange={(event) => setFilters((current) => ({ ...current, severity: event.target.value as ReviewSeverity | 'all' }))}>
          <option value="all">全部级别</option>
          {(Object.keys(severityLabels) as ReviewSeverity[]).map((severity) => <option value={severity} key={severity}>{severityLabels[severity]}</option>)}
        </select>
        <select value={filters.category} aria-label="按类别筛选" onChange={(event) => setFilters((current) => ({ ...current, category: event.target.value as ReviewCategory | 'all' }))}>
          <option value="all">全部类别</option>
          {(Object.keys(categoryLabels) as ReviewCategory[]).map((category) => <option value={category} key={category}>{categoryLabels[category]}</option>)}
        </select>
        <select value={filters.status} aria-label="按状态筛选" onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value as ReviewFindingStatus | 'all' }))}>
          <option value="all">全部状态</option>
          {(Object.keys(findingStatusLabels) as ReviewFindingStatus[]).map((status) => <option value={status} key={status}>{findingStatusLabels[status]}</option>)}
        </select>
      </div>
      <div className="review-finding-list">
        {visible.map((finding) => {
          const path = finding.file_path ? relativeReviewPath(finding.file_path, props.diff.repository_virtual_path) : '全局问题'
          const line = finding.start_line === null ? '' : `:${finding.start_line}${finding.end_line !== finding.start_line ? `-${finding.end_line}` : ''}`
          const publicationTarget = findingPublicationTarget(finding, props.diff.files)
          const publicationDisabled = (
            props.publicationLocked
            || finding.status !== 'open'
            || publicationTarget === 'invalid'
          )
          return (
            <article className={`review-finding-item ${props.selectedId === finding.id ? 'active' : ''}`} key={finding.id}>
              <button className="review-finding-main" type="button" onClick={() => props.onLocate(finding)}>
                <span className={`severity-badge severity-${finding.severity}`}><AlertTriangle size={12} />{severityLabels[finding.severity]}</span>
                <span className="category-badge">{categoryLabels[finding.category]}</span>
                <strong>{finding.title}</strong>
                <code title={`${path}${line}`}>{path}{line}</code>
                <p>{finding.description}</p>
                {finding.suggestion ? <small><b>建议</b>{finding.suggestion}</small> : null}
              </button>
              <footer>
                {props.publicationMode ? (
                  <label className={`finding-publish-select ${publicationDisabled ? 'disabled' : ''}`} title={publicationTarget === 'invalid' ? '无法安全映射到本次 PR Diff' : publicationTarget === 'summary' ? '将放入 Review 总结' : '将发布为行内评论'}>
                    <input
                      type="checkbox"
                      checked={props.selectedPublicationIds.has(finding.id)}
                      disabled={publicationDisabled}
                      onChange={() => props.onTogglePublication(finding)}
                    />
                    <span>{publicationTarget === 'summary' ? '总结' : publicationTarget === 'invalid' ? '不可发布' : '发布'}</span>
                  </label>
                ) : null}
                <button type="button" className="finding-locate" title="定位到 Diff" aria-label={`定位问题：${finding.title}`} onClick={() => props.onLocate(finding)} disabled={finding.file_path === null}><LocateFixed size={15} /></button>
                <select value={finding.status} aria-label={`更新问题状态：${finding.title}`} disabled={props.busyId === finding.id} onChange={(event) => props.onStatus(finding, event.target.value as ReviewFindingStatus)}>
                  {(Object.keys(findingStatusLabels) as ReviewFindingStatus[]).map((status) => <option value={status} key={status}>{findingStatusLabels[status]}</option>)}
                </select>
                {props.busyId === finding.id ? <LoaderCircle className="review-spin" size={14} /> : finding.status === 'resolved' ? <CheckCircle2 size={14} /> : null}
              </footer>
            </article>
          )
        })}
        {!visible.length ? <p className="review-list-empty">当前筛选条件下没有问题。</p> : null}
      </div>
    </aside>
  )
}

export function GitHubPublicationBar(props: {
  snapshot: ReviewSnapshot
  capability: GitHubReviewCapability | null
  pullRequest: GitHubPullRequest | null
  selectedCount: number
  event: GitHubReviewEvent
  summary: string
  preparing: boolean
  publication: GitHubReviewPublication | null
  error: string
  onEvent: (event: GitHubReviewEvent) => void
  onSummary: (summary: string) => void
  onPrepare: () => void
}) {
  const isPullRequest = props.snapshot.diff.source === 'pull_request'
  const locked = props.publication?.status === 'published' || props.publication?.status === 'unknown'
  const unavailableReason = props.capability?.reason
    ? capabilityReasonLabels[props.capability.reason] ?? props.capability.reason
    : null
  const canPrepare = Boolean(
    isPullRequest
    && props.capability?.can_publish
    && !props.pullRequest?.is_draft
    && !locked
    && !props.preparing
    && (props.selectedCount > 0 || props.summary.trim()),
  )

  return (
    <section className={`github-publication-bar ${isPullRequest ? '' : 'disabled'}`} aria-label="GitHub Review 发布">
      <div className="github-publication-identity">
        <span className="github-publication-icon"><GitPullRequest size={18} /></span>
        <div>
          <strong>{isPullRequest ? `${props.snapshot.diff.repository} · PR #${props.snapshot.diff.pr_number}` : 'GitHub Review 发布'}</strong>
          <span>{isPullRequest ? props.pullRequest?.title ?? '已保存 Pull Request 快照' : '本地未提交 Diff 不能映射到 GitHub PR 行号'}</span>
        </div>
      </div>
      {isPullRequest ? (
        <>
          <label className="github-event-select">
            <span>Review 类型</span>
            <select value={props.event} onChange={(event) => props.onEvent(event.target.value as GitHubReviewEvent)} disabled={locked || props.preparing}>
              {(Object.keys(githubEventLabels) as GitHubReviewEvent[]).map((event) => <option value={event} key={event}>{githubEventLabels[event]}</option>)}
            </select>
          </label>
          <label className="github-summary-input">
            <span>总结（可选）</span>
            <input value={props.summary} maxLength={20_000} disabled={locked || props.preparing} onChange={(event) => props.onSummary(event.target.value)} placeholder="补充给 PR 作者的简短说明" />
          </label>
          <div className="github-publication-action">
            <span>{props.selectedCount} 个 Finding</span>
            <button type="button" disabled={!canPrepare} onClick={props.onPrepare} title={!canPrepare && unavailableReason ? unavailableReason : '先生成服务器端发布预览'}>
              {props.preparing ? <LoaderCircle className="review-spin" size={16} /> : <ShieldAlert size={16} />}
              {props.preparing ? '正在校验' : '预览发布'}
            </button>
          </div>
        </>
      ) : (
        <div className="github-working-tree-message">
          <span>请先提交、推送并创建 PR，再对 PR 快照重新审查。</span>
          <button type="button" disabled><Send size={16} />预览发布</button>
        </div>
      )}
      {props.event !== 'COMMENT' && isPullRequest ? <p className="github-event-impact">{props.event === 'APPROVE' ? '批准会记录同意合并的 Review。' : '请求修改会阻止满足分支保护的合并。'}</p> : null}
      {unavailableReason && isPullRequest ? <p className="github-publication-warning"><AlertTriangle size={14} />{unavailableReason}</p> : null}
      {props.error ? <p className="github-publication-error" role="alert">{props.error}</p> : null}
      {props.publication?.status === 'published' ? (
        <p className="github-publication-success"><CheckCircle2 size={15} />已由 {props.publication.github_user ?? '当前用户'} 发布
          {props.publication.github_review_url ? <a href={props.publication.github_review_url} target="_blank" rel="noreferrer">查看 GitHub Review <ExternalLink size={13} /></a> : null}
        </p>
      ) : null}
      {props.publication?.status === 'unknown' ? <p className="github-publication-unknown"><AlertTriangle size={15} />发布结果不确定，已禁止重试；请人工检查 PR。</p> : null}
    </section>
  )
}

export function GitHubPublishDialog(props: {
  preview: GitHubReviewPreview
  publishing: boolean
  error: string
  onCancel: () => void
  onPublish: () => void
}) {
  return (
    <div className="github-publish-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !props.publishing) props.onCancel() }}>
      <section
        className="github-publish-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="github-publish-title"
        onKeyDown={(event) => { if (shouldSuppressPublishKey(event.key)) event.preventDefault() }}
      >
        <header>
          <div><GitPullRequest size={20} /><div><h2 id="github-publish-title">发布 GitHub Review</h2><p>这会对 GitHub 产生真实外部写入</p></div></div>
          <button type="button" aria-label="关闭发布预览" title="关闭" disabled={props.publishing} onClick={props.onCancel}><X size={18} /></button>
        </header>
        <dl className="github-preview-facts">
          <div><dt>仓库</dt><dd>{props.preview.repository}</dd></div>
          <div><dt>Pull Request</dt><dd><a href={props.preview.pr_url} target="_blank" rel="noreferrer"><span>#{props.preview.pr_number} {props.preview.pr_title}</span><ExternalLink size={13} /></a></dd></div>
          <div><dt>Review 类型</dt><dd>{githubEventLabels[props.preview.event]}</dd></div>
          <div><dt>Head SHA</dt><dd><code>{props.preview.head_sha.slice(0, 8)}</code></dd></div>
          <div><dt>行内评论</dt><dd>{props.preview.inline_comments.length}</dd></div>
          <div><dt>总结问题</dt><dd>{props.preview.summary_comments.length}</dd></div>
        </dl>
        {props.preview.warnings.length ? <div className="github-preview-warnings">{props.preview.warnings.map((warning) => <p key={warning}><AlertTriangle size={14} />{warning}</p>)}</div> : null}
        {props.preview.skipped_findings.length ? (
          <div className="github-preview-skipped"><strong>不会发布</strong>{props.preview.skipped_findings.map((item) => <p key={item.finding_id}><span>{item.title}</span><small>{item.reason}</small></p>)}</div>
        ) : null}
        <div className="github-preview-summary"><strong>Review 总结</strong><p>{props.preview.summary_body}</p></div>
        {props.error ? <div className="github-dialog-error" role="alert"><AlertTriangle size={15} />{props.error}</div> : null}
        <footer>
          <button className="github-dialog-cancel" type="button" disabled={props.publishing} onClick={props.onCancel}>取消</button>
          <button className="github-dialog-publish" type="button" disabled={props.publishing} onClick={props.onPublish}>
            {props.publishing ? <LoaderCircle className="review-spin" size={16} /> : <Send size={16} />}
            {props.publishing ? '正在发布' : '发布到 GitHub'}
          </button>
        </footer>
      </section>
    </div>
  )
}

export function ReviewWorkspace(props: ReviewWorkspaceProps) {
  const [activeRunId, setActiveRunId] = useState(props.runId)
  const [scope, setScope] = useState<ReviewScope>('all')
  const [reviewSource, setReviewSource] = useState<ReviewSource>('working_tree')
  const [capability, setCapability] = useState<GitHubReviewCapability | null>(null)
  const [capabilityLoading, setCapabilityLoading] = useState(true)
  const [selectedPrNumber, setSelectedPrNumber] = useState<number | null>(null)
  const [snapshot, setSnapshot] = useState<ReviewSnapshot | null>(null)
  const [findings, setFindings] = useState<ReviewFinding[]>([])
  const [selectedFileKey, setSelectedFileKey] = useState<string | null>(null)
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null)
  const [location, setLocation] = useState<FindingLocation | null>(null)
  const [loading, setLoading] = useState(Boolean(props.runId))
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busyFindingId, setBusyFindingId] = useState<string | null>(null)
  const [filesCollapsed, setFilesCollapsed] = useState(false)
  const [findingsCollapsed, setFindingsCollapsed] = useState(false)
  const [mobileTab, setMobileTab] = useState<MobileTab>('diff')
  const [selectedPublicationIds, setSelectedPublicationIds] = useState<Set<string>>(new Set())
  const [githubEvent, setGithubEvent] = useState<GitHubReviewEvent>('COMMENT')
  const [githubSummary, setGithubSummary] = useState('')
  const [preparingPublication, setPreparingPublication] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [publicationPreview, setPublicationPreview] = useState<GitHubReviewPreview | null>(null)
  const [publication, setPublication] = useState<GitHubReviewPublication | null>(null)
  const [publicationError, setPublicationError] = useState('')
  const requestRef = useRef(0)

  useEffect(() => {
    let cancelled = false
    setCapabilityLoading(true)
    getGitHubReviewCapability(props.project.project_id)
      .then((loaded) => {
        if (cancelled) return
        setCapability(loaded)
        setSelectedPrNumber((current) => (
          loaded.pull_requests.some((item) => item.pr_number === current)
            ? current
            : loaded.pull_requests[0]?.pr_number ?? null
        ))
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setCapability(null)
          setPublicationError(reviewErrorMessage(loadError))
        }
      })
      .finally(() => { if (!cancelled) setCapabilityLoading(false) })
    return () => { cancelled = true }
  }, [props.project.project_id])

  const loadSnapshot = useCallback(async (runId: string) => {
    const requestId = ++requestRef.current
    setLoading(true)
    setError('')
    try {
      const loaded = await getReviewSnapshot(runId)
      if (requestId !== requestRef.current) return
      setSnapshot(loaded)
      setFindings(loaded.findings)
      setScope(loaded.scope)
      setReviewSource(loaded.diff.source)
      setSelectedPrNumber(loaded.diff.pr_number)
      setSelectedPublicationIds(
        defaultPublicationFindingIds(loaded.findings, loaded.diff.files),
      )
      setSelectedFileKey((current) => (
        loaded.diff.files.some((file) => reviewFileKey(file) === current)
          ? current
          : loaded.diff.files[0] ? reviewFileKey(loaded.diff.files[0]) : null
      ))
    } catch (loadError) {
      if (requestId !== requestRef.current) return
      if (loadError instanceof ApiError && loadError.status === 404 && loadError.message === 'review snapshot not found') {
        setSnapshot(null)
        setFindings([])
      } else {
        setError(reviewErrorMessage(loadError))
      }
    } finally {
      if (requestId === requestRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    setActiveRunId(props.runId)
    setSnapshot(null)
    setFindings([])
    setLocation(null)
    setSelectedFindingId(null)
    setPublicationPreview(null)
    setPublication(null)
    setPublicationError('')
    if (props.runId) void loadSnapshot(props.runId)
    else setLoading(false)
    return () => { requestRef.current += 1 }
  }, [loadSnapshot, props.runId])

  useEffect(() => {
    if (!activeRunId || snapshot?.diff.source !== 'pull_request') return
    let cancelled = false
    listGitHubReviewPublications(activeRunId)
      .then((items) => {
        if (!cancelled) setPublication(items[0] ?? null)
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [activeRunId, snapshot?.diff.source])

  useEffect(() => {
    if (!location || location.kind === 'invalid' || location.kind === 'global') return
    const targetId = location.kind === 'file'
      ? reviewFileDomId(location.fileKey)
      : diffLineDomId(location.fileKey, location.side, location.startLine)
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(targetId)
      if (target) target.scrollIntoView({ block: 'center', behavior: 'smooth' })
      else setNotice('该问题无法在当前审查快照中定位。')
    })
    return () => cancelAnimationFrame(frame)
  }, [location, selectedFileKey])

  const selectedFile = useMemo(() => (
    snapshot?.diff.files.find((file) => reviewFileKey(file) === selectedFileKey) ?? null
  ), [selectedFileKey, snapshot?.diff.files])
  const selectedPullRequest = useMemo(() => (
    capability?.pull_requests.find((item) => (
      item.pr_number === (snapshot?.diff.pr_number ?? selectedPrNumber)
    )) ?? null
  ), [capability?.pull_requests, selectedPrNumber, snapshot?.diff.pr_number])
  const publicationLocked = (
    publication?.status === 'published'
    || publication?.status === 'unknown'
    || preparingPublication
    || publishing
  )

  async function startReview(forceNewRun: boolean) {
    if (starting) return
    setStarting(true)
    setError('')
    setNotice('')
    const existingRunId = forceNewRun || snapshot ? null : activeRunId
    try {
      const prNumber = reviewSource === 'pull_request' ? selectedPrNumber : null
      const result = await props.onStartReview(
        scope,
        existingRunId,
        reviewSource,
        prNumber,
      )
      setActiveRunId(result.run_id)
      props.onRunChanged(result.run_id)
      await loadSnapshot(result.run_id)
    } catch (startError) {
      const retainedRunId = startError instanceof ApiError ? startError.runId : null
      if (retainedRunId) {
        setActiveRunId(retainedRunId)
        props.onRunChanged(retainedRunId)
        await loadSnapshot(retainedRunId)
        setNotice(reviewErrorMessage(startError))
      } else {
        setError(reviewErrorMessage(startError))
      }
    } finally {
      setStarting(false)
    }
  }

  function selectFile(file: ReviewDiffFile) {
    setSelectedFileKey(reviewFileKey(file))
    setSelectedFindingId(null)
    setLocation(null)
    setMobileTab('diff')
  }

  function locateFinding(finding: ReviewFinding) {
    if (!snapshot) return
    const nextLocation = resolveFindingLocation(finding, snapshot.diff.files)
    setSelectedFindingId(finding.id)
    if (nextLocation.kind === 'invalid' || nextLocation.kind === 'global') {
      setNotice(nextLocation.kind === 'global' ? '这是全局问题，没有对应代码行。' : '该问题无法在当前审查快照中定位。')
      setLocation(null)
      return
    }
    setNotice('')
    setSelectedFileKey(nextLocation.fileKey)
    setLocation(nextLocation)
    setMobileTab('diff')
  }

  async function changeFindingStatus(finding: ReviewFinding, status: ReviewFindingStatus) {
    if (!activeRunId || busyFindingId || finding.status === status) return
    setBusyFindingId(finding.id)
    setNotice('')
    try {
      await updateFindingStatusOptimistically({
        findingId: finding.id,
        status,
        setFindings,
        request: () => updateReviewFindingStatus(activeRunId, finding.id, status),
      })
      if (status !== 'open') {
        setSelectedPublicationIds((current) => {
          const next = new Set(current)
          next.delete(finding.id)
          return next
        })
      }
      setNotice(`问题状态已更新为“${findingStatusLabels[status]}”。`)
    } catch (updateError) {
      setNotice(`状态更新失败，已恢复原状态：${reviewErrorMessage(updateError)}`)
    } finally {
      setBusyFindingId(null)
    }
  }

  function toggleFindingPublication(finding: ReviewFinding) {
    if (!snapshot || finding.status !== 'open') return
    if (findingPublicationTarget(finding, snapshot.diff.files) === 'invalid') return
    setSelectedPublicationIds((current) => {
      const next = new Set(current)
      if (next.has(finding.id)) next.delete(finding.id)
      else next.add(finding.id)
      return next
    })
  }

  async function preparePublication() {
    if (!activeRunId || !snapshot?.diff.pr_number || preparingPublication) return
    setPreparingPublication(true)
    setPublicationError('')
    try {
      const preview = await prepareGitHubReview(activeRunId, {
        pr_number: snapshot.diff.pr_number,
        selected_finding_ids: [...selectedPublicationIds],
        event: githubEvent,
        summary: githubSummary.trim() || null,
      })
      setPublicationPreview(preview)
    } catch (prepareError) {
      setPublicationError(reviewErrorMessage(prepareError))
    } finally {
      setPreparingPublication(false)
    }
  }

  async function confirmPublication() {
    if (!activeRunId || !publicationPreview || publishing) return
    setPublishing(true)
    setPublicationError('')
    try {
      const published = await publishGitHubReview(
        activeRunId,
        publicationPreview.publication_id,
      )
      setPublication(published)
      setPublicationPreview(null)
      setNotice('GitHub Review 已发布。')
    } catch (publishError) {
      const message = reviewErrorMessage(publishError)
      setPublicationError(message)
      try {
        const items = await listGitHubReviewPublications(activeRunId)
        setPublication(items[0] ?? null)
        if (items[0]?.status === 'unknown') setPublicationPreview(null)
      } catch {
        // 保留原始发布错误，避免用审计记录加载失败覆盖它。
      }
    } finally {
      setPublishing(false)
    }
  }

  const workspaceClasses = [
    'review-workspace',
    filesCollapsed ? 'files-collapsed' : '',
    findingsCollapsed ? 'findings-collapsed' : '',
    `mobile-tab-${mobileTab}`,
  ].filter(Boolean).join(' ')

  return (
    <main className={workspaceClasses}>
      <ReviewToolbar
        project={props.project}
        snapshot={snapshot}
        scope={scope}
        starting={starting}
        filesCollapsed={filesCollapsed}
        findingsCollapsed={findingsCollapsed}
        onBack={props.onBack}
        onReview={() => void startReview(true)}
        onToggleFiles={() => setFilesCollapsed((value) => !value)}
        onToggleFindings={() => setFindingsCollapsed((value) => !value)}
      />

      <div className="review-mobile-tabs" role="tablist" aria-label="代码审查视图">
        <button type="button" role="tab" aria-selected={mobileTab === 'files'} className={mobileTab === 'files' ? 'active' : ''} onClick={() => setMobileTab('files')}>文件</button>
        <button type="button" role="tab" aria-selected={mobileTab === 'diff'} className={mobileTab === 'diff' ? 'active' : ''} onClick={() => setMobileTab('diff')}>Diff</button>
        <button type="button" role="tab" aria-selected={mobileTab === 'findings'} className={mobileTab === 'findings' ? 'active' : ''} onClick={() => setMobileTab('findings')}>问题{findings.length ? ` ${findings.length}` : ''}</button>
      </div>

      {notice ? <div className="review-toast" role="status">{notice}<button type="button" aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div> : null}
      {error ? <div className="review-error" role="alert"><AlertTriangle size={17} /><span>{error}</span><button type="button" onClick={() => activeRunId ? void loadSnapshot(activeRunId) : setError('')}>重试</button></div> : null}

      {loading ? (
        <div className="review-loading"><LoaderCircle className="review-spin" size={22} /><span>正在加载审查快照…</span></div>
      ) : !snapshot ? (
        <section className="review-start-state">
          <div className="review-start-heading"><GitCompareArrows size={24} /><div><h2>代码审查</h2><p>选择审查来源并保存 Reviewer 实际看到的受控 Diff 快照。</p></div></div>
          <div className="review-source-control" role="group" aria-label="审查来源">
            {(Object.keys(reviewSourceLabels) as ReviewSource[]).map((source) => (
              <button type="button" key={source} className={reviewSource === source ? 'active' : ''} aria-pressed={reviewSource === source} onClick={() => setReviewSource(source)}>
                {source === 'pull_request' ? <GitPullRequest size={16} /> : <GitCompareArrows size={16} />}
                {reviewSourceLabels[source]}
              </button>
            ))}
          </div>
          <dl>
            <div><dt>当前项目</dt><dd>{props.project.name}</dd></div>
            <div><dt>{reviewSource === 'pull_request' ? 'GitHub 仓库' : '本地变更'}</dt><dd>{reviewSource === 'pull_request' ? capability?.repository ?? '尚未识别' : props.hasLocalChanges === null ? '正在确认' : props.hasLocalChanges ? '存在本地变更' : '工作区干净'}</dd></div>
            <div><dt>关联会话</dt><dd>{props.threadId ? '已就绪' : '将自动创建审查会话'}</dd></div>
          </dl>
          {reviewSource === 'pull_request' ? (
            <div className="review-pr-picker">
              <label>
                <span>Pull Request</span>
                <select value={selectedPrNumber ?? ''} disabled={capabilityLoading || !capability?.pull_requests.length} onChange={(event) => setSelectedPrNumber(Number(event.target.value))}>
                  {!capability?.pull_requests.length ? <option value="">没有可用的 open PR</option> : null}
                  {capability?.pull_requests.map((pullRequest) => <option value={pullRequest.pr_number} key={pullRequest.pr_number}>#{pullRequest.pr_number} · {pullRequest.title}{pullRequest.is_draft ? '（Draft）' : ''}</option>)}
                </select>
              </label>
              {capabilityLoading ? <span><LoaderCircle className="review-spin" size={14} />正在查询 GitHub</span> : capability?.reason ? <span className="review-pr-warning"><AlertTriangle size={14} />{capabilityReasonLabels[capability.reason] ?? capability.reason}</span> : selectedPullRequest ? <span>{selectedPullRequest.base_branch} ← {selectedPullRequest.head_branch} · {selectedPullRequest.head_sha.slice(0, 8)}</span> : null}
            </div>
          ) : null}
          <div className="review-start-controls">
            {reviewSource === 'working_tree' ? <div className="review-scope-control" role="group" aria-label="审查范围">
              {(Object.keys(scopeLabels) as ReviewScope[]).map((value) => <button type="button" key={value} className={scope === value ? 'active' : ''} aria-pressed={scope === value} onClick={() => setScope(value)}>{scopeLabels[value]}</button>)}
            </div> : <p className="review-pr-note">PR Review 使用已验证的 base/head SHA；不会 checkout、merge 或修改本地文件。</p>}
            <button className="review-start-button" type="button" disabled={starting || (reviewSource === 'pull_request' && (!selectedPrNumber || capabilityLoading || !capability?.authenticated))} onClick={() => void startReview(false)}>
              {starting ? <LoaderCircle className="review-spin" size={17} /> : <Play size={17} />}
              {starting ? '正在审查…' : '开始审查'}
            </button>
          </div>
        </section>
      ) : snapshot.diff.file_count === 0 ? (
        <section className="review-no-changes">
          <CheckCircle2 size={24} /><div><h2>当前范围没有可审查的代码变更。</h2><p>{snapshot.summary}</p></div>
          <button type="button" onClick={() => void startReview(true)} disabled={starting}><RefreshCw size={16} />重新审查</button>
        </section>
      ) : (
        <>
          <div className="review-safety-banners">
            {snapshot.diff.truncated ? <div><AlertTriangle size={15} /><span>变更内容超过审查限制，本次结果仅覆盖已展示部分。</span></div> : null}
            {snapshot.diff.redacted ? <div><EyeOff size={15} /><span>部分疑似凭据已隐藏，审查内容不会显示原始值。</span></div> : null}
            {snapshot.status === 'failed' ? <div><AlertTriangle size={15} /><span>{snapshot.summary}</span></div> : null}
          </div>
          <GitHubPublicationBar
            snapshot={{ ...snapshot, findings }}
            capability={capability}
            pullRequest={selectedPullRequest}
            selectedCount={selectedPublicationIds.size}
            event={githubEvent}
            summary={githubSummary}
            preparing={preparingPublication}
            publication={publication}
            error={publicationError}
            onEvent={setGithubEvent}
            onSummary={setGithubSummary}
            onPrepare={() => void preparePublication()}
          />
          <div className="review-grid">
            <ReviewFileList diff={snapshot.diff} findings={findings} selectedKey={selectedFileKey} onSelect={selectFile} />
            <DiffViewer diff={snapshot.diff} file={selectedFile} location={location} />
            <FindingPanel findings={findings} diff={snapshot.diff} selectedId={selectedFindingId} busyId={busyFindingId} onLocate={locateFinding} onStatus={(finding, status) => void changeFindingStatus(finding, status)} publicationMode={snapshot.diff.source === 'pull_request'} selectedPublicationIds={selectedPublicationIds} publicationLocked={publicationLocked} onTogglePublication={toggleFindingPublication} />
          </div>
        </>
      )}
      {publicationPreview ? <GitHubPublishDialog preview={publicationPreview} publishing={publishing} error={publicationError} onCancel={() => { if (!publishing) { setPublicationPreview(null); setPublicationError('') } }} onPublish={() => void confirmPublication()} /> : null}
    </main>
  )
}
