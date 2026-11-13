import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { readDecisions, readRequest, readState } from './store.js'

/**
 * 任务停在复核位，**用户点了「AI 复核这批」**之后，才把任务自己的复核会话叫起来。
 *
 * 为什么不再自动叫：跑一个功能是用户的动作，复核是另一个动作——自动叫等于
 * 跑完就凭空开一个会话替人做判断，用户既没要求也没得选。现在复核位上给两条
 * 出路：让 AI 复核（`force` 走的这条），或者不复核、直接按原判出结果。
 * 配置里 `autoWake: true` 可以回到自动叫的老行为。
 *
 * 为什么不叫发起它的会话：一个会话里干几个任务，上下文会互相污染——
 * 任务 1 判过的口径会被带进任务 2，而两份账的业务未必一样。所以每个任务
 * 绑一个自己的复核会话（`task.json` 的 `session`），第一次复核时建，
 * 之后一直用它；会话没了就 resume，resume 也不行就重建一个。
 *
 * 但**经验是共享的**：复核会话开工前读 `_storage_jobs/.lessons/<功能>.md`
 * （没有就建一个），判完把新踩的坑追加回去。数据（哪个任务、哪张凭证判成什么）
 * 留在任务目录里，经验（怎么判）进这份 md，跨任务复用。
 *
 * 三条纪律没变：
 *  1. **只叫自己人**：优先任务会话；任务会话建不出来/叫不醒，才退回发起它的会话。
 *     没有归属的任务（命令行起的）不猜——多会话时叫错人比不叫更糟。
 *  2. **同一处只叫一次**：键是 `job:阶段:条数:已答条数`。
 *  3. **agent 正在驱动这个任务时不叫**：提交/轮询之后 90 秒内它本来就会接着看。
 */
export function createNotifier(ctx, settings, tasks) {
  const owners = new Map()
  const notified = new Map()
  const lastTouch = new Map()

  /** agent 自己碰过这个任务（提交/轮询/续跑），说明它正在开车。 */
  function touch(jobId, kind) {
    if (jobId) lastTouch.set(String(jobId), { at: Date.now(), kind })
  }

  function remember(jobId, agent) {
    const id = agent?.id
    if (jobId && id) owners.set(String(jobId), String(id))
  }

  function agentFor(jobId) {
    const id = owners.get(String(jobId))
    if (!id) return null
    try {
      return ctx.get('agents')?.get?.(id) || null
    } catch {
      return null
    }
  }

  function driving(jobId) {
    const hit = lastTouch.get(String(jobId))
    if (!hit || hit.kind === 'start') return false
    return Date.now() - hit.at < DRIVE_WINDOW_MS
  }

  /** 新会话用什么模型：跟这个 Harness 的默认选择走，拿不到就不传（走默认）。 */
  function agentOptions() {
    try {
      const selection = ctx.get('agentDefaultModel')?.currentSelection?.()
      if (selection?.provider && selection?.model) {
        return { provider: selection.provider, model: selection.model }
      }
    } catch {
      // 拿不到就用 Harness 的默认，不影响复核
    }
    return {}
  }

  /**
   * 任务的复核会话：活着就用、能 resume 就 resume、都不行就新建一个并记下来。
   *
   * 会话必须带 `agentPreset`。不带它建出来的会话只挂得住插件自己注册的工具
   * （journal_*），读不了文件、跑不了脚本——15 万字的一本账，AI 只能一条条
   * 用眼睛看，这就是"工具不趁手"的根源。老会话（没记 preset）不能接着用，
   * 换一个带工具的（旧会话还在侧栏，只是不再往里投活）。
   *
   * 任何一步失败都返回 null，让调用方退回"叫发起它的会话"——复核不能因为
   * 会话建不出来就没人管。
   */
  async function reviewSession(taskId, repo) {
    if (!taskId) return null
    const service = ctx.get('agents')
    if (!service || typeof service.create !== 'function') return null

    const preset = settings.reviewPreset
    const saved = tasks?.sessionOf?.(repo, taskId) || ''
    const savedPreset = tasks?.presetOf?.(repo, taskId) || ''
    if (saved && savedPreset && savedPreset === preset) {
      const live = service.get?.(saved)
      if (live) return live
      try {
        const handle = await service.resume({ resumeSessionId: saved, agentOptions: agentOptions() })
        return handle?.agent || null
      } catch {
        // 会话被删除/持久化没了：往下建一个新的，别把复核卡死
      }
    }

    try {
      const sessionId = `session-${randomUUID()}`
      const handle = await service.create({
        sessionId,
        meta: { cwd: repo, agentPreset: preset },
        agentOptions: agentOptions(),
      })
      tasks?.setSession?.(repo, taskId, sessionId, preset)
      return handle?.agent || null
    } catch {
      return null
    }
  }

  /**
   * 看一眼这个任务，该叫就叫。
   *
   * @returns 发出去的消息键，或 null（不该叫/叫不动）
   */
  async function notify({ jobId, repo }, { force = false } = {}) {
    if (!force && !settings.autoWake) return null
    if (!jobId) return null
    if (!force && driving(jobId)) return null

    const jobsDir = settings.jobsFor(repo)
    const state = readState(jobsDir, jobId)
    if (!state) return null

    const stages = state.stages || {}
    const awaiting = Object.keys(stages).find((id) => stages[id]?.status === 'awaiting')
    const failed = state.status === 'failed'
    if (!awaiting && !failed) return null

    const count = awaiting ? Number(stages[awaiting]?.facts?.count) || 0 : 0
    // 键里带上"已经答了几条"：待办是分批交的，只答一半就走，阶段还停在复核位，
    // 条数却没变——不带上答过的数，第二次就不会再叫，剩下的永远没人管。
    const answered = awaiting ? Object.keys(readDecisions(jobsDir, jobId, awaiting) || {}).length : 0
    const key = `${jobId}:${awaiting || 'failed'}:${count}:${answered}`
    if (notified.has(key)) return null

    const taskId = state?.options?.task || ''
    let agent = null
    try {
      agent = await reviewSession(taskId, repo)
    } catch {
      agent = null
    }
    // 任务会话建不出来/叫不醒：退回发起它的那个会话，总比没人管强
    if (!agent) agent = agentFor(jobId)
    if (!agent) return null

    const lessons = lessonsFor(jobsDir, state.feature)
    const request = awaiting ? readRequest(jobsDir, jobId, awaiting) : null
    const text = awaiting
      ? reviewText(state, awaiting, count, { repo, jobsDir, lessons, request })
      : failedText(state)

    try {
      agent.followup(message(text))
      notified.set(key, true)
      return key
    } catch {
      // 叫醒失败不该影响任务本身：页面还在，用户还能自己接手
      return null
    }
  }

  return { remember, notify, touch }
}

