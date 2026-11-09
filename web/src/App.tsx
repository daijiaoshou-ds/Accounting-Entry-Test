import { useEffect, useState } from 'react'
import { api } from './api'
import type { Health } from './types'
import CleanPage from './pages/CleanPage'
import ReviewPage from './pages/ReviewPage'

type Tab = 'clean' | 'review'

interface RecentJob {
  job_id: string
  filename: string
  rows: number
  vouchers: number
  created_at: string
  status: { state: string }
}

export default function App() {
  const [tab, setTab] = useState<Tab>('clean')
  const [health, setHealth] = useState<Health | null>(null)
  const [healthErr, setHealthErr] = useState('')
  // 清洗完成后把 job 交给复核页（同一份结果，不重复上传）
  const [jobId, setJobId] = useState<string | null>(null)
  const [recent, setRecent] = useState<RecentJob[]>([])

  useEffect(() => {
    api.health().then(setHealth).catch(e => setHealthErr(String(e.message ?? e)))
    api.jobs(6).then(r => setRecent(r.items)).catch(() => setRecent([]))
  }, [])

  const modelText = health
    ? health.model.loaded
      ? `模型已加载${health.model.device ? `（${health.model.device}）` : ''}`
      : health.model.configured
        ? '模型待加载'
        : '无模型（纯程序）'
    : healthErr || '连接后端中…'

  const openJob = (id: string) => {
    setJobId(id)
    setTab('review')
  }

  return (
    <div className="app">
      <div className="topbar">
        <h1>序时账清洗</h1>
        <span className="sub">会计分析工具箱</span>
        <span className="spacer" />
        <span
          className="sub flex"
          title={health ? `v${health.version}，${health.buckets} 个业务桶` : ''}
        >
          <i
            className="badge-dot"
            style={{
              background: healthErr
                ? 'var(--danger)'
                : health?.status === 'ok' ? 'var(--ok)' : 'var(--text-3)',
            }}
          />
          {modelText}
        </span>
      </div>

      <div className="tabs">
        <button
          className={`tab ${tab === 'clean' ? 'active' : ''}`}
          onClick={() => setTab('clean')}
        >
          清洗
        </button>
        <button
          className={`tab ${tab === 'review' ? 'active' : ''}`}
          onClick={() => setTab('review')}
          disabled={!jobId}
          title={jobId ? '' : '先跑完一本账'}
        >
          复核
        </button>

        {recent.length > 0 && (
          <>
            <span className="spacer" />
            <span className="small muted" style={{ alignSelf: 'center' }}>最近：</span>
            {recent.slice(0, 3).map(j => (
              <button
                key={j.job_id}
                className="tab small"
                style={{ padding: '4px 9px', fontSize: 12 }}
                title={`${j.created_at} · ${j.vouchers} 凭证`}
                onClick={() => openJob(j.job_id)}
              >
                {j.filename || j.job_id}
                <span className="muted"> · {j.vouchers}</span>
              </button>
            ))}
          </>
        )}
      </div>

      {/* 两个页面都常驻，切 tab 只切显示——否则上传的文件和映射会被卸载丢掉 */}
      <div style={{ display: tab === 'clean' ? 'block' : 'none' }}>
        <CleanPage
          onJobReady={(id) => { setJobId(id) }}
          onOpenReview={() => setTab('review')}
        />
      </div>
      {jobId && (
        <div style={{ display: tab === 'review' ? 'block' : 'none' }}>
          <ReviewPage jobId={jobId} />
        </div>
      )}
    </div>
  )
}
