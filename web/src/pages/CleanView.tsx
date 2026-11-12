import { useEffect, useState } from 'react'
import { api } from '../api'
import type { JobStatus } from '../types'
import CleanPage from './CleanPage'
import ReviewPage from './ReviewPage'

/**
 * 序时账清洗（老入口）：清洗 + 复核两个 tab，仍然走 `/api/jobs/*` 那套
 * 一次性任务。等清洗也迁进编排层（架构文档第 4 步）之后，这一页会被
 * `FlowPage` 取代。
 */
interface RecentJob {
  job_id: string
  filename: string
  rows: number
  vouchers: number
  created_at: string
  status: JobStatus
}

export default function CleanView({ onNav }: { onNav: (hash: string) => void }) {
  const [tab, setTab] = useState<'clean' | 'review'>('clean')
  // 清洗完成后把 job 交给复核页（同一份结果，不重复上传）
  const [jobId, setJobId] = useState<string | null>(null)
  const [recent, setRecent] = useState<RecentJob[]>([])

  useEffect(() => {
    api.jobs(6).then(r => setRecent(r.items)).catch(() => setRecent([]))
  }, [])

  return (
    <>
      <div className="tabs">
        <button className={`tab ${tab === 'clean' ? 'active' : ''}`} onClick={() => setTab('clean')}>
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
        <span className="spacer" />
        <button className="tab small" onClick={() => onNav('#/')}>← 功能</button>

        {recent.length > 0 && (
          <>
            <span className="small muted" style={{ alignSelf: 'center' }}>最近：</span>
            {recent.slice(0, 3).map(j => (
              <button
                key={j.job_id}
                className="tab small"
                style={{ padding: '4px 9px', fontSize: 12 }}
                title={`${j.created_at} · ${j.vouchers} 凭证`}
                onClick={() => { setJobId(j.job_id); setTab('review') }}
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
          onJobReady={(id) => setJobId(id)}
          onOpenReview={() => setTab('review')}
        />
      </div>
      {jobId && (
        <div style={{ display: tab === 'review' ? 'block' : 'none' }}>
          <ReviewPage jobId={jobId} />
        </div>
      )}
    </>
  )
}
