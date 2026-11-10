import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { BucketInfo, JobDetail, RowPage } from '../types'

const PAGE_SIZE = 50
const LEVELS = [
  { key: 'low', label: '仅低置信' },
  { key: 'mid_low', label: '中+低置信' },
  { key: 'all', label: '全部' },
]

export default function ReviewPage({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<JobDetail | null>(null)
  const [ready, setReady] = useState(false)
  const [buckets, setBuckets] = useState<BucketInfo[]>([])
  const [level, setLevel] = useState('mid_low')
  const [bucket, setBucket] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<RowPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 任务可能还在跑：先盯状态，done 之后再去读结果文件
  // （否则会拿到 not_found：vouchers.parquet 还没落盘）
  useEffect(() => {
    let alive = true
    let timer: number | null = null
    const tick = async () => {
      try {
        const detail = await api.job(jobId)
        if (!alive) return
        setJob(detail)
        if (detail.status.state === 'done') {
          setReady(true)
          return
        }
        if (detail.status.state === 'failed') {
          setError(detail.status.message || '任务失败')
          return
        }
        timer = window.setTimeout(tick, 1500)
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    }
    tick()
    return () => { alive = false; if (timer) window.clearTimeout(timer) }
  }, [jobId])

  useEffect(() => {
    api.buckets().then(r => setBuckets(r.items)).catch(() => setBuckets([]))
  }, [])

  const load = useCallback(async () => {
    if (!ready) return
    setLoading(true); setError('')
    try {
      const res = await api.rows(jobId, { level, offset, limit: PAGE_SIZE, bucket })
      setPage(res)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [jobId, level, offset, bucket, ready])

  useEffect(() => { load() }, [load])

  const conf = job?.summary?.confidence_dist ?? {}
  const total = page?.total ?? 0
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + PAGE_SIZE, total)

  const downloadUnreviewed = async () => {
    try {
      const res = await fetch(api.unreviewedUrl(jobId))
      if (!res.ok) throw new Error(`下载失败（${res.status}）`)
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${job?.filename?.replace(/\.[^.]+$/, '') || '未审'}_未审训练数据.json`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <>
      <div className="card">
        <header>
          <h2>复核</h2>
          <span className="hint">{job?.filename ?? jobId}</span>
          <span className="spacer" />
          <span className="tag high">高 {conf['高'] ?? 0}</span>
          <span className="tag mid">中 {conf['中'] ?? 0}</span>
          <span className="tag low">低 {conf['低'] ?? 0}</span>
        </header>

        <div className="flex wrap mb">
          {LEVELS.map(l => (
            <button
              key={l.key}
              className={`btn small ${level === l.key ? 'primary' : ''}`}
              onClick={() => { setLevel(l.key); setOffset(0) }}
            >
              {l.label}
            </button>
          ))}
          <span style={{ width: 12 }} />
          <select
            style={{ width: 230 }}
            value={bucket}
            onChange={e => { setBucket(e.target.value); setOffset(0) }}
          >
            <option value="">全部业务桶</option>
            {buckets.filter(b => b.active !== false)
              .map(b => <option key={b.name} value={b.name}>{b.name}</option>)}
          </select>
          <span className="spacer" />
          <button className="btn small" onClick={downloadUnreviewed}>下载未审训练数据</button>
        </div>

        {!ready && !error && (
          <div className="notice info mb">任务还在跑，完成后自动加载复核清单…</div>
        )}
        {error && <div className="notice err mb">{error}</div>}

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: 110 }}>凭证号</th>
                <th>摘要</th>
                <th style={{ width: 220 }}>科目</th>
                <th className="num" style={{ width: 100 }}>金额</th>
                <th style={{ width: 130 }}>业务分类</th>
                <th style={{ width: 74 }}>置信度</th>
                <th className="num" style={{ width: 76 }}>校准概率</th>
                <th style={{ width: 150 }}>判定来源</th>
              </tr>
            </thead>
            <tbody>
              {(page?.items ?? []).map((r, i) => (
                <tr key={`${r.凭证号}-${i}`}>
                  <td className="mono">{r.凭证号}</td>
                  <td title={r.摘要}>{r.摘要}</td>
                  <td className="small" title={r.科目}>{r.科目}</td>
                  <td className="num">{fmtAmount(r.金额)}</td>
                  <td>{r.业务分类}</td>
                  <td><span className={confClass(r.置信度)}>{r.置信度}</span></td>
                  <td className="num">{r.校准概率 == null ? '-' : r.校准概率.toFixed(3)}</td>
                  <td className="small muted" title={r.判定来源}>{r.判定来源}</td>
                </tr>
              ))}
              {!loading && (page?.items.length ?? 0) === 0 && (
                <tr><td colSpan={8} className="muted">没有需要复核的凭证</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex mt">
          <span className="small muted">
            {loading ? '加载中…' : `共 ${total.toLocaleString()} 张 · 当前 ${from}–${to}`}
          </span>
          <span className="spacer" />
          <button className="btn small" disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>上一页</button>
          <button className="btn small" disabled={to >= total || loading}
            onClick={() => setOffset(offset + PAGE_SIZE)}>下一页</button>
        </div>
        <p className="small muted mt">
          按校准概率升序：最不确定的排最前。低置信基本等于“模型犹豫或科目没见过”。
        </p>
      </div>

    </>
  )
}

function confClass(level: string) {
  if (level === '高') return 'tag high'
  if (level === '中') return 'tag mid'
  if (level === '低') return 'tag low'
  return 'tag plain'
}

function fmtAmount(v: number | null) {
  if (v == null) return '-'
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
}
