import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync, copyFileSync, statSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { basename, dirname, join } from 'node:path'

import { runJournal } from './cli.js'
import { scanJobs } from './store.js'

/**
 * 任务（task）= 一份账 + 在这份账上跑过的所有功能。
 *
 * 为什么要它：几个功能对**同一份账**干活，而且互相有依赖（现金流要等清洗，
 * 分录测试要等清洗 + 对方科目）。每个功能各自上传一次，就会出现"这份账配的是
 * 这张表、那份账配的是另一张表"的混乱。
 *
 * 所以：一个任务配一次账，之后几个功能都从它起跑；要换账就再建一个任务。
 * 任务目录放在 `_storage_jobs/.tasks/<id>/`，原件**复制**进去——原文件被挪走、
 * 被改名都不影响已经建好的任务。
 *
 * 任务和运行记录的归属是**显式**的：建任务时 `--option task=<id>` 记进 state.json
 * （这个选项不进阶段哈希，见 jobflow/job.py 的 `_HASH_IGNORED_OPTIONS`）。
 * 老任务没有这个字段，才退回按 sha256 认——同一份账被建成两个任务时，
 * 靠 sha 认会把运行记录算到两个任务头上，所以只当兼容留一条路。
 */

const TASKS_DIR = '.tasks'
const TRASH_DIR = '.trash'

/** 哪些字段进任务摘要卡（别把整个 mapping 塞给前端）。 */
function summary(doc, runs) {
  return {
    id: doc.id,
    name: doc.name,
    filename: doc.filename,
    file: doc.file,
    mapping: doc.mapping || {},
    bytes: doc.bytes || 0,
    sha256: doc.sha256 || '',
    created_at: doc.created_at || '',
    combo: doc.combo || null,
    // 这个任务绑的复核会话（第一次停到复核位时建），见 notify.js
    session: doc.session || '',
    runs,
  }
}

export function createTasks({ settings }) {
  async function root(agent) {
    const repo = await settings.repoFor(agent)
    return { repo, jobs: settings.jobsFor(repo) }
  }

  async function list(agent) {
    const { jobs: jobsDir } = await root(agent)
    const dir = join(jobsDir, TASKS_DIR)
    const jobs = scanJobs(jobsDir)

    if (!existsSync(dir)) return { tasks: [], jobs }
    const tasks = []
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      const meta = join(dir, entry.name, 'task.json')
      if (!existsSync(meta)) continue
      let doc
      try {
        doc = JSON.parse(readFileSync(meta, 'utf8'))
      } catch {
        continue
      }
      const runs = {}
      for (const job of jobs) {
        // 显式归属优先；老任务没记归属，才按哈希认（且不能抢别人已认领的）
        const mine = job.task
          ? job.task === doc.id
          : (!!job.sha256 && job.sha256 === doc.sha256)
        if (!mine) continue
        const previous = runs[job.feature]
        // 同一个功能跑过几次就取最新的一个
        if (!previous || String(job.created_at) > String(previous.created_at)) {
          runs[job.feature] = job
        }
      }
      tasks.push(summary(doc, runs))
    }
    tasks.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
    return { tasks, jobs }
  }

  async function save(agent, { file, name, mapping }) {
    const path = String(file || '').trim()
    if (!path || !existsSync(path)) throw new Error(`文件不存在：${path}`)
    const { jobs: jobsDir } = await root(agent)

    const id = `task_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
    const folder = join(jobsDir, TASKS_DIR, id)
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
    // 配好就跑一次科目组合：它是"账本身的属性"，几个功能都要用
    doc.combo = await prepareTask(agent, doc).catch((error) => ({ ok: false, error: error.message }))
    writeFileSync(join(folder, 'task.json'), JSON.stringify(doc, null, 2), 'utf8')
    return summary(doc, {})
  }

  /** 重算某个任务的组合（页面上的"重算"按钮）。 */
  async function prepare(agent, id) {
    const { jobs: jobsDir } = await root(agent)
    const folder = join(jobsDir, TASKS_DIR, String(id || ''))
    const meta = join(folder, 'task.json')
    if (!existsSync(meta)) throw new Error('任务不存在')
    const doc = JSON.parse(readFileSync(meta, 'utf8'))
    doc.combo = await prepareTask(agent, doc)
    writeFileSync(meta, JSON.stringify(doc, null, 2), 'utf8')
    return summary(doc, {})
  }

  /**
   * 删任务：连同它名下的运行记录一起挪进 `_storage_jobs/.trash/<时间>-<id>/`。
   *
   * 不直接 rm：一个任务目录里躺着几份产物和几十个中间文件，页面点错一下
   * 就再也找不回来。回收站只是个目录，用户想彻底删自己去删。
   */
  async function remove(agent, id) {
    const taskId = String(id || '')
    if (!/^task_[a-z0-9_]+$/.test(taskId)) throw new Error('任务 id 不合法')
    const { jobs: jobsDir } = await root(agent)
    const folder = join(jobsDir, TASKS_DIR, taskId)
    if (!existsSync(folder)) throw new Error('任务不存在')

    const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
    const bin = join(jobsDir, TRASH_DIR, `${stamp}-${taskId}`)
    mkdirSync(bin, { recursive: true })

    // 先搬运行记录，再搬任务自己：中途出错时任务还在，页面不会显示"没了但产物还在"
    let moved = 0
    for (const job of scanJobs(jobsDir)) {
      if (job.task !== taskId) continue
      renameSync(join(jobsDir, job.job_id), join(bin, job.job_id))
      moved += 1
    }
    renameSync(folder, join(bin, taskId))
    return { ok: true, trashed: bin, jobs: moved }
  }

  /**
   * 跑 `journal prepare`：原生账上的科目组合（一级 / 一级+明细）。
   *
   * 不做 SS 归一化——归一化后的"模式"是对方科目分析自己的逻辑，不是账的属性。
   *
   * 走 `runJournal` 而不是自己 spawn 一份：管道里的中文默认按 cp936 编码，
   * 那边固定了 `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1`，自己抄一个精简版就会
   * 把 RESULT 里带中文的字段解成乱码（写进 task.json 之后还看不出来）。
   */
  async function prepareTask(agent, doc) {
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
    const result = out.value || {}
    if (result.ok === false) throw new Error(result.error || 'prepare 失败')
    return { ...result, at: new Date().toISOString() }
  }

  function metaPath(repo, id) {
    return join(settings.jobsFor(repo), TASKS_DIR, String(id || ''), 'task.json')
  }

  /** 这个任务绑的复核会话 id（还没停过复核位就是空串）。 */
  function sessionOf(repo, id) {
    try {
      const doc = JSON.parse(readFileSync(metaPath(repo, id), 'utf8'))
      return String(doc.session || '')
    } catch {
      return ''
    }
  }

  /** 记下这个任务的复核会话；任务已经不在了就当没这回事（返回 false）。 */
  function setSession(repo, id, sessionId) {
    const file = metaPath(repo, id)
    try {
      const doc = JSON.parse(readFileSync(file, 'utf8'))
      doc.session = String(sessionId || '')
      writeFileSync(file, JSON.stringify(doc, null, 2), 'utf8')
      return true
    } catch {
      return false
    }
  }

  return { list, save, prepare, remove, sessionOf, setSession }
}

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
