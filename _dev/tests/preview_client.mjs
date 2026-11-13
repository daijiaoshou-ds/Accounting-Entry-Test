/**
 * 把工作台渲成一张静态 HTML，给无头浏览器截图看版式。
 * 只在开发时用：产物写到系统临时目录，不进仓库。
 */
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const CLIENT = join(ROOT, 'dsh_plugin', 'journal-workbench', 'client.js')

const webRequire = createRequire(join(ROOT, 'web', 'package.json'))
const React = webRequire('react')
const { renderToStaticMarkup } = webRequire('react-dom/server')

let captured = null
globalThis.window = { __ModuleLoader__: { load(spec) { captured = spec } } }
await import(pathToFileURL(CLIENT).href)
const mod = captured.factory((name) => React)
const I = mod.__internals
const t = (key) => I.DICT.zh[key] ?? key

// CSS 是 client.js 里的一个模板字符串，这里原样抽出来（预览必须用同一份样式）
const source = readFileSync(CLIENT, 'utf8')
const css = /const CSS = `([\s\S]*?)`\r?\n/.exec(source)?.[1]
if (!css) throw new Error('没从 client.js 里抽到 CSS')

const features = [
  { id: 'cleaning', name: '序时账清洗', description: '模型判业务桶（24 类）+ 四类硬约束' },
  { id: 'cashflow', name: '现金流量表编写', description: '桶 + 货币资金方向 → 现金流项目' },
  { id: 'contra', name: '对方科目分析', description: '多借多贷拆成一对一' },
  { id: 'entry_test', name: '会计分录测试', description: '业务群聚类打异常分' },
]
const tasks = [
  {
    id: 'task_a', name: '11 月账', filename: '序时账1.xlsx', mapping: { a: 1, b: 2, c: 3 },
    combo: { ok: true, rows: 11604, l1_patterns: 136, l1l2_patterns: 402 },
    session: 'session-abc',
    runs: {
      cleaning: { job_id: 'j1', status: 'done' },
      cashflow: { job_id: 'j2', status: 'awaiting_review', awaiting: 'review_bucket' },
      contra: { job_id: 'j3', status: 'failed' },
    },
  },
  {
    id: 'task_b', name: '全年账', filename: '序时账-全年.xlsx', mapping: {},
    combo: { ok: true, rows: 64255, l1_patterns: 214, l1l2_patterns: 880 }, runs: {},
  },
]
const env = {
  ready: { basic: true, model: true }, pythonVersion: '3.13.5',
  venv: { exists: true }, packages: { torch: { version: '2.13.0+cu126' } }, torchCuda: '12.6',
}
const stateOf = (feature) => I.featureState(t, feature, { env, task: tasks[0], features })
const api = { get: async () => ({}), post: async () => ({}) }

const tiles = renderToStaticMarkup(React.createElement('div', { className: 'jw-grid' },
  features.map((feature) => React.createElement(I.FeatureTile, {
    key: feature.id, feature, state: stateOf(feature), active: false, onOpen() {},
  }))))
const envc = renderToStaticMarkup(React.createElement(I.EnvCard, { t, env, onOpen() {} }))
const side = renderToStaticMarkup(React.createElement(React.Fragment, null,
  React.createElement(I.TaskRail, {
    api, t, tasks, features, activeId: 'task_a',
    onChanged: async () => {}, onUse() {}, onOpenFeature() {}, onNew() {}, onError() {},
  })))

const head = `<div class="jw-head">
  <h2 class="jw-title">会计分录工作台</h2>
  <span class="jw-muted jw-small">D:\\桌面\\公众号\\会计分录测试</span>
  <span class="jw-spacer"></span>
  <select class="jw-select"><option>11 月账</option><option>全年账</option></select>
  <button class="jw-btn jw-btn-primary">▶ 开始处理</button>
  <button class="jw-btn">＋ 新建任务</button>
  <button class="jw-btn jw-btn-ghost">刷新</button>
</div>`

