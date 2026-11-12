import type {
  FieldDef, FlowFeature, FlowFiles, FlowInspect, FlowJobItem,
  FlowNextResponse, FlowReview, Health,
} from './types'

const BASE = ''   // 开发走 Vite 代理，生产同源

class ApiError extends Error {
  code: string
  detail: Record<string, unknown>
  constructor(code: string, message: string, detail: Record<string, unknown> = {}) {
    super(message)
    this.code = code
    this.detail = detail
  }
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; detail?: Record<string, unknown> } })?.error
    throw new ApiError(err?.code ?? `http_${res.status}`,
      err?.message ?? `请求失败（${res.status}）`, err?.detail ?? {})
  }
  return data as T
}

export const api = {
  health: () => fetch(`${BASE}/api/health`).then(r => parse<Health>(r)),
}

// ============================================================================
// 编排层（jobflow）—— 功能清单 + 阶段任务 + 复核结论
// ============================================================================

export const flow = {
  features: () => fetch(`${BASE}/api/features`).then(r =>
    parse<{ items: FlowFeature[] }>(r)),

  fields: (featureId: string) =>
    fetch(`${BASE}/api/features/${featureId}/fields`).then(r =>
      parse<{ feature: string; required: string[]; fields: FieldDef[] }>(r)),

  /** 可"接着跑"的已完成任务（功能声明了 accepts_upstream 才有） */
  upstreams: (featureId: string) =>
    fetch(`${BASE}/api/features/${featureId}/upstream`).then(r =>
      parse<{ accepts: string[]; items: FlowJobItem[] }>(r)),

  inspect: (featureId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    form.append('feature', featureId)
    return fetch(`${BASE}/api/flow/inspect`, { method: 'POST', body: form })
      .then(r => parse<FlowInspect>(r))
  },

  create: (featureId: string, file: File, mapping: Record<string, string>) => {
    const form = new FormData()
    form.append('file', file)
    form.append('mapping', JSON.stringify(mapping))
    return fetch(`${BASE}/api/features/${featureId}/jobs`, { method: 'POST', body: form })
      .then(r => parse<{ job_id: string; status: string }>(r))
  },

  /** 不传文件，接某个已完成任务的分类结果与改桶结论 */
  createFromUpstream: (featureId: string, upstream: string) => {
    const form = new FormData()
    form.append('upstream', upstream)
    return fetch(`${BASE}/api/features/${featureId}/jobs`, { method: 'POST', body: form })
      .then(r => parse<{ job_id: string; status: string }>(r))
  },

  createLocal: (featureId: string, path: string,
                mapping: Record<string, string>) =>
    fetch(`${BASE}/api/features/${featureId}/jobs/local`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, mapping }),
    }).then(r => parse<{ job_id: string; status: string }>(r)),

  jobs: (limit = 20) => fetch(`${BASE}/api/flow?limit=${limit}`).then(r =>
    parse<{ items: FlowJobItem[] }>(r)),

  job: (id: string) =>
    fetch(`${BASE}/api/flow/${id}`).then(r => parse<FlowNextResponse>(r)),

  next: (id: string) =>
    fetch(`${BASE}/api/flow/${id}/next`).then(r => parse<FlowNextResponse>(r)),

  review: (id: string, opts: { stage?: string; offset?: number; limit?: number } = {}) => {
    const q = new URLSearchParams()
    if (opts.stage) q.set('stage', opts.stage)
    if (opts.offset != null) q.set('offset', String(opts.offset))
    if (opts.limit != null) q.set('limit', String(opts.limit))
    return fetch(`${BASE}/api/flow/${id}/review?${q}`).then(r => parse<FlowReview>(r))
  },

  decide: (id: string, body: {
    stage?: string
    decisions: { key: string; value: string; note?: string }[]
    waive?: boolean
  }) => fetch(`${BASE}/api/flow/${id}/decisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => parse<{
    ok: boolean; job_id: string; stage: string; accepted: number
    rejected: { key: string; reason: string }[]; waived: boolean; status: string
  }>(r)),

  resume: (id: string) =>
    fetch(`${BASE}/api/flow/${id}/resume`, { method: 'POST' }).then(r =>
      parse<{ job_id: string; status: string }>(r)),

  files: (id: string) =>
    fetch(`${BASE}/api/flow/${id}/files`).then(r => parse<FlowFiles>(r)),

  remove: (id: string) =>
    fetch(`${BASE}/api/flow/${id}`, { method: 'DELETE' }).then(r =>
      parse<{ deleted: string }>(r)),
}

export { ApiError }
