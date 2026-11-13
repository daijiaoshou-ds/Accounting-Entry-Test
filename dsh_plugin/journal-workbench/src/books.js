import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'

/**
 * 账套（book）= 一份被确认过的序时账：原件副本 + 字段映射。
 *
 * 为什么要它：四个功能都对**同一份账**干活，而且互相有依赖（现金流要等清洗，
 * 分录测试要等清洗 + 对方科目）。每个功能各自上传一次，就会出现"这份账配的是
 * 这张表、那份账配的是另一张表"的混乱。
 *
 * 所以：一个账套配一次，之后四个功能都从它起跑；要换账就再建一个账套。
 * 账套目录放在 `_storage_jobs/.books/<id>/`，原件**复制**进去——原文件被挪走、
 * 被改名都不影响已经建好的账套。
 *
 * 功能跑出来的任务不写进账套文件，而是**按 sha256 反查**：账套里存一份 sha，
 * 列出任务时扫一遍 `_storage_jobs` 下每个任务的 `state.json`，`input.sha256` 相同的
 * 就算这个账套跑出来的。这样不用维护两份会打架的索引。
 */

const BOOKS_DIR = '.books'

export function createBooks({ settings }) {
  async function root(agent) {
    const repo = await settings.repoFor(agent)
    return { repo, dir: join(settings.jobsFor(repo), BOOKS_DIR) }
  }

  async function list(agent) {
    const { dir } = await root(agent)
    if (!existsSync(dir)) return { books: [], jobs: [] }
    const jobs = scanJobs(join(dir, '..'))

    const books = []
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      const meta = join(dir, entry.name, 'book.json')
      if (!existsSync(meta)) continue
      let doc
      try {
        doc = JSON.parse(readFileSync(meta, 'utf8'))
      } catch {
        continue
      }
      const runs = {}
      for (const job of jobs) {
        if (job.sha256 && job.sha256 === doc.sha256) {
          const previous = runs[job.feature]
          // 同一个功能跑过几次就取最新的一个
          if (!previous || String(job.created_at) > String(previous.created_at)) {
            runs[job.feature] = job
          }
        }
      }
      books.push({ ...doc, runs })
    }
    books.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
    return { books, jobs }
  }

  async function save(agent, { file, name, mapping }) {
    const path = String(file || '').trim()
    if (!path || !existsSync(path)) throw new Error(`文件不存在：${path}`)
    const { dir } = await root(agent)

    const id = `book_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
    const folder = join(dir, id)
    mkdirSync(folder, { recursive: true })

    // 文件名保持原样：导出产物叫 `<原名>_业务分类.xlsx`，别掺 ID
    const filename = basename(path)
    const target = join(folder, filename)
    copyFileSync(path, target)

    const doc = {
      id,
      name: String(name || filename).trim() || filename,
      filename,
      file: target,
      mapping: mapping && typeof mapping === 'object' ? mapping : {},
      bytes: statSize(target),
      sha256: sha256Of(target),
      created_at: new Date().toISOString(),
    }
    writeFileSync(join(folder, 'book.json'), JSON.stringify(doc, null, 2), 'utf8')
    return doc
  }

  async function remove(agent, id) {
    const { dir } = await root(agent)
    const folder = join(dir, String(id || ''))
    if (!String(id || '').startsWith('book_') || !folder.startsWith(dir)) {
      throw new Error('账套 id 不合法')
    }
    if (!existsSync(folder)) return false
    rmSync(folder, { recursive: true, force: true })
    return true
  }

  return { list, save, remove }
}

// ── 小工具（不引第三方包，sha256 用 node 自带） ─────────────────────────

import { createHash } from 'node:crypto'
import { statSync } from 'node:fs'

function sha256Of(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function statSize(path) {
  try {
    return statSync(path).size
  } catch {
    return 0
  }
}

/** 扫 `_storage_jobs` 下每个任务的 state.json，只要列账套要用到的几个字段。 */
function scanJobs(jobsDir) {
  if (!existsSync(jobsDir)) return []
  const out = []
  for (const entry of readdirSync(jobsDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith('.')) continue
    const statePath = join(jobsDir, entry.name, 'state.json')
    if (!existsSync(statePath)) continue
    try {
      const state = JSON.parse(readFileSync(statePath, 'utf8'))
      out.push({
        job_id: state.job_id,
        feature: state.feature,
        feature_name: state.feature_name,
        status: state.status,
        filename: state.input?.filename,
        sha256: state.input?.sha256,
        created_at: state.created_at,
        awaiting: awaitingStage(state),
      })
    } catch {
      // 半截的状态文件，跳过
    }
  }
  return out
}

function awaitingStage(state) {
  for (const [id, stage] of Object.entries(state.stages || {})) {
    if (stage.status === 'awaiting') return id
  }
  return ''
}
