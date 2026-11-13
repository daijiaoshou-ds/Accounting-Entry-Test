import { runJournal, stderrTail } from './cli.js'

/** 每个任务留最近 40 行进度：够回答"跑到哪一步了"，又不至于把上下文撑爆。 */
const LOG_RING = 40

/**
 * 后台跑任务。
 *
 * 为什么必须后台：`journal resume` 会一直跑到**下一个复核位**——一本 6 万行的
 * 账要几分钟。工具调用卡在这里，agent 就没法回话、页面也没法显示进度。
 * 所以工具只负责"起跑 + 立刻返回 job_id"，进度从 `state.json` 与这里攒的
 * stderr 尾巴上取。
 *
 * 并发固定 1：分类阶段吃 GPU（`api/flow_api.py` 的执行器也是单线程），
 * 两个任务同时跑只会互相抢显存。
 */
export function createRunner(settings, hooks = {}) {
  const active = new Map()
  let chain = Promise.resolve()

  function isRunning(jobId) {
    if (!jobId) return false
    return active.has(String(jobId))
  }

  function snapshot(jobId) {
    const rec = active.get(String(jobId))
    if (!rec) return null
    return {
      label: rec.label,
      seconds: Math.max(0, Math.round((Date.now() - rec.startedAt) / 1000)),
      log: rec.log.slice(-8),
    }
  }

  function start({ jobId, label, repo, python, args, timeoutMs }) {
    const key = String(jobId)
    if (active.has(key)) return false

    const controller = new AbortController()
    const rec = { label, repo, startedAt: Date.now(), log: [], error: '', controller }
    active.set(key, rec)

    // 排队：前一个任务跑完才开始下一个（不是并行）
    chain = chain.then(async () => {
      try {
        const result = await runJournal({
          python,
          repo,
          args,
          timeoutMs: timeoutMs || settings.timeoutMs,
          signal: controller.signal,
          onStderr: (chunk) => {
            for (const line of String(chunk).split(/\r?\n/)) {
              const text = line.trim()
              if (text) rec.log.push(text)
            }
            if (rec.log.length > LOG_RING) rec.log.splice(0, rec.log.length - LOG_RING)
          },
        })
        if (!result.ok && !controller.signal.aborted) {
          rec.error = result.error || '后台任务失败'
        }
      } catch (error) {
        if (!controller.signal.aborted) rec.error = error?.message || String(error)
      } finally {
        rec.finishedAt = Date.now()
        active.delete(key)
        // 跑完了才谈"下一步该谁动"：停到复核位就叫醒发起者（M3）
        try {
          hooks.onFinish?.({ jobId: key, repo, label, error: rec.error, aborted: controller.signal.aborted })
        } catch {
          // 通知失败不影响任务
        }
      }
    })

    return true
  }

  /** 后台任务的最后一句错（跑完就从 map 里消失了，状态以 state.json 为准）。 */
  function lastError(jobId) {
    return stderrTail(active.get(String(jobId))?.error || '', 400)
  }

  /**
   * 中止：杀掉 python 进程。
   *
   * 只杀进程不改状态——状态由 `journal abort` 写（`ops.stop` 负责先后顺序）。
   * 反过来先写状态的话，还在跑的进程会接着跑、接着写 state.json，把「已中止」
   * 又盖回「正在跑」，页面就永远停不下来。
   *
   * 返回 true 表示确实有一个进程在跑（否则就是"已经停了"，只需改状态）。
   */
  function stop(jobId) {
    const rec = active.get(String(jobId))
    if (!rec) return false
    try {
      rec.controller.abort()
    } catch {
      // 已经结束
    }
    return true
  }

  /**
   * 等进程真的退出（杀掉是异步的：`taskkill` 到进程消失有几十到几百毫秒）。
   *
   * `journal abort` 必须等这个窗口过去，否则它的 state 写入会和正在退出的进程抢。
   */
  async function settled(jobId, budgetMs = 10_000) {
    const key = String(jobId)
    const deadline = Date.now() + budgetMs
    while (Date.now() < deadline) {
      if (!active.has(key)) return true
      await new Promise((done) => {
        const timer = setTimeout(done, 100)
        timer.unref?.()
      })
    }
    return !active.has(key)
  }

  /** 插件卸载时把还在跑的 python 收掉——不留孤儿进程。 */
  function dispose() {
    for (const rec of active.values()) {
      try {
        rec.controller.abort()
      } catch {
        // 已经结束
      }
    }
    active.clear()
  }

  return { start, isRunning, snapshot, lastError, stop, settled, dispose }
}
