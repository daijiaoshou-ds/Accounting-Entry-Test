// 与后端 api/flow_api.py 一一对应的类型。改接口先改这里，再改后端。

export interface FieldDef {
  key: string
  label: string
  required: boolean
}

export interface Health {
  status: string
  version: string
  time: string
  buckets: number
  subjects: number
  model: {
    configured: boolean
    available: boolean
    loaded: boolean
    device: string | null
  }
}

// ============================================================================
// 编排层（jobflow）
// ============================================================================

export interface FlowStage {
  id: string
  title: string
  actor: 'program' | 'ai'
  depends_on: string[]
  help: string
}

export interface FlowFeature {
  id: string
  name: string
  description: string
  requires: string[]
  accept: string[]
  exports: { key: string; label: string; stage: string }[]
  stages: FlowStage[]
  /** 可以"接着哪个功能的任务跑"（功能 id） */
  accepts_upstream: string[]
}

export interface FlowNext {
  actor: 'program' | 'ai' | 'operator' | 'none'
  stage: string | null
  title?: string
  action: string
  count?: number
  truncated?: boolean
  read?: string
  submit?: string
  running?: boolean
  outputs?: Record<string, string>
}

export interface FlowStageState {
  id: string
  title: string
  actor: string
  status: 'pending' | 'running' | 'done' | 'skipped' | 'awaiting' | 'failed'
  elapsed?: number
  note?: string
  error?: string
  finished_at?: string
  facts?: Record<string, unknown>
}

export interface FlowStatus {
  job_id: string
  feature: string
  feature_name: string
  status: string
  created_at: string
  updated_at: string
  input: { filename?: string; rows?: number; sha256?: string }
  options: Record<string, unknown>
  stages: FlowStageState[]
  outputs: Record<string, string>
  error?: string | null
  running?: boolean
}

export interface FlowNextResponse extends FlowStatus {
  ok: boolean
  next: FlowNext
}

export interface FlowReviewColumn {
  key: string
  label: string
  kind: 'str' | 'num' | 'tag' | 'text' | 'list'
  secondary?: boolean
}

export interface FlowReview {
  job_id: string
  stage: string | null
  title: string
  help: string
  key_name: string
  value_enum: string[]
  total: number
  truncated: boolean
  offset: number
  limit: number
  columns: FlowReviewColumn[]
  items: Record<string, unknown>[]
  submitted: Record<string, string>
}

export interface FlowFile {
  key: string
  label: string
  filename: string
  bytes: number
  url: string
}

export interface FlowFiles {
  job_id: string
  status: string
  files: FlowFile[]
  sheets: { sheet: string; filename: string; bytes: number; path: string }[]
  /** 导出阶段的事实：清洗给 vouchers/confidence_dist，现金流给 cashflow/statement */
  facts: Record<string, unknown>
  cashflow: Record<string, number>
  statement: Record<string, unknown>[]
  export: { format?: string; seconds?: number; hint?: string }
  patch: { applied?: number; rejected?: unknown[] }
}

export interface FlowInspect {
  filename: string
  feature: string
  rows: number
  columns: string[]
  engine: string
  suggested_mapping: Record<string, string>
  unmapped: string[]
  required: string[]
  missing: string[]
  mapping_complete: boolean
  preview: Record<string, unknown>[]
}

export interface FlowJobItem {
  job_id: string
  feature: string
  feature_name: string
  filename: string
  status: string
  created_at: string
  updated_at: string
  done_stages: number
  clean_status?: string
  vouchers?: number | null
  awaiting: string | null
}
