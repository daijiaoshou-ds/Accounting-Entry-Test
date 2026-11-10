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
