import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { readDecisions, readState } from './store.js'

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
   * 任何一步失败都返回 null，让调用方退回"叫发起它的会话"——复核不能因为
   * 会话建不出来就没人管。
   */
  async function reviewSession(taskId, repo) {
    if (!taskId) return null
    const service = ctx.get('agents')
    if (!service || typeof service.create !== 'function') return null

    const saved = tasks?.sessionOf?.(repo, taskId) || ''
    if (saved) {
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
        meta: { cwd: repo },
        agentOptions: agentOptions(),
      })
      tasks?.setSession?.(repo, taskId, sessionId)
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
    const text = awaiting
      ? reviewText(state, awaiting, count, lessons)
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

function reviewText(state, stage, count, lessons) {
  const name = state.feature_name || state.feature || ''
  const file = state.input?.filename || ''
  const title = state.stages?.[stage]?.title || stage
  const task = state?.options?.task || ''
  return [
    `【会计分录工作台】任务 ${state.job_id}（${name}${file ? ` · ${file}` : ''}）跑到复核位了：`,
    `停在「${title}」，${count} 条待办。`,
    task ? `这份账属于任务 ${task}，复核会话是它自己的，别把别的任务的口径带进来。` : '',
    `开工前先读 ${lessons}（这是跨任务共享的复核经验，没有就按里面的模板写），判完把新踩的坑追加回去。`,
    '用 journal_next 接手（每条带整张凭证的分录），判完用 journal_submit 交结论；'
      + '一条都不改就用 waive。',
    '**待办是分批给的**：交完一批再 journal_next，直到它说这个阶段没有待办为止，'
      + '别交一半就收工——阶段会一直停在复核位。',
    STALE_HINT,
  ].filter(Boolean).join('\n')
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
