import type {
  BucketInfo, FieldDef, Health, InspectResult, JobDetail, JobStatus, RowPage,
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

  buckets: () => fetch(`${BASE}/api/buckets`).then(r =>
    parse<{ count: number; items: BucketInfo[] }>(r)),

  fields: () => fetch(`${BASE}/api/fields`).then(r =>
    parse<{ fields: FieldDef[] }>(r)),

  inspect: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch(`${BASE}/api/inspect`, { method: 'POST', body: form })
      .then(r => parse<InspectResult>(r))
  },

  createJob: (file: File, mapping: Record<string, string>) => {
    const form = new FormData()
    form.append('file', file)
    form.append('mapping', JSON.stringify(mapping))
    return fetch(`${BASE}/api/jobs`, { method: 'POST', body: form })
      .then(r => parse<{ job_id: string; status: JobStatus }>(r))
  },

  createJobLocal: (path: string) =>
    fetch(`${BASE}/api/jobs/local`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    }).then(r => parse<{ job_id: string; status: JobStatus }>(r)),

  job: (id: string) => fetch(`${BASE}/api/jobs/${id}`).then(r => parse<JobDetail>(r)),

  jobs: (limit = 20) => fetch(`${BASE}/api/jobs?limit=${limit}`).then(r =>
    parse<{ items: { job_id: string; filename: string; rows: number; vouchers: number; created_at: string; status: JobStatus }[] }>(r)),

  rows: (id: string, opts: { level: string; offset: number; limit: number; bucket?: string }) => {
    const q = new URLSearchParams({
      level: opts.level,
      offset: String(opts.offset),
      limit: String(opts.limit),
    })
    if (opts.bucket) q.set('bucket', opts.bucket)
    return fetch(`${BASE}/api/jobs/${id}/rows?${q}`).then(r => parse<RowPage>(r))
  },

  exportUrl: (id: string, kind: 'result' | 'detail' | 'vouchers') =>
    `${BASE}/api/jobs/${id}/export?kind=${kind}`,

  correctionTemplateUrl: (id: string) =>
    `${BASE}/api/jobs/${id}/corrections/template`,

  unreviewedUrl: (id: string) => `${BASE}/api/jobs/${id}/unreviewed`,

  unknownReportUrl: (id: string) => `${BASE}/api/jobs/${id}/unknown-subjects`,

  submitCorrections: (id: string, file: File, dryRun = false) => {
    const form = new FormData()
    form.append('file', file)
    form.append('dry_run', dryRun ? 'true' : 'false')
    return fetch(`${BASE}/api/jobs/${id}/corrections`, { method: 'POST', body: form })
      .then(r => parse<{ dry_run: boolean; corrections: number; vouchers: number }>(r))
  },
}

export { ApiError }
