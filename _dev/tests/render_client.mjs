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
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const CLIENT = join(ROOT, 'dsh_plugin', 'journal-workbench', 'client.js')

// react/react-dom 还是旧前端那一份。旧 web/ 已归档到 _archive/legacy-web/web，
// 两处都找一下，归档前后都能跑。
const webRoot = ['web', join('_archive', 'legacy-web', 'web')]
  .map((rel) => join(ROOT, rel))
  .find((dir) => existsSync(join(dir, 'package.json')))
if (!webRoot) throw new Error('找不到 react/react-dom（web/ 或 _archive/legacy-web/web/）')
const webRequire = createRequire(join(webRoot, 'package.json'))
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
const api = {
  get: async () => ({}),
  post: async () => ({}),
  upload: async () => ({}),
  downloadUrl: (id, key) => `/plugin/journal/download?id=${id}&key=${key}`,
  openUrl: (id, key) => `/plugin/journal/download?id=${id}&key=${key}&inline=1`,
  preview: async () => ({ preview: {} }),
}
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
check(!emptyRail.includes('jw-trash'), '没有残留时不该出现回收站那一行')

// 旧版本留下的 .trash：只报数 + 一次清空，不当"回收站"用（删任务是真删了）
const railTrash = render(React.createElement(I.TaskRail, {
  api, t, tasks, features, trash: { count: 2, bytes: 33_554_432 },
  activeId: 'task_a',
  onChanged: async () => {}, onUse() {}, onOpenFeature() {}, onNew() {}, onError() {},
}))
check(railTrash.includes('回收站残留 2 项') && railTrash.includes('32.0MB'),
  '右栏没提示旧回收站的残留与大小')
check(railTrash.includes('清空'), '残留那儿没有清空入口')
check(I.DICT.zh.taskDeleteNote.includes('永久删除') && I.DICT.zh.taskDeleteNote.includes('不能恢复'),
  '删除确认只说"删除"，没说是永久的')
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
  api, t, onDecide() {}, onWaive() {}, onWake() {}, onResume() {},
}))
check(view.includes('现金流量表编写') && view.includes('复核业务分类'), '任务详情没渲染出来')
check(view.includes('银行存款 借 100'), '待办里的分录没渲染')
check(view.includes('AI 复核这批') && view.includes('不复核，直接用'), '复核位缺两条出路')
check(!view.includes('复制给 AI'), '复核位不该再有「复制给 AI」')

// ── ⑤ 在跑 / 进程没了 / 已中止：三种状态各自要有出口 ──
const running = render(React.createElement(I.JobView, {
  job: { ...job, status: 'running', stages: [], running: { label: '开始跑', seconds: 7, log: [] } },
  chain: [], review: null, api, t, busy: false,
  onDecide() {}, onWaive() {}, onWake() {}, onResume() {}, onStop() {},
}))
check(running.includes('■ 中止'), '在跑的任务没有中止按钮')

const stalled = render(React.createElement(I.JobView, {
  job: { ...job, status: 'running', stages: [], running: null, stalled: true },
  chain: [], review: null, api, t, busy: false,
  onDecide() {}, onWaive() {}, onWake() {}, onResume() {}, onStop() {},
}))
check(stalled.includes('进程已经不在了'), '进程没了却没说清楚')
check(stalled.includes('重试') && stalled.includes('■ 中止'), '进程没了没有重试/中止两条路')

const stopped = render(React.createElement(I.JobView, {
  job: { ...job, status: 'aborted', stages: [], running: null },
  chain: [], review: null, api, t, busy: false,
  onDecide() {}, onWaive() {}, onWake() {}, onResume() {}, onStop() {},
}))
check(stopped.includes('已中止'), '中止状态没显示')
check(stopped.includes('重试'), '中止后没有重试入口')
check(!stopped.includes('选一个任务，或新建一个'), '中止后不该再显示空状态文案')

