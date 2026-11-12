import { useEffect, useState } from 'react'
import { api } from './api'
import type { Health } from './types'
import HomePage from './pages/HomePage'
import FlowPage from './pages/FlowPage'

/**
 * 极简 hash 路由（不引 react-router）：
 *
 *     #/                     功能列表
 *     #/f/cleaning           序时账清洗
 *     #/f/cashflow/<job_id>  某个任务的当前阶段（刷新不丢）
 *     #/clean                老链接，跳到 #/f/cleaning
 */
type Route =
  | { name: 'home' }
  | { name: 'feature'; id: string; jobId?: string }

function parse(hash: string): Route {
  const parts = (hash || '').replace(/^#\/?/, '').split('/').filter(Boolean)
  if (parts[0] === 'f' && parts[1]) {
    return { name: 'feature', id: parts[1], jobId: parts[2] }
  }
  if (parts[0] === 'clean') {
    // 老书签：清洗已经是一个功能了
    return { name: 'feature', id: 'cleaning' }
  }
  return { name: 'home' }
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))
  const [health, setHealth] = useState<Health | null>(null)
  const [healthErr, setHealthErr] = useState('')

  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', on)
    // 老链接清完地址栏，免得刷新又跳一次
    if (window.location.hash.startsWith('#/clean')) {
      window.history.replaceState(null, '', '#/f/cleaning')
    }
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
