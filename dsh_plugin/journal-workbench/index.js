import { registerRoutes } from './src/http.js'
import { createEnv } from './src/env.js'
import { createNotifier } from './src/notify.js'
import { createOps } from './src/ops.js'
import { createRunner } from './src/runner.js'
import { makeSettings } from './src/settings.js'
import { createTasks } from './src/tasks.js'
import { createTools } from './src/tools.js'

/**
 * 会计分录工作台（Host 半）。
 *
 * 只做一件事：把仓库里已有的 `journal` 命令行接到 DSH 的工具表与页面上。
 *
 * 不在这里实现的东西，都不是省略：
 *   - 状态机、幂等、重跑范围       → `jobflow`（`_storage_jobs/<id>/state.json`）
 *   - 判桶/编表/写盘               → `features/*` 的阶段实现
 *   - AI 怎么想                    → 当前会话的 agent（插件不接模型 API）
 *
 * 插件只补四样：**后台跑**（工具不能卡几分钟）、**给人看的排版**、**页面**、
 * 以及**跑到复核位时叫醒发起它的那个 agent**。
 */
export const name = 'dsh-journal-workbench'

export const inject = ['tools']

export function apply(ctx, config) {
  const settings = makeSettings(ctx, config)
  const tasks = createTasks({ settings })
  const notifier = createNotifier(ctx, settings, tasks)

  // 后台任务跑完 → 看一眼是不是停在复核位/失败了 → 叫醒任务自己的复核会话
  const runner = createRunner(settings, {
    onFinish: ({ jobId, repo }) => {
      // 叫醒会顺手建会话（异步）：失败了也不该把跑完的任务带崩
      notifier.notify({ jobId, repo }).catch(() => {})
    },
  })
  const ops = createOps(settings, runner, notifier)
  const env = createEnv({ settings })

  ctx.effect(() => {
    const disposers = createTools(ops).map((tool) => ctx.tools.register(tool))
    return () => disposers.forEach((dispose) => dispose())
  }, 'journal-workbench: tools')

  installRoutes(ctx, { ops, settings, env, tasks })

  ctx.effect(() => () => runner.dispose(), 'journal-workbench: runner')
}

/**
 * 把页面要的数据接口挂到 webServer 上。
 *
 * **不能直接 `ctx.get('webServer')` 拿一次就完事**：应用刚启动时 webServer 可能
 * 还排在后面没起来，拿一次是 undefined，`registerRoutes` 就静悄悄地什么都不做，
 * 页面整片 404（"服务返回的不是 JSON（HTTP 404）"）。工具还照常能用，所以只在
 * 页面上炸——排查时容易以为是前端的问题。
 *
 * 所以：拿得到就当场挂；拿不到就每 200ms 看一眼，最多等 60 秒。插件卸载时把
 * 定时器和已挂上的路由一起收掉。
 */
function installRoutes(ctx, deps) {
  ctx.effect(() => {
    let dispose = null
    let timer = null
    let left = 300
    const tryAttach = () => {
      if (!ctx.get('webServer')) return false
      dispose = registerRoutes(ctx, deps)
      return true
    }

    if (!tryAttach()) {
      timer = setInterval(() => {
        left -= 1
        if (tryAttach() || left <= 0) {
          clearInterval(timer)
          timer = null
        }
      }, 200)
    }

    return () => {
      if (timer) clearInterval(timer)
      if (typeof dispose === 'function') dispose()
    }
  }, 'journal-workbench: routes (等 webServer)')
}
