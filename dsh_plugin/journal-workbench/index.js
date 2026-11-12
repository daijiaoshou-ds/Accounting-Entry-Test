import { registerRoutes } from './src/http.js'
import { createOps } from './src/ops.js'
import { createRunner } from './src/runner.js'
import { makeSettings } from './src/settings.js'
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
 * 插件只补三样：**后台跑**（工具不能卡几分钟）、**给人看的排版**、
 * 以及一个把同一批操作摆到屏幕上的页面。
 */
export const name = 'dsh-journal-workbench'

export const inject = ['tools']

export function apply(ctx, config) {
  const settings = makeSettings(ctx, config)
  const runner = createRunner(settings)
  const ops = createOps(settings, runner)

  ctx.effect(() => {
    const disposers = createTools(ops).map((tool) => ctx.tools.register(tool))
    return () => disposers.forEach((dispose) => dispose())
  }, 'journal-workbench: tools')

  // 页面要的数据接口。没有 webServer 的 profile（纯 CLI）里这条什么都不做。
  ctx.effect(() => registerRoutes(ctx, { ops, settings }), 'journal-workbench: routes')

  ctx.effect(() => () => runner.dispose(), 'journal-workbench: runner')
}
