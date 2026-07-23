import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import type { Page, Request, Route } from '@playwright/test'

const createdAt = '2026-07-23T12:00:00Z'
const baseSha = 'a'.repeat(40)
const headSha = 'b'.repeat(40)
const screenshotDir = process.env.TANG_AGENT_E2E_SCREENSHOT_DIR
  || '/tmp/tang-agent-lesson-38/screenshots'

interface MockState {
  workingSnapshot: Record<string, any>
  pullRequestSnapshot: Record<string, any>
  publication: Record<string, any> | null
  prepareCalls: number
  publishCalls: number
  publishErrorCode: string | null
  failStatusOnce: boolean
  searchRunStarted: boolean
  unhandledRequests: string[]
  requestBodies: Array<{ method: string; path: string; body: unknown }>
}

function textFile(index: number, overrides: Record<string, any> = {}) {
  const path = index === 0
    ? '/projects/demo/src/features/reviewer/very-long-component-name-for-overflow-validation.ts'
    : `/projects/demo/src/generated/file-${String(index).padStart(2, '0')}.ts`
  return {
    old_path: path,
    new_path: path,
    change_type: 'modified',
    binary: false,
    submodule: false,
    additions: 1,
    deletions: 1,
    truncated: index === 0,
    truncation_reason: index === 0 ? 'file_patch_chars' : null,
    changed_old_lines: [2],
    changed_new_lines: [2],
    redacted: index === 0,
    hunks: [{
      header: '@@ -1,3 +1,3 @@',
      old_start: 1,
      old_count: 3,
      new_start: 1,
      new_count: 3,
      lines: [
        { type: 'context', old_line_number: 1, new_line_number: 1, content: 'export function calculateReviewResult() {' },
        { type: 'deletion', old_line_number: 2, new_line_number: null, content: '  return legacyValue' },
        { type: 'addition', old_line_number: null, new_line_number: 2, content: `  return nextValueWithAReallyLongIdentifierThatMustScrollHorizontally_${'x'.repeat(140)} // [REDACTED]` },
        { type: 'context', old_line_number: 3, new_line_number: 3, content: '}' },
      ],
    }],
    ...overrides,
  }
}

function reviewFiles() {
  const files = Array.from({ length: 18 }, (_, index) => textFile(index))
  files.splice(1, 0, textFile(100, {
    old_path: '/projects/demo/src/legacy/removed-handler.ts',
    new_path: null,
    change_type: 'deleted',
    additions: 0,
    deletions: 2,
    truncated: false,
    truncation_reason: null,
    changed_old_lines: [8, 9],
    changed_new_lines: [],
    redacted: false,
    hunks: [{
      header: '@@ -8,2 +0,0 @@',
      old_start: 8,
      old_count: 2,
      new_start: 0,
      new_count: 0,
      lines: [
        { type: 'deletion', old_line_number: 8, new_line_number: null, content: 'const removed = unsafeFallback()' },
        { type: 'deletion', old_line_number: 9, new_line_number: null, content: 'export default removed' },
      ],
    }],
  }))
  files.splice(2, 0, {
    old_path: null,
    new_path: '/projects/demo/assets/build-output-with-a-very-long-file-name.bin',
    change_type: 'added',
    binary: true,
    submodule: false,
    additions: 0,
    deletions: 0,
    truncated: false,
    truncation_reason: null,
    changed_old_lines: [],
    changed_new_lines: [],
    redacted: false,
    hunks: [],
  })
  return files
}

function finding(id: string, overrides: Record<string, any> = {}) {
  return {
    id,
    run_id: 'run-pr',
    severity: 'high',
    category: 'correctness',
    file_path: '/projects/demo/src/features/reviewer/very-long-component-name-for-overflow-validation.ts',
    start_line: 2,
    end_line: 2,
    line_side: 'new',
    title: '新逻辑返回值错误',
    description: '这里会把无效结果继续传给调用方。',
    suggestion: '在返回前校验结果。',
    status: 'open',
    fingerprint: `fingerprint-${id}`,
    review_diff_hash: 'diff-hash',
    review_scope: 'all',
    base_revision: baseSha,
    head_revision: headSha,
    created_at: createdAt,
    updated_at: createdAt,
    ...overrides,
  }
}

