import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { isAbsolute, join, resolve } from 'node:path'

import { JournalError, runJournal } from './cli.js'
import {
  listFiles, readDecisions, readRequest, readState, scanJobs, stageRows,
} from './store.js'
import {
  clamp,
  formatFeatures,
  formatInbox,
  formatInspect,
  formatNext,
  formatReport,
  formatStart,
  formatSubmit,
} from './format.js'

/** `journal features` 要把每个功能都 import 一遍（连带 torch，约 5s），缓存一会儿。 */
const FEATURES_TTL_MS = 10 * 60 * 1000

/** `journal_next` 在任务跑着时最多等多久：够一次读表+一段分类推进。 */
const WAIT_MS = 40_000

/** 轮询 state.json 的间隔。文件很小，1.5s 一次对磁盘没压力。 */
const POLL_MS = 1_500

/**
 * 操作层：工具只做"参数校验 + 排版"，真正的事都在这里。
 *
 * 放在一处的理由是 `references/user-actions.md` 那条规矩：**一个操作只实现一次**，
 * UI 按钮和 agent 工具都调同一个方法。M1 只有工具这一侧，M2 加页面时
 * `/plugin/journal/*` 路由直接复用这些函数，不另写一套。
 */
export function createOps(settings, runner, notifier) {
  const featureCache = new Map()

  async function ctxFor(agent) {
    const repo = await settings.repoFor(agent)
    return { repo, python: settings.pythonFor(repo), jobs: settings.jobsFor(repo) }
  }

  async function call(ctx, args, timeoutMs = 180_000) {
    const result = await runJournal({
      python: ctx.python,
      repo: ctx.repo,
      args,
      timeoutMs,
    })
    if (!result.ok) throw new JournalError(result.error)
    const value = result.value || {}
    if (value.ok === false) throw new JournalError(value.error || '命令失败')
    return value
  }

  function startBackground(ctx, jobId, label) {
    runner.start({
      jobId,
      label,
      repo: ctx.repo,
      python: ctx.python,
      args: ['resume', jobId],
      timeoutMs: settings.timeoutMs,
    })
  }

  /**
   * "还在算吗"。
   *
   * 光看 `runner.isRunning` 不够：python 写完 `state.json`（已经停在复核位）到进程
   * 真正退出之间还有几十毫秒，这时候状态说"该你判了"、runner 却说"在跑"。
   * 所以**状态是权威**：已经停下（复核/完成/失败）就不算忙，哪怕进程还没退干净。
   */
  function isBusy(jobId, state) {
    if (!runner.isRunning(jobId)) return false
    const status = state?.status
    return status !== 'awaiting_review' && status !== 'done' && status !== 'failed'
  }

  // ── 发现 ────────────────────────────────────────────────────────────────

  async function features(agent) {
    const ctx = await ctxFor(agent)
    const cached = featureCache.get(ctx.repo)
    if (cached && Date.now() - cached.at < FEATURES_TTL_MS) {
      return clamp(formatFeatures(cached.value))
    }
    const value = await call(ctx, ['features'])
    featureCache.set(ctx.repo, { at: Date.now(), value })
    return clamp(formatFeatures(value))
  }

  async function inspect({ file, feature }, agent) {
    return clamp(formatInspect(await inspectRaw({ file, feature }, agent)))
  }

  /** 和 `inspect` 同一个动作，只是把原始 JSON 交出去（页面要用它画映射表）。 */
  async function inspectRaw({ file, feature }, agent) {
    const ctx = await ctxFor(agent)
    const target = resolveInput(file, ctx.repo)
    return call(
      ctx,
      ['inspect', target, '--feature', String(feature || 'cleaning').trim()],
      300_000,
    )
  }

  // ── 建任务 ──────────────────────────────────────────────────────────────

  async function start({ feature, file, upstream, mapping, max_review, task }, agent) {
    return clamp(formatStart(await startRaw({ feature, file, upstream, mapping, max_review, task }, agent)))
  }

  /** 建任务并在后台起跑，返回 `journal create` 的原始 payload（页面要 job_id）。 */
  async function startRaw({ feature, file, upstream, mapping, max_review, task }, agent) {
    const featureId = String(feature || '').trim()
    if (!featureId) throw new JournalError('要给 feature；先用 journal_features 看有哪些功能。')

    const ctx = await ctxFor(agent)
    const args = ['create']
    if (file) args.push(resolveInput(file, ctx.repo))
    args.push('--feature', featureId)

    const up = String(upstream || '').trim()
    if (up) args.push('--option', `upstream=${up}`)
    // 归属哪个任务：只用来把运行记录归到任务的账下面，不影响阶段哈希
    const taskId = String(task || '').trim()
    if (taskId) args.push('--option', `task=${taskId}`)
    if (max_review) args.push('--max-review', String(Math.max(1, Math.floor(Number(max_review)))))
    const map = normalizeMapping(mapping)
    if (map) args.push('--mapping', JSON.stringify(map))

    // `create` 只建不跑：先拿到 job_id，才能在它跑的同时查状态（`run` 会阻塞到复核位）
    const value = await call(ctx, args, 300_000)
    // 记下"谁发起的"：跑完停在复核位时，只叫这个会话的 agent（M3）
    notifier?.remember(value.job_id, agent)
    // start 不算"正在驱动"：建完任务 agent 就该走了，之后正需要有人叫它回来
    notifier?.touch(value.job_id, 'start')
    startBackground(ctx, value.job_id, '开始跑')
    return value
  }

  // ── 进度与待办 ──────────────────────────────────────────────────────────

  async function next({ job_id, limit }, agent) {
    const ctx = await ctxFor(agent)
    const jobId = String(job_id || '').trim()

    if (!jobId) {
      const value = await call(ctx, ['inbox'], 60_000)
      return clamp(formatInbox(value))
    }

    // agent 正在盯着这个任务：停位提醒就不必再发了
    notifier?.touch(jobId, 'drive')

    // 后台在跑：等一小会儿，阶段一推进就立刻返回。
    // 不让 agent 自己空转轮询——一次工具调用等 40 秒，比"还在跑"重复十遍省得多。
    if (runner.isRunning(jobId)) {
      await waitForProgress(ctx.jobs, jobId, runner, WAIT_MS)
    }

    const state = readState(ctx.jobs, jobId)

    if (isBusy(jobId, state)) {
      // 真在算：不调 CLI（省 2~3 秒，也不跟正在写 state 的进程抢），
      // 直接读 state.json + runner 攒的 stderr 尾巴。
      return clamp(formatNext({
        next: { job_id: jobId, status: state?.status || 'running' },
        state,
        stages: stageRows(state),
        running: runner.snapshot(jobId),
      }))
    }

    const rows = Math.min(60, Math.max(1, Math.floor(Number(limit) || settings.reviewLimit)))
    let value
    try {
      value = await call(ctx, ['next', jobId, '--limit', String(rows)])
    } catch (error) {
      // 失败的任务里 `journal next` 返回 ok:false（只有 error 没有时间线）。
      // 这时从 state.json 补一条阶段链出来——"哪一步炸的"比一句异常有用。
      if (state?.status === 'failed') {
        return clamp(formatNext({
          next: { job_id: jobId, status: 'failed', error: state.error },
          state,
          stages: stageRows(state),
        }))
      }
      throw error
    }
    return clamp(formatNext({
      next: value,
      state,
      stages: stageRows(state),
      running: null,
      request: readRequest(ctx.jobs, jobId, value?.next?.stage),
    }))
  }

  // ── 交结论 ──────────────────────────────────────────────────────────────

  async function submit({ job_id, stage, decisions, waive }, agent) {
    const { value, jobId } = await submitRaw({ job_id, stage, decisions, waive }, agent)
    return clamp(formatSubmit(value, { resumed: true, jobId }))
  }

  /** 和 `submit` 同一个动作，返回回执原文（页面要显示采纳/驳回明细）。 */
  async function submitRaw({ job_id, stage, decisions, waive }, agent) {
    const jobId = String(job_id || '').trim()
    if (!jobId) throw new JournalError('要给 job_id。')

    const ctx = await ctxFor(agent)
    if (isBusy(jobId, readState(ctx.jobs, jobId))) {
      throw new JournalError('任务正在算，等它停到复核位再交结论（用 journal_next 看进度）。')
    }
    const sid = String(stage || '').trim()
    let cleanup = () => {}

    try {
      let value
      if (waive) {
        if (!sid) throw new JournalError('waive 要说清楚是哪个阶段（journal_next 的输出里有）。')
        value = await call(ctx, ['submit', jobId, '--empty', '--stage', sid,
          '--by', 'ai', '--no-advance'])
      } else {
        const list = Array.isArray(decisions) ? decisions : []
        if (!list.length) {
          throw new JournalError('decisions 是空的；一条都不改就用 waive（要和 stage 一起给）。')
        }
        const { file, dir } = writeDecisionFile({ stage: sid, decisions: list })
        cleanup = () => rmSync(dir, { recursive: true, force: true })
        const args = ['submit', jobId, '--file', file, '--by', 'ai', '--no-advance']
        value = await call(ctx, args)
      }
      // 只落盘不往下跑（`--no-advance`）：下游可能是几分钟的分类，得丢后台。
      notifier?.remember(jobId, agent)
      // 它刚交完、马上就轮到它接着看：这时候别再往它 inbox 里塞一条（会过时）
      notifier?.touch(jobId, 'drive')
      startBackground(ctx, jobId, '继续跑')
      return { value, jobId }
    } finally {
      cleanup()
    }
  }

  // ── 页面用的只读视图 ────────────────────────────────────────────────────

  /** 任务清单 + 目录（页面顶部的下拉、时间线都从这里来）。 */
  async function overview(agent) {
    const ctx = await ctxFor(agent)
    return {
      repo: ctx.repo,
      jobs: ctx.repo === null ? [] : scanJobs(ctx.jobs),
    }
  }

  /** 一个任务的完整视图：状态、阶段链、下一步、在跑进度、产物。 */
  async function jobView({ job_id }, agent) {
    const jobId = String(job_id || '').trim()
    const ctx = await ctxFor(agent)
    const state = readState(ctx.jobs, jobId)
    if (!state) throw new JournalError(`找不到任务 ${jobId}（目录 ${ctx.jobs}）`)
    return {
      job_id: jobId,
      feature: state.feature,
      feature_name: state.feature_name || state.feature,
      status: state.status || 'created',
      error: state.error || '',
      created_at: state.created_at || '',
      updated_at: state.updated_at || '',
      input: state.input || {},
      options: state.options || {},
      stages: stageRows(state),
      running: runner.snapshot(jobId),
      files: listFiles(state, ctx.jobs),
    }
  }

  /** 某个 AI 阶段的待办面（分页 + 已交结论 + 列定义所需的一切）。 */
  async function reviewView({ job_id, stage, offset = 0, limit = 200, q = '' }, agent) {
    const jobId = String(job_id || '').trim()
    const ctx = await ctxFor(agent)
    const state = readState(ctx.jobs, jobId)
    const sid = String(stage || '').trim() || awaitingStage(state)
    if (!sid) return { job_id: jobId, stage: null, total: 0, items: [], value_enum: [], submitted: {} }

    const doc = readRequest(ctx.jobs, jobId, sid)
    if (!doc) throw new JournalError(`阶段 ${sid} 还没有待办（没跑到，或已提交完）`)

    const submitted = readDecisions(ctx.jobs, jobId, sid)
    let items = Array.isArray(doc.items) ? doc.items : []
    const needle = String(q || '').trim().toLowerCase()
    if (needle) {
      items = items.filter((item) => Object.values(item || {})
        .some((value) => String(value ?? '').toLowerCase().includes(needle)))
    }
    const total = items.length
    const from = Math.max(0, Math.floor(Number(offset) || 0))
    const size = Math.min(500, Math.max(1, Math.floor(Number(limit) || 200)))
    return {
      job_id: jobId,
      stage: sid,
      title: state?.stages?.[sid]?.title || sid,
      help: doc.help || '',
      key_name: doc.key_name || 'key',
      value_enum: Array.isArray(doc.value_enum) ? doc.value_enum : [],
      truncated: Boolean(doc.truncated),
      total,
      offset: from,
      limit: size,
      items: items.slice(from, from + size),
      submitted,
    }
  }

  /** 恢复/续跑（页面上的"重试"按钮）。 */
  async function resume({ job_id }, agent) {
    const jobId = String(job_id || '').trim()
    const ctx = await ctxFor(agent)
    if (isBusy(jobId, readState(ctx.jobs, jobId))) {
      throw new JournalError('任务已经在跑了。')
    }
    notifier?.remember(jobId, agent)
    notifier?.touch(jobId, 'drive')
    startBackground(ctx, jobId, '续跑')
    return { ok: true, job_id: jobId }
  }

  /**
   * 现在就叫醒 agent（页面上的「叫 AI 复核」）。
   *
   * 自动唤醒是"跑完才叫"，这里允许人在任务停着的时候手动叫一次——比如你刚关掉
   * 自动唤醒，或者上一个会话的 agent 已经被别的事占住。
   */
  async function wake({ job_id }, agent) {
    const jobId = String(job_id || '').trim()
    if (!jobId) throw new JournalError('要给 job_id。')
    const ctx = await ctxFor(agent)
    notifier?.remember(jobId, agent)
    const key = await notifier?.notify({ jobId, repo: ctx.repo }, { force: true })
    return key
      ? { ok: true, woken: true, job_id: jobId }
      : { ok: true, woken: false, job_id: jobId }
  }

  // ── 结果 ────────────────────────────────────────────────────────────────

  async function report({ job_id }, agent) {
    const jobId = String(job_id || '').trim()
    if (!jobId) throw new JournalError('要给 job_id。')
    const ctx = await ctxFor(agent)
    const value = await call(ctx, ['report', jobId])
    return clamp(formatReport(value))
  }

  /** 功能清单原文（页面用它画功能卡与阶段链）。 */
  async function featuresRaw(agent) {
    const ctx = await ctxFor(agent)
    const cached = featureCache.get(ctx.repo)
    if (cached && Date.now() - cached.at < FEATURES_TTL_MS) return cached.value
    const value = await call(ctx, ['features'])
    featureCache.set(ctx.repo, { at: Date.now(), value })
    return value
  }

  /** 下载白名单：只有 state 里登记过的产物才给。（页面用） */
  async function resolveDownload({ job_id, key }, agent) {
    const jobId = String(job_id || '').trim()
    const ctx = await ctxFor(agent)
    const state = readState(ctx.jobs, jobId)
    if (!state) throw new JournalError(`找不到任务 ${jobId}`)
    const files = listFiles(state, ctx.jobs)
    const hit = files.find((item) => item.key === key)
    if (!hit) throw new JournalError(`没有产物 ${key}（任务状态 ${state.status}）`)
    return { path: hit.path, filename: hit.filename }
  }

  return {
    features, featuresRaw, inspect, inspectRaw, start, startRaw,
    next, submit, submitRaw, report,
    overview, jobView, reviewView, resume, wake, resolveDownload,
  }
}

