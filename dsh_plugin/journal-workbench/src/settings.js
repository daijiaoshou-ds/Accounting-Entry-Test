import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** 单次后台跑的上限：一本 6 万行的账分类要几分钟，30 分钟足够。 */
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000

const HERE = dirname(fileURLToPath(import.meta.url))
/** 打包版把 Python 引擎塞在插件包里（`<pkg>/engine`）；开发版没有这个目录。 */
const BUNDLED_ENGINE = join(HERE, '..', 'engine')
const ENGINE_STAMP = '.engine-version'

/** 用户数据目录：插件所有"装出来的东西"都在这儿，跟插件包目录解耦。 */
export function dataHome() {
  const base = process.env.LOCALAPPDATA
    || process.env.XDG_DATA_HOME
    || join(process.env.HOME || process.env.USERPROFILE || '.', '.local', 'share')
  return join(base, 'dsh-auditor')
}

/**
 * 引擎的工作目录：**复制到用户数据目录再跑**。
 *
 * 为什么不在插件目录里原地跑：插件是 npm 包，升级/重装整目录替换；而这个目录
 * 里会生出 venv（几百 MB）和下载的模型（约 2GB）。放在包目录里，一次升级就把
 * 别人装好的环境抹掉。复制一份过去，引擎代码随包更新、环境留在用户目录。
 */
export function engineHome() {
  return join(dataHome(), 'engine')
}

/**
 * venv 的家：**数据目录下、engine 的兄弟目录**，不能放进 `engine/`。
 *
 * 踩过的坑（Windows + DSH 默认的"工作区内修改"权限）：DSH 的沙箱会给它经手过的
 * 目录打 ACL（会话身份 Allow Write + `Everyone Deny DeleteSubdirectoriesAndFiles`），
 * 而 `engine/` 正是插件自己写的那棵树。venv 落在里面时，`venv\Scripts\python.exe`
 * 这个 255KB 的启动器读不到同目录的 `pyvenv.cfg`，报一句极误导人的
 * `failed to locate pyvenv.cfg`——看起来像"没装 Python"，其实是路径被挡住了。
 * 放到 engine 外面就正常（同一台机器上实测：同样的字节，换个位置立刻能跑）。
 */
export function venvHome() {
  return join(dataHome(), 'venv')
}

/** 两个路径是不是同一个目录（Windows 不分大小写）。 */
function sameDir(a, b) {
  if (!a || !b) return false
  const norm = (path) => {
    try {
      const out = resolve(path)
      return process.platform === 'win32' ? out.toLowerCase() : out
    } catch {
      return ''
    }
  }
  return norm(a) === norm(b)
}

/** 这个目录里的 venv 解释器（Windows 是 Scripts/python.exe，POSIX 是 bin/python）。 */
export function venvPythonIn(dir) {
  if (!dir) return ''
  for (const parts of [['Scripts', 'python.exe'], ['bin', 'python']]) {
    const hit = join(dir, ...parts)
    if (existsSync(hit)) return hit
  }
  return ''
}

/**
 * venv 里的基座解释器（`pyvenv.cfg` 的 `home=`）。
 *
 * venv 的启动器起不来时，这条路径还可能是好的——修环境要用它来建新的 venv，
 * 不然用户会卡在"解释器坏了 → 连重建都做不了"。
 */
export function basePythonOf(dir) {
  if (!dir) return ''
  try {
    const text = readFileSync(join(dir, 'pyvenv.cfg'), 'utf8')
    const hit = /^\s*home\s*=\s*(.+?)\s*$/m.exec(text)
    const home = hit ? hit[1].replace(/[\r\n]/g, '') : ''
    if (!home) return ''
    const exe = process.platform === 'win32' ? join(home, 'python.exe') : join(home, 'python3')
    return existsSync(exe) ? exe : ''
  } catch {
    return ''
  }
}

/** 打包版：把随包的引擎同步到用户目录，返回它；开发版（没有 engine/）返回空串。 */
function syncBundledEngine() {
  if (!existsSync(join(BUNDLED_ENGINE, 'journal.py'))) return ''
  const target = engineHome()
  const want = (() => {
    try {
      return readFileSync(join(BUNDLED_ENGINE, ENGINE_STAMP), 'utf8').trim()
    } catch {
      return ''
    }
  })()
  let have = ''
  try {
    have = readFileSync(join(target, ENGINE_STAMP), 'utf8').trim()
  } catch {
    have = ''
  }
  if (existsSync(join(target, 'journal.py')) && want && have === want) return target

  mkdirSync(target, { recursive: true })
  cpSync(BUNDLED_ENGINE, target, {
    recursive: true,
    force: true,
    filter: (src) => !src.includes('__pycache__') && !src.endsWith('.pyc'),
  })
  writeFileSync(join(target, ENGINE_STAMP), want)
  return target
}

