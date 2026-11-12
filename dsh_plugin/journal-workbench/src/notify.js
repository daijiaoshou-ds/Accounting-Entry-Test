import { readState } from './store.js'

/**
 * 任务停在复核位时，把当前会话的 agent 叫起来。
 *
 * 这是 DSH 相对"网页 + 复制一句话给 AI"最关键的一处：agent 和页面在同一个进程里，
 * 所以不需要用户去粘任何东西。`agent.followup(message)` 的语义是
 * **投进 inbox 的 next-turn 并唤醒 driver**（`agent.inject()` 只投 next-step、不唤醒，
 * 所以拿它做"提醒"会一直躺在 inbox 里等下一次有人说话）。
 *
 * 三条纪律：
 *  1. **只叫醒自己人**：任务由哪个 agent 发起（工具调用或页面带过来的 sessionId）
 *     就只叫那个 agent。没有归属的任务（命令行起的）不猜——多会话时叫错人比不叫更糟。
 *  2. **同一处只叫一次**：键是 `job:阶段:条数`。agent 交完结论、下游重新停在同一处
 *     且条数没变（例如弃权后又停回来），就不该再叫一遍，否则会自己叫自己形成死循环。
 *  3. **agent 正在驱动这个任务时不叫**：提交/轮询之后 90 秒内它本来就会自己接着看，
 *     这时候再投一条只会在它下一轮开会话时冒出来——而且往往已经过时。
 *     `start` 不算"驱动"：刚建完任务它就该走了，之后正需要有人叫它回来。
 */
export function createNotifier(ctx, settings) {
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

  /**
   * 看一眼这个任务，该叫就叫。
   *
   * @returns 发出去的消息键，或 null（不该叫/叫不动）
   */
  function notify({ jobId, repo }, { force = false } = {}) {
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
    const key = `${jobId}:${awaiting || 'failed'}:${count}`
    if (notified.has(key)) return null

    const agent = agentFor(jobId)
    if (!agent) return null

    const text = awaiting
      ? reviewText(state, awaiting, count)
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

function reviewText(state, stage, count) {
  const name = state.feature_name || state.feature || ''
  const file = state.input?.filename || ''
  const title = state.stages?.[stage]?.title || stage
  return [
    `【会计分录工作台】任务 ${state.job_id}（${name}${file ? ` · ${file}` : ''}）跑到复核位了：`,
    `停在「${title}」，${count} 条待办。`,
    '用 journal_next 接手（每条带整张凭证的分录），判完用 journal_submit 交结论；'
      + '一条都不改就用 waive。',
    STALE_HINT,
  ].join('\n')
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
