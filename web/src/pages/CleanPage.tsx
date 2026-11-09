import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { FieldDef, InspectResult, JobDetail } from '../types'

const FIELD_LABELS: Record<string, string> = {
  voucher_no: '凭证号',
  subject: '一级科目',
  subject_name: '科目名称/明细',
  summary: '摘要',
  debit: '借方金额',
  credit: '贷方金额',
  bookkeeper: '制单人',
}
const REQUIRED = ['voucher_no', 'subject']

interface Props {
  onJobReady: (jobId: string) => void
  onOpenReview: () => void
}

export default function CleanPage({ onJobReady, onOpenReview }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [inspect, setInspect] = useState<InspectResult | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [fields, setFields] = useState<FieldDef[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobDetail | null>(null)
  const [over, setOver] = useState(false)
  const [localPath, setLocalPath] = useState('')
  const timer = useRef<number | null>(null)

  useEffect(() => {
    api.fields().then(r => setFields(r.fields)).catch(() => {
      setFields(Object.entries(FIELD_LABELS).map(([key, label]) => ({
        key, label, required: REQUIRED.includes(key),
      })))
    })
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [])

  const pickFile = useCallback(async (f: File) => {
    setFile(f); setError(''); setJob(null); setJobId(null); setInspect(null)
    setBusy(true)
    try {
      const info = await api.inspect(f)
      setInspect(info)
      setMapping(info.suggested_mapping ?? {})
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }, [])

  const start = async () => {
    setError(''); setJob(null)
    if (!inspect) return
    setBusy(true)
    try {
      const res = file
        ? await api.createJob(file, mapping)
        : await api.createJobLocal(localPath.trim())
      setJobId(res.job_id)
      onJobReady(res.job_id)
      poll(res.job_id)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  const poll = (id: string) => {
    if (timer.current) window.clearInterval(timer.current)
    const tick = async () => {
      try {
        const detail = await api.job(id)
        setJob(detail)
        if (detail.status.state === 'done' || detail.status.state === 'failed') {
          if (timer.current) window.clearInterval(timer.current)
          setBusy(false)
          if (detail.status.state === 'failed') {
            setError(detail.status.message || '清洗失败')
          }
        }
      } catch (e) {
        setError((e as Error).message)
        if (timer.current) window.clearInterval(timer.current)
        setBusy(false)
      }
    }
    tick()
    timer.current = window.setInterval(tick, 1000)
  }

  const missing = fields
    .filter(f => f.required && !mapping[f.key])
    .map(f => f.label)

  const summary = job?.summary
  const conf = summary?.confidence_dist ?? {}
  const confTotal = Object.values(conf).reduce((a, b) => a + b, 0) || 1

  return (
    <>
      {!inspect && (
        <div className="card">
          <header>
            <h2>1 · 选择序时账</h2>
            <span className="hint">支持 xlsx / xls / csv，读表后先确认字段映射</span>
          </header>

          <label
            className={`drop ${over ? 'over' : ''}`}
            onDragOver={e => { e.preventDefault(); setOver(true) }}
            onDragLeave={() => setOver(false)}
            onDrop={e => {
              e.preventDefault(); setOver(false)
              const f = e.dataTransfer.files?.[0]
              if (f) pickFile(f)
            }}
          >
            <input
              type="file"
              accept=".xlsx,.xls,.xlsm,.xlsb,.csv,.txt,.tsv"
              style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; if (f) pickFile(f) }}
            />
            <p><strong>点击选择文件</strong>，或把序时账拖到这里</p>
            <p className="small muted mt">一份文件一次只读一遍；原件会保存在任务目录里，可复现</p>
          </label>

          <details className="mt">
            <summary className="small muted" style={{ cursor: 'pointer' }}>或者用本机路径（调试用，免上传）</summary>
            <div className="row mt">
              <input
                type="text"
                className="grow"
                placeholder="D:\\账\\序时账.xlsx"
                value={localPath}
                onChange={e => setLocalPath(e.target.value)}
              />
              <button className="btn" disabled={!localPath.trim() || busy} onClick={start}>
                从路径建任务
              </button>
            </div>
          </details>

          {busy && <p className="small muted mt">读表中…</p>}
          {error && <div className="notice err mt">{error}</div>}
        </div>
      )}

      {inspect && !(job && job.status.state === 'done') && (
        <div className="card">
          <header>
            <h2>2 · 确认字段映射</h2>
            <span className="hint">
              {inspect.filename} · {inspect.rows.toLocaleString()} 行 · {inspect.columns.length} 列
            </span>
            <span className="spacer" />
            <button
              className="btn small"
              onClick={() => { setInspect(null); setFile(null); setError('') }}
            >
              换一个文件
            </button>
          </header>

          <div className="mapping">
            {fields.map(f => (
              <Fragment key={f.key}>
                <label>
                  {f.label}
                  {f.required && <span className="req">*</span>}
                </label>
                <select
                  value={mapping[f.key] ?? ''}
                  onChange={e => {
                    const next = { ...mapping }
                    if (e.target.value) next[f.key] = e.target.value
                    else delete next[f.key]
                    setMapping(next)
                  }}
                >
                  <option value="">（不映射）</option>
                  {inspect.columns.map(c => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </Fragment>
            ))}
          </div>

          <div className="mt flex wrap">
            <button className="btn primary" disabled={busy || !!missing.length} onClick={start}>
              开始清洗
            </button>
            {missing.length > 0 && (
              <span className="small" style={{ color: 'var(--danger)' }}>
                还缺：{missing.join('、')}
              </span>
            )}
            {!missing.length && (
              <span className="small muted">
                模型 + 程序融合；约 {Math.max(3, Math.round(inspect.rows / 1800))} 秒
              </span>
            )}
          </div>

          <details className="mt">
            <summary className="small muted" style={{ cursor: 'pointer' }}>看前 20 行</summary>
            <div className="table-wrap mt">
              <table>
                <thead>
                  <tr>{inspect.columns.map(c => <th key={c}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {inspect.preview.map((row, i) => (
                    <tr key={i}>
                      {inspect.columns.map(c => (
                        <td key={c} className="mono">{formatCell(row[c])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}

      {jobId && job && job.status.state !== 'done' && (
        <div className="card">
          <header><h2>3 · 清洗中</h2></header>
          <div className="progress mb"><i style={{ width: `${job.status.progress}%` }} /></div>
          <p className="small muted">
            {job.status.progress}% · {job.status.message || job.status.state}
          </p>
          {error && <div className="notice err mt">{error}</div>}
        </div>
      )}

      {job?.status.state === 'done' && summary && (
        <>
          <div className="card">
            <header>
              <h2>3 · 结果</h2>
              <span className="hint">{job.filename}</span>
              <span className="spacer" />
              <button className="btn small primary" onClick={onOpenReview}>去复核</button>
            </header>

            <div className="stat-grid">
              <Stat k="凭证数" v={summary.vouchers.toLocaleString()} />
              <Stat k="分录行" v={summary.rows.toLocaleString()} />
              <Stat
                k="覆盖率"
                v={summary.coverage != null ? `${(summary.coverage * 100).toFixed(1)}%` : '-'}
              />
              <Stat
                k="高置信占比"
                v={`${(((conf['高'] ?? 0) / confTotal) * 100).toFixed(1)}%`}
                sub={`低 ${conf['低'] ?? 0}`}
              />
            </div>

            <div className="mt flex wrap small">
              <span className="tag plain">{summary.engine.mode || '纯模型'}</span>
              {summary.engine.model_calls != null && (
                <span className="tag plain">模型判定 {summary.engine.model_calls}</span>
              )}
              {summary.engine.hard_rule_vouchers != null && (
                <span className="tag plain">硬约束 {summary.engine.hard_rule_vouchers}</span>
              )}
              {summary.engine.fallback_to_parent ? (
                <span className="tag mid">父级回退 {summary.engine.fallback_to_parent}</span>
              ) : null}
            </div>

            {(summary.unknown_subjects?.vouchers ?? 0) > 0 && (
              <div className="notice warn mt">
                有 <strong>{summary.unknown_subjects?.vouchers}</strong> 张凭证含模型没见过的科目：
                {(summary.unknown_subjects?.subjects ?? []).slice(0, 8).map(s => (
                  <span key={s.name} className="tag plain" style={{ marginLeft: 6 }}>
                    {s.name} · {s.vouchers}
                  </span>
                ))}
                <div className="small mt">
                  这些凭证仍由模型按摘要判定，但科目信号缺位。多数情况是别名表没维护——
                  把上面的科目补进 <span className="mono">subject_aliases.json</span> 后重跑即可。
                </div>
              </div>
            )}

            <div className="mt flex wrap">
              <a className="btn" href={api.exportUrl(job.job_id, 'result')}>导出分类结果</a>
              <a className="btn" href={api.exportUrl(job.job_id, 'vouchers')}>导出凭证汇总</a>
              <a className="btn" href={api.exportUrl(job.job_id, 'detail')}>导出凭证明细</a>
            </div>
          </div>

          <div className="card">
            <header>
              <h2>桶分布</h2>
              <span className="hint">按凭证数</span>
            </header>
            <BucketBars counts={summary.bucket_counts} />
          </div>
        </>
      )}
    </>
  )
}

function Stat({ k, v, sub }: { k: string; v: string; sub?: string }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}{sub && <small> · {sub}</small>}</div>
    </div>
  )
}

function BucketBars({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1])
  const max = entries.length ? entries[0][1] : 1
  if (!entries.length) return <p className="muted small">没有数据</p>
  return (
    <div className="bars">
      {entries.map(([name, n]) => (
        <div className="bar-row" key={name}>
          <span className="name" title={name}>{name}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(n / max) * 100}%` }} />
          </span>
          <span className="val">{n.toLocaleString()}</span>
        </div>
      ))}
    </div>
  )
}

function formatCell(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'number') return String(v)
  const s = String(v)
  return s.length > 40 ? `${s.slice(0, 40)}…` : s
}
