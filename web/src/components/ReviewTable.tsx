import { useMemo, useState } from 'react'
import type { FlowReview, FlowReviewColumn } from '../types'

/**
 * 通用复核表：一屏渲染任意 AI 阶段的待办。
 *
 * 列、取值清单、键名全部来自后端（`/api/flow/{id}/review` 的 columns /
 * value_enum / key_name）——所以"复核业务分类"和"复核现金流条目"是同一个
 * 组件，加功能不用再写表格。
 */
interface Props {
  payload: FlowReview
  busy: boolean
  onSubmit: (decisions: { key: string; value: string; note?: string }[]) => void
  onWaive: () => void
}

export default function ReviewTable({ payload, busy, onSubmit, onWaive }: Props) {
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [q, setQ] = useState('')
  const [detail, setDetail] = useState(false)
  const [showAll, setShowAll] = useState(false)

  const items = payload.items
  // 后端把补丁键也放在 item 里（`key`），和「凭证号 / 行号」往往重复，去掉一份
  const hide = useMemo(() => {
    const out = new Set<string>()
    const first = items[0]
    if (!first) return out
    for (const k of ['凭证号', '行号']) {
      if (k in first && String(first[k]) === String(first.key)) out.add(k)
    }
    return out
  }, [items])

  const all = payload.columns.filter(c => c.key !== 'key' && !hide.has(c.key))
  const cols = showAll ? all : all.filter(c => !c.secondary)
  const hidden = all.length - cols.length

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return items
    return items.filter(it =>
      Object.values(it).some(v => String(v ?? '').toLowerCase().includes(needle)))
  }, [items, q])

  const changed = Object.keys(edits).length
  const set = (key: string, value: string) => {
    setEdits(prev => {
      const next = { ...prev }
      if (value) next[key] = value
      else delete next[key]
      return next
    })
  }

  const submit = () => {
    const payloadIn = Object.entries(edits).map(([key, value]) => ({ key, value }))
    if (!payloadIn.length) return
    onSubmit(payloadIn)
    setEdits({})
  }

  return (
    <div className="card">
      <header>
        <h2>复核 · {payload.title}</h2>
        <span className="hint">
          {payload.total} 条
          {payload.truncated && '（超出单批上限，先处理这一批）'}
        </span>
        <span className="spacer" />
        {hidden > 0 && (
          <button className="btn small" onClick={() => setShowAll(v => !v)}>
            {showAll ? `收起 ${hidden} 列` : `展开 ${hidden} 列`}
          </button>
        )}
        <button className="btn small" onClick={() => setDetail(d => !d)}>
          {detail ? '收起说明' : '看说明'}
        </button>
      </header>

      {detail && (
        <div className="notice info mb" style={{ whiteSpace: 'pre-wrap' }}>
          {payload.help}
          <div className="small muted mt">
            键：{payload.key_name} · 可填取值 {payload.value_enum.length} 个。
            认同原判的行不用改，提交时只带上改过的。
          </div>
        </div>
      )}

      <div className="row mb">
        <input
          type="text"
          className="grow"
          placeholder="搜索凭证号 / 摘要 / 科目…"
          value={q}
          onChange={e => setQ(e.target.value)}
        />
        <button className="btn primary" disabled={busy || !changed} onClick={submit}>
          提交 {changed} 条修改
        </button>
        <button className="btn" disabled={busy} onClick={onWaive}
                title="本批不再改，剩下的会在结果里标「需复核」">
          本批没有要改的
        </button>
      </div>

      <div className="table-wrap">
        <table className="review-table">
          <thead>
            <tr>
              <th style={{ width: 32 }}>#</th>
              <th style={{ width: 150 }}>{labelOf(payload.key_name)}</th>
              {cols.map(c => <th key={c.key} className={c.kind === 'text' ? 'wrap' : ''}>{c.label}</th>)}
              <th style={{ width: 190 }}>改为</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((it, i) => {
              const key = String(it.key)
              const editing = key in edits
              return (
                <tr key={key} className={editing ? 'edited' : ''}>
                  <td className="muted small">{i + 1}</td>
                  <td className="mono key">{key}</td>
                  {cols.map(c => (
                    <td key={c.key} className={c.kind === 'text' ? 'wrap' : ''}>
                      <Cell col={c} value={it[c.key]} />
                    </td>
                  ))}
                  <td className="select">
                    <select
                      value={edits[key] ?? ''}
                      onChange={e => set(key, e.target.value)}
                    >
                      <option value="">— 保持原判 —</option>
                      {payload.value_enum.map(v => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              )
            })}
            {!rows.length && (
              <tr><td colSpan={cols.length + 3} className="muted small">没有匹配的行</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function labelOf(keyName: string) {
  if (keyName === 'row_no') return '行号'
  if (keyName === 'voucher_id') return '凭证号'
  return '键'
}

function Cell({ col, value }: { col: FlowReviewColumn; value: unknown }) {
  if (value == null || value === '') return <span className="muted">—</span>
  if (col.kind === 'list') {
    const arr = Array.isArray(value) ? value : [value]
    return <span>{arr.slice(0, 4).join('、')}{arr.length > 4 ? ` 等 ${arr.length} 项` : ''}</span>
  }
  if (col.kind === 'tag') {
    const v = String(value)
    const cls = v === '高' ? 'high' : v === '中' ? 'mid' : v === '低' ? 'low' : 'plain'
    return <span className={`tag ${cls}`}>{v}</span>
  }
  if (col.kind === 'num') {
    const n = Number(value)
    return <span className="mono">{Number.isFinite(n)
      ? (Number.isInteger(n) ? n.toLocaleString() : n.toFixed(4))
      : String(value)}</span>
  }
  const text = String(value)
  const max = col.kind === 'text' ? 90 : 40
  return <span title={text}>{text.length > max ? `${text.slice(0, max)}…` : text}</span>
}