/** 每次内联多少条待办。上下文是稀缺的，30 条以上就该分批。 */
const DEFAULT_REVIEW_LIMIT = 20

/**
 * 复核会话用哪个 agent preset。
 *
 * 这个值决定复核会话**有没有工具**：不带 preset 建出来的会话只挂得住
 * 插件自己注册的工具（journal_*），读不了文件、跑不了脚本——AI 只能一条条
 * 肉眼看。`standard` 是 DSH 自带的完整组合（读写文件、grep、跑 python），
 * 也是这个 profile 的默认。想换成别的 preset，在 patch 里配 `reviewPreset`。
 */
const DEFAULT_REVIEW_PRESET = 'standard'

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

  // 跑到复核位要不要**自动**叫醒 agent。默认**关**：跑功能是跑功能，复核是另一件事，
  // 由用户在复核位上点「AI 复核这批」才叫（也可以不复核、直接按原判出结果）。
  // 想回到"一到复核位就叫"的老行为，配 autoWake: true。
  const autoWake = cfg.autoWake === true

  const reviewPreset = pickString(cfg.reviewPreset) || DEFAULT_REVIEW_PRESET

  // 打包版：引擎随插件走，用户目录里那才是项目根（venv / 模型 / 任务都在这儿）
  const bundledRepo = syncBundledEngine()

  async function repoFor(agent) {
    if (fixedRepo) return fixedRepo
    if (bundledRepo) return bundledRepo
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
    const venv = venvFor(repo)
    if (venv.python) return venv.python
    // 用户机器上可能根本没装 Python：环境配置页可以下装一个我们托管的解释器
    if (managedPython()) return managedPython()
    return process.platform === 'win32' ? 'python.exe' : 'python3'
  }

  /**
   * venv 在哪、该建在哪。
   *
   * `create` 是"该在的地方"：打包版（repo = 引擎目录）是数据目录，开发版是仓库根。
   * `found` 是实际找到的那个——老版本把 venv 建在引擎目录里、用户也可能自己配过，
   * 所以还去那些地方找一圈；找到了就先接着用，别为了整齐把人家的环境当没装。
   */
  function venvFor(repo) {
    const onEngine = sameDir(repo, engineHome()) || (!!bundledRepo && sameDir(repo, bundledRepo))
    const own = onEngine ? venvHome() : join(repo, 'venv')
    const candidates = [own]
    for (const dir of [join(repo, 'venv'), join(repo, '.venv'), venvHome()]) {
      if (!sameDir(dir, own) && !candidates.some((seen) => sameDir(seen, dir))) candidates.push(dir)
    }
    const found = candidates.find((dir) => venvPythonIn(dir)) || ''
    return {
      create: own,
      found,
      python: found ? venvPythonIn(found) : '',
      elsewhere: !!found && !sameDir(found, own),
    }
  }

  function jobsFor(repo) {
    return fixedJobs || join(repo, '_storage_jobs')
  }

  return { repoFor, pythonFor, venvFor, jobsFor, timeoutMs, reviewLimit, autoWake, reviewPreset }
}

/**
 * 插件自己管的那份 Python（环境配置页下载安装的）。
 *
 * 放用户数据目录而不是插件目录：插件是 npm 包，升级/重装会整目录替换；
 * 解释器有几百兆，不能跟着包一起被覆盖。老版本放在 `dsh-journal/python`，
 * 那份也还认（装过的人不用重下）。
 */
export function managedPythonRoot() {
  return join(dataHome(), 'python')
}

export function managedPython() {
  const roots = [managedPythonRoot(), join(dataHome(), '..', 'dsh-journal', 'python')]
  for (const root of roots) {
    const candidate = process.platform === 'win32'
      ? join(root, 'python.exe')
      : join(root, 'bin', 'python3')
    if (existsSync(candidate)) return candidate
  }
  return ''
}
