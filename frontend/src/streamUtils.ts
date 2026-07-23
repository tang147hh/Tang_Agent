import type { Message } from './api'

export interface RunStreamState {
  runId: string | null
  text: string
}

export function reconcileRunStream(
  stream: RunStreamState,
  messages: Message[],
  refreshedRunId?: string,
): RunStreamState {
  if (!refreshedRunId || stream.runId !== refreshedRunId) return stream

  const persisted = messages.some((message) => (
    message.role === 'assistant' && message.run_id === refreshedRunId
  ))

  return persisted ? { runId: null, text: '' } : stream
}

export function acceptRunEvent(eventId: string, processedEventIds: Set<string>): boolean {
  if (!eventId) return true
  if (processedEventIds.has(eventId)) return false
  processedEventIds.add(eventId)
  return true
}
