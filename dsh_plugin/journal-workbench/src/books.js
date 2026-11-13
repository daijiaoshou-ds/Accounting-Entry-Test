import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync, copyFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'

import { runJournal } from './cli.js'

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
    // 配好就跑一次科目组合：它是"账本身的属性"，四个功能都要用
    doc.combo = await prepareBook(agent, doc).catch((error) => ({ ok: false, error: error.message }))
    writeFileSync(join(folder, 'book.json'), JSON.stringify(doc, null, 2), 'utf8')
    return doc
  }

  /** 重算某个账套的组合（页面上的"重算"按钮）。 */
  async function prepare(agent, id) {
    const { dir } = await root(agent)
    const folder = join(dir, String(id || ''))
    const meta = join(folder, 'book.json')
    if (!existsSync(meta)) throw new Error('账套不存在')
    const doc = JSON.parse(readFileSync(meta, 'utf8'))
    doc.combo = await prepareBook(agent, doc)
    writeFileSync(meta, JSON.stringify(doc, null, 2), 'utf8')
    return doc
  }

  /**
   * 跑 `journal prepare`：原生账上的科目组合（一级 / 一级+明细）。
   *
   * 不做 SS 归一化——归一化后的"模式"是对方科目分析自己的逻辑，不是账的属性。
   *
   * 走 `runJournal` 而不是自己 spawn 一份：管道里的中文默认按 cp936 编码，
   * 那边固定了 `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1`，自己抄一个精简版就会
   * 把 RESULT 里带中文的字段解成乱码（写进 book.json 之后还看不出来）。
   */
  async function prepareBook(agent, doc) {
    const { repo } = await root(agent)
    const python = settings.pythonFor(repo)
    const folder = dirname(doc.file)
    const args = ['prepare', doc.file, '--out', folder]
    if (doc.mapping && Object.keys(doc.mapping).length) {
      const tmp = join(folder, '.mapping.json')
      writeFileSync(tmp, JSON.stringify(doc.mapping), 'utf8')
      args.push('--mapping', `@${tmp}`)
    }
    const out = await runJournal({ python, repo, args, timeoutMs: 300_000 })
    if (!out.ok) throw new Error(out.error || 'prepare 失败')
    const summary = out.value || {}
    if (summary.ok === false) throw new Error(summary.error || 'prepare 失败')
    return { ...summary, at: new Date().toISOString() }
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

  return { list, save, prepare, remove }
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