const shell = (main) => `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
/* 照 DSH 真实浅色主题：边框 token 近乎透明（这正是"看不见线"的根因），
   品牌色接近黑，成功/警告是低饱和的绿与琥珀。预览必须用这一套，否则看不出问题。 */
:root{
  --dsw-alias-bg-base:#ffffff;--dsw-alias-bg-layer-1:#ffffff;--dsw-alias-bg-layer-2:#f6f7f8;
  --dsw-alias-label-primary:#1a1d21;--dsw-alias-label-secondary:#6b7280;
  --dsw-alias-border-l1:rgba(26,29,33,.03);--dsw-alias-border-l2:rgba(26,29,33,.07);
  --dsw-alias-brand-primary:#23262b;
  --dsw-alias-state-success-primary:#2f9e5e;--dsw-alias-state-warn-primary:#c98a1a;
  --dsw-alias-state-error-primary:#cf4438;
}
body{margin:0;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#fff}
.jw-root{height:100vh}
${css}
</style></head><body>
<div class="jw-root">${head}
  <div class="jw-split">
    <main class="jw-main">${main}</main>
    <div class="jw-gutter"></div>
    <aside class="jw-side" style="width:288px">${side}</aside>
  </div>
</div></body></html>`

const home = shell(`${envc}<div class="jw-section" style="margin:4px 0 0">功能</div>${tiles}`)
const out = join(process.env.TEMP || '/tmp', 'jw_preview.html')
writeFileSync(out, home, 'utf8')
console.log(out)

const STAGES = [
  { id: 'read', title: '读表与字段识别', status: 'done', elapsed: 0.7 },
  { id: 'clean', title: '业务分类', status: 'done', elapsed: 9.74 },
  { id: 'review_bucket', title: '复核业务分类（中/低置信）', status: 'awaiting', count: 29 },
  { id: 'export', title: '写回并导出', status: 'pending' },
]

// ── 第二张：点进某个功能之后（阶段链 + 复核表），宽度最吃紧的那一屏 ──
const detail = renderToStaticMarkup(React.createElement(React.Fragment, null,
  React.createElement(I.Detail, {
    title: '序时账清洗', hint: '模型判业务桶（24 类）+ 四类硬约束', t, onBack() {},
  }),
  React.createElement(I.JobView, {
    api, t, busy: false, chain: STAGES, onDecide() {}, onWaive() {}, onWake() {}, onCopy() {}, onResume() {},
    job: {
      job_id: '20261113_021117_9b1529', feature: 'cleaning', feature_name: '序时账清洗',
      status: 'awaiting_review', input: { filename: '序时账1.xlsx' },
      created_at: '2026-11-13 02:11',
      stages: [
        { id: 'read', title: '读表与字段识别', status: 'done', elapsed: 0.7 },
        { id: 'clean', title: '业务分类', status: 'done', elapsed: 9.74 },
        { id: 'review_bucket', title: '复核业务分类（中/低置信）', status: 'awaiting', count: 29 },
        { id: 'export', title: '写回并导出', status: 'pending' },
      ],
    },
    review: {
      stage: 'review_bucket', key_name: '凭证号', total: 29, help: '',
      value_enum: ['销售', '采购', '费用', '单位往来', '员工借支'],
      items: [
        {
          key: '2025年1月-记-0025号', 为什么要看: '置信度中（0.5198）',
          模型桶: '员工借支', 置信度: '中', 校准概率: 0.5198,
          科目: '销售费用、其他应收款', 金额: 44.77,
          摘要: '01/08邓梦霞报销胡春霞招待费，冲备用金',
          分录: ['销售费用 借 44.77', '其他应收款 贷 44.77'],
        },
        {
          key: '2025年1月-记-0037号', 为什么要看: '方向冲突：费用桶却收到钱',
          模型桶: '费用', 置信度: '高', 校准概率: 0.9123,
          科目: '管理费用、银行存款', 金额: 273.1,
          摘要: '01/17 退回多付的办公费',
          分录: ['管理费用 借 273.10（3 笔）', '银行存款 贷 273.10'],
        },
      ],
      submitted: {},
    },
  })))

const out2 = join(process.env.TEMP || '/tmp', 'jw_preview_detail.html')
writeFileSync(out2, shell(detail), 'utf8')
console.log(out2)