function snapshot(source: 'working_tree' | 'pull_request') {
  const files = reviewFiles()
  const findings = [
    finding('finding-new'),
    finding('finding-old', {
      severity: 'critical',
      category: 'security',
      file_path: '/projects/demo/src/legacy/removed-handler.ts',
      start_line: 8,
      end_line: 8,
      line_side: 'old',
      title: '删除行仍隐藏安全回退',
      description: '删除的处理器说明旧路径曾绕过校验。',
    }),
    finding('finding-file', {
      severity: 'medium',
      category: 'maintainability',
      file_path: '/projects/demo/assets/build-output-with-a-very-long-file-name.bin',
      start_line: null,
      end_line: null,
      line_side: null,
      title: '二进制产物不应进入仓库',
      description: '构建产物会持续增大仓库体积。',
    }),
    finding('finding-global', {
      severity: 'low',
      category: 'testing',
      file_path: null,
      start_line: null,
      end_line: null,
      line_side: null,
      title: '缺少组合场景测试',
      description: '建议覆盖 Review 发布失败后的恢复路径。',
    }),
  ]
  const runId = source === 'working_tree' ? 'run-working' : 'run-pr'
  for (const item of findings) item.run_id = runId
  return {
    run_id: runId,
    status: 'completed',
    scope: 'all',
    source,
    diff: {
      scope: 'all',
      source,
      repository: source === 'pull_request' ? 'acme/reviewer-fixture' : null,
      pr_number: source === 'pull_request' ? 38 : null,
      repository_virtual_path: '/projects/demo',
      base_revision: source === 'pull_request' ? baseSha : 'HEAD',
      head_revision: source === 'pull_request' ? headSha : null,
      files,
      file_count: files.length,
      total_additions: files.reduce((total, file) => total + file.additions, 0),
      total_deletions: files.reduce((total, file) => total + file.deletions, 0),
      truncated: true,
      truncation_reasons: ['file_patch_chars'],
      content_hash: 'diff-hash',
      created_at: createdAt,
      redacted: true,
    },
    findings,
    finding_count: findings.length,
    summary: '完成受控 Diff 审查。',
    created_at: createdAt,
  }
}

function publication(status: string, errorCode: string | null = null) {
  return {
    id: 'publication-38',
    run_id: 'run-pr',
    repository: 'acme/reviewer-fixture',
    pr_number: 38,
    base_sha: baseSha,
    head_sha: headSha,
    event: 'COMMENT',
    selected_finding_ids: ['finding-new', 'finding-old'],
    payload_hash: 'payload-hash-38',
    status,
    github_review_id: status === 'published' ? '3801' : null,
    github_review_url: status === 'published'
      ? 'https://github.com/acme/reviewer-fixture/pull/38#pullrequestreview-3801'
      : null,
    github_user: status === 'published' ? 'fixture-reviewer' : null,
    prepared_at: createdAt,
    expires_at: '2026-07-24T12:00:00Z',
    published_at: status === 'published' ? '2026-07-23T12:05:00Z' : null,
    error_code: errorCode,
    error_message: errorCode ? 'sanitized mock error' : null,
  }
}

function createState(): MockState {
  return {
    workingSnapshot: snapshot('working_tree'),
    pullRequestSnapshot: snapshot('pull_request'),
    publication: null,
    prepareCalls: 0,
    publishCalls: 0,
    publishErrorCode: null,
    failStatusOnce: false,
    searchRunStarted: false,
    unhandledRequests: [],
    requestBodies: [],
  }
}

const project = {
  project_id: 'project-1',
  name: 'Reviewer Fixture With Long Project Name',
  virtual_path: '/projects/demo',
  created_at: createdAt,
  updated_at: createdAt,
}

const thread = {
  thread_id: 'thread-1',
  project_id: 'project-1',
  title: 'Review delivery flow',
  status: 'idle',
  created_at: createdAt,
  updated_at: createdAt,
}

const run = {
  run_id: 'run-pr',
  thread_id: 'thread-1',
  status: 'completed',
  task_kind: 'analysis',
  error: null,
  network_access: false,
  network_provider: 'fake',
  network_request_count: 0,
  network_result_count: 0,
  network_bytes_received: 0,
  network_cache_hit_count: 0,
  network_limit_reached: false,
  network_limit_reason: null,
  created_at: createdAt,
  started_at: createdAt,
  finished_at: createdAt,
}

