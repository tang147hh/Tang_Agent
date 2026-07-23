import { describe, expect, it, vi } from 'vitest'
import type { ReviewDiffFile, ReviewFinding } from '../api'
import {
  aggregateFileFindings,
  defaultPublicationFindingIds,
  filterAndSortFindings,
  filterReviewFiles,
  findingPublicationTarget,
  resolveFindingLocation,
  reviewErrorMessage,
  updateFindingStatusOptimistically,
} from './reviewUtils'
import { ApiError } from '../api'

const files: ReviewDiffFile[] = [
  {
    old_path: '/projects/demo/src/old.ts',
    new_path: '/projects/demo/src/new.ts',
    change_type: 'renamed',
    binary: false,
    submodule: false,
    additions: 1,
    deletions: 1,
    truncated: false,
    truncation_reason: null,
    changed_new_lines: [8],
    changed_old_lines: [7],
    redacted: false,
    hunks: [{
      header: '@@ -7,2 +8,2 @@',
      old_start: 7,
      old_count: 2,
      new_start: 8,
      new_count: 2,
      lines: [
        { type: 'deletion', old_line_number: 7, new_line_number: null, content: 'old' },
        { type: 'addition', old_line_number: null, new_line_number: 8, content: 'new' },
        { type: 'context', old_line_number: 8, new_line_number: 9, content: 'same' },
      ],
    }],
  },
  {
    old_path: null,
    new_path: '/projects/demo/assets/logo.bin',
    change_type: 'added',
    binary: true,
    submodule: false,
    additions: 0,
    deletions: 0,
    truncated: false,
    truncation_reason: null,
    changed_new_lines: [],
    changed_old_lines: [],
    redacted: false,
    hunks: [],
  },
]

function finding(overrides: Partial<ReviewFinding> = {}): ReviewFinding {
  return {
    id: 'finding-1',
    run_id: 'run-1',
    severity: 'high',
    category: 'correctness',
    file_path: '/projects/demo/src/new.ts',
    start_line: 8,
    end_line: 8,
    line_side: 'new',
    title: 'Result is wrong',
    description: 'The changed value is not valid.',
    suggestion: 'Use the previous behavior.',
    status: 'open',
    fingerprint: 'fingerprint',
    review_diff_hash: 'hash',
    review_scope: 'all',
    base_revision: 'base',
    head_revision: null,
    created_at: '2026-07-23T00:00:00Z',
    updated_at: '2026-07-23T00:00:00Z',
    ...overrides,
  }
}

describe('review list transforms', () => {
  it('searches both old and new file paths', () => {
    expect(filterReviewFiles(files, 'old.ts')).toEqual([files[0]])
    expect(filterReviewFiles(files, 'LOGO')).toEqual([files[1]])
    expect(filterReviewFiles(files, 'missing')).toEqual([])
  })

  it('filters severity, category and status then sorts by severity', () => {
    const findings = [
      finding({ id: 'low', severity: 'low', category: 'testing' }),
      finding({ id: 'critical', severity: 'critical', category: 'security' }),
      finding({ id: 'resolved', severity: 'medium', status: 'resolved' }),
    ]
    expect(filterAndSortFindings(findings, { severity: 'all', category: 'all', status: 'all' }).map((item) => item.id)).toEqual(['critical', 'resolved', 'low'])
    expect(filterAndSortFindings(findings, { severity: 'critical', category: 'all', status: 'all' }).map((item) => item.id)).toEqual(['critical'])
    expect(filterAndSortFindings(findings, { severity: 'all', category: 'testing', status: 'all' }).map((item) => item.id)).toEqual(['low'])
    expect(filterAndSortFindings(findings, { severity: 'all', category: 'all', status: 'resolved' }).map((item) => item.id)).toEqual(['resolved'])
  })

  it('aggregates file findings and urgent counts', () => {
    const summaries = aggregateFileFindings(files, [
      finding(),
      finding({ id: 'old', file_path: '/projects/demo/src/old.ts', severity: 'critical' }),
      finding({ id: 'binary', file_path: '/projects/demo/assets/logo.bin', severity: 'low' }),
    ])
    expect([...summaries.values()]).toEqual([
      { count: 2, urgent: 2 },
      { count: 1, urgent: 0 },
    ])
  })
})

