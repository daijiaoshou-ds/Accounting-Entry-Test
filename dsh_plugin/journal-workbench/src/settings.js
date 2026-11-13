import { existsSync } from 'node:fs'
import { join } from 'node:path'

/** 单次后台跑的上限：一本 6 万行的账分类要几分钟，30 分钟足够。 */
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000

/** 每次内联多少条待办。上下文是稀缺的，30 条以上就该分批。 */
const DEFAULT_REVIEW_LIMIT = 20

const PYTHON_CANDIDATES = [
  ['venv', 'Scripts', 'python.exe'],
  ['venv', 'bin', 'python'],
  ['.venv', 'Scripts', 'python.exe'],
  ['.venv', 'bin', 'python'],
]

function pickString(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

/**
 * 配置解析。
 *
 * 插件没有声明 Config schema（那样要 import 一个解析库，本地目录安装的包
 * 解析不到），所以直接读 patch 行里的 `config` 对象，缺省值在下面兜。
 *
 * 项目根默认取**会话的工作目录**：插件是装在 profile 上的、对所有会话生效，
 * 而"这个会话在哪个仓库里"只有运行时才知道。用户在一个别的项目里开着会话时，
 * 工具会明确报"这里没有 journal.py"，而不是去猜一个全局路径。
 */
export function makeSettings(ctx, config) {
  const cfg = config && typeof config === 'object' ? config : {}

  const fixedRepo = pickString(cfg.repoRoot)
  const fixedPython = pickString(cfg.python)
  const fixedJobs = pickString(cfg.jobsDir)

  const timeoutMs = Number(cfg.timeoutMs) > 0 ? Number(cfg.timeoutMs) : DEFAULT_TIMEOUT_MS
  const reviewLimit = Number(cfg.reviewLimit) > 0
    ? Math.min(200, Math.floor(Number(cfg.reviewLimit)))
    : DEFAULT_REVIEW_LIMIT

  // 跑到复核位要不要自动叫醒 agent。默认开；不想被"主动打断"就配 autoWake: false。
  const autoWake = cfg.autoWake !== false

  async function repoFor(agent) {
    if (fixedRepo) return fixedRepo
    const wd = ctx.get('workingDirectory')
    if (agent && wd?.ensure) {
      try {
        const dir = await wd.ensure(agent)
        if (dir) return dir
      } catch {
        // 取不到就退回进程目录，让 journalPath() 去报那句能看懂的错
      }
    }
    return process.cwd()
  }

  function pythonFor(repo) {
    if (fixedPython) return fixedPython
    for (const parts of PYTHON_CANDIDATES) {
      const candidate = join(repo, ...parts)
      if (existsSync(candidate)) return candidate
    }
    // 用户机器上可能根本没装 Python：环境配置页可以下装一个我们托管的解释器
    if (managedPython()) return managedPython()
    return process.platform === 'win32' ? 'python.exe' : 'python3'
  }

  function jobsFor(repo) {
    return fixedJobs || join(repo, '_storage_jobs')
  }

  return { repoFor, pythonFor, jobsFor, timeoutMs, reviewLimit, autoWake }
}

/**
 * 插件自己管的那份 Python（环境配置页下载安装的）。
 *
 * 放用户数据目录而不是插件目录：插件是 npm 包，升级/重装会整目录替换；
 * 解释器有几百兆，不能跟着包一起被覆盖。
 */
export function managedPythonRoot() {
  const base = process.env.LOCALAPPDATA
    || process.env.XDG_DATA_HOME
    || join(process.env.HOME || process.env.USERPROFILE || '.', '.local', 'share')
  return join(base, 'dsh-journal', 'python')
}

export function managedPython() {
  const root = managedPythonRoot()
  const candidate = process.platform === 'win32'
    ? join(root, 'python.exe')
    : join(root, 'bin', 'python3')
  if (existsSync(candidate)) return candidate
  // python.org 的安装程序会建一层目录；两种布局都认
  const nested = process.platform === 'win32'
    ? join(root, 'python.exe')
    : join(root, 'python3')
  return existsSync(nested) ? nested : ''
}
