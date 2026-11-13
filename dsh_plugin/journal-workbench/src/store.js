import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const JOB_ID_RE = /^[0-9A-Za-z_-]{4,64}$/

/**
 * 读 `state.json`（只读）。
 *
 * 任务状态本来就是磁盘上的共享状态：`jobflow` 每跑完一个阶段都原子写一次，
 * 所以"进度"不需要问 Python——直接读文件就是最新的，而且不用为一次轮询
 * 付 2~5 秒的 import torch。
 *
 * 读失败一律返回 null 交给调用方：任务可能刚建、可能被删、可能正在被替换。
 */
export function readState(jobsDir, jobId) {
  if (!jobId || !JOB_ID_RE.test(String(jobId))) return null
  try {
    const text = readFileSync(join(jobsDir, String(jobId), 'state.json'), 'utf8')
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** 阶段链 → `[读表 ✓ 3.2s, 分类 ✓ 62.1s, 复核 ⏸, 写回 ○]` 的中间数据。 */
export function stageRows(state) {
  const stages = state?.stages || {}
  return Object.entries(stages).map(([id, raw]) => {
    const value = raw && typeof raw === 'object' ? raw : {}
    return {
      id,
      title: String(value.title || id),
      status: String(value.status || 'pending'),
      elapsed: typeof value.elapsed === 'number' ? value.elapsed : null,
      note: String(value.note || value.error || ''),
      count: Number(value?.facts?.count) || 0,
      facts: value.facts && typeof value.facts === 'object' ? value.facts : {},
    }
  })
}

/**
 * 某个 AI 阶段落盘的待办原文（`reviews/<stage>.request.json`）。
 *
 * `journal next` 不返回 `value_enum`（那是 `journal request` 才有），但 agent
 * 交结论时必须知道合法取值——所以从这里补一份。读不到就返回 null，不编。
 */
export function readRequest(jobsDir, jobId, stageId) {
  if (!jobId || !stageId) return null
  try {
    const text = readFileSync(
      join(jobsDir, String(jobId), 'reviews', `${stageId}.request.json`),
      'utf8',
    )
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** 某个 AI 阶段已经交过的结论：`{key: value}`（和 `Job.decisions()` 同语义）。 */
export function readDecisions(jobsDir, jobId, stageId) {
  if (!jobId || !stageId) return {}
  try {
    const text = readFileSync(
      join(jobsDir, String(jobId), 'decisions', `${stageId}.json`),
      'utf8',
    )
    const doc = JSON.parse(text)
    const out = {}
    for (const [key, item] of Object.entries(doc.items || {})) {
      if (item && typeof item === 'object' && 'value' in item) out[key] = item.value
    }
    return out
  } catch {
    return {}
  }
}

/** 某个阶段的产物原文（`out/<stage>/<name>` 的表/JSON）。页面不需要，留给以后。 */
export function readArtifactText(jobsDir, jobId, relative) {
  if (!jobId || !relative) return null
  if (relative.includes('..')) return null
  try {
    return readFileSync(join(jobsDir, String(jobId), relative), 'utf8')
  } catch {
    return null
  }
}

/**
 * 可下载的产物。
 *
 * 来源是各阶段 state 里的 `artifacts`（`run_export` 写进去的路径）。
 * 两条过滤规则都是从真数据里踩出来的：
 *
 *   - **跳过 `.parquet`**：那是 `target="archive"` 的内部落盘（clean 阶段的
 *     `result.parquet`），不是给人下载的东西，而且它和导出文件撞同一个 key；
 *   - **同一路径只留一个 key**，优先 `result`：一份 Excel 会被登记成
 *     `result` + `sheet:业务分类结果` + …，全都指向同一个文件。
 *
 * 下载接口据此做白名单，不信任前端传来的任意路径。
 */
export function listFiles(state, jobDir) {
  const stages = state?.stages || {}
  const byKey = new Map()
  for (const raw of Object.values(stages)) {
    const artifacts = raw?.artifacts || {}
    for (const [key, path] of Object.entries(artifacts)) {
      if (typeof path !== 'string') continue
      if (path.toLowerCase().endsWith('.parquet')) continue
      byKey.set(key, { key, path }) // 后面的阶段覆盖前面的（export 在 clean 之后）
    }
  }

  const byPath = new Map()
  for (const item of byKey.values()) {
    const previous = byPath.get(item.path)
    if (!previous || (item.key === 'result' && previous.key !== 'result')) {
      byPath.set(item.path, item)
    }
  }

  const files = []
  for (const item of byPath.values()) {
    try {
      const info = statSync(item.path)
      if (!info.isFile()) continue
      files.push({
        key: item.key,
        label: item.key.startsWith('sheet:') ? item.key.slice(6) : item.key,
        filename: item.path.split(/[\\/]/).pop(),
        bytes: info.size,
        path: item.path,
      })
    } catch {
      // 文件没了（被清理/任务被删）——不列出来
    }
  }
  files.sort((a, b) => (a.key === 'result' ? -1 : b.key === 'result' ? 1 : 0))
  return files
}

/**
 * 扫任务目录（等价于 `jobflow.scan`，但只读 state.json、不 import 任何功能）。
 *
 * 字段和 `journal inbox` 保持一致，页面与 agent 看到的是同一份清单。
 */
export function scanJobs(jobsDir) {
  let entries = []
  try {
    entries = readdirSync(jobsDir, { withFileTypes: true })
  } catch {
    return []
  }
  const items = []
  for (const entry of entries) {
    if (!entry.isDirectory() || !JOB_ID_RE.test(entry.name)) continue
    const state = readState(jobsDir, entry.name)
    if (!state) continue
    const stages = state.stages || {}
    const awaiting = Object.keys(stages).find((id) => stages[id]?.status === 'awaiting') || null
    items.push({
      job_id: state.job_id || entry.name,
      feature: state.feature,
      feature_name: state.feature_name || state.feature,
      filename: state?.input?.filename || '',
      sha256: state?.input?.sha256 || '',
      // 归属哪个任务（账套）。老任务没有这个字段，靠 sha256 兜（见 tasks.js）
      task: state?.options?.task || '',
      status: state.status || 'created',
      created_at: state.created_at || '',
      updated_at: state.updated_at || '',
      awaiting,
      awaiting_count: awaiting ? Number(stages[awaiting]?.facts?.count) || 0 : null,
      clean_status: stages.clean?.status || '',
      done_stages: Object.values(stages).filter(
        (s) => s?.status === 'done' || s?.status === 'skipped',
      ).length,
      total_stages: Object.keys(stages).length,
    })
  }
  items.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
  return items
}