/** agent 正在开车的窗口：一次提交 → 后台重跑 → 重新停位，通常几秒内完成。 */
const DRIVE_WINDOW_MS = 90_000

/**
 * 提醒末尾那句"先核对"不是客套。
 *
 * `followup` 是排队投递：消息进的是 inbox 的下一轮。排进去的时候任务确实停在复核位，
 * 但等 agent 真读到它，人（或 agent 自己）可能已经把这个任务跑完了——那它就是一条
 * 过时的指令。让它先 `journal_next` 看当前状态，再决定做不做。
 */
const STALE_HINT = '（这是排队投递的提醒，可能已经过时——先 journal_next 看当前状态；'
  + '任务已经跑完就忽略本条。）'

/** 复核经验：一个功能一份，跨任务共享。看的是"怎么判"，不是"这张凭证判成什么"。 */
const LESSON_TEMPLATE = `# 复核经验（%s）

> 每个任务的复核会话开工前先读这一份；判完把新踩的坑追加到文末。
> **这里只写"怎么判"**——判据顺序、容易想错的地方、口径。某张凭证判成什么不要写进来，
> 那是任务自己的数据。

## 判据顺序

1. 先看对方科目（借贷另一侧），再看摘要；摘要里的"报销/付/收"只提示方向，不定业务。
2. 金额与科目冲突时以科目为准；只有一行且金额为 0 的凭证先放"待确认"。

## 踩过的坑

- （追加在这里）
`

function lessonsFor(jobsDir, feature) {
  const dir = join(jobsDir, '.lessons')
  const file = join(dir, `${feature || 'unknown'}.md`)
  try {
    if (!existsSync(file)) {
      mkdirSync(dir, { recursive: true })
      writeFileSync(file, LESSON_TEMPLATE.replace('%s', feature || ''), 'utf8')
    }
  } catch {
    // 写不了经验文件不该挡住复核
  }
  return file
}

/**
 * 叫醒复核会话时发的那条消息：**把任务讲清楚，再让它开工**。
 *
 * 为什么要这么长：只丢一句"有 155 条待办"的话，AI 得自己猜这是什么账、这些桶
 * 是什么意思、判完往哪交。把"账多大、停在哪个阶段、这个功能的判定口径、可选值、
 * 分批规则、经验文件在哪"一次给全，它第一轮就能干正事，不用来回问。
 *
 * 口径不手抄：可选桶直接从待办的 `value_enum` 来（就是引擎允许的取值），
 * 定义从 `bucket_taxonomy_v10.json` 来（引擎用的同一份资产）——抄一份，模型改
 * 了这里就悄悄过期。
 */
