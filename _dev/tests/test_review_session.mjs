/**
 * 复核会话的两件事：**粒度**（一个任务一个会话）和**能力与上下文**。
 *
 * 粒度：同一任务的不同功能都进同一个会话——同一份账、同一套口径，判过的不重问。
 *
 * 能力：会话必须带 `agentPreset`。不带它建出来的会话只挂得住插件自己的工具，
 * 读不了文件、跑不了脚本——AI 只能一条条肉眼看 155 条待办（用户就是这么撞上的）。
 * 老会话（没记 preset）也不能接着用，要换一个带工具的。
 *
 * 上下文：叫醒消息里要有"这个功能在做什么 + 可选值 + 这一批要你做什么 + 怎么干"，
 * 不能只丢一句"有 N 条待办"。
 *
 *     node _dev/tests/test_review_session.mjs
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { createNotifier } from '../../dsh_plugin/journal-workbench/src/notify.js'

const failures = []
const check = (ok, message) => { if (!ok) failures.push(message) }

const repo = mkdtempSync(join(tmpdir(), 'jw-session-'))
const jobs = join(repo, 'jobs')

function makeJob(id, taskId, feature) {
  const dir = join(jobs, id)
  mkdirSync(join(dir, 'reviews'), { recursive: true })
  writeFileSync(join(dir, 'state.json'), JSON.stringify({
    job_id: id,
    feature,
    feature_name: feature === 'cleaning' ? '序时账清洗' : '现金流量表编写',
    status: 'awaiting_review',
    options: { task: taskId },
    input: { filename: '序时账1.xlsx' },
    stages: {
      read: { status: 'done', facts: { rows: 11604, vouchers: 2216 } },
      review_bucket: { status: 'awaiting', facts: { count: 2 } },
    },
  }), 'utf8')
  writeFileSync(join(dir, 'reviews', 'review_bucket.request.json'), JSON.stringify({
    stage: 'review_bucket',
    help: '逐条确认它属于哪个业务桶。',
    value_enum: ['销售', '采购'],
    items: [{ key: '记-1' }, { key: '记-2' }],
  }), 'utf8')
}

// 假的"仓库"：功能的说明文件 + 引擎的桶注册表
mkdirSync(join(repo, 'features', 'cleaning'), { recursive: true })
writeFileSync(join(repo, 'features', 'cleaning', 'BRIEF.md'),
  '# 清洗口径\n判桶看对方科目，不看货币资金那一行。', 'utf8')
mkdirSync(join(repo, 'summary_cleaner', 'v2', 'assets'), { recursive: true })
writeFileSync(join(repo, 'summary_cleaner', 'v2', 'assets', 'bucket_taxonomy_v10.json'),
  JSON.stringify({ buckets: { 销售: { definition: '卖货、提供劳务并收款' } } }), 'utf8')

const sessions = new Map()          // taskId → sessionId
const presets = new Map()           // taskId → preset
const created = []
const delivered = []
const live = new Map()

const agents = {
  get: (id) => live.get(id) || null,
  resume: async () => null,
  create: async ({ sessionId, meta }) => {
    created.push({ sessionId, preset: meta?.agentPreset || '' })
    const agent = {
      followup: (msg) => delivered.push({ sessionId, text: msg?.content?.[0]?.text || '' }),
    }
    live.set(sessionId, agent)
    return { agent }
  },
}

const ctx = { get: (name) => (name === 'agents' ? agents : null) }
const settings = { autoWake: true, jobsFor: () => jobs, reviewPreset: 'standard' }
const tasks = {
  sessionOf: (_repo, taskId) => sessions.get(taskId) || '',
  presetOf: (_repo, taskId) => presets.get(taskId) || '',
  setSession: (_repo, taskId, sessionId, preset) => {
    sessions.set(taskId, sessionId)
    presets.set(taskId, preset || '')
    return true
  },
}

const notifier = createNotifier(ctx, settings, tasks)

makeJob('job1', 'task_a', 'cleaning')
makeJob('job2', 'task_a', 'cashflow')
makeJob('job3', 'task_b', 'cleaning')

check(await notifier.notify({ jobId: 'job1', repo }), '第一个任务没被叫醒')
check(await notifier.notify({ jobId: 'job2', repo }), '同一任务的第二个功能没被叫醒')

// ── 粒度：同一任务一个会话 ──
check(created.length === 1,
  `同一任务的两个功能开了 ${created.length} 个会话（应该 1 个）`)
check(delivered.length === 2, `应该投递 2 条提醒，实际 ${delivered.length}`)
check(delivered.every((item) => item.sessionId === created[0].sessionId),
  '同一任务的两条提醒进了不同的会话')

// ── 能力：带 preset，否则会话没有文件/脚本工具 ──
check(created[0].preset === 'standard',
  `复核会话没有带 agentPreset（收到 ${created[0].preset || '空'}），AI 会拿不到读写文件的工具`)
check(presets.get('task_a') === 'standard', '任务没记住会话的 preset，下次没法判断能不能复用')

// ── 上下文：说明 + 可选值定义 + 这一批要做什么 + 怎么干 ──
const text = delivered[0].text
check(text.includes('判桶看对方科目'), '叫醒消息里没有功能的判定说明（BRIEF.md）')
check(text.includes('销售：卖货、提供劳务并收款'), '叫醒消息里没有可选取值的定义')
check(text.includes('逐条确认它属于哪个业务桶'), '叫醒消息里没有这一批要做什么')
check(text.includes('11,604 行'), '叫醒消息里没有账的规模')
check(text.includes('journal_submit'), '叫醒消息里没有交代怎么交结论')
check(text.includes('request.json'), '叫醒消息里没给全量待办的路径（它有工具，可以直接读）')

// ── 老会话没有 preset：不能接着用，要换一个 ──
sessions.set('task_c', 'session-legacy')
presets.set('task_c', '')
makeJob('job4', 'task_c', 'cleaning')
check(await notifier.notify({ jobId: 'job4', repo }), '带老会话的任务没被叫醒')
check(created.length === 2,
  '老会话（建的时候没带 preset）被接着用了——它没有文件/脚本工具')
check(sessions.get('task_c') !== 'session-legacy', '没有换掉那个没有工具的老会话')

// ── 不同任务各一个会话 ──
check(await notifier.notify({ jobId: 'job3', repo }), '另一个任务没被叫醒')
check(created.length === 3, '不同任务应该各开一个会话')
check(sessions.get('task_a') !== sessions.get('task_b'), '两个任务绑到了同一个会话')

rmSync(repo, { recursive: true, force: true })

console.log(failures.length
  ? `\n${failures.map((item) => `FAIL ${item}`).join('\n')}`
  : '✅ 复核会话按任务隔离、带工具、说明齐全')
process.exit(failures.length ? 1 : 0)
