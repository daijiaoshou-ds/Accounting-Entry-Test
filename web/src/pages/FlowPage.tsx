import { useCallback, useEffect, useRef, useState } from 'react'
import { flow } from '../api'
import type {
  FieldDef, FlowFeature, FlowFiles, FlowInspect, FlowJobItem, FlowNextResponse,
  FlowReview, FlowStageState,
} from '../types'
import ReviewTable from '../components/ReviewTable'

/**
 * 一个功能的分阶段页面：上传 → 程序跑 → AI/人复核 → 再跑 → 导出。
 *
 * 页面**不知道**这个功能有哪些阶段、要复核什么、能填哪些值——全部来自
 * `/api/features` 与 `/api/flow/{id}/review`。所以加一个功能不用改这里。
 */
interface Props {
  featureId: string
  initialJobId?: string
  onHome: () => void
}

export default function FlowPage({ featureId, initialJobId, onHome }: Props) {
  const [feature, setFeature] = useState<FlowFeature | null>(null)
  const [fields, setFields] = useState<FieldDef[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [localPath, setLocalPath] = useState('')
  const [inspect, setInspect] = useState<FlowInspect | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [jobId, setJobId] = useState<string | null>(initialJobId ?? null)
  const [st, setSt] = useState<FlowNextResponse | null>(null)
  const [review, setReview] = useState<FlowReview | null>(null)
  const [files, setFiles] = useState<FlowFiles | null>(null)
  const [acting, setActing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [over, setOver] = useState(false)
  // 可"接着跑"的上游任务（这个功能声明了 accepts_upstream 才有）
  const [upstreams, setUpstreams] = useState<FlowJobItem[]>([])
  const [upstreamId, setUpstreamId] = useState('')
  // 轮询的重建信号：resume 之后 state 没变（同一个 jobId），得靠它重新拉起 effect
  const [epoch, setEpoch] = useState(0)

  const timer = useRef<number | null>(null)
  const reviewKey = useRef('')

  useEffect(() => {
    flow.features()
      .then(r => setFeature(r.items.find(f => f.id === featureId) ?? null))
      .catch(e => setError(String(e.message ?? e)))
    flow.fields(featureId)
      .then(r => setFields(r.fields))
      .catch(() => setFields([]))
    flow.upstreams(featureId)
      .then(r => setUpstreams(r.items))
      .catch(() => setUpstreams([]))
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [featureId])

  // ── 轮询：任务在后台跑（HTTP 请求不阻塞），这里只问"下一步" ──
  useEffect(() => {
    if (!jobId) return
    let stop = false

    const loadReview = async (stage: string, count: number) => {
      const k = `${stage}:${count}`
      if (reviewKey.current === k) return
      reviewKey.current = k
      try {
        const r = await flow.review(jobId, { stage, limit: 500 })
        if (!stop) setReview(r)
      } catch (e) {
        if (!stop) setError(String((e as Error).message))
      }
    }

    const tick = async () => {
      if (stop) return
      try {
        const n = await flow.next(jobId)
        if (stop) return
        setSt(n)
        if (n.status === 'failed') {
          setError(n.error || '任务失败')
          return
        }
        if (n.next.actor === 'ai' && n.next.stage) {
          await loadReview(n.next.stage, n.next.count ?? 0)
        } else if (n.status === 'done') {
          setReview(null)
          const f = await flow.files(jobId)
          if (!stop) setFiles(f)
          return
        }
        if (!stop) timer.current = window.setTimeout(tick, 1200)
      } catch (e) {
        if (!stop) setError(String((e as Error).message))
      }
    }
    tick()
    return () => {
      stop = true
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [jobId, epoch])

  const pickFile = useCallback(async (f: File) => {
    setFile(f); setError(''); setNotice(''); setInspect(null)
    setActing(true)
    try {
      const info = await flow.inspect(featureId, f)
      setInspect(info)
      setMapping(info.suggested_mapping ?? {})
      if (info.missing.length) {
        setError(`这些必需字段没认出来：${info.missing.join('、')}，请在下面手工指定`)
      }
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setActing(false)
    }
  }, [featureId])

  const start = async () => {
    setError(''); setNotice(''); setActing(true)
    try {
      const res = file
        ? await flow.create(featureId, file, mapping)
        : await flow.createLocal(featureId, localPath.trim(), mapping)
      reviewKey.current = ''
      setJobId(res.job_id)
      // 记住任务号（replaceState 不触发 hashchange，页面不会被重建）
      window.history.replaceState(null, '', `#/f/${featureId}/${res.job_id}`)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setActing(false)
    }
  }

  const startFromUpstream = async () => {
    if (!upstreamId) return
    setError(''); setNotice(''); setActing(true)
    try {
      const res = await flow.createFromUpstream(featureId, upstreamId)
      reviewKey.current = ''
      setJobId(res.job_id)
      window.history.replaceState(null, '', `#/f/${featureId}/${res.job_id}`)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setActing(false)
    }
  }

  const submit = async (decisions: { key: string; value: string }[]) => {
    if (!jobId) return
    setError(''); setNotice(''); setActing(true)
    try {
      const res = await flow.decide(jobId, {
        stage: review?.stage ?? undefined, decisions,
      })
      reviewKey.current = ''
      setReview(null)
      if (res.rejected.length) {
        setError(`${res.rejected.length} 条被驳回：` +
          res.rejected.map(r => `${r.key}（${r.reason}）`).join('；'))
      } else {
        setNotice(`已采纳 ${res.accepted} 条`)
      }
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setActing(false)
    }
  }

  const waive = async () => {
    if (!jobId) return
    setError(''); setNotice(''); setActing(true)
    try {
      await flow.decide(jobId, { stage: review?.stage ?? undefined, decisions: [], waive: true })
      reviewKey.current = ''
      setReview(null)
      setNotice('这批不再改，剩下的会在结果里标「需复核」')
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setActing(false)
    }
  }

  const resume = async () => {
    if (!jobId) return
    setError('')
    try {
      await flow.resume(jobId)
      reviewKey.current = ''
      setEpoch(e => e + 1)     // 让轮询 effect 重新起来
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const restart = () => {
    if (timer.current) window.clearTimeout(timer.current)
    reviewKey.current = ''
    setJobId(null); setSt(null); setReview(null); setFiles(null)
    setInspect(null); setFile(null); setMapping({}); setError(''); setNotice('')
    setUpstreamId('')
    window.history.replaceState(null, '', `#/f/${featureId}`)
  }

  const running = !!st && (st.running || st.status === 'running' || st.status === 'created')
  const awaiting = !!st && st.status === 'awaiting_review' && !!review
  const done = !!st && st.status === 'done'
  const failed = !!st && st.status === 'failed'
  const missing = fields.filter(f => f.required && !mapping[f.key]).map(f => f.label)

  return (
    <>
      <div className="card">
        <header>
          <button className="btn small" onClick={onHome}>← 功能</button>
          <h2>{feature?.name ?? featureId}</h2>
          <span className="hint">{feature?.description}</span>
        </header>
        {feature && <Stages stages={feature.stages} states={st?.stages ?? []} />}
        {st && (
          <div className="small muted mt">
            任务 <span className="mono">{st.job_id}</span>
            {st.input?.filename ? ` · ${st.input.filename}` : ''}
            {' · '}命令行等价：<span className="mono">python journal.py next {st.job_id}</span>
          </div>
        )}
      </div>

      {error && <div className="notice err mb">{error}</div>}
      {notice && !error && <div className="notice ok mb">{notice}</div>}

      {/* ① 选文件 */}
      {!jobId && !inspect && (
        <div className="card">
          <header>
            <h2>1 · 选择序时账</h2>
            <span className="hint">
              支持 {feature?.accept.join(' / ') ?? 'xlsx / csv'}，读表后确认字段映射
            </span>
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
              accept={(feature?.accept ?? ['.xlsx', '.csv']).join(',')}
              style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; if (f) pickFile(f) }}
            />
            <p><strong>点击选择文件</strong>，或把序时账拖到这里</p>
            <p className="small muted mt">原件会存进任务目录，同一个任务可反复重跑</p>
          </label>
          {upstreams.length > 0 && (
            <div className="notice info mt">
              <div className="flex wrap">
                <strong>或者接着已有的清洗任务跑</strong>
                <span className="small">同一份账不必再分类、再复核一遍（分类阶段直接跳过）</span>
              </div>
              <div className="row mt">
                <select className="grow" value={upstreamId}
                        onChange={e => setUpstreamId(e.target.value)}>
                  <option value="">（不用上游，重新读表分类）</option>
                  {upstreams.map(j => (
                    <option key={j.job_id} value={j.job_id}>
                      {j.filename} · {j.vouchers ?? '?'} 凭证 · {j.created_at}
                    </option>
                  ))}
                </select>
                <button className="btn primary" disabled={!upstreamId || acting}
                        onClick={startFromUpstream}>
                  从该任务起跑
                </button>
              </div>
            </div>
          )}

          <details className="mt">
            <summary className="small muted" style={{ cursor: 'pointer' }}>
              或者用本机路径（调试用，免上传）
            </summary>
            <div className="row mt">
              <input
                type="text" className="grow"
                placeholder="D:\账\序时账.xlsx"
                value={localPath}
                onChange={e => setLocalPath(e.target.value)}
              />
              <button className="btn" disabled={!localPath.trim() || acting} onClick={start}>
                从路径建任务
              </button>
            </div>
          </details>
          {acting && <p className="small muted mt">读表中…</p>}
        </div>
      )}

      {/* ② 确认映射并开跑 */}
      {!jobId && inspect && (
        <div className="card">
          <header>
            <h2>2 · 确认字段映射</h2>
            <span className="hint">
              {inspect.filename} · {inspect.rows.toLocaleString()} 行 ·
              {' '}{inspect.columns.length} 列 · 引擎 {inspect.engine}
            </span>
            <span className="spacer" />
            <button className="btn small" onClick={() => { setInspect(null); setFile(null) }}>
              换一个文件
            </button>
          </header>

          <div className="mapping">
            {fields.map(f => (
              <FieldRow
                key={f.key}
                field={f}
                columns={inspect.columns}
                value={mapping[f.key] ?? ''}
                onChange={v => {
                  const next = { ...mapping }
                  if (v) next[f.key] = v
                  else delete next[f.key]
                  setMapping(next)
                }}
              />
            ))}
          </div>

          {inspect.unmapped.length > 0 && (
            <p className="small muted mt">
              没被认领的列（不影响，会原样保留在导出文件里）：
              {inspect.unmapped.slice(0, 8).join('、')}
              {inspect.unmapped.length > 8 ? ` 等 ${inspect.unmapped.length} 列` : ''}
            </p>
          )}

          <div className="mt flex wrap">
            <button className="btn primary" disabled={acting || !!missing.length} onClick={start}>
              开始
            </button>
            {missing.length > 0 && (
              <span className="small" style={{ color: 'var(--danger)' }}>
                还缺：{missing.join('、')}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ③ 运行中 */}
      {jobId && running && (
        <div className="card">
          <header><h2>运行中</h2></header>
          <div className="progress mb"><i style={{ width: '45%' }} /></div>
          <p className="small muted">
            程序在后台跑（分类阶段吃 GPU，一本账几十秒到几分钟）。这一页会自动刷新。
          </p>
        </div>
      )}

      {/* ④ 失败 */}
      {jobId && failed && (
        <div className="card">
          <header><h2>失败了</h2></header>
          <div className="notice err mb">{st?.error}</div>
          <button className="btn" onClick={resume}>修好后重试（resume）</button>
        </div>
      )}

      {/* ⑤ 复核：两条路，交给 AI 或自己改 */}
      {jobId && awaiting && review && (
        <>
          <AiHandoff jobId={jobId} featureName={st?.feature_name ?? feature?.name ?? ''}
                     stageTitle={st?.next?.title ?? review.title}
                     stage={review.stage ?? ''} count={review.total} />
          <ReviewTable payload={review} busy={acting} onSubmit={submit} onWaive={waive} />
        </>
      )}

      {/* ⑥ 结果 */}
      {jobId && done && (
        <ResultCard files={files} onRestart={restart} />
      )}

      {!jobId && !inspect && feature && (
        <div className="card">
          <header><h2>这个功能会经过哪些阶段</h2></header>
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
            {feature.stages.map(s => (
              <li key={s.id}>
                <strong style={{ color: 'var(--text-2)' }}>{s.title}</strong>
                {' — '}
                {s.actor === 'ai' ? '停下来等复核（AI 或人给结论）' : '程序跑，结论确定'}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

// ============================================================================

function FieldRow({ field, columns, value, onChange }: {
  field: FieldDef
  columns: string[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <>
      <label>
        {field.label}
        {field.required && <span className="req">*</span>}
      </label>
      <select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">（不映射）</option>
        {columns.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    </>
  )
}

const STATUS_ICON: Record<string, string> = {
  done: '✓', skipped: '↷', awaiting: '⏸', running: '●', failed: '✕', pending: '○',
}

function Stages({ stages, states }: {
  stages: FlowFeature['stages']
  states: FlowStageState[]
}) {
  const byId = new Map(states.map(s => [s.id, s]))
  return (
    <div className="stages">
      {stages.map((s, i) => {
        const cur = byId.get(s.id)
        const status = cur?.status ?? 'pending'
        return (
          <div key={s.id} className={`stage ${status}`} title={cur?.note || cur?.error || ''}>
            <span className="dot">{STATUS_ICON[status] ?? '○'}</span>
            <span className="name">{s.title}</span>
            {s.actor === 'ai' && <span className="tag plain">复核</span>}
            {cur?.elapsed != null && <span className="mono small muted">{cur.elapsed}s</span>}
            {i < stages.length - 1 && <span className="arrow">→</span>}
          </div>
        )
      })}
    </div>
  )
}

function ResultCard({ files, onRestart }: { files: FlowFiles | null; onRestart: () => void }) {
  if (!files) {
    return <div className="card"><p className="small muted">读产物中…</p></div>
  }
  const cf = files.cashflow ?? {}
  const facts = files.facts ?? {}
  const fmt = (n: unknown) => typeof n === 'number'
    ? n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
    : '—'
  const num = (k: string) => (typeof facts[k] === 'number' ? facts[k] as number : null)
  const buckets = (facts.bucket_counts ?? {}) as Record<string, number>
  const conf = (facts.confidence_dist ?? {}) as Record<string, number>
  const confTotal = Object.values(conf).reduce((a, b) => a + b, 0)

  // 有没有现金流统计决定这是"编表"还是"分类"——前端不认功能名
  const isCashflow = typeof cf.money_rows === 'number'

  return (
    <>
      <div className="card">
        <header>
          <h2>完成</h2>
          <span className="hint">
            {files.files.length} 个文件 · 导出 {files.export?.format ?? '?'}
            {files.export?.seconds != null ? ` / ${files.export.seconds}s` : ''}
          </span>
          <span className="spacer" />
          <button className="btn small" onClick={onRestart}>再跑一本</button>
        </header>

        <div className="stat-grid">
          {isCashflow ? (
            <>
              <Stat k="货币资金分录" v={fmt(cf.money_rows)} />
              <Stat k="待复核" v={fmt(cf.needs_review_rows)}
                    sub={cf.money_rows ? `${((Number(cf.needs_review_rows) / Number(cf.money_rows)) * 100).toFixed(1)}%` : ''} />
              <Stat k="净现金" v={fmt(cf.net_cash)} />
              <Stat k="涉及凭证" v={fmt(cf.money_vouchers)} />
            </>
          ) : (
            <>
              <Stat k="凭证数" v={fmt(num('vouchers'))} />
              <Stat k="分录行" v={fmt(num('rows'))} />
              <Stat k="高置信占比"
                    v={confTotal ? `${(((conf['高'] ?? 0) / confTotal) * 100).toFixed(1)}%` : '—'}
                    sub={confTotal ? `中 ${conf['中'] ?? 0} · 低 ${conf['低'] ?? 0}` : ''} />
              <Stat k="复核改判" v={fmt(num('bucket_overrides'))} />
            </>
          )}
        </div>

        <div className="mt flex wrap">
          {files.files.map(f => (
            <a key={f.key} className="btn primary" href={f.url}>{f.label}
              <span className="small" style={{ opacity: .8 }}>
                （{(f.bytes / 1024 / 1024).toFixed(1)}MB）
              </span>
            </a>
          ))}
        </div>
        {files.sheets.length > 1 && (
          <p className="small muted mt">
            一个文件里有 {files.sheets.length} 张表：{files.sheets.map(s => s.sheet).join(' / ')}
          </p>
        )}
        {files.export?.hint && <div className="notice warn mt">{files.export.hint}</div>}
        {(files.patch?.applied ?? 0) > 0 && (
          <p className="small muted mt">复核结论已并入 {files.patch.applied} 条</p>
        )}
      </div>

      {Object.keys(buckets).length > 0 && (
        <div className="card">
          <header>
            <h2>业务桶分布</h2>
            <span className="hint">按凭证数</span>
          </header>
          <BucketBars counts={buckets} />
        </div>
      )}

      {files.statement.length > 0 && (
        <div className="card">
          <header>
            <h2>现金流量表</h2>
            <span className="hint">按项目汇总（仅货币资金行）</span>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>活动</th><th>项目</th>
                  <th className="num">收现</th><th className="num">付现</th>
                  <th className="num">净额</th><th className="num">笔数</th>
                </tr>
              </thead>
              <tbody>
                {files.statement.map((r, i) => (
                  <tr key={i}>
                    <td>{String(r['活动类型'] ?? '')}</td>
                    <td>{String(r['现金流量项目'] ?? '')}</td>
                    <td className="num">{fmt(r['收现'])}</td>
                    <td className="num">{fmt(r['付现'])}</td>
                    <td className="num">{fmt(r['净额'])}</td>
                    <td className="num">{fmt(r['笔数'])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
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

/**
 * 「交给 AI」——网页**不**调模型 API。
 *
 * 复核需要 agent 框架：能读文件、跑脚本、看整批上下文、失败了重来。裸调一次
 * completion 拿不回可信结论（单轮、无上下文、返回格式还得赌）。所以这里只做
 * 一件事：把"任务号 + 停在哪一步"变成一句可以直接粘给 AI 的话。
 *
 * 粘过去之后 AI 走 `journal next / submit`，网页轮询 `/next` 会自动刷新——
 * 两边读的是同一份 `state.json`。
 */
function AiHandoff({ jobId, featureName, stageTitle, stage, count }:
  { jobId: string; featureName: string; stageTitle: string; stage: string; count: number }) {
  const [copied, setCopied] = useState(false)
  const prompt = `帮我接着跑任务 ${jobId}（${featureName}），当前停在「${stageTitle}」，`
    + `${count} 条待复核。`

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      // 非 https / 无权限时退回选中（本地 127.0.0.1 一般是允许的）
      const el = document.createElement('textarea')
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      el.remove()
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <div className="card">
      <header>
        <h2>这一步可以交给 AI</h2>
        <span className="hint">程序已经跑完，只差判断</span>
      </header>
      <p className="small muted" style={{ marginTop: 0 }}>
        把这句发给 AI（WorkBuddy / Claude Code 这类能读写本机文件的 agent）。
        它会读待办、逐条判、把结论写回任务，这一页会自动刷新——
        <strong>你也可以直接在下面自己改</strong>，两条路改的是同一份结论。
      </p>
      <div className="row">
        <code className="grow mono small" style={{ padding: '8px 10px', background: 'var(--panel-2)',
                     borderRadius: 6, overflowX: 'auto', whiteSpace: 'nowrap' }}>
          {prompt}
        </code>
        <button className="btn primary" onClick={() => copy(prompt)}>
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <details className="mt">
        <summary className="small muted" style={{ cursor: 'pointer' }}>
          不用 AI，用命令行（同一套动作）
        </summary>
        <pre className="small mono" style={{ margin: '8px 0 0', overflowX: 'auto' }}>
{`python journal.py next ${jobId}
python journal.py submit ${jobId} --file decisions.json   # 或 --empty --stage ${stage}`}
        </pre>
      </details>
    </div>
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