describe('finding locations', () => {
  it('locates old, new and file-level findings', () => {
    expect(resolveFindingLocation(finding(), files)).toMatchObject({ kind: 'line', side: 'new', startLine: 8 })
    expect(resolveFindingLocation(finding({ file_path: '/projects/demo/src/old.ts', start_line: 7, end_line: 7, line_side: 'old' }), files)).toMatchObject({ kind: 'line', side: 'old', startLine: 7 })
    expect(resolveFindingLocation(finding({ start_line: null, end_line: null, line_side: null }), files)).toMatchObject({ kind: 'file' })
  })

  it('classifies inline, summary and unsafe publication targets', () => {
    expect(findingPublicationTarget(finding(), files)).toBe('inline')
    expect(findingPublicationTarget(finding({ start_line: null, end_line: null, line_side: null }), files)).toBe('summary')
    expect(findingPublicationTarget(finding({ file_path: null, start_line: null, end_line: null, line_side: null }), files)).toBe('summary')
    expect(findingPublicationTarget(finding({ start_line: 999, end_line: 999 }), files)).toBe('invalid')
  })

  it('selects only open and safely publishable findings by default', () => {
    const selected = defaultPublicationFindingIds([
      finding({ id: 'open' }),
      finding({ id: 'resolved', status: 'resolved' }),
      finding({ id: 'dismissed', status: 'dismissed' }),
      finding({ id: 'invalid', start_line: 999, end_line: 999 }),
      finding({ id: 'summary', file_path: null, start_line: null, end_line: null, line_side: null }),
    ], files)
    expect([...selected]).toEqual(['open', 'summary'])
  })

  it('does not throw for missing lines, paths, global or binary locations', () => {
    expect(resolveFindingLocation(finding({ start_line: 999, end_line: 999 }), files)).toEqual({ kind: 'invalid' })
    expect(resolveFindingLocation(finding({ file_path: '/projects/demo/missing.ts' }), files)).toEqual({ kind: 'invalid' })
    expect(resolveFindingLocation(finding({ file_path: null, start_line: null, end_line: null, line_side: null }), files)).toEqual({ kind: 'global' })
    expect(resolveFindingLocation(finding({ file_path: '/projects/demo/assets/logo.bin', start_line: 1, end_line: 1 }), files)).toEqual({ kind: 'invalid' })
  })
})

describe('finding status updates', () => {
  it('keeps the server result after a successful optimistic update', async () => {
    let state = [finding()]
    const setFindings = (update: typeof state | ((current: typeof state) => typeof state)) => {
      state = typeof update === 'function' ? update(state) : update
    }
    await updateFindingStatusOptimistically({
      findingId: 'finding-1',
      status: 'resolved',
      setFindings,
      request: async () => finding({ status: 'resolved', updated_at: 'later' }),
    })
    expect(state[0].status).toBe('resolved')
    expect(state[0].updated_at).toBe('later')
  })

  it('rolls back after a failed optimistic update', async () => {
    let state = [finding()]
    const setFindings = (update: typeof state | ((current: typeof state) => typeof state)) => {
      state = typeof update === 'function' ? update(state) : update
    }
    await expect(updateFindingStatusOptimistically({
      findingId: 'finding-1',
      status: 'dismissed',
      setFindings,
      request: async () => { throw new Error('offline') },
    })).rejects.toThrow('offline')
    expect(state[0].status).toBe('open')
  })

  it('converts review API failures to useful Chinese messages', () => {
    expect(reviewErrorMessage(new ApiError('limit', { status: 429, code: 'budget_exceeded' }))).toContain('运行预算')
    expect(reviewErrorMessage(new ApiError('bad input', { status: 422 }))).toContain('校验失败')
    expect(reviewErrorMessage(new ApiError('changed', { status: 409, code: 'pull_request_changed' }))).toContain('重新审查')
    expect(reviewErrorMessage(new ApiError('unknown', { status: 409, code: 'publication_result_unknown' }))).toContain('不会自动重试')
    expect(vi.isMockFunction(vi.fn())).toBe(true)
  })
})
