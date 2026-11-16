/**
 * 把工作台渲成一张静态 HTML，给无头浏览器截图看版式。
 * 只在开发时用：产物写到系统临时目录，不进仓库。
 */
import { createRequire } from 'node:module'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const CLIENT = join(ROOT, 'dsh_plugin', 'journal-workbench', 'client.js')

// react/react-dom 取自旧前端那份；web/ 归档到 _archive/legacy-web/web 也要能找到
const webRoot = ['web', join('_archive', 'legacy-web', 'web')]
  .map((rel) => join(ROOT, rel))
  .find((dir) => existsSync(join(dir, 'package.json')))
if (!webRoot) throw new Error('找不到 react/react-dom（web/ 或 _archive/legacy-web/web/）')
const webRequire = createRequire(join(webRoot, 'package.json'))
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
  { id: 'cleaning', name: '序时账清洗', description: '模型判业务桶（24 类）+ 四类硬约束', domain: 'ledger' },
  { id: 'cashflow', name: '现金流量表编写', description: '桶 + 货币资金方向 → 现金流项目', domain: 'ledger' },
  { id: 'contra', name: '对方科目分析', description: '多借多贷拆成一对一', domain: 'ledger' },
  { id: 'entry_test', name: '会计分录测试', description: '业务群聚类打异常分', domain: 'ledger' },
  { id: 'cost', name: '成本计算', description: '采购明细 → 物料成本；尚未接入', domain: 'cost', available: false },
]

// 功能层（域）：与 `journal_io/contracts.py` 的 DOMAINS 对齐
const domains = [
  {
    id: 'ledger', name: '序时账处理', solo: false,
    description: '一份序时账，四个功能共用：清洗、现金流、对方科目、分录测试',
    features: ['cleaning', 'cashflow', 'contra', 'entry_test'],
    inputs: [{ label: '序时账', hint: '凭证号 / 一级科目 / 借方 / 贷方，多个月分表也行' }],
  },
  {
    id: 'cost', name: '成本计算', solo: true,
    description: '采购明细等成本资料，与序时账完全独立',
    features: ['cost'],
    inputs: [{ label: '采购明细' }],
  },
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

const gridOf = (items) => renderToStaticMarkup(React.createElement('div', { className: 'jw-grid' },
  items.map((feature) => React.createElement(I.FeatureTile, {
    key: feature.id, feature, state: stateOf(feature), active: false, onOpen() {},
  }))))

// 一层 = 一份可共用的数据：大簇画分隔标题，单例层标题退成灰色小字
const domainHtml = (dom) => {
  const items = features.filter((feature) => feature.domain === dom.id)
  return `<section class="jw-domain${dom.solo ? ' solo' : ''}">
  <div class="jw-domain-head">
    <span class="jw-domain-name">${dom.name}</span>
    <span class="jw-domain-count">${items.length} 个功能</span>
    ${dom.description ? `<span class="jw-domain-desc">${dom.description}</span>` : ''}
    <span class="jw-spacer"></span>
    <button class="jw-btn jw-btn-ghost jw-btn-tiny">＋ 新建</button>
  </div>${gridOf(items)}</section>`
}
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

const home = shell(`${envc}${domains.map(domainHtml).join('')}`)
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
    api, t, busy: false, chain: STAGES, onDecide() {}, onWaive() {}, onWake() {}, onResume() {},
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

// ── 第三张：新建任务配字段。上半是账域（已认全 → 只有一行结论），
//    下半是成本域（缺 3 个 → 自动展开，且这一层没有可用功能时说明原因）──
const rowsOf = (items) => items.map(([label, picked, sample]) => `
      <span class="jw-muted jw-small">${label}<span class="jw-req">*</span></span>
      <select class="jw-select">${['—'].concat(sample.cols).map((col) => (
        `<option${col === picked ? ' selected' : ''}>${col}</option>`)).join('')}</select>
      <span class="jw-muted jw-small">${picked === '—' ? '' : sample.value}</span>`).join('')

const create = `<div class="jw-stack">
  <div class="jw-card">
    <header><h3>新建任务</h3><span class="jw-muted jw-small">序时账处理</span>
      <span class="jw-spacer"></span>
      <button class="jw-btn jw-btn-ghost jw-btn-tiny">换一层</button></header>
    <div class="jw-row" style="margin-bottom:10px">
      <span class="jw-muted jw-small">任务名</span>
      <input class="jw-input" style="flex:1;min-width:180px;max-width:320px" value="序时账1"></div>
    <div class="jw-row" style="margin-bottom:10px"><strong>序时账1.xlsx</strong>
      <span class="jw-muted jw-small">11,604 行 · 27 列</span>
      <span class="jw-spacer"></span>
      <button class="jw-btn jw-btn-ghost">取消</button></div>
    <div class="jw-maphead"><span class="jw-chip on">必需字段已识别 4/4</span>
      <span class="jw-spacer"></span>
      <button class="jw-btn jw-btn-ghost jw-btn-tiny">展开调整</button></div>
    <div class="jw-unused"><b>10 列未使用</b>：会计分录特征、审核人、主管签字人、出纳签字人、
      审核日期、记账人、系统名、备注 —— 不参与计算，导出时原样保留</div>
    <div class="jw-actions"><button class="jw-btn jw-btn-primary">创建任务</button></div>
  </div>

  <div class="jw-card">
    <header><h3>新建任务</h3><span class="jw-muted jw-small">成本计算</span>
      <span class="jw-spacer"></span>
      <button class="jw-btn jw-btn-ghost jw-btn-tiny">换一层</button></header>
    <div class="jw-banner" style="margin-bottom:10px">这一层还没有可用的功能，配好的数据暂时没人能跑</div>
    <div class="jw-row" style="margin-bottom:10px">
      <span class="jw-muted jw-small">任务名</span>
      <input class="jw-input" style="flex:1;min-width:180px;max-width:320px" value="2 月采购"></div>
    <div class="jw-row" style="margin-bottom:10px"><strong>采购明细.xlsx</strong>
      <span class="jw-muted jw-small">1,208 行 · 9 列</span>
      <span class="jw-spacer"></span>
      <button class="jw-btn jw-btn-ghost">取消</button></div>
    <div class="jw-maphead"><span class="jw-chip warn">还缺 3</span>
      <span class="jw-muted jw-small">物料、数量、单价</span>
      <span class="jw-spacer"></span>
      <button class="jw-btn jw-btn-ghost jw-btn-tiny">收起</button></div>
    <div class="jw-map">${rowsOf([
      ['物料', '品名', { cols: ['—', '品名', '规格', '供应商'], value: 'A4 复印纸 / 订书机' }],
      ['数量', '—', { cols: ['—', '入库数', '装箱数', '金额'], value: '' }],
      ['单价', '—', { cols: ['—', '含税价', '不含税价', '金额'], value: '' }],
    ])}</div>
    <div class="jw-unused"><b>4 列未使用</b>：规格、供应商、备注、录入人 —— 不参与计算，导出时原样保留</div>
    <div class="jw-actions"><button class="jw-btn jw-btn-primary" disabled>创建任务</button></div>
  </div>
</div>`

const out3 = join(process.env.TEMP || '/tmp', 'jw_preview_create.html')
writeFileSync(out3, shell(create), 'utf8')
console.log(out3)
