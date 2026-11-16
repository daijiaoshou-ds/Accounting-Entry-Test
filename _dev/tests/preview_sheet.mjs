/**
 * 把「结果页的表格预览」渲成一张静态 HTML，给人看版式（不跑 `client.js` 的取数逻辑）。
 *
 * 为什么要有它：预览那半截要跑 effect 才能拿到数据，`render_client.mjs` 是 SSR，
 * 跑到的地方只有"正在读这张表"。所以这里直接拿真账、真列宽、真单元格，
 * 单独渲 `SheetTable`，外面套同一份 CSS——看得见的样子就是浏览器里会长的样子。
 *
 * 数据来自 `case/序时账1.xlsx`（项目约定的功能测试账）。
 *
 *     node _dev/tests/preview_sheet.mjs && 打开它打印的路径
 */
import { createRequire } from 'node:module'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { createPreview } from '../../dsh_plugin/journal-workbench/src/preview.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const CLIENT = join(ROOT, 'dsh_plugin', 'journal-workbench', 'client.js')

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
const I = captured.factory((name) => React).__internals

const source = readFileSync(CLIENT, 'utf8')
const css = /const CSS = `([\s\S]*?)`\r?\n/.exec(source)?.[1]
if (!css) throw new Error('没从 client.js 里抽到 CSS')

// ── 真数据 ──
const repo = ROOT
const file = join(repo, 'case', '序时账1.xlsx')
const preview = createPreview({
  ops: { resolveDownload: async () => ({ path: file, filename: '序时账1.xlsx' }) },
  settings: { repoFor: async () => repo, pythonFor: () => join(repo, 'venv', 'Scripts', 'python.exe') },
})
const data = await preview.sheetView({ job_id: 'demo', key: 'result', sheet: '', offset: 0, limit: 24 }, undefined)
const layout = I.columnLayout(data.header, data.rows.slice(0, 40))
const grid = renderToStaticMarkup(React.createElement(I.SheetTable, {
  data, layout, copied: null, onCopy() {},
}))

const fmt = (value) => Number(value).toLocaleString('zh-CN')
const tabs = data.sheets.map((item, index) => `<button class="jw-sheet-tab${index === 0 ? ' on' : ''}">`
  + `${item.name}<b> ${fmt(item.rows)}</b></button>`).join('')

const html = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
:root{
  --dsw-alias-bg-base:#ffffff;--dsw-alias-bg-layer-1:#ffffff;--dsw-alias-bg-layer-2:#f6f7f8;
  --dsw-alias-label-primary:#1a1d21;--dsw-alias-label-secondary:#6b7280;
  --dsw-alias-brand-primary:#23262b;
  --dsw-alias-state-success-primary:#2f9e5e;--dsw-alias-state-warn-primary:#c98a1a;
  --dsw-alias-state-error-primary:#cf4438;
}
body{margin:0;background:#f4f5f6;padding:22px;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
${css}
</style></head><body>
<div class="jw-root" style="height:auto">
  <div class="jw-card" style="max-width:960px;margin:0 auto">
    <header><h3>结果</h3><span class="jw-muted jw-small">序时账清洗 · 序时账1.xlsx</span></header>
    <div class="jw-row" style="margin-bottom:10px">
      <span class="jw-stat"><span class="jw-muted jw-small">凭证</span><b>${fmt(2216)}</b></span>
      <span class="jw-stat"><span class="jw-muted jw-small">行</span><b>${fmt(data.total)}</b></span>
      <span class="jw-stat"><span class="jw-muted jw-small">复核改判</span><b>0</b></span>
      <span class="jw-stat"><span class="jw-muted jw-small">置信度</span><b>高 2054 · 中 142 · 低 20</b></span>
    </div>
    <div class="jw-row"><a class="jw-btn" href="#">下载 result · 1.2MB</a></div>
    <div class="jw-sheet">
      <div class="jw-sheet-head">
        <div class="jw-sheet-tabs">${tabs}</div>
        <input class="jw-input" placeholder="搜索这张表">
      </div>
      ${grid}
      <div class="jw-sheet-foot">
        <span>第 1–${data.rows.length} 行 / 共 ${fmt(data.total)} 行</span>
        <span class="jw-spacer"></span>
        <span>只读 · 点单元格复制</span>
        <button class="jw-btn jw-btn-ghost">继续加载</button>
      </div>
    </div>
  </div>
</div>
</body></html>`

const out = join(process.env.TEMP || '/tmp', 'jw_preview_sheet.html')
writeFileSync(out, html, 'utf8')
console.log(out)