function awaitingStage(state) {
  const stages = state?.stages || {}
  return Object.keys(stages).find((id) => stages[id]?.status === 'awaiting') || ''
}

function resolveInput(file, repo) {
  const raw = String(file || '').trim()
  if (!raw) throw new JournalError('没给文件路径。')
  return isAbsolute(raw) ? raw : resolve(repo, raw)
}

const sleep = (ms) => new Promise((done) => {
  const timer = setTimeout(done, ms)
  timer.unref?.()
})

/**
 * 等任务推进一格。
 *
 * 进度的最小单位是**阶段**：`jobflow` 每跑完一个阶段才写一次 state.json，
 * 所以"阶段的条数或总状态变了"就等于"真的往前走了"。不满足就一直等到预算用完，
 * 调用方再拿 stderr 尾巴告诉人跑到了哪。
 */
async function waitForProgress(jobsDir, jobId, runner, budgetMs) {
  const signature = (state) => `${state?.status || ''}#${Object.keys(state?.stages || {}).length}`
  const before = signature(readState(jobsDir, jobId))
  const deadline = Date.now() + budgetMs
  while (Date.now() < deadline && runner.isRunning(jobId)) {
    await sleep(POLL_MS)
    if (signature(readState(jobsDir, jobId)) !== before) return
  }
}

/** 只保留"有值"的映射：空串等于没指定，传给阶段反而会报"表里没有这一列"。 */
function normalizeMapping(mapping) {
  if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) return null
  const out = {}
  for (const [key, value] of Object.entries(mapping)) {
    const column = typeof value === 'string' ? value.trim() : ''
    if (column) out[String(key)] = column
  }
  return Object.keys(out).length ? out : null
}

function writeDecisionFile({ stage, decisions }) {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-journal-'))
  const file = join(dir, 'decisions.json')
  const doc = {
    schema_version: 1,
    stage: stage || '',
    decisions: decisions.map((item) => ({
      key: String(item?.key ?? ''),
      value: String(item?.value ?? ''),
      note: String(item?.note ?? ''),
    })),
    by: 'ai',
  }
  writeFileSync(file, JSON.stringify(doc, null, 2), 'utf8')
  return { file, dir }
}
