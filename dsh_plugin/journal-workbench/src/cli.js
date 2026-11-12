import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

const RESULT_PREFIX = 'RESULT '

/** stdout/stderr 各留这么多字节就够定位问题；再多只是把内存喂胖。 */
const MAX_CAPTURE = 2 * 1024 * 1024

/** 报给用户的那句话要能一眼看出该改什么。 */
export class JournalError extends Error {}

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
 *
 * 两个 Windows 专属的坑：
 *   - 不设 `PYTHONIOENCODING` 时，管道里的中文按 cp936 编，`json.dumps(
 *     ensure_ascii=False)` 会直接抛 UnicodeEncodeError；
 *   - `child.kill()` 只杀 python 自己，它拉起的子进程（读表用的引擎）会留着，
 *     超时要靠 `taskkill /T` 收整棵树。
 */
export function runJournal({ python, repo, args, timeoutMs = 120_000, signal, env, onStderr }) {
  const script = journalPath(repo)

  return new Promise((resolve) => {
    let child
    try {
      child = spawn(python, [script, ...args], {
        cwd: repo,
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
      if (outBytes < MAX_CAPTURE) {
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
          error: stderrTail(stderr) || `命令没有输出结果（退出码 ${code}）`,
          stdout,
          stderr,
          code,
        })
        return
      }
      try {
        finish({ ok: true, value: JSON.parse(line), stdout, stderr, code })
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
