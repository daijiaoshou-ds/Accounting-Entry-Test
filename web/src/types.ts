// 与后端 api/app.py 一一对应的类型。改接口先改这里，再改后端。

export interface JobStatus {
  state: 'queued' | 'running' | 'done' | 'failed'
  progress: number
  message: string
  updated_at?: string
  error?: string
}

export interface BucketInfo {
  name: string
  code: string
  parent: string
  definition: string
  train: boolean
  is_parent: boolean
  hard_rule: boolean
  sentinel: boolean
  scored: boolean
  anchors: string[]
  subjects: string[]
  merged_into: string
  active: boolean
}

export interface UnknownSubject {
  name: string
  reason: 'alias_candidate' | 'need_training' | 'direction'
  in_standard: boolean
  vouchers: number
  rows: number
  raw_names: string[]
  sample_summaries: string[]
}

export interface UnknownReport {
  generated_at?: string
  rows?: number
  vouchers?: number
  known?: {
    model_switches: number
    model_names: number
    standard_names: number
    model_only: number
  }
  unknown: {
    vouchers: number
    ratio: number
    names: number
    by_reason?: Record<string, number>
    subjects: UnknownSubject[]
  }
  model_only_subjects?: string[]
}

export interface InspectResult {
  filename: string
  rows: number
  columns: string[]
  suggested_mapping: Record<string, string>
  mapping_complete: boolean
  missing_fields: string[]
  excel_row_limit: number
  preview: Record<string, unknown>[]
  unknown_subjects?: UnknownReport | null
}

export interface JobSummary {
  rows: number
  vouchers: number
  coverage: number | null
  classified_count: number | null
  bucket_counts: Record<string, number>
  confidence_dist: Record<string, number>
  hard_rule_counts: Record<string, number>
  unknown_subjects: UnknownReport['unknown']
  engine: {
    mode?: string
    model_calls?: number
    hard_rule_vouchers?: number
    unclassified?: number
    fallback_to_parent?: number
    model?: { available?: boolean; loaded?: boolean; device?: string | null; temperature?: number }
  }
  detail_rows: number
  detail_columns: number
}

export interface JobDetail {
  job_id: string
  filename: string
  status: JobStatus
  mapping: Record<string, string>
  summary: JobSummary | null
  created_at: string
  finished_at: string | null
}

export interface ReviewRow {
  凭证号: string
  摘要: string
  科目: string
  金额: number | null
  业务分类: string
  置信度: string
  校准概率: number | null
  模型Top3: string
  判定来源: string
  后处理: string
}

export interface RowPage {
  total: number
  offset: number
  limit: number
  level: string
  bucket: string
  items: ReviewRow[]
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

export interface FieldDef {
  key: string
  label: string
  required: boolean
}

// ============================================================================
// 编排层（jobflow）—— 与 api/flow_api.py 一一对应
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
  awaiting: string | null
}
