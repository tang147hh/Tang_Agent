import type { Dispatch, SetStateAction } from 'react'
import { ApiError } from '../api'
import type {
  ReviewCategory,
  ReviewDiffFile,
  ReviewFinding,
  ReviewFindingStatus,
  ReviewLineSide,
  ReviewSeverity,
} from '../api'

export const severityOrder: Record<ReviewSeverity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
}

export const severityLabels: Record<ReviewSeverity, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
}

export const categoryLabels: Record<ReviewCategory, string> = {
  correctness: '正确性',
  security: '安全',
  performance: '性能',
  maintainability: '可维护性',
  testing: '测试',
  documentation: '文档',
}

export const findingStatusLabels: Record<ReviewFindingStatus, string> = {
  open: '待处理',
  resolved: '已解决',
  dismissed: '已忽略',
}

export function reviewFileKey(file: ReviewDiffFile): string {
  return `${file.old_path ?? ''}\u0000${file.new_path ?? ''}`
}

export function reviewFilePath(file: ReviewDiffFile): string {
  return file.new_path ?? file.old_path ?? '未知文件'
}

export function relativeReviewPath(path: string, repositoryPath: string): string {
  const prefix = `${repositoryPath.replace(/\/$/, '')}/`
  return path.startsWith(prefix) ? path.slice(prefix.length) : path
}

export function filterReviewFiles(
  files: ReviewDiffFile[],
  query: string,
): ReviewDiffFile[] {
  const normalized = query.trim().toLocaleLowerCase()
  if (!normalized) return files
  return files.filter((file) => (
    `${file.old_path ?? ''} ${file.new_path ?? ''}`
      .toLocaleLowerCase()
      .includes(normalized)
  ))
}

export interface FindingFilters {
  severity: ReviewSeverity | 'all'
  category: ReviewCategory | 'all'
  status: ReviewFindingStatus | 'all'
}

export function filterAndSortFindings(
  findings: ReviewFinding[],
  filters: FindingFilters,
): ReviewFinding[] {
  return findings
    .filter((finding) => (
      (filters.severity === 'all' || finding.severity === filters.severity)
      && (filters.category === 'all' || finding.category === filters.category)
      && (filters.status === 'all' || finding.status === filters.status)
    ))
    .toSorted((left, right) => (
      severityOrder[left.severity] - severityOrder[right.severity]
      || (left.file_path ?? '').localeCompare(right.file_path ?? '')
      || (left.start_line ?? 0) - (right.start_line ?? 0)
      || left.id.localeCompare(right.id)
    ))
}

export interface FileFindingSummary {
  count: number
  urgent: number
}

export function aggregateFileFindings(
  files: ReviewDiffFile[],
  findings: ReviewFinding[],
): Map<string, FileFindingSummary> {
  const output = new Map<string, FileFindingSummary>()
  for (const file of files) {
    const paths = new Set([file.old_path, file.new_path].filter((path): path is string => Boolean(path)))
    const matching = findings.filter((finding) => (
      finding.file_path !== null && paths.has(finding.file_path)
    ))
    output.set(reviewFileKey(file), {
      count: matching.length,
      urgent: matching.filter((finding) => (
        finding.severity === 'critical' || finding.severity === 'high'
      )).length,
    })
  }
  return output
}

export type FindingLocation =
  | { kind: 'global' }
  | { kind: 'file'; fileKey: string }
  | {
      kind: 'line'
      fileKey: string
      side: ReviewLineSide
      startLine: number
      endLine: number
    }
  | { kind: 'invalid' }

export function resolveFindingLocation(
  finding: ReviewFinding,
  files: ReviewDiffFile[],
): FindingLocation {
  if (finding.file_path === null) return { kind: 'global' }
  const file = files.find((candidate) => (
    candidate.old_path === finding.file_path
    || candidate.new_path === finding.file_path
  ))
  if (!file) return { kind: 'invalid' }
  const fileKey = reviewFileKey(file)
  if (
    finding.start_line === null
    || finding.end_line === null
    || finding.line_side === null
  ) {
    return { kind: 'file', fileKey }
  }
  if (file.binary) return { kind: 'invalid' }

  const visibleLines = new Set<number>()
  for (const hunk of file.hunks) {
    for (const line of hunk.lines) {
      const lineNumber = finding.line_side === 'old'
        ? line.old_line_number
        : line.new_line_number
      if (lineNumber !== null) visibleLines.add(lineNumber)
    }
  }
  for (let line = finding.start_line; line <= finding.end_line; line += 1) {
    if (!visibleLines.has(line)) return { kind: 'invalid' }
  }
  return {
    kind: 'line',
    fileKey,
    side: finding.line_side,
    startLine: finding.start_line,
    endLine: finding.end_line,
  }
}

export type FindingPublicationTarget = 'inline' | 'summary' | 'invalid'

