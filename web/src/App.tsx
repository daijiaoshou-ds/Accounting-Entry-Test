import { useEffect, useState } from 'react'
import { api } from './api'
import type { Health } from './types'
import HomePage from './pages/HomePage'
import FlowPage from './pages/FlowPage'
import CleanView from './pages/CleanView'

/**
 * 极简 hash 路由（不引 react-router）：
 *
 *     #/                     功能列表
 *     #/f/cashflow           某个功能的新任务
 *     #/f/cashflow/<job_id>  回到某个任务的当前阶段
 *     #/clean                序时账清洗（老入口，仍在 /api/jobs/* 上）
 */
type Route =
  | { name: 'home' }
  | { name: 'feature'; id: string; jobId?: string }
  | { name: 'clean' }

function parse(hash: string): Route {
  const parts = (hash || '').replace(/^#\/?/, '').split('/').filter(Boolean)
  if (parts[0] === 'f' && parts[1]) {
    return { name: 'feature', id: parts[1], jobId: parts[2] }
  }
  if (parts[0] === 'clean') return { name: 'clean' }
  return { name: 'home' }
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))
  const [health, setHealth] = useState<Health | null>(null)
  const [healthErr, setHealthErr] = useState('')

  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  useEffect(() => {
    api.health().then(setHealth).catch(e => setHealthErr(String(e.message ?? e)))
  }, [])

  const go = (hash: string) => { window.location.hash = hash }

  const modelText = health
    ? health.model.loaded
      ? `模型已加载${health.model.device ? `（${health.model.device}）` : ''}`
      : health.model.configured
        ? '模型待加载'
        : '无模型（纯程序）'
    : healthErr || '连接后端中…'

  return (
    <div className="app">
      <div className="topbar">
        <h1>会计分析工具箱</h1>
        <span className="sub">序时账 → 清洗 → 现金流</span>
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

      {route.name === 'home' && <HomePage onNav={go} />}
      {route.name === 'clean' && <CleanView onNav={go} />}
      {route.name === 'feature' && (
        <FlowPage
          key={`${route.id}:${route.jobId ?? 'new'}`}
          featureId={route.id}
          initialJobId={route.jobId}
          onHome={() => go('#/')}
        />
      )}
    </div>
  )
}
