import { describe, expect, it } from 'vitest'
import type { RunEventPayload } from './api'
import { stepPresentation } from './stepPresentation'

function payload(overrides: Partial<RunEventPayload>): RunEventPayload {
  return {
    run_id: 'run-1',
    source: 'main',
    created_at: '2026-07-23T00:00:00Z',
    ...overrides,
  }
}

describe('workspace search tool steps', () => {
  it('shows a glob pattern and virtual root while locating files', () => {
    expect(stepPresentation('tool_started', payload({
      name: 'workspace_glob',
      path: '/projects/demo',
      pattern: '**/*.py',
    }))).toEqual({
      title: '正在定位文件',
      detail: '**/*.py · /projects/demo',
    })
  })

  it('shows bounded code search metrics without a query or snippet', () => {
    const presentation = stepPresentation('tool_finished', payload({
      name: 'workspace_search',
      status: 'completed',
      match_count: 7,
      files_searched: 12,
      duration_ms: 4.2,
      truncated: true,
    }))

    expect(presentation).toEqual({
      title: '代码搜索完成',
      detail: '7 处匹配 · 扫描 12 个文件 · 4 ms · 结果已截断',
    })
    expect(JSON.stringify(presentation)).not.toMatch(/query|snippet/i)
  })
})
