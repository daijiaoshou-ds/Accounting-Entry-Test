import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

const RESULT_PREFIX = 'RESULT '

/** stdout/stderr 各留这么多字节就够定位问题；再多只是把内存喂胖。 */
const MAX_CAPTURE = 2 * 1024 * 1024

/** 报给用户的那句话要能一眼看出该改什么。 */
export class JournalError extends Error {}

/**
 * Python 抛的错里最值得翻译的一种：缺包。
 *
 * 新机器上环境还没配齐时，任何工具都会以 `ModuleNotFoundError: No module named
 * 'torch'` 收场——这句话本身没错，但看的人不知道去哪补。翻译成一句能照做的话。
 */
export function explainPythonError(text) {
  const raw = String(text || '')
  const missing = /ModuleNotFoundError: No module named '([^']+)'/.exec(raw)
  if (!missing) return raw
  return `${raw}\n\n缺 Python 包「${missing[1]}」：到「环境配置」页把对应步骤装齐`
    + '（基础依赖 → 模型依赖 → 模型文件），或者把插件配置的 python 指到本项目的 venv。'
}

export function journalPath(repo) {
  const file = join(repo, 'journal.py')
  if (!existsSync(file)) {
    throw new JournalError(
      `在 ${repo} 找不到 journal.py。把插件配置的 repoRoot 指向项目根，`
      + '或者在一个位于该项目的会话里再试。',
    )
  }
  return file
}

/**
 * 跑一次 `python journal.py …`。
 *
 * 约定（仓库里 `jobflow/cli.py` 定的）：stdout 只输出单行 `RESULT <json>`，
 * 进度走 stderr。所以这里只找最后一行 RESULT，stderr 留给调用方摘个尾巴。
 */
export function runJournal({ python, repo, args, timeoutMs = 120_000, signal, env, onStderr }) {
  return runPython({
    python,
    cwd: repo,
    script: journalPath(repo),
    args,
    timeoutMs,
    signal,
    env,
    onStderr,
  })
}

/**
 * 跑一次任意 Python 脚本，同样只认末尾那行 `RESULT <json>`。
 *
 * 抽出来是为了给预览用（`sheet_preview.py`）：它要读整张表、输出可能十几兆，
 * 但进程管理、编码、超时、杀进程树这些事和 `journal.py` 一模一样，不该抄第二份。
 * `maxCapture` 是 stdout 的字节上限，预览要调大（默认 2MB 会把大表截断成非法 JSON）。
 *
 * 两个 Windows 专属的坑：
 *   - 不设 `PYTHONIOENCODING` 时，管道里的中文按 cp936 编，`json.dumps(
 *     ensure_ascii=False)` 会直接抛 UnicodeEncodeError；
 *   - `child.kill()` 只杀 python 自己，它拉起的子进程（读表用的引擎）会留着，
 *     超时要靠 `taskkill /T` 收整棵树。
 */
export function runPython({
  python, cwd, script, args, timeoutMs = 120_000, signal, env, onStderr,
  maxCapture = MAX_CAPTURE,
}) {
  return new Promise((resolve) => {
    let child
    try {
      child = spawn(python, [script, ...args], {
        cwd,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
          PYTHONUNBUFFERED: '1',
          // 分录测试的风险分档会随 set/dict 的字符串迭代顺序浮动（进程级哈希随机化）：
          // 同一份账两次跑，高风险可能是 52 张也可能是 54 张。固定种子让"同一输入
          // 同一输出"成立，否则复核过的结论下次重跑会对不上号。
          PYTHONHASHSEED: '0',
          ...(env || {}),
        },
      })
    } catch (error) {
      resolve({ ok: false, error: `启动 Python 失败：${error.message}`, stdout: '', stderr: '' })
      return
    }

    const out = []
    const err = []
    let outBytes = 0
    let errBytes = 0
    let settled = false
    let timer = null

    const finish = (extra) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener?.('abort', onAbort)
      resolve(extra)
    }

    const onAbort = () => {
      killTree(child)
      finish({ ok: false, error: '已取消', stdout: out.join(''), stderr: err.join('') })
    }

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      if (outBytes < maxCapture) {
        out.push(chunk)
        outBytes += chunk.length
      }
    })
    child.stderr.on('data', (chunk) => {
      if (errBytes < MAX_CAPTURE) {
        err.push(chunk)
        errBytes += chunk.length
      }
      if (onStderr) {
        try {
          onStderr(chunk)
        } catch {
          // 观察者抛错不能把命令带走
        }
      }
    })

    child.on('error', (error) => {
      const hint = error?.code === 'ENOENT'
        ? `找不到 Python 解释器：${python}。把插件配置的 python 指到 venv\\Scripts\\python.exe。`
        : `启动 Python 失败：${error.message}`
      finish({ ok: false, error: hint, stdout: '', stderr: '' })
    })

    child.on('close', (code) => {
      const stdout = out.join('')
      const stderr = err.join('')
      const line = lastResultLine(stdout)
      if (!line) {
        finish({
          ok: false,
          error: explainPythonError(stderrTail(stderr)) || `命令没有输出结果（退出码 ${code}）`,
          stdout,
          stderr,
          code,
        })
        return
      }
      try {
        const value = JSON.parse(line)
        // Python 自己抓住的异常走的是 RESULT ok:false，不在这里翻译的话，
        // 界面上只会看到一句 "ModuleNotFoundError"，不知道该去哪补。
        if (value && value.ok === false && typeof value.error === 'string') {
          value.error = explainPythonError(value.error)
        }
        finish({ ok: true, value, stdout, stderr, code })
      } catch (error) {
        finish({ ok: false, error: `结果不是合法 JSON：${error.message}`, stdout, stderr, code })
      }
    })

    if (signal) {
      if (signal.aborted) onAbort()
      else signal.addEventListener?.('abort', onAbort, { once: true })
    }
    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        killTree(child)
        finish({
          ok: false,
          error: `超时（${Math.round(timeoutMs / 1000)}s）：${stderrTail(err.join(''))}`,
          stdout: out.join(''),
          stderr: err.join(''),
        })
      }, timeoutMs)
      timer.unref?.()
    }
  })
}

function lastResultLine(stdout) {
  const lines = stdout.split(/\r?\n/)
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i].trim()
    if (line.startsWith(RESULT_PREFIX)) return line.slice(RESULT_PREFIX.length).trim()
  }
  return ''
}

/** stderr 的最后几行：进度日志很长，报错只关心末尾。 */
export function stderrTail(text, limit = 600) {
  const trimmed = String(text || '').trim()
  if (!trimmed) return ''
  return trimmed.length > limit ? `…${trimmed.slice(-limit)}` : trimmed
}

export function killTree(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return
  try {
    child.kill('SIGTERM')
  } catch {
    // 已经退了
  }
  if (process.platform === 'win32' && child.pid) {
    try {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      })
    } catch {
      // 尽力而为
    }
  }
}