function reviewText(state, stage, count, { repo, jobsDir, lessons, request } = {}) {
  const name = state.feature_name || state.feature || ''
  const file = state.input?.filename || ''
  const title = state.stages?.[stage]?.title || stage
  const task = state?.options?.task || ''
  const size = ledgerSize(state)
  const dir = join(jobsDir || '', state.job_id || '')
  const buckets = bucketTable(repo, request?.value_enum || [])
  const brief = featureBrief(repo, state.feature)
  return [
    '【会计分录工作台 · 复核】',
    `${name}${file ? `｜${file}` : ''}${size ? `｜${size}` : ''}${task ? `｜任务 ${task}` : ''}`,
    `停在「${title}」，${count} 条待确认。`,
    brief ? `\n—— 这个功能在做什么 ——\n${brief}` : '',
    buckets ? `\n—— 可选值（引擎的注册表，别自造）——\n${buckets}` : '',
    request?.help ? `\n—— 这一批要你做什么 ——\n${request.help}` : '',
    '',
    '—— 怎么干 ——',
    '1. journal_next 拿一批。每条都带整张凭证的分录：判业务看的是对方科目，不是货币资金那一行。',
    '2. 判完 journal_submit 交一批，再 journal_next。它可能给同一个阶段的下一批，也可能直接给'
      + '下游另一个复核位（一个任务里有几个复核位就要走几遍）——一直到 journal_next 说没有待办、'
      + '任务跑完为止，最后 journal_report 汇报结果。',
    `3. 一条都不改：journal_submit waive=true stage=${stage}。`,
    `4. 条目多、想按模板归组：直接读 ${join(dir, 'reviews', `${stage}.request.json`)}（全量条目），`
      + '或 out/ 下的 parquet，用脚本分析——结论仍走 journal_submit 交回，不要手改 state.json。',
    '5. 你有普通的读写文件 / grep / 跑 shell 和 python 的工具，不必一条条用眼睛看。',
    '',
    `—— 经验（跨任务共享）——\n开工前先读 ${lessons}，判完把新踩的坑追加进去；只写"怎么判"，不写某张凭证判成什么。`,
    task ? `你是任务 ${task} 的复核会话：同一份账的其它功能也会来找你，别的任务的口径不要带进来。` : '',
    STALE_HINT,
  ].filter(Boolean).join('\n')
}

/** 账的规模：read / link / clean 哪个阶段先有就取哪个（接上游时 read 是跳过的）。 */
function ledgerSize(state) {
  for (const id of ['read', 'link', 'clean']) {
    const facts = state.stages?.[id]?.facts || {}
    const rows = Number(facts.rows)
    if (!rows) continue
    const vouchers = Number(facts.vouchers)
    return `${rows.toLocaleString('zh-CN')} 行`
      + (vouchers ? ` / ${vouchers.toLocaleString('zh-CN')} 张凭证` : '')
  }
  return ''
}

/** 功能自带的判定说明：`features/<id>/BRIEF.md`（没有就跳过，不编内容）。 */
function featureBrief(repo, feature) {
  if (!repo || !feature) return ''
  const file = join(repo, 'features', feature, 'BRIEF.md')
  try {
    if (!existsSync(file)) return ''
    const text = readFileSync(file, 'utf8').trim()
    return text.length > 3500 ? `${text.slice(0, 3500)}\n…（全文：${file}）` : text
  } catch {
    return ''
  }
}

/** 可选取值 + 定义：定义从引擎的注册表资产来，查不到就只给名字。
 *
 * 一个都查不到就整段不写：现金流复核位的可选值是"现金流量项目"，桶注册表里没有，
 * 列一串没有解释的名字只是噪声——那边看 `BRIEF.md` 和这一批的 help 更清楚。
 */
function bucketTable(repo, names) {
  const list = Array.isArray(names) ? names.filter(Boolean) : []
  if (!list.length) return ''
  const defs = taxonomyDefs(repo)
  const hit = list.filter((name) => defs.get(name))
  if (!hit.length) return ''
  return list
    .map((name) => (defs.get(name) ? `- ${name}：${defs.get(name)}` : `- ${name}`))
    .join('\n')
}

function taxonomyDefs(repo) {
  const out = new Map()
  if (!repo) return out
  const file = join(repo, 'summary_cleaner', 'v2', 'assets', 'bucket_taxonomy_v10.json')
  try {
    if (!existsSync(file)) return out
    const doc = JSON.parse(readFileSync(file, 'utf8'))
    for (const group of [doc.buckets || {}, doc.parent_nodes || {}]) {
      for (const [name, item] of Object.entries(group)) {
        if (item && typeof item.definition === 'string' && item.definition.trim()) {
          out.set(name, item.definition.trim())
        }
      }
    }
  } catch {
    // 资产不在/改了格式：退化成只给名字，不影响复核
  }
  return out
}

function failedText(state) {
  const name = state.feature_name || state.feature || ''
  const file = state.input?.filename || ''
  return [
    `【会计分录工作台】任务 ${state.job_id}（${name}${file ? ` · ${file}` : ''}）跑失败了：`,
    String(state.error || '（没有 error 字段）').slice(0, 400),
    '用 journal_next 看停在哪一步与阶段时间线；如果只是进程没了，可以 resume 重试。',
    STALE_HINT,
  ].join('\n')
}

/**
 * inbox 里一条用户消息的形状（从 `agent/inbox/spliced` 的日志里抄的）：
 * `{role:'user', id, content:[{type:'text',text}], source:{kind:'user'}}`。
 * `id` 是 MessageId，运行时就是个字符串；`time` 不是必需，但带上便于界面排序。
 */
function message(text) {
  return {
    role: 'user',
    id: `journal-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    time: Date.now(),
    content: [{ type: 'text', text }],
    source: { kind: 'user' },
  }
}
