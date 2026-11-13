/**
 * 复核会话的粒度：**一个任务一个会话**，同一任务的不同功能都进同一个会话。
 *
 * 为什么值得单独测：用户问得很直接——"清洗复核开了会话 A，现金流复核会在 A 里
 * 接着判，还是新开 B？" 期望是 A：同一份账、同一套口径，判过的东西不该换个会话
 * 重新问一遍。会话是按**任务**绑的（`task.json` 的 `session`），不是按运行、
 * 也不是按功能，代码里最容易在"叫醒"这一步写错。
 *
 * 用假的 agents 服务（只记 create / followup），不碰真的会话，不花模型钱。
 *
 *     node _dev/tests/test_review_session.mjs
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { createNotifier } from '../../dsh_plugin/journal-workbench/src/notify.js'

const failures = []
const check = (ok, message) => { if (!ok) failures.push(message) }

const root = mkdtempSync(join(tmpdir(), 'jw-session-'))

function makeJob(id, taskId, feature) {
  const dir = join(root, id)
  mkdirSync(join(dir, 'reviews'), { recursive: true })
  writeFileSync(join(dir, 'state.json'), JSON.stringify({
    job_id: id,
    feature,
    feature_name: feature,
    status: 'awaiting_review',
    options: { task: taskId },
    input: { filename: '序时账1.xlsx' },
    stages: { review_bucket: { status: 'awaiting', facts: { count: 2 } } },
  }), 'utf8')
  writeFileSync(join(dir, 'reviews', 'review_bucket.request.json'), JSON.stringify({
    stage: 'review_bucket', value_enum: ['销售'], items: [{ key: '记-1' }, { key: '记-2' }],
  }), 'utf8')
}

const sessions = new Map()          // taskId → sessionId
const created = []
const delivered = []
const live = new Map()

const agents = {
  get: (id) => live.get(id) || null,
  resume: async () => null,
  create: async ({ sessionId }) => {
    created.push(sessionId)
    const agent = {
      followup: (msg) => delivered.push({
        sessionId, text: msg?.content?.[0]?.text || '',
      }),
    }
    live.set(sessionId, agent)
    return { agent }
  },
}

const ctx = { get: (name) => (name === 'agents' ? agents : null) }
const settings = { autoWake: true, jobsFor: () => root }
const tasks = {
  sessionOf: (_repo, taskId) => sessions.get(taskId) || '',
  setSession: (_repo, taskId, sessionId) => { sessions.set(taskId, sessionId); return true },
}

const notifier = createNotifier(ctx, settings, tasks)

makeJob('job1', 'task_a', 'cleaning')
makeJob('job2', 'task_a', 'cashflow')
makeJob('job3', 'task_b', 'cleaning')

check(await notifier.notify({ jobId: 'job1', repo: root }), '第一个任务没被叫醒')
check(await notifier.notify({ jobId: 'job2', repo: root }), '同一任务的第二个功能没被叫醒')

check(created.length === 1,
  `同一任务的两个功能开了 ${created.length} 个会话（应该 1 个）`)
check(delivered.length === 2, `应该投递 2 条提醒，实际 ${delivered.length}`)
check(delivered.every((item) => item.sessionId === created[0]),
  '同一任务的两条提醒进了不同的会话')
check(delivered.some((item) => item.text.includes('job2')), '第二条提醒没提现金流那次运行')

check(await notifier.notify({ jobId: 'job3', repo: root }), '另一个任务没被叫醒')
check(created.length === 2, '不同任务应该各开一个会话')
check(sessions.get('task_a') !== sessions.get('task_b'), '两个任务绑到了同一个会话')

rmSync(root, { recursive: true, force: true })

console.log(failures.length
  ? `\n${failures.map((item) => `FAIL ${item}`).join('\n')}`
  : '✅ 复核会话按任务隔离：同一任务的不同功能共用一个会话')
process.exit(failures.length ? 1 : 0)
