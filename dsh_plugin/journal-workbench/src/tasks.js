import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync, copyFileSync, statSync } from 'node:fs'
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

/**
 * 配账阶段落盘的「已解析账」：`raw.parquet` + `schema.json`（+ 科目组合）。
 *
 * 读表 + 认列只做这一次。之后四个功能都从这里载入——不再各自解析 xlsx，
 * 也就不会各认各的列。格式选 parquet 不选 csv：列有类型（日期是日期、
 * 金额是数字）、凭证号的前导零不会丢、没有 GBK/UTF-8 的编码歧义，
 * 同一张表反复读只要几毫秒（csv 每次都要重新推断类型）。
 */
const PREPARED_DIR = 'prepared'

/** 哪些字段进任务摘要卡（别把整个 mapping 塞给前端）。 */
function summary(doc, runs) {
  return {
    id: doc.id,
    name: doc.name,
    // 任务的归属层（`journal_io/contracts.py` 的域）。老任务没有这个字段：
    // 一律补成 ledger —— 那时候只有账务功能，迁移是单调的，不需要人工判断。
    domain: doc.domain || 'ledger',
    filename: doc.filename,
    file: doc.file,
    mapping: doc.mapping || {},
    bytes: doc.bytes || 0,
    sha256: doc.sha256 || '',
    created_at: doc.created_at || '',
    combo: doc.combo || null,
    // 账解析好没有：没有就说明这个任务是老版本建的，开工前要先补一次
    prepared: Boolean(doc.prepared),
    prepared_at: doc.prepared?.at || '',
    // 配账时归一化之后仍不是标准科目的写法：页面据此让人先对一下科目
    unknown: doc.unknown || [],
    // 这个任务绑的复核会话（第一次停到复核位时建），见 notify.js
    session: doc.session || '',
    runs,
  }
}

