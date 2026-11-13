/**
 * 离线渲染 `client.js` 里的组件。
 *
 * 这个仓库里没有浏览器、插件也要装进 DSH 才能跑起来，所以"页面改完有没有炸"
 * 只能靠把组件真的渲一遍：`react-dom/server` 出 HTML，再用字符串断言关键结构。
 * 抓不到样式好不好看，但能抓到"少一个括号""props 名字写错""依赖 undefined
 * 的字段"这类一进去就白屏的错——这正是改布局最容易犯的那类。
 *
 * 用法：
 *     node _dev/tests/render_client.mjs
 *
 * 依赖 `web/node_modules` 里的 react/react-dom（就是老前端那一份，不额外装包）。
 */
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const CLIENT = join(ROOT, 'dsh_plugin', 'journal-workbench', 'client.js')

const webRequire = createRequire(join(ROOT, 'web', 'package.json'))
const React = webRequire('react')
const { renderToStaticMarkup } = webRequire('react-dom/server')

// client.js 是个"注册进模块加载器"的脚本，不是普通模块：进门前先给它一个
// 假的 loader，把 factory 接住，然后自己喂一份 react 进去。
let captured = null
globalThis.window = {
  __ModuleLoader__: {
    load(spec) { captured = spec },
  },
}
await import(pathToFileURL(CLIENT).href)
if (!captured) throw new Error('client.js 没有调用 __ModuleLoader__.load')

const mod = captured.factory((name) => {
  if (name === 'react') return React
  throw new Error(`client.js 只该 require('react')，实际要了 ${name}`)
})
const I = mod.__internals

const failures = []
const check = (ok, message) => { if (!ok) failures.push(message) }
const render = (element) => renderToStaticMarkup(element)
const t = (key) => I.DICT.zh[key] ?? key

// ── 假数据：四功能、两任务，覆盖"没跑过 / 跑完 / 复核中 / 失败"──
const features = [
  { id: 'cleaning', name: '序时账清洗', description: '模型判业务桶', stages: [] },
  { id: 'cashflow', name: '现金流量表编写', description: '桶+方向 → 项目', stages: [] },
  { id: 'contra', name: '对方科目分析', description: '拆一对一', stages: [] },
  { id: 'entry_test', name: '会计分录测试', description: '异常打分', stages: [] },
]
const tasks = [
  {
    id: 'task_a', name: '11 月账', filename: '序时账1.xlsx',
    mapping: { voucher_id: '凭证号' }, created_at: '2026-11-13T02:00:00Z',
    combo: { ok: true, rows: 11604, l1_patterns: 136, l1l2_patterns: 402 },
    session: 'session-abc',
    runs: {
      cleaning: { job_id: 'j1', feature: 'cleaning', status: 'done', created_at: '1' },
      cashflow: { job_id: 'j2', feature: 'cashflow', status: 'awaiting_review', created_at: '2', awaiting: 'review_bucket' },
      contra: { job_id: 'j3', feature: 'contra', status: 'failed', created_at: '3' },
    },
  },
  {
    id: 'task_b', name: '全年账', filename: '序时账-全年.xlsx',
    mapping: {}, created_at: '2026-11-12T02:00:00Z', combo: { ok: false }, runs: {},
  },
]
const api = { get: async () => ({}), post: async () => ({}), upload: async () => ({}) }
const env = {
  ready: { basic: true, model: true }, pythonVersion: '3.13.5',
  venv: { exists: true }, packages: { torch: { version: '2.13.0+cu126' } }, torchCuda: '12.6',
}
const stateOf = (feature) => I.featureState(t, feature, { env, task: tasks[0], features })

// ── ① 功能框：只有名字 + 状态，不带描述 ──
const tile = render(React.createElement(I.FeatureTile, {
  feature: features[0], state: stateOf(features[0]), active: false, onOpen() {},
}))
check(tile.includes('序时账清洗'), '功能框没有功能名')
check(tile.includes('jw-chip'), '功能框没有状态')
check(!tile.includes('>模型判业务桶<'), '功能框不该把描述当正文写出来')

const blocked = render(React.createElement(I.FeatureTile, {
  feature: features[1], state: stateOf(features[1]), active: false, onOpen() {},
}))
check(blocked.includes('jw-tile'), '功能框没有 jw-tile 样式')
check(!blocked.includes('jw-tile off'), '现金流已跑完，不该是置灰态')

