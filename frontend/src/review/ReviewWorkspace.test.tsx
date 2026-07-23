import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type {
  GitHubReviewCapability,
  GitHubReviewPreview,
  ReviewDiff,
  ReviewDiffFile,
  ReviewSnapshot,
} from '../api'
import {
  DiffViewer,
  GitHubPublicationBar,
  GitHubPublishDialog,
} from './ReviewWorkspace'
import { shouldSuppressPublishKey } from './reviewUtils'

const diff: ReviewDiff = {
  scope: 'all',
  source: 'working_tree',
  repository: null,
  pr_number: null,
  repository_virtual_path: '/projects/demo',
  base_revision: null,
  head_revision: null,
  files: [],
  file_count: 1,
  total_additions: 1,
  total_deletions: 0,
  truncated: false,
  truncation_reasons: [],
  content_hash: 'hash',
  created_at: '2026-07-23T00:00:00Z',
  redacted: false,
}

function textFile(overrides: Partial<ReviewDiffFile> = {}): ReviewDiffFile {
  return {
    old_path: '/projects/demo/src/app.ts',
    new_path: '/projects/demo/src/app.ts',
    change_type: 'modified',
    binary: false,
    submodule: false,
    additions: 1,
    deletions: 0,
    truncated: false,
    truncation_reason: null,
    changed_new_lines: [2],
    changed_old_lines: [],
    redacted: false,
    hunks: [{
      header: '@@ -1,1 +1,2 @@',
      old_start: 1,
      old_count: 1,
      new_start: 1,
      new_count: 2,
      lines: [{
        type: 'addition',
        old_line_number: null,
        new_line_number: 2,
        content: '<img src=x onerror=alert(1)>',
      }],
    }],
    ...overrides,
  }
}

describe('DiffViewer', () => {
  it('renders diff content as escaped text and only the selected file', () => {
    const html = renderToStaticMarkup(<DiffViewer diff={diff} file={textFile()} location={null} />)
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
    expect(html).not.toContain('<img src=x')
    expect(html).toContain('>2</span>')
  })

  it('shows truncation and binary states without rendering hunks', () => {
    const truncated = renderToStaticMarkup(<DiffViewer diff={diff} file={textFile({ truncated: true })} location={null} />)
    const binary = renderToStaticMarkup(<DiffViewer diff={diff} file={textFile({ binary: true, hunks: [] })} location={null} />)
    expect(truncated).toContain('该文件只展示本次审查实际覆盖的部分 Diff')
    expect(binary).toContain('二进制文件不展示内容')
    expect(binary).not.toContain('onerror')
  })
})

const snapshot: ReviewSnapshot = {
  run_id: 'run-1',
  status: 'completed',
  scope: 'all',
  diff,
  findings: [],
  finding_count: 0,
  summary: 'done',
  created_at: '2026-07-23T00:00:00Z',
  updated_at: '2026-07-23T00:00:00Z',
}

const capability: GitHubReviewCapability = {
  gh_installed: true,
  authenticated: true,
  remote_found: true,
  publish_enabled: true,
  can_publish: true,
  reason: null,
  repository: 'acme/demo',
  current_user: 'reviewer',
  pull_requests: [],
}

const preview: GitHubReviewPreview = {
  publication_id: 'pub-1',
  repository: 'acme/demo',
  pr_number: 7,
  pr_title: 'Safe review',
  pr_url: 'https://github.com/acme/demo/pull/7',
  base_sha: 'a'.repeat(40),
  head_sha: 'b'.repeat(40),
  event: 'REQUEST_CHANGES',
  inline_comments: [{ finding_id: 'finding-1', path: 'src/app.ts', line: 8, side: 'RIGHT', body: 'issue' }],
  summary_comments: [{ finding_id: 'finding-2', title: 'global', reason: 'summary' }],
  summary_body: 'Review summary',
  skipped_findings: [{ finding_id: 'finding-3', title: 'resolved', reason: '仅发布 open 状态的问题' }],
  warnings: ['部分 Finding 不会发布。'],
  payload_hash: 'hash',
  expires_at: '2026-07-23T00:15:00Z',
}

describe('GitHub publication UI', () => {
  it('disables publication for working-tree snapshots', () => {
    const html = renderToStaticMarkup(
      <GitHubPublicationBar
        snapshot={snapshot}
        capability={capability}
        pullRequest={null}
        selectedCount={1}
        event="COMMENT"
        summary=""
        preparing={false}
        publication={null}
        error=""
        onEvent={() => undefined}
        onSummary={() => undefined}
        onPrepare={() => undefined}
      />,
    )
    expect(html).toContain('本地未提交 Diff 不能映射到 GitHub PR 行号')
    expect(html).toContain('disabled=""')
  })

  it('shows an explicit external-write confirmation with skipped findings', () => {
    const html = renderToStaticMarkup(
      <GitHubPublishDialog preview={preview} publishing={false} error="" onCancel={() => undefined} onPublish={() => undefined} />,
    )
    expect(html).toContain('这会对 GitHub 产生真实外部写入')
    expect(html).toContain('发布到 GitHub')
    expect(html).toContain('不会发布')
    expect(html).toContain('请求修改')
    expect(html).not.toContain('ghp_')
    expect(html).not.toContain('/Users/')
  })

  it('keeps publish disabled while a request is in progress', () => {
    const html = renderToStaticMarkup(
      <GitHubPublishDialog preview={preview} publishing error="" onCancel={() => undefined} onPublish={() => undefined} />,
    )
    expect(html).toContain('正在发布')
    expect((html.match(/disabled=""/g) ?? []).length).toBeGreaterThanOrEqual(2)
  })

  it('suppresses Enter but not other confirmation keys', () => {
    expect(shouldSuppressPublishKey('Enter')).toBe(true)
    expect(shouldSuppressPublishKey(' ')).toBe(false)
  })
})