const searchRun = {
  ...run,
  run_id: 'run-search',
  task_kind: 'qa',
  network_access: true,
  network_request_count: 1,
  network_result_count: 1,
  network_bytes_received: 256,
}

function responseForReview(snapshotValue: Record<string, any>) {
  return {
    run_id: snapshotValue.run_id,
    status: snapshotValue.status,
    scope: snapshotValue.scope,
    source: snapshotValue.source,
    diff: snapshotValue.diff,
    findings: snapshotValue.findings,
    finding_count: snapshotValue.finding_count,
    created_count: snapshotValue.finding_count,
    duplicate_count: 0,
    summary: snapshotValue.summary,
  }
}

async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function installMockApi(page: Page, state: MockState) {
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    let body: any = null
    if (request.postData()) body = request.postDataJSON()
    state.requestBodies.push({ method, path, body })

    if (method === 'GET' && path === '/api/projects') return fulfill(route, [project])
    if (method === 'GET' && path === '/api/tool-capabilities') {
      const enabled = url.searchParams.get('network_access') === 'true'
      return fulfill(route, {
        task_kind: url.searchParams.get('task_kind') || 'coding',
        run_id: null,
        network_access: enabled,
        network_provider: 'fake',
        web_search: {
          available: enabled,
          provider: 'fake',
          configured: true,
          provider_available: true,
          allowed_in_mode: true,
          enabled_for_run: enabled,
          unavailable_reason: enabled ? null : '当前 Run 未允许联网搜索。',
        },
        network_budget: {
          max_searches: 4,
          max_results_per_search: 5,
          request_timeout_seconds: 15,
          max_result_chars_per_search: 8000,
          max_total_result_chars: 24000,
          max_bytes_received: 2097152,
        },
        tools: [],
      })
    }
    if (method === 'GET' && path === '/api/projects/project-1/threads') return fulfill(route, [thread])
    if (method === 'GET' && path === '/api/projects/project-1/file-changes') {
      return fulfill(route, {
        project_path: '/projects/demo',
        changed_files: 20,
        additions: 18,
        deletions: 20,
        binary_files: 1,
        hidden_files: 1,
        files: [],
      })
    }
    if (method === 'GET' && path === '/api/threads/thread-1') return fulfill(route, thread)
    if (method === 'GET' && path === '/api/threads/thread-review') {
      return fulfill(route, { ...thread, thread_id: 'thread-review', title: '代码审查' })
    }
    if (method === 'GET' && path === '/api/threads/thread-1/messages') {
      const messages = [{
        sequence: 1,
        message_id: 'message-1',
        thread_id: 'thread-1',
        run_id: 'run-pr',
        role: 'assistant',
        content: 'PR 审查已经完成。',
        created_at: createdAt,
      }]
      if (state.searchRunStarted) {
        messages.push({
          sequence: 2,
          message_id: 'message-search-user',
          thread_id: 'thread-1',
          run_id: 'run-search',
          role: 'user',
          content: '查询 FastAPI 最新文档',
          created_at: createdAt,
        })
        messages.push({
          sequence: 3,
          message_id: 'message-search-assistant',
          thread_id: 'thread-1',
          run_id: 'run-search',
          role: 'assistant',
          content: '[S1] FastAPI Documentation — https://fastapi.tiangolo.com/',
          created_at: createdAt,
        })
      }
      return fulfill(route, messages)
    }
    if (method === 'GET' && path === '/api/threads/thread-1/runs') {
      return fulfill(route, state.searchRunStarted ? [run, searchRun] : [run])
    }
    if (method === 'GET' && path === '/api/threads/thread-review/messages') return fulfill(route, [])
    if (method === 'GET' && path === '/api/threads/thread-review/runs') return fulfill(route, [])
    if (method === 'GET' && path === '/api/runs/run-pr') return fulfill(route, run)
    if (method === 'GET' && path === '/api/runs/run-search') return fulfill(route, searchRun)
    if (method === 'GET' && path === '/api/runs/run-search/events') {
      const event = (id: number, name: string, data: Record<string, any>) => (
        `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify({
          run_id: 'run-search',
          source: 'main',
          created_at: createdAt,
          ...data,
        })}\n\n`
      )
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache' },
        body: [
          event(1, 'created', { status: 'pending', task_kind: 'qa', network_access: true, network_provider: 'fake' }),
          event(2, 'running', { status: 'running', task_kind: 'qa', network_access: true, network_provider: 'fake' }),
          event(3, 'tool_started', {
            name: 'workspace_search',
            tool_call_id: 'workspace-search-1',
            path: '/projects/demo',
            file_pattern: '**/*.py',
            max_results: 100,
          }),
          event(4, 'tool_finished', {
            name: 'workspace_search',
            tool_call_id: 'workspace-search-1',
            status: 'completed',
            recoverable: false,
            match_count: 3,
            files_searched: 12,
            skipped_file_count: 1,
            scanned_bytes: 4096,
            duration_ms: 4,
            truncated: true,
          }),
          event(5, 'tool_started', { name: 'web_search', tool_call_id: 'search-1', query: 'FastAPI latest docs', provider: 'fake', max_results: 5 }),
          event(6, 'tool_finished', {
            name: 'web_search',
            tool_call_id: 'search-1',
            status: 'completed',
            recoverable: false,
            result_count: 1,
            duration_ms: 18,
            cached: false,
            truncated: false,
            sources: [{ citation_id: 'S1', title: 'FastAPI Documentation', url: 'https://fastapi.tiangolo.com/' }],
          }),
          event(7, 'token', { text: '[S1] FastAPI Documentation — https://fastapi.tiangolo.com/' }),
          event(8, 'completed', { status: 'completed' }),
        ].join(''),
      })
    }
    if (method === 'GET' && path.startsWith('/api/runs/') && path.endsWith('/performance')) return fulfill(route, null)
    if (method === 'GET' && path === '/api/repositories') {
      return fulfill(route, [{
        name: 'demo',
        path: '/projects/demo',
        current_branch: 'feature/review-fixture',
        branches: ['feature/review-fixture', 'main'],
        dirty: true,
        remote_url: 'https://github.com/acme/reviewer-fixture.git',
      }])
    }
    if (method === 'GET' && path === '/api/projects/project-1/github-review/capability') {
      return fulfill(route, {
        gh_installed: true,
        authenticated: true,
        remote_found: true,
        publish_enabled: true,
        can_publish: true,
        reason: null,
        repository: 'acme/reviewer-fixture',
        current_user: 'fixture-reviewer',
        pull_requests: [{
          pr_number: 38,
          title: 'Validate the complete reviewer delivery flow',
          url: 'https://github.com/acme/reviewer-fixture/pull/38',
          state: 'OPEN',
          is_draft: false,
          base_branch: 'main',
          head_branch: 'feature/review-fixture',
          base_sha: baseSha,
          head_sha: headSha,
          author: 'fixture-author',
          repository: 'acme/reviewer-fixture',
        }],
      })
    }
    if (method === 'POST' && path === '/api/projects/project-1/threads') {
      return fulfill(route, { ...thread, thread_id: 'thread-review', title: body?.title ?? '代码审查' }, 201)
    }
    if (method === 'POST' && path === '/api/threads/thread-1/runs') {
      state.searchRunStarted = true
      return fulfill(route, {
        run: searchRun,
        message: {
          sequence: 2,
          message_id: 'message-search-user',
          thread_id: 'thread-1',
          run_id: 'run-search',
          role: 'user',
          content: body.content,
          created_at: createdAt,
        },
      }, 202)
    }
    if (method === 'POST' && path === '/api/threads/thread-review/review-runs') {
      const selected = body?.source === 'pull_request'
        ? state.pullRequestSnapshot
        : state.workingSnapshot
      return fulfill(route, responseForReview(selected))
    }
    if (method === 'GET' && path === '/api/runs/run-working') {
      return fulfill(route, { ...run, run_id: 'run-working', thread_id: 'thread-review' })
    }
    if (method === 'GET' && path === '/api/runs/run-working/review') return fulfill(route, state.workingSnapshot)
    if (method === 'GET' && path === '/api/runs/run-pr/review') return fulfill(route, state.pullRequestSnapshot)
    if (method === 'GET' && path === '/api/runs/run-pr/github-review/publications') {
      return fulfill(route, state.publication ? [state.publication] : [])
    }
    if (method === 'PATCH' && path.includes('/review-findings/')) {
      const findingId = decodeURIComponent(path.split('/').at(-1) ?? '')
      const source = path.includes('run-working') ? state.workingSnapshot : state.pullRequestSnapshot
      const existing = source.findings.find((item: any) => item.id === findingId)
      if (state.failStatusOnce) {
        state.failStatusOnce = false
        return fulfill(route, { detail: { code: 'github_api_error', message: 'internal stack hidden' } }, 503)
      }
      const updated = { ...existing, status: body.status, updated_at: '2026-07-23T12:10:00Z' }
      source.findings = source.findings.map((item: any) => item.id === findingId ? updated : item)
      return fulfill(route, updated)
    }
    if (method === 'POST' && path === '/api/runs/run-pr/github-review/prepare') {
      state.prepareCalls += 1
      return fulfill(route, {
        publication_id: 'publication-38',
        repository: 'acme/reviewer-fixture',
        pr_number: 38,
        pr_title: 'Validate the complete reviewer delivery flow',
        pr_url: 'https://github.com/acme/reviewer-fixture/pull/38',
        base_sha: baseSha,
        head_sha: headSha,
        event: body.event,
        inline_comments: [
          { finding_id: 'finding-new', path: 'src/features/reviewer/very-long-component-name-for-overflow-validation.ts', line: 2, side: 'RIGHT', body: 'New line issue.' },
          { finding_id: 'finding-old', path: 'src/legacy/removed-handler.ts', line: 8, side: 'LEFT', body: 'Old line issue.' },
        ],
        summary_comments: [
          { finding_id: 'finding-file', title: '二进制产物不应进入仓库', reason: '文件级 Finding' },
          { finding_id: 'finding-global', title: '缺少组合场景测试', reason: '全局 Finding' },
        ],
        summary_body: body.summary || 'Automated acceptance preview.',
        skipped_findings: [{ finding_id: 'finding-closed', title: '已解决问题', reason: 'Finding 已不是 open' }],
        warnings: ['文件级与全局 Finding 将写入 Review 总结。'],
        payload_hash: 'payload-hash-38',
        expires_at: '2026-07-24T12:00:00Z',
      })
    }
    if (method === 'POST' && path === '/api/runs/run-pr/github-review/publish') {
      state.publishCalls += 1
      await new Promise((resolve) => setTimeout(resolve, 180))
      if (state.publishErrorCode) {
        const status = state.publishErrorCode === 'permission_denied' ? 403
          : state.publishErrorCode === 'publishing_disabled' ? 503
            : 409
        state.publication = publication(
          state.publishErrorCode === 'publication_result_unknown' ? 'unknown' : 'failed',
          state.publishErrorCode,
        )
        return fulfill(route, {
          detail: {
            code: state.publishErrorCode,
            message: 'Traceback and host path must never be shown',
          },
        }, status)
      }
      state.publication = publication('published')
      return fulfill(route, state.publication)
    }

    state.unhandledRequests.push(`${method} ${path}`)
    return fulfill(route, { detail: 'unhandled mock request' }, 599)
  })
}