// ── ② 右栏：环境卡 + 任务列表 ──
const envCard = render(React.createElement(I.EnvCard, { t, env, onOpen() {} }))
check(envCard.includes('环境配置') && envCard.includes('可以用了'), '环境卡没有标题与状态')
check(envCard.includes('Python 3.13.5'), '环境卡没显示解释器')
check(envCard.includes('CUDA 12.6'), '环境卡没显示 CUDA')

const rail = render(React.createElement(I.TaskRail, {
  api, t, tasks, features, activeId: 'task_a',
  onChanged: async () => {}, onUse() {}, onOpenFeature() {}, onNew() {}, onError() {},
}))
check(rail.includes('11 月账') && rail.includes('全年账'), '任务栏少了任务')
check(rail.includes('序时账1.xlsx'), '任务块没显示账名')
check(rail.includes('136 种一级组合'), '任务块没显示科目组合')
check(rail.includes('jw-taskrow on'), '当前任务没有高亮')
check(rail.includes('jw-runchip done'), '跑完的功能没有对应语气')
check(rail.includes('jw-runchip await'), '复核中/在跑的功能没有对应语气')
check(rail.includes('jw-runchip failed'), '失败的功能没有对应语气')
check(rail.includes('重算组合') && rail.includes('删除'), '任务块少了操作')

const emptyRail = render(React.createElement(I.TaskRail, {
  api, t, tasks: [], features, activeId: '',
  onChanged: async () => {}, onUse() {}, onOpenFeature() {}, onNew() {}, onError() {},
}))
check(emptyRail.includes('jw-empty'), '没有任务时没有空状态')
check(emptyRail.includes('新建任务'), '空状态里没有建任务入口')

// ── ③ 整个工作台：主区在前、右栏在后，中间有可拖的分隔条 ──
const page = render(React.createElement(I.Workbench, { sessionId: 'session-abc' }))
check(page.includes('jw-split'), '工作台没有左右分栏')
check(page.includes('jw-gutter'), '分栏没有可拖的分隔条')
check(page.includes('jw-main') && page.includes('jw-side'), '主区或右栏没挂上')
check(page.indexOf('jw-main') < page.indexOf('jw-side'), '主区应该在右栏之前')
check(page.includes('width:288px'), '右栏默认宽度没生效')
check(!page.includes('jw-left') && !page.includes('jw-right'), '旧的左右栏结构还在')
// 环境卡在主区（功能那一块），右栏只有任务 —— 用户明确要的顺序
// 注意：<style> 里也有这些类名，所以按 class 属性定位，别按裸类名找
const mainAt = page.indexOf('class="jw-main"')
const sideAt = page.indexOf('class="jw-side"')
const mainHtml = page.slice(mainAt, sideAt)
const sideHtml = page.slice(sideAt)
check(mainHtml.includes('jw-env'), '环境卡不在主区')
check(!sideHtml.includes('jw-env'), '环境卡不该出现在右栏')
check(sideHtml.includes('jw-taskrow') || sideHtml.includes('jw-empty'), '右栏没有任务区')

// ── ④ 任务详情：阶段链 + 待办 + 结果 ──
const job = {
  job_id: 'j2', feature: 'cashflow', feature_name: '现金流量表编写',
  status: 'awaiting_review', input: { filename: '序时账1.xlsx' },
  stages: [
    { id: 'read', title: '读表', status: 'done', elapsed: 3.2 },
    { id: 'review_bucket', title: '复核业务分类', status: 'awaiting', count: 29 },
  ],
  running: null,
}
const view = render(React.createElement(I.JobView, {
  job, chain: job.stages, busy: false,
  review: { stage: 'review_bucket', title: '复核业务分类', total: 1, help: '',
    value_enum: ['销售', '采购'], columns: [], items: [
      { key: '记-1', 为什么要看: '置信度中', 分录: ['银行存款 借 100'], 模型桶: '销售' },
    ], submitted: {} },
  api, t, onDecide() {}, onWaive() {}, onWake() {}, onCopy() {}, onResume() {},
}))
check(view.includes('现金流量表编写') && view.includes('复核业务分类'), '任务详情没渲染出来')
check(view.includes('银行存款 借 100'), '待办里的分录没渲染')

console.log(failures.length
  ? `\n${failures.map((item) => `FAIL ${item}`).join('\n')}`
  : '✅ 离线渲染全部通过')
process.exit(failures.length ? 1 : 0)