export function createTasks({ settings }) {
  /** `.trash` 摘要的缓存：jobsDir → { sig: mtime, info } */
  const trashCache = new Map()

  async function root(agent) {
    const repo = await settings.repoFor(agent)
    return { repo, jobs: settings.jobsFor(repo) }
  }

  async function list(agent) {
    const { jobs: jobsDir } = await root(agent)
    const dir = join(jobsDir, TASKS_DIR)
    const jobs = scanJobs(jobsDir)

    if (!existsSync(dir)) return { tasks: [], jobs, trash: trashInfo(jobsDir) }
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
    return { tasks, jobs, trash: trashInfo(jobsDir) }
  }

  async function save(agent, { file, name, mapping, domain }) {
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
      // 层决定这份数据"是什么"：账务域 = 序时账，成本域 = 采购明细。
      // 缺省 ledger 是老调用方（命令行 / 早一版页面）的兼容。
      domain: String(domain || 'ledger').trim() || 'ledger',
      filename,
      file: target,
      mapping: mapping && typeof mapping === 'object' ? mapping : {},
      bytes: statSize(target),
      sha256: sha256Of(target),
      created_at: new Date().toISOString(),
    }
    // 配好就跑一次：解析这份账 + 算科目组合。之后所有功能都从这里载入
    const result = await prepareTask(agent, doc).catch((error) => ({ ok: false, error: error.message }))
    doc.combo = result
    doc.prepared = result.ok === false ? null : { at: result.at, rows: result.rows }
    doc.unknown = result.unknown || []
    doc.standard = result.standard || []
    writeFileSync(join(folder, 'task.json'), JSON.stringify(doc, null, 2), 'utf8')
    return { ...summary(doc, {}), standard: doc.standard }
  }

  /** 重算某个任务的组合与已解析账（页面上的"重算"按钮）。 */
  async function prepare(agent, id) {
    const { jobs: jobsDir } = await root(agent)
    const folder = join(jobsDir, TASKS_DIR, String(id || ''))
    const meta = join(folder, 'task.json')
    if (!existsSync(meta)) throw new Error('任务不存在')
    const doc = JSON.parse(readFileSync(meta, 'utf8'))
    doc.combo = await prepareTask(agent, doc)
    doc.prepared = { at: doc.combo.at, rows: doc.combo.rows }
    doc.unknown = doc.combo.unknown || []
    doc.standard = doc.combo.standard || []
    writeFileSync(meta, JSON.stringify(doc, null, 2), 'utf8')
    return { ...summary(doc, {}), standard: doc.standard }
  }

  /**
   * 把「账上写法 → 标准一级科目」写进别名表，然后重新解析这份账。
   *
   * 别名表是**全局**的（所有账共用），所以这一步只在配账时由人确认：写进去
   * 之后，别的账里同样的写法也会按新映射归一。写表由 Python 那边做（校验目标
   * 科目合法、原子写盘、清缓存），这里只负责把它接回这份账的 prepared。
   */
  async function mapSubjects(agent, id, pairs) {
    const { repo, jobs: jobsDir } = await root(agent)
    const folder = join(jobsDir, TASKS_DIR, String(id || ''))
    const meta = join(folder, 'task.json')
    if (!existsSync(meta)) throw new Error('任务不存在')

    const entries = Object.entries(pairs || {})
      .map(([raw, target]) => [String(raw || '').trim(), String(target || '').trim()])
      .filter(([raw, target]) => raw && target)
    if (!entries.length) throw new Error('没有要写入的对应关系')

    const python = settings.pythonFor(repo)
    const args = ['alias', '--set', ...entries.map(([raw, target]) => `${raw}=${target}`)]
    const result = await runJournal({ python, repo, args, timeoutMs: 60_000 })
    if (!result.ok) throw new Error(result.error || '写别名表失败')
    const value = result.value || {}
    if (value.ok === false) throw new Error(value.error || '写别名表失败')

    // 别名表变了：重新解析这份账（归一化 + 未知清单都重算），页面据此刷新
    const doc = JSON.parse(readFileSync(meta, 'utf8'))
    doc.combo = await prepareTask(agent, doc)
    doc.prepared = { at: doc.combo.at, rows: doc.combo.rows }
    doc.unknown = doc.combo.unknown || []
    doc.standard = doc.combo.standard || []
    writeFileSync(meta, JSON.stringify(doc, null, 2), 'utf8')
    return { ...summary(doc, {}), standard: doc.standard, alias: value }
  }

  /**
   * 已解析账的目录；老任务还没解析过就先补一次（0.6 秒），再返回。
   *
   * 为什么"开工时才补"而不是建任务时就强制：`prepared/` 是这次改动才有的，
   * 之前建的任务没有。让它们一开工自动补上，比让用户去点一次「重算」友好得多，
   * 也比回退到"每个功能各自解析 xlsx"一致。
   */
  async function ensure(agent, id) {
    const { jobs: jobsDir } = await root(agent)
    const folder = join(jobsDir, TASKS_DIR, String(id || ''))
    if (!existsSync(join(folder, 'task.json'))) throw new Error('任务不存在')
    const dir = join(folder, PREPARED_DIR)
    if (existsSync(join(dir, 'raw.parquet')) && existsSync(join(dir, 'schema.json'))) {
      return dir
    }
    const doc = JSON.parse(readFileSync(join(folder, 'task.json'), 'utf8'))
    doc.combo = await prepareTask(agent, doc)
    doc.prepared = { at: doc.combo.at, rows: doc.combo.rows }
    doc.unknown = doc.combo.unknown || []
    doc.standard = doc.combo.standard || []
    writeFileSync(join(folder, 'task.json'), JSON.stringify(doc, null, 2), 'utf8')
    return dir
  }

  /**
   * 删任务 = **真删**（任务目录 + 它名下的运行记录）。
   *
   * 以前是挪进 `_storage_jobs/.trash/`，理由是"点错一下找不回来"。但那条路只有
   * 半个：页面没有回收站、命令行也没有恢复入口，删掉的东西只是换个地方继续占着
   * 几十兆，谁都不会再去打开它。放弃一半的保护，比没有保护更糟——它给人"还能找回来"
   * 的错觉，同时把目录越堆越大。
   *
   * 所以这里只留一条路：删就删干净，误删靠页面上的两步确认挡住（先点「删除」，
   * 顶上的红条写清要删什么，再点「删除 ✓」）。旧版本已经压进 `.trash` 的东西
   * 不动——那是别人的数据，只报大小，清不清由人点 `purgeTrash()`。
   */
  async function remove(agent, id) {
    const taskId = String(id || '')
    if (!/^task_[a-z0-9_]+$/.test(taskId)) throw new Error('任务 id 不合法')
    const { jobs: jobsDir } = await root(agent)
    const folder = join(jobsDir, TASKS_DIR, taskId)
    if (!existsSync(folder)) throw new Error('任务不存在')

    let bytes = dirSize(folder)
    // 先删运行记录，再删任务自己：中途出错时任务还在，页面不会显示"没了但产物还在"
    let dropped = 0
    for (const job of scanJobs(jobsDir)) {
      if (job.task !== taskId) continue
      if (!/^[A-Za-z0-9_-]+$/.test(job.job_id)) continue
      const target = join(jobsDir, job.job_id)
      bytes += dirSize(target)
      rmSync(target, { recursive: true, force: true })
      dropped += 1
    }
    rmSync(folder, { recursive: true, force: true })
    return { ok: true, jobs: dropped, bytes }
  }

  /**
   * 旧版本留下的 `.trash` 还有多少：只统计，不碰。
   *
   * 列任务时顺手带上，页面在右栏底下显示一行"回收站残留 N 项 · 32MB"。
   * 留着不删是因为"删掉它们"这个决定应该由人点，不该由升级悄悄做掉。
   *
   * 结果按 `.trash` 的 mtime 缓存：页面每 5 秒列一次任务，而量一遍 32MB 目录
   * 要爬几万个 stat——不缓存的话这一行提示会变成一个常驻的磁盘扫描。
   * （现在没有任何代码往 `.trash` 里写，mtime 变了只可能是人删了或清了。）
   */
  function trashInfo(jobsDir) {
    const bin = join(jobsDir, TRASH_DIR)
    if (!existsSync(bin)) {
      trashCache.delete(jobsDir)
      return { count: 0, bytes: 0 }
    }
    let sig = ''
    try {
      sig = String(statSync(bin).mtimeMs)
    } catch {
      sig = ''
    }
    const hit = sig ? trashCache.get(jobsDir) : null
    if (hit && hit.sig === sig) return hit.info

    let count = 0
    let bytes = 0
    try {
      for (const entry of readdirSync(bin, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue
        count += 1
        bytes += dirSize(join(bin, entry.name))
      }
    } catch {
      return { count: 0, bytes: 0 }
    }
    const info = { count, bytes }
    if (sig) trashCache.set(jobsDir, { sig, info })
    return info
  }

  /** 清空 `.trash`（页面上的显式动作，不是删除任务的副产品）。 */
  async function purgeTrash(agent) {
    const { jobs: jobsDir } = await root(agent)
    const info = trashInfo(jobsDir)
    if (!info.count) return { ok: true, ...info }
    rmSync(join(jobsDir, TRASH_DIR), { recursive: true, force: true })
    return { ok: true, ...info }
  }

  /**
   * 跑 `journal prepare`：解析账（raw.parquet + schema.json）+ 原生账上的科目组合。
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
    const out = join(folder, PREPARED_DIR)
    const args = ['prepare', doc.file, '--out', out]
    if (doc.mapping && Object.keys(doc.mapping).length) {
      const tmp = join(folder, '.mapping.json')
      writeFileSync(tmp, JSON.stringify(doc.mapping), 'utf8')
      args.push('--mapping', `@${tmp}`)
    }
    const result = await runJournal({ python, repo, args, timeoutMs: 300_000 })
    if (!result.ok) throw new Error(result.error || 'prepare 失败')
    const value = result.value || {}
    if (value.ok === false) throw new Error(value.error || 'prepare 失败')
    return { ...value, at: new Date().toISOString() }
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

  /** 记下这个任务的复核会话（连带建它时用的 preset）；任务不在了返回 false。 */
  function setSession(repo, id, sessionId, preset) {
    const file = metaPath(repo, id)
    try {
      const doc = JSON.parse(readFileSync(file, 'utf8'))
      doc.session = String(sessionId || '')
      doc.session_preset = String(preset || '')
      writeFileSync(file, JSON.stringify(doc, null, 2), 'utf8')
      return true
    } catch {
      return false
    }
  }

  /**
   * 这个任务的复核会话是用哪个 preset 建的；老任务没有这个字段 → 空串。
   *
   * 用途是"会话能力不够就换一个"：preset 决定会话有没有读文件、跑脚本的工具，
   * 换了配置（或老会话压根没带 preset）就得重建，不能接着用一个没有工具的会话。
   */
  function presetOf(repo, id) {
    try {
      const doc = JSON.parse(readFileSync(metaPath(repo, id), 'utf8'))
      return String(doc.session_preset || '')
    } catch {
      return ''
    }
  }

  return { list, save, prepare, mapSubjects, ensure, remove, purgeTrash,
           sessionOf, presetOf, setSession }
}

/**
 * 目录占多少字节（递归）。
 *
 * 只为了两件事：删任务时告诉人"释放了多少"，以及旧 `.trash` 的残留有多大。
 * 读不到的文件直接跳过——统计数字比"因为一个坏链接报错"重要。
 */
function dirSize(path) {
  let total = 0
  let entries
  try {
    entries = readdirSync(path, { withFileTypes: true })
  } catch {
    return 0
  }
  for (const entry of entries) {
    const child = join(path, entry.name)
    try {
      if (entry.isDirectory()) total += dirSize(child)
      else total += statSync(child).size
    } catch {
      // 删了一半/没权限：算不进去就行
    }
  }
  return total
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