export function findingPublicationTarget(
  finding: ReviewFinding,
  files: ReviewDiffFile[],
): FindingPublicationTarget {
  const location = resolveFindingLocation(finding, files)
  if (location.kind === 'line') return 'inline'
  if (location.kind === 'global') return 'summary'
  if (location.kind === 'file') {
    const file = files.find((item) => reviewFileKey(item) === location.fileKey)
    return file ? 'summary' : 'invalid'
  }
  return 'invalid'
}

export function defaultPublicationFindingIds(
  findings: ReviewFinding[],
  files: ReviewDiffFile[],
): Set<string> {
  return new Set(
    findings
      .filter((finding) => (
        finding.status === 'open'
        && findingPublicationTarget(finding, files) !== 'invalid'
      ))
      .map((finding) => finding.id),
  )
}

export function shouldSuppressPublishKey(key: string): boolean {
  return key === 'Enter'
}

export function diffLineDomId(
  fileKey: string,
  side: ReviewLineSide,
  lineNumber: number,
): string {
  return `review-line-${encodeURIComponent(fileKey)}-${side}-${lineNumber}`
}

export function reviewFileDomId(fileKey: string): string {
  return `review-file-${encodeURIComponent(fileKey)}`
}

export async function updateFindingStatusOptimistically(
  options: {
    findingId: string
    status: ReviewFindingStatus
    setFindings: Dispatch<SetStateAction<ReviewFinding[]>>
    request: () => Promise<ReviewFinding>
  },
): Promise<void> {
  let previous: ReviewFinding | null = null
  options.setFindings((current) => current.map((finding) => {
    if (finding.id !== options.findingId) return finding
    previous = finding
    return { ...finding, status: options.status }
  }))

  try {
    const saved = await options.request()
    options.setFindings((current) => current.map((finding) => (
      finding.id === saved.id ? saved : finding
    )))
  } catch (error) {
    if (previous !== null) {
      const rollback = previous
      options.setFindings((current) => current.map((finding) => (
        finding.id === options.findingId ? rollback : finding
      )))
    }
    throw error
  }
}

const reviewErrorLabels: Record<string, string> = {
  run_not_found: '找不到对应的 Run。',
  project_not_found: '找不到对应的项目。',
  repository_not_found: '当前项目不是可审查的 Git 仓库。',
  repository_outside_workspace: '项目仓库不在允许的工作区内。',
  git_command_timeout: '读取 Git 变更超时，请稍后重试。',
  run_time_limit: '本次审查达到运行预算，已保留完成的审查结果。',
  budget_exceeded: '本次审查达到运行预算，已保留完成的审查结果。',
  reviewer_unavailable: 'Reviewer 当前不可用，请检查后端模型配置。',
  reviewer_output_invalid: 'Reviewer 返回的结果未通过安全校验。',
  review_already_exists: '当前 Run 已有审查结果，请使用重新审查创建新 Run。',
  gh_not_installed: '宿主机未安装 GitHub CLI，暂不能查询或发布 Review。',
  github_not_authenticated: 'GitHub CLI 尚未登录或认证已失效。',
  github_remote_not_found: '当前项目没有可验证的 GitHub origin。',
  unsupported_github_host: '当前版本只支持 github.com，不支持 GitHub Enterprise。',
  pull_request_not_found: '找不到当前仓库中的 Pull Request。',
  pull_request_closed: 'Pull Request 已关闭，不能发布 Review。',
  pull_request_changed: 'Pull Request 已发生变化，请重新审查最新提交后再发布。',
  pull_request_draft: 'Draft Pull Request 暂不允许发布 Review。',
  permission_denied: '当前 GitHub 账号没有发布 Review 的权限。',
  review_not_publishable: '当前 Review 不能发布到 GitHub。',
  finding_not_publishable: '部分 Finding 不属于已审查的 PR Diff。',
  publication_expired: '发布预览已过期，请重新预览。',
  publication_changed: 'Finding 或发布内容已变化，请重新预览。',
  publication_already_published: '相同 Review 已经发布，不能重复发布。',
  publication_in_progress: '当前 Review 正在发布，请勿重复提交。',
  publication_result_unknown: 'GitHub 返回结果不确定，请人工检查 PR；系统不会自动重试。',
  github_timeout: 'GitHub 请求超时，请稍后重试。',
  github_api_error: 'GitHub API 请求失败，请稍后重试。',
  publishing_disabled: 'GitHub Review 发布功能未启用。',
}

export function reviewErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code && reviewErrorLabels[error.code]) return reviewErrorLabels[error.code]
    if (error.status === 404 && error.message === 'review snapshot not found') {
      return '尚未发起代码审查。'
    }
    if (error.status === 422) return `审查请求校验失败：${error.message}`
    return error.message
  }
  if (error instanceof Error) return error.message
  return '网络请求失败，请检查后端服务后重试。'
}
