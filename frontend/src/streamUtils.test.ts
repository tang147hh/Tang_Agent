import { describe, expect, it } from 'vitest'
import type { Message } from './api'
import { acceptRunEvent, reconcileRunStream } from './streamUtils'

function message(overrides: Partial<Message> = {}): Message {
  return {
    sequence: 1,
    message_id: 'message-1',
    thread_id: 'thread-1',
    run_id: 'run-1',
    role: 'assistant',
    content: '完成',
    created_at: '2026-07-23T00:00:00Z',
    ...overrides,
  }
}

describe('run stream reconciliation', () => {
  it('clears temporary output after the current run reply is persisted', () => {
    const stream = { runId: 'run-1', text: '完成' }

    expect(reconcileRunStream(stream, [message()], 'run-1')).toEqual({
      runId: null,
      text: '',
    })
  })

  it('does not clear output when only another run reply is persisted', () => {
    const stream = { runId: 'run-1', text: '正在处理' }

    expect(reconcileRunStream(
      stream,
      [message({ run_id: 'run-2' })],
      'run-1',
    )).toBe(stream)
  })

  it('retains partial output when a failed run has no persisted reply', () => {
    const stream = { runId: 'run-1', text: '已完成部分分析' }

    expect(reconcileRunStream(stream, [], 'run-1')).toBe(stream)
  })

  it('does not let an old refresh clear a newer run stream', () => {
    const stream = { runId: 'run-2', text: '新任务输出' }

    expect(reconcileRunStream(stream, [message()], 'run-1')).toBe(stream)
  })
})

describe('run event deduplication', () => {
  it('accepts each SSE event id only once', () => {
    const processedEventIds = new Set<string>()
    let output = ''

    for (const event of [
      { id: '1', text: 'Agent ' },
      { id: '1', text: 'Agent ' },
      { id: '2', text: '回复' },
    ]) {
      if (acceptRunEvent(event.id, processedEventIds)) output += event.text
    }

    expect(output).toBe('Agent 回复')
  })
})
