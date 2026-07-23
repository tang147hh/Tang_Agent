import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createCodeReviewRun,
  getGitHubReviewCapability,
  getToolCapabilities,
  prepareGitHubReview,
  publishGitHubReview,
  startCodeReview,
  startRun,
} from './api'

const responseBody = {
  run_id: 'run-1',
  status: 'completed',
  scope: 'all',
  diff: {
    scope: 'all',
    source: 'working_tree',
    repository: null,
    pr_number: null,
    repository_virtual_path: '/projects/demo',
    base_revision: null,
    head_revision: null,
    files: [],
    file_count: 0,
    total_additions: 0,
    total_deletions: 0,
    truncated: false,
    truncation_reasons: [],
    content_hash: 'hash',
    created_at: '2026-07-23T00:00:00Z',
    redacted: false,
  },
  findings: [],
  finding_count: 0,
  created_count: 0,
  duplicate_count: 0,
  summary: 'none',
}

afterEach(() => vi.unstubAllGlobals())

describe('review API', () => {
  it('passes the selected scope when reviewing an existing run', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await startCodeReview('run/1', 'staged')
    expect(fetchMock).toHaveBeenCalledWith('/api/runs/run%2F1/reviews', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ scope: 'staged', source: 'working_tree' }),
    }))
  })

  it('creates a new review run with the selected scope', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await createCodeReviewRun('thread 1', 'unstaged')
    expect(fetchMock).toHaveBeenCalledWith('/api/threads/thread%201/review-runs', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ scope: 'unstaged', source: 'working_tree' }),
    }))
  })

  it('starts a PR review with only source and PR number', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await startCodeReview('run-1', 'all', 'pull_request', 7)
    expect(fetchMock).toHaveBeenCalledWith('/api/runs/run-1/reviews', expect.objectContaining({
      body: JSON.stringify({ scope: 'all', source: 'pull_request', pr_number: 7 }),
    }))
    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit | undefined]>
    expect(String(calls[0][1]?.body)).not.toContain('owner')
  })

  it('prepare sends finding ids and controlled options only', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ publication_id: 'pub-1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await prepareGitHubReview('run-1', {
      pr_number: 7,
      selected_finding_ids: ['finding-1'],
      event: 'REQUEST_CHANGES',
      summary: 'Please fix.',
    })
    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit | undefined]>
    const request = calls[0][1]
    expect(String(request?.body)).toBe(JSON.stringify({
      pr_number: 7,
      selected_finding_ids: ['finding-1'],
      event: 'REQUEST_CHANGES',
      summary: 'Please fix.',
    }))
    expect(String(request?.body)).not.toContain('path')
    expect(String(request?.body)).not.toContain('line')
  })

  it('publish sends only the server publication id', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: 'pub-1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await publishGitHubReview('run/1', 'pub-1')
    expect(fetchMock).toHaveBeenCalledWith('/api/runs/run%2F1/github-review/publish', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ publication_id: 'pub-1' }),
    }))
  })

  it('loads capability through the registered project id', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ pull_requests: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getGitHubReviewCapability('project/1')
    expect(fetchMock).toHaveBeenCalledWith('/api/projects/project%2F1/github-review/capability', undefined)
  })
})

describe('network capability API', () => {
  it('queries only the fixed mode and network snapshot fields', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ tools: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getToolCapabilities('analysis', true)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tool-capabilities?task_kind=analysis&network_access=true',
      undefined,
    )
  })

  it('snapshots network access when creating a run', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ run: {}, message: {} }), { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    await startRun('thread 1', '查找最新文档', 'qa', true)
    expect(fetchMock).toHaveBeenCalledWith('/api/threads/thread%201/runs', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        content: '查找最新文档',
        task_kind: 'qa',
        network_access: true,
      }),
    }))
    expect(String((fetchMock.mock.calls[0] as any)[1]?.body)).not.toMatch(/api.?key|token/i)
  })
})