async function openRunReview(page: Page) {
  await page.goto('/')
  await expect(page.getByText('PR 审查已经完成。')).toBeVisible()
  await page.getByRole('button', { name: '审查' }).click()
  await expect(page.getByLabel('Diff 查看器')).toBeVisible()
}

async function openRepositoryReview(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: /Repositories/ }).click()
  await expect(page.getByRole('heading', { name: 'Repositories' })).toBeVisible()
  await expect(page.getByRole('button', { name: /代码审查/ })).toBeEnabled()
  await page.getByRole('button', { name: /代码审查/ }).click()
  await expect(page.getByRole('heading', { name: '代码审查' })).toBeVisible()
}

function watchPage(page: Page) {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const failedRequests: string[] = []
  page.on('console', (message) => {
    if (
      message.type() === 'error'
      && !message.text().startsWith('Failed to load resource: the server responded with a status of')
    ) {
      consoleErrors.push(message.text())
    }
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('requestfailed', (request: Request) => failedRequests.push(request.url()))
  return { consoleErrors, pageErrors, failedRequests }
}

function assertCleanPage(
  state: MockState,
  diagnostics: ReturnType<typeof watchPage>,
) {
  expect(state.unhandledRequests).toEqual([])
  expect(diagnostics.consoleErrors).toEqual([])
  expect(diagnostics.pageErrors).toEqual([])
  expect(diagnostics.failedRequests).toEqual([])
}

async function saveScreenshot(page: Page, fileName: string) {
  mkdirSync(screenshotDir, { recursive: true })
  await page.screenshot({
    path: join(screenshotDir, fileName),
    animations: 'disabled',
  })
}

test('working tree review completes repository flow and remains non-publishable', async ({ page }) => {
  const state = createState()
  const diagnostics = watchPage(page)
  await installMockApi(page, state)
  await openRepositoryReview(page)

  await expect(page.getByRole('button', { name: '全部变更' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '开始审查' }).click()
  await expect(page.getByRole('complementary', { name: '变更文件' })).toBeVisible()
  await expect(page.getByRole('button', { name: /预览发布/ })).toBeDisabled()

  const search = page.getByLabel('搜索变更文件')
  await search.fill('removed-handler')
  await expect(
    page.getByRole('complementary', { name: '变更文件' })
      .getByRole('button', { name: /removed-handler\.ts/ }),
  ).toHaveCount(1)
  await search.fill('')

  await page.getByRole('button', { name: /定位问题：删除行仍隐藏安全回退/ }).click()
  await expect(page.locator('.review-diff-line.finding-highlight.line-deletion')).toBeVisible()
  await page.getByRole('button', { name: /定位问题：新逻辑返回值错误/ }).click()
  await expect(page.locator('.review-diff-line.finding-highlight.line-addition')).toBeVisible()

  const status = page.getByLabel('更新问题状态：新逻辑返回值错误')
  state.failStatusOnce = true
  await status.selectOption('dismissed')
  await expect(page.getByText(/状态更新失败，已恢复原状态/)).toBeVisible()
  await expect(status).toHaveValue('open')
  await status.selectOption('resolved')
  await expect(status).toHaveValue('resolved')

  await saveScreenshot(page, '1440x900-working-tree-review.png')
  const bodyWidth = await page.evaluate(() => document.body.scrollWidth)
  expect(bodyWidth).toBeLessThanOrEqual(1440)
  expect(state.requestBodies.some((item) => (
    item.method === 'POST'
    && item.path.endsWith('/review-runs')
    && (item.body as any)?.scope === 'all'
    && (item.body as any)?.source === 'working_tree'
  ))).toBe(true)
  assertCleanPage(state, diagnostics)
})

test('pull request preview cancels safely and publishes only after explicit confirmation', async ({ page }) => {
  const state = createState()
  const diagnostics = watchPage(page)
  await installMockApi(page, state)
  await openRunReview(page)

  const eventSelect = page.locator('.github-event-select select')
  await expect(eventSelect.locator('option')).toHaveText(['评论', '批准', '请求修改'])
  await eventSelect.selectOption('REQUEST_CHANGES')
  await expect(page.getByText(/请求修改会阻止/)).toBeVisible()
  await eventSelect.selectOption('APPROVE')
  await expect(page.getByText(/批准会记录/)).toBeVisible()
  await eventSelect.selectOption('COMMENT')

  await page.getByRole('button', { name: '预览发布' }).click()
  const dialog = page.getByRole('dialog', { name: '发布 GitHub Review' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('2', { exact: true })).toHaveCount(2)
  await expect(dialog.getByText('已解决问题')).toBeVisible()
  await page.setViewportSize({ width: 1280, height: 720 })
  await saveScreenshot(page, '1280x720-pull-request-preview.png')
  const dialogBox = await dialog.boundingBox()
  expect(dialogBox).not.toBeNull()
  expect(dialogBox!.y).toBeGreaterThanOrEqual(0)
  expect(dialogBox!.y + dialogBox!.height).toBeLessThanOrEqual(720)

  await page.keyboard.press('Enter')
  expect(state.publishCalls).toBe(0)
  await dialog.getByRole('button', { name: '取消' }).click()
  expect(state.publishCalls).toBe(0)

  await page.getByRole('button', { name: '预览发布' }).click()
  await dialog.getByRole('button', { name: '发布到 GitHub' }).click()
  await expect(dialog.getByRole('button', { name: '正在发布' })).toBeDisabled()
  await expect(page.getByText(/已由 fixture-reviewer 发布/)).toBeVisible()
  const reviewLink = page.getByRole('link', { name: /查看 GitHub Review/ })
  await expect(reviewLink).toHaveAttribute(
    'href',
    'https://github.com/acme/reviewer-fixture/pull/38#pullrequestreview-3801',
  )
  await expect(page.getByRole('button', { name: '预览发布' })).toBeDisabled()
  expect(state.publishCalls).toBe(1)
  assertCleanPage(state, diagnostics)
})

test('tablet and mobile layouts keep tabs, scrolling, locations and dialog usable', async ({ page }) => {
  const state = createState()
  const diagnostics = watchPage(page)
  await installMockApi(page, state)
  await openRunReview(page)

  await page.setViewportSize({ width: 768, height: 1024 })
  await expect(page.locator('.view-review > .sidebar')).toBeHidden()
  const tabs = page.getByRole('tablist', { name: '代码审查视图' })
  await expect(tabs).toBeVisible()
  await page.getByRole('tab', { name: '文件' }).click()
  const fileList = page.locator('.review-file-list')
  await expect(fileList).toBeVisible()
  expect(await fileList.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true)
  await saveScreenshot(page, '768x1024-tablet-file-list.png')
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(768)

  await page.getByRole('tab', { name: /问题/ }).click()
  await page.getByRole('button', { name: /定位问题：删除行仍隐藏安全回退/ }).click()
  await expect(page.locator('.review-diff-line.finding-highlight.line-deletion')).toBeVisible()
  const oldNumber = page.locator('.review-diff-line.finding-highlight .review-line-number').first()
  const marker = page.locator('.review-diff-line.finding-highlight .review-line-marker')
  expect((await oldNumber.boundingBox())?.y).toBe((await marker.boundingBox())?.y)

  const diffScroll = page.locator('.review-diff-scroll')
  await page.getByRole('tab', { name: '文件' }).click()
  await page.getByRole('button', { name: /very-long-component-name/ }).click()
  await expect(diffScroll).toBeVisible()
  expect(await diffScroll.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true)
  await diffScroll.evaluate((element) => { element.scrollLeft = 240 })
  expect(await diffScroll.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0)

  await page.getByRole('tab', { name: /问题/ }).click()
  await page.getByRole('button', { name: /定位问题：二进制产物不应进入仓库/ }).click()
  await expect(page.getByText('二进制文件不展示内容')).toBeVisible()

  await page.getByRole('button', { name: '预览发布' }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  const dialog = page.getByRole('dialog', { name: '发布 GitHub Review' })
  await expect(dialog).toBeVisible()
  await saveScreenshot(page, '390x844-mobile-publish-dialog.png')
  const dialogBox = await dialog.boundingBox()
  expect(dialogBox).not.toBeNull()
  expect(dialogBox!.x).toBeGreaterThanOrEqual(0)
  expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(390)
  expect(dialogBox!.y + dialogBox!.height).toBeLessThanOrEqual(844)
  const pullRequestLink = dialog.getByRole('link', { name: /#38/ })
  expect(await pullRequestLink.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await expect(dialog.getByRole('button', { name: '发布到 GitHub' })).toBeEnabled()
  await expect(dialog.getByRole('button', { name: '取消' })).toBeEnabled()
  const bodyText = await page.locator('body').innerText()
  expect(bodyText).not.toContain('/Users/')
  expect(bodyText).not.toMatch(/gh[pousr]_[A-Za-z0-9]+/)
  assertCleanPage(state, diagnostics)
})

test('publish failures are localized and unknown results remain locked', async ({ page }) => {
  const state = createState()
  const diagnostics = watchPage(page)
  await installMockApi(page, state)
  await openRunReview(page)

  const cases = [
    ['pull_request_changed', 'Pull Request 已发生变化，请重新审查最新提交后再发布。'],
    ['permission_denied', '当前 GitHub 账号没有发布 Review 的权限。'],
    ['publication_expired', '发布预览已过期，请重新预览。'],
    ['publication_already_published', '相同 Review 已经发布，不能重复发布。'],
    ['publishing_disabled', 'GitHub Review 发布功能未启用。'],
  ] as const

  for (const [code, message] of cases) {
    state.publishErrorCode = code
    state.publication = null
    await page.getByRole('button', { name: '预览发布' }).click()
    const dialog = page.getByRole('dialog', { name: '发布 GitHub Review' })
    await dialog.getByRole('button', { name: '发布到 GitHub' }).click()
    await expect(dialog.getByText(message)).toBeVisible()
    expect(await dialog.innerText()).not.toContain('Traceback')
    await dialog.getByRole('button', { name: '取消' }).click()
  }

  state.publishErrorCode = 'publication_result_unknown'
  state.publication = null
  await page.getByRole('button', { name: '预览发布' }).click()
  await page.getByRole('dialog', { name: '发布 GitHub Review' })
    .getByRole('button', { name: '发布到 GitHub' }).click()
  await expect(page.getByText(/发布结果不确定，已禁止重试/)).toBeVisible()
  await expect(page.getByRole('button', { name: '预览发布' })).toBeDisabled()
  expect(state.publishCalls).toBe(6)
  assertCleanPage(state, diagnostics)
})

test('network permission is snapshotted and structured sources remain usable at four viewports', async ({ page }) => {
  const state = createState()
  const diagnostics = watchPage(page)
  await installMockApi(page, state)
  await page.goto('/')
  await expect(page.getByText('PR 审查已经完成。')).toBeVisible()

  await page.locator('.task-kind-trigger').click()
  await page.getByRole('option', { name: 'qa' }).click()
  const networkTrigger = page.locator('.network-access-trigger')
  await expect(networkTrigger).toContainText('关闭')
  await networkTrigger.click()
  await page.getByRole('menuitemradio', { name: /允许联网/ }).click()
  await expect(networkTrigger).toContainText('开启')

  await page.locator('.composer textarea').fill('查询 FastAPI 最新文档')
  await page.locator('.send-button').click()
  await expect(page.getByText('代码搜索完成', { exact: true })).toBeVisible()
  await expect(page.getByText('3 处匹配 · 扫描 12 个文件 · 4 ms · 结果已截断')).toBeVisible()
  await expect(page.getByText('搜索完成', { exact: true })).toBeVisible()
  const source = page.getByRole('link', { name: /S1\s+FastAPI Documentation/ })
  await expect(source).toHaveAttribute('href', 'https://fastapi.tiangolo.com/')
  await expect(source).toHaveAttribute('target', '_blank')
  await expect(source).toHaveAttribute('rel', 'noopener noreferrer')

  const runRequest = state.requestBodies.find((item) => (
    item.method === 'POST' && item.path === '/api/threads/thread-1/runs'
  ))
  expect(runRequest).toBeDefined()
  expect(runRequest?.body).toEqual({
    content: '查询 FastAPI 最新文档',
    task_kind: 'qa',
    network_access: true,
  })
  expect(JSON.stringify(runRequest?.body)).not.toMatch(/api.?key|token|provider/i)

  const viewports = [
    { width: 1440, height: 900, name: '1440x900' },
    { width: 1280, height: 720, name: '1280x720' },
    { width: 768, height: 1024, name: '768x1024' },
    { width: 390, height: 844, name: '390x844' },
  ]
  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await expect(source).toBeVisible()
    expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(viewport.width)
    await saveScreenshot(page, `${viewport.name}-network-search.png`)
  }

  await page.getByRole('button', { name: '执行状态' }).click()
  await expect(page.locator('.run-panel')).not.toBeVisible()
  await networkTrigger.click()
  await page.getByRole('menuitemradio', { name: /禁止联网/ }).click()
  await expect(networkTrigger).toContainText('关闭')
  expect((runRequest!.body as any).network_access).toBe(true)
  assertCleanPage(state, diagnostics)
})
