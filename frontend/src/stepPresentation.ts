import type { RunEventKind, RunEventPayload } from './api'

const stepCopy: Record<RunEventKind, { title: string; detail: string }> = {
  created: { title: '任务已创建', detail: '已接收用户请求并创建执行记录' },
  running: { title: '分析项目', detail: '正在读取上下文并执行任务' },
  token: { title: '生成回答', detail: '正在整理结果并生成回复' },
  tool_started: { title: '调用工具', detail: '正在执行项目工具' },
  tool_finished: { title: '工具执行完成', detail: '工具结果已返回 Agent' },
  completed: { title: '完成', detail: '任务执行完成，等待下一条指令' },
  failed: { title: '执行失败', detail: '任务未能完成，请查看错误信息' },
  terminated: { title: '预算已终止', detail: 'Run 已达到执行预算并安全结束' },
  review_findings_saved: { title: '审查结果已保存', detail: '结构化问题已完成校验和去重' },
}

export function durationLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(1)} s`
}

export function stepPresentation(kind: RunEventKind, payload: RunEventPayload) {
  const copy = stepCopy[kind]
  const isSubagent = payload.source.startsWith('subagent')
  const subagent = payload.subagent ?? 'general-purpose'

  if (kind === 'token' && isSubagent) {
    return {
      title: '子 Agent 分析',
      detail: `${subagent} 正在整理分析结果`,
    }
  }

  if (kind === 'tool_started' || kind === 'tool_finished') {
    const finished = kind === 'tool_finished'

    if (payload.name === 'task') {
      return {
        title: '委派子 Agent',
        detail: finished
          ? `${subagent} 已返回分析结果`
          : `正在调用 ${subagent}`,
      }
    }

    if (payload.name === 'web_search') {
      if (!finished) {
        return {
          title: '正在搜索',
          detail: `${payload.query ?? '公开网页'} · ${payload.provider ?? '搜索服务'}`,
        }
      }
      if (payload.status === 'error') {
        return {
          title: '搜索失败',
          detail: payload.error ?? '联网搜索未返回可用结果',
        }
      }
      const resultCount = payload.result_count ?? payload.sources?.length ?? 0
      const qualifiers = [
        payload.cached ? '命中缓存' : '',
        payload.truncated ? '结果已截断' : '',
      ].filter(Boolean)
      return {
        title: '搜索完成',
        detail: `${resultCount} 个来源${qualifiers.length ? ` · ${qualifiers.join(' · ')}` : ''}`,
      }
    }

    if (payload.name === 'workspace_glob' || payload.name === 'workspace_search') {
      const isGlob = payload.name === 'workspace_glob'
      if (!finished) {
        return {
          title: isGlob ? '正在定位文件' : '正在搜索代码',
          detail: `${isGlob ? payload.pattern ?? '**/*' : payload.file_pattern ?? '**/*'} · ${payload.path ?? '/projects'}`,
        }
      }
      if (payload.status === 'error') {
        return {
          title: isGlob ? '文件定位失败' : '代码搜索失败',
          detail: '工具参数或工作区边界校验未通过',
        }
      }
      const matchCount = payload.match_count ?? 0
      const qualifiers = [
        !isGlob && payload.files_searched !== undefined ? `扫描 ${payload.files_searched} 个文件` : '',
        payload.duration_ms !== undefined ? durationLabel(payload.duration_ms) : '',
        payload.truncated ? '结果已截断' : '',
      ].filter(Boolean)
      return {
        title: isGlob ? '文件定位完成' : '代码搜索完成',
        detail: `${matchCount} ${isGlob ? '个路径' : '处匹配'}${qualifiers.length ? ` · ${qualifiers.join(' · ')}` : ''}`,
      }
    }

    const toolName = payload.name ?? '项目工具'
    return {
      title: isSubagent ? '子 Agent 调用工具' : copy.title,
      detail: `${isSubagent ? `${subagent} · ` : ''}${toolName}${finished ? ' 已完成' : ''}`,
    }
  }

  return {
    title: copy.title,
    detail: kind === 'failed' || kind === 'terminated'
      ? payload.error ?? copy.detail
      : copy.detail,
  }
}
