import { useEffect, useState } from 'react'
import { flow } from '../api'
import type { FlowFeature, FlowJobItem } from '../types'

/**
 * 首页 = 功能入口。清单来自 `GET /api/features`（后端 `features/*` 的注册表），
 * 加一个功能这里不用改代码。
 */
export default function HomePage({ onNav }: { onNav: (hash: string) => void }) {
  const [features, setFeatures] = useState<FlowFeature[]>([])
  const [jobs, setJobs] = useState<FlowJobItem[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    flow.features().then(r => setFeatures(r.items))
      .catch(e => setError(String(e.message ?? e)))
    flow.jobs(8).then(r => setJobs(r.items)).catch(() => setJobs([]))
  }, [])

  return (
    <>
      {error && <div className="notice err mb">{error}</div>}

      <div className="cards">
        {features.map(f => (
          <button key={f.id} className="feature-card" onClick={() => onNav(`#/f/${f.id}`)}>
            <span className="title">{f.name}</span>
            <span className="desc">{f.description}</span>
            <span className="meta">
              {f.stages.length} 个阶段 · {f.stages.filter(s => s.actor === 'ai').length} 处复核
            </span>
          </button>
        ))}

        <button className="feature-card legacy" onClick={() => onNav('#/clean')}>
          <span className="title">序时账清洗</span>
          <span className="desc">
            业务分类（模型判定）+ 复核清单 + 导出。老入口，功能与「现金流量表编写」
            的第一段相同。
          </span>
          <span className="meta">单跑 · 不经过编排层</span>
        </button>
      </div>

      {jobs.length > 0 && (
        <div className="card mt">
          <header>
            <h2>最近任务</h2>
            <span className="hint">点一条回到它当前的阶段</span>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>任务</th><th>功能</th><th>文件</th>
                  <th>状态</th><th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map(j => (
                  <tr key={j.job_id} className="clickable"
                      onClick={() => onNav(`#/f/${j.feature}/${j.job_id}`)}>
                    <td className="mono small">{j.job_id}</td>
                    <td>{j.feature_name}</td>
                    <td>{j.filename}</td>
                    <td><StatusChip status={j.status} awaiting={j.awaiting} /></td>
                    <td className="small muted">{j.created_at}</td>
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

function StatusChip({ status, awaiting }: { status: string; awaiting: string | null }) {
  if (status === 'done') return <span className="tag high">已完成</span>
  if (status === 'failed') return <span className="tag low">失败</span>
  if (status === 'awaiting_review' || awaiting) {
    return <span className="tag mid">待复核</span>
  }
  if (status === 'running') return <span className="tag plain">运行中</span>
  return <span className="tag plain">{status}</span>
}