// ── ⑥ 轮询间隔：刚建好的任务必须继续问 ──
// `journal create` 建完就返回，python 写下 running 还要一两秒。这段时间页面如果
// 停止轮询，就永远停在"刚建好"那一页——用户报的"跑到等我复核了页面还不更新"。
check(I.pollDelay('created') > 0, '刚建好的任务不该停止轮询（页面会停在刚建那一页）')
check(I.pollDelay('running') > 0 && I.pollDelay('running') <= 1500, '在跑时的轮询间隔不对')
check(I.pollDelay('awaiting_review') > 0, '等你复核时还要问（AI 交完结论页面才跟着走）')
check(I.pollDelay('done') === 0 && I.pollDelay('aborted') === 0, '终态不该继续轮询')

// ── ⑦ 结果页：产物预览（跑完就能看表，不用先下载）──
// SSR 不跑 effect，所以这里验的是"第一屏该说什么"和"什么文件不该出预览框"。
const doneJob = {
  job_id: 'j3', feature: 'cleaning', feature_name: '序时账清洗', status: 'done',
  input: { filename: '序时账1.xlsx' },
  stages: [{ id: 'export', title: '导出', status: 'done', facts: { vouchers: 7883, rows: 42683 } }],
  files: [{ key: 'result', label: 'result', filename: '序时账1_业务分类.xlsx', bytes: 4_100_000 }],
}
const resultPanel = render(React.createElement(I.ResultPanel, { job: doneJob, api, t }))
check(resultPanel.includes('jw-sheet'), '结果页没有产物预览区')
check(resultPanel.includes('正在读这张表'), '预览首屏不是「正在读」（第一次读表要 1~2 秒，不能先显示"没有数据"）')
check(resultPanel.includes('搜索这张表'), '预览没有搜索框')
check(resultPanel.includes('下载 result'), '加了预览不该把下载挤掉')
check(!resultPanel.includes('jw-stat') && !resultPanel.includes('jw-bars'),
  '结果页只留预览 + 下载；统计条与桶分布都该拿掉')

// 报告类产物：在页面里直接读 iframe，不再走表格预览器
const reportJob = {
  ...doneJob,
  files: [
    { key: 'result', label: 'result', filename: '序时账1_会计分录测试.html', bytes: 41_000 },
    { key: 'detail', label: 'detail', filename: '序时账1_会计分录测试_标记.xlsx', bytes: 28_000 },
  ],
}
const reportHtml = render(React.createElement(I.ResultPanel, { job: reportJob, api, t }))
check(reportHtml.includes('jw-report-frame') && reportHtml.includes('inline=1'),
  'HTML 报告没在页面里打开（还要人先下载）')
check(reportHtml.includes('sandbox'), '报告 iframe 该带 sandbox（静态报告不需要脚本）')
check(!reportHtml.includes('jw-sheet'), '报告不该同时走表格预览器')
check(reportHtml.includes('下载 html') && reportHtml.includes('序时账（含异常标记）'),
  '报告与标记表两个下载入口都要在（标记表不能只写 detail）')

const parquetJob = {
  ...doneJob,
  files: [{ key: 'result', label: 'result', filename: 'result.parquet', bytes: 2_000_000 }],
}
check(!render(React.createElement(I.ResultPanel, { job: parquetJob, api, t })).includes('jw-sheet'),
  'parquet 不该出现预览框（读不了就是读不了，别装作能看）')

const csvJob = {
  ...doneJob,
  files: [
    { key: 'sheet:业务分类结果', label: '业务分类结果', filename: 'a_业务分类结果.csv', bytes: 900 },
    { key: 'sheet:凭证明细', label: '凭证明细', filename: 'a_凭证明细.csv', bytes: 900 },
  ],
}
const csvHtml = render(React.createElement(I.ResultPanel, { job: csvJob, api, t }))
check(csvHtml.includes('jw-sheet-files') && csvHtml.includes('a_凭证明细.csv'),
  '一份产物拆成多个 csv 时，要能在这几个文件之间切')

// 表格本体（纯组件）：表头、行号、数字列右对齐，**只渲可见窗口**
const gridHeader = ['制单日期', '凭证编号', '摘要', '金额', '业务分类']
const gridRows = [
  ['2025-01-31', '2025年1月-记-0001号', '2024年终尾牙费用/二等奖（9人/1800元）', '16200', '费用'],
  ['2025-01-31', '2025年1月-记-0002号', '采购包装箱一批', '8430', '采购'],
]
const gridLayout = I.columnLayout(gridHeader, gridRows)
const grid = render(React.createElement(I.SheetTable, {
  pages: { 0: { rows: gridRows, rowIndexes: null } },
  total: 42683,
  header: gridHeader,
  layout: gridLayout,
  copied: null,
  onCopy() {},
  onNeed() {},
  onRange() {},
}))
check(grid.includes('jw-sheet-scroll'), '表格没有滚动容器（表头就吸不住了）')
check(grid.includes('title="摘要"'), '表头少了列名/悬停标题')
check(grid.includes('2024年终尾牙费用'), '单元格内容没渲染')
check(grid.includes('jw-rn">1') && grid.includes('jw-rn">2'), '行号没渲染')
check(grid.includes('jw-num'), '金额列没有右对齐')
check((grid.match(/<col /g) || []).length === 6, '列宽没按「行号 + 每列」出来')
// 虚拟滚动：4 万行的表只该渲染一屏多一点，否则 DOM 会先卡死
const trCount = (grid.match(/<tr/g) || []).length
check(trCount > 2 && trCount < 60, `4 万行只该渲一屏，实际渲了 ${trCount} 个 tr`)
check(grid.includes('jw-sheet-padrow'), '缺了上下留白行，滚动条长度就不等于整表长度')
const pad = /jw-sheet-padrow[^>]*><td[^>]*height:(\d+)px/.exec(grid)
check(pad && Number(pad[1]) > 1_000_000, '底部留白没按「剩余行数 × 行高」算（滚动条会短一大截）')

// 搜索结果：行号必须是原表位置，不能从 1 重数（否则对着 Excel 找不到那行）
const hitGrid = render(React.createElement(I.SheetTable, {
  pages: { 0: { rows: gridRows, rowIndexes: [29999, 30005] } },
  total: 2,
  header: gridHeader,
  layout: gridLayout,
  copied: null,
  onCopy() {},
  onNeed() {},
  onRange() {},
}))
check(hitGrid.includes('30,000') && hitGrid.includes('30,006'), '搜索出来的行没保留原始行号')

// 虚拟滚动的窗口：滚到底必须看得到最后一行，且留白加起来正好是整表高度
const TOTAL = 42683
const BOX = 420
const HEAD = 32
const top = I.windowOf({ scrollTop: 0, boxH: BOX, headH: HEAD, total: TOTAL })
check(top.first === 0 && top.topPad === 0, '滚在最上面却留了白')
const bottom = I.windowOf({ scrollTop: TOTAL * 26 + HEAD - BOX, boxH: BOX, headH: HEAD, total: TOTAL })
check(bottom.last === TOTAL, '滚到底看不到最后一行')
check(bottom.bottomPad === 0, '滚到底还有底部留白（会滚出一片空白）')
check(top.topPad + (top.last - top.first) * 26 + top.bottomPad === TOTAL * 26,
  '上下留白 + 已渲染行不等于整表高度：滚动条会比内容短或长')
const huge = I.windowOf({ scrollTop: 99999999, boxH: BOX, headH: HEAD, total: 40 })
check(huge.last === 40 && huge.first <= 40, '总行数比一屏还少时窗口算飞了')

console.log(failures.length
  ? `\n${failures.map((item) => `FAIL ${item}`).join('\n')}`
  : '✅ 离线渲染全部通过')
process.exit(failures.length ? 1 : 0)
