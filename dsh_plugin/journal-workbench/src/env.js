import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

/**
 * 环境配置：这个插件自己不带模型和 Python 库，别人的机器上得先装。
 *
 * 这里只做三件事，都很"薄"——真正的安装在 python/pip 里跑：
 *   ① status()   看一眼现在缺什么（不装、不猜，缺什么说什么）
 *   ② start()    点一下按钮 → 起一个后台任务（pip / modelscope）
 *   ③ task(id)   拿任务进度和日志（页面轮询）
 *
 * 为什么不在界面上直接 `exec` 一串命令：装依赖要几分钟，HTTP 请求扛不住；
 * 而且失败时要能看到是哪一行报错，所以走"任务 + 日志"。
 */

/** 国内可用的几个源。阿里云最稳，清华偶尔抽风，官方源国内慢。 */
export const MIRRORS = [
  { id: 'aliyun', name: '阿里云', url: 'https://mirrors.aliyun.com/pypi/simple/' },
  { id: 'tsinghua', name: '清华大学', url: 'https://pypi.tuna.tsinghua.edu.cn/simple' },
  { id: 'official', name: 'PyPI 官方', url: 'https://pypi.org/simple' },
]

/** torch 的 CPU 轮子只在 PyTorch 自己的源上；走 pip 镜像会下 CUDA 版（2.4GB）。 */
const TORCH_CPU_INDEX = 'https://download.pytorch.org/whl/cpu'

/** 模型发布在 ModelScope（和 `download_model.bat` 用的是同一个）。 */
const MODEL_REPO = 'daijiaoshou/hajishou-V1.0'
const MODEL_DIR = join('summary_cleaner', 'nn', '_storage')
const MODEL_FILES = [
  join('fine_tuned', 'model.safetensors'),
  'finance_classifier.pt',
  'subject_to_index.json',
  'index_to_bucket.json',
]

/** 基础版要有的包（没有它们连读表都跑不了）。 */
const BASE_PACKAGES = ['pandas', 'numpy', 'pyarrow', 'python-calamine', 'openpyxl', 'scikit-learn']
/** 模型版额外要有的包（清洗/现金流才需要）。 */
const NN_PACKAGES = ['torch', 'transformers', 'safetensors', 'modelscope']

const MAX_LOG_LINES = 400
const TASK_TTL_MS = 6 * 60 * 60 * 1000

/** 探测脚本：一次进程问清所有版本，比十几个 `pip show` 快得多。 */
const PROBE = `
import json, sys, importlib.metadata as md
names = ${JSON.stringify([...BASE_PACKAGES, ...NN_PACKAGES, 'streamlit'])}
out = {"python": sys.version.split()[0], "exe": sys.executable, "packages": {}}
for n in names:
    try:
        out["packages"][n] = md.version(n)
    except Exception:
        out["packages"][n] = None
try:
    import torch
    out["torch_cuda"] = torch.version.cuda or ""
except Exception:
    out["torch_cuda"] = ""
print(json.dumps(out, ensure_ascii=False))
`

export function createEnv({ settings }) {
  const tasks = new Map()
  let cached = { at: 0, key: '', value: null }

  // ── ① 现状 ───────────────────────────────────────────────────────────

  async function status(agent, { fresh = false } = {}) {
    const repo = await settings.repoFor(agent)
    const python = settings.pythonFor(repo)
    const key = `${repo}|${python}`
    if (!fresh && cached.value && cached.key === key && Date.now() - cached.at < 60_000) {
      return cached.value
    }

    const journalPy = join(repo, 'journal.py')
    const repoOk = existsSync(journalPy)
    const probe = repoOk ? await runProbe(python, repo) : { ok: false, error: `这里没有 journal.py：${repo}` }

    const packages = {}
    for (const name of [...BASE_PACKAGES, ...NN_PACKAGES]) {
      const version = probe.packages?.[name] || null
      packages[name] = { ok: !!version, version }
    }

    const modelDir = join(repo, MODEL_DIR)
    const files = MODEL_FILES.map((name) => ({
      name, ok: existsSync(join(modelDir, name)),
    }))

    const baseOk = BASE_PACKAGES.every((name) => packages[name].ok)
    const nnOk = NN_PACKAGES.every((name) => packages[name].ok)
    const modelOk = files.every((file) => file.ok)
    const pythonOk = !!probe.ok

    const blockers = []
    if (!repoOk) blockers.push(`没找到项目：${repo}（插件配置的 repoRoot 指向哪里？）`)
    else if (!pythonOk) blockers.push(`Python 起不来：${probe.error || python}`)
    if (repoOk && pythonOk && !baseOk) {
      blockers.push(`缺基础库：${BASE_PACKAGES.filter((n) => !packages[n].ok).join('、')}`)
    }
    if (repoOk && pythonOk && !nnOk) {
      blockers.push(`缺模型版依赖：${NN_PACKAGES.filter((n) => !packages[n].ok).join('、')}`)
    }
    if (repoOk && !modelOk) {
      const missing = files.filter((f) => !f.ok).map((f) => f.name)
      blockers.push(`模型交付物不全：缺 ${missing.join('、')}`)
    }

    const value = {
      repo,
      python,
      pythonVersion: probe.python || '',
      pythonOk,
      probeError: probe.error || '',
      venv: {
        path: join(repo, 'venv'),
        exists: existsSync(join(repo, 'venv', 'Scripts', 'python.exe'))
          || existsSync(join(repo, 'venv', 'bin', 'python')),
      },
      packages,
      model: { dir: modelDir, files, ok: modelOk },
      // 两级就绪：contra/entry_test 只要基础库，cleaning/cashflow 还要模型
      ready: { basic: repoOk && pythonOk && baseOk, model: repoOk && pythonOk && baseOk && nnOk && modelOk },
      blockers,
      torchCuda: probe.torch_cuda || '',
      mirrors: MIRRORS,
      checkedAt: new Date().toISOString(),
    }
    cached = { at: Date.now(), key, value }
    return value
  }

  async function runProbe(python, cwd) {
    return await new Promise((resolve) => {
      let out = ''
      let err = ''
      let child
      try {
        child = spawn(python, ['-c', PROBE], { cwd, windowsHide: true })
      } catch (error) {
        resolve({ ok: false, error: error.message })
        return
      }
      child.stdout.on('data', (chunk) => { out += chunk })
      child.stderr.on('data', (chunk) => { err += chunk })
      child.on('error', (error) => resolve({ ok: false, error: error.message }))
      child.on('close', (code) => {
        if (code !== 0) {
          resolve({ ok: false, error: err.trim().split('\n').slice(-3).join(' ') || `退出码 ${code}` })
          return
        }
        try {
          resolve({ ok: true, ...JSON.parse(out.trim().split('\n').pop()) })
        } catch (error) {
          resolve({ ok: false, error: `探测输出读不懂：${out.slice(-200)}` })
        }
      })
    })
  }

  // ── ② 起任务 ─────────────────────────────────────────────────────────

  /**
   * @param {object} spec
   * @param {'base'|'nn'|'model'} spec.action
   * @param {'cpu'|'gpu'} spec.mode       nn 用：CPU 装 CPU 轮子，GPU 装镜像里的 CUDA 版
   * @param {string} spec.mirror          mirrors 里的 id
   */
  async function start(agent, spec = {}) {
    const repo = await settings.repoFor(agent)
    const python = settings.pythonFor(repo)
    const action = String(spec.action || '').trim()
    const mode = spec.mode === 'gpu' ? 'gpu' : 'cpu'
    const mirror = MIRRORS.find((m) => m.id === spec.mirror) || MIRRORS[0]

    if (!existsSync(join(repo, 'journal.py'))) {
      throw new Error(`这里没有 journal.py：${repo}`)
    }

    const steps = []
    if (action === 'base') {
      steps.push({ label: '安装基础依赖', args: ['-m', 'pip', 'install', '-r', 'requirements.txt', '-i', mirror.url] })
    } else if (action === 'nn') {
      steps.push({
        label: mode === 'cpu' ? '安装 CPU 版 torch' : '安装 torch（CUDA 版）',
        args: ['-m', 'pip', 'install', 'torch']
          .concat(mode === 'cpu' ? ['--index-url', TORCH_CPU_INDEX] : ['-i', mirror.url]),
      })
      steps.push({ label: '安装模型版依赖', args: ['-m', 'pip', 'install', '-r', 'requirements-nn.txt', '-i', mirror.url] })
    } else if (action === 'model') {
      steps.push({
        label: `下载模型 ${MODEL_REPO}`,
        args: ['-m', 'modelscope', 'download', MODEL_REPO, '--local_dir', MODEL_DIR],
      })
    } else {
      throw new Error(`不认识的安装动作：${action}`)
    }

    const id = `env_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
    const task = {
      id,
      action,
      mode,
      mirror: mirror.id,
      label: steps.map((s) => s.label).join(' → '),
      status: 'running',
      log: [],
      startedAt: new Date().toISOString(),
      finishedAt: '',
      exit: null,
      error: '',
    }
    tasks.set(id, task)
    cached.at = 0                      // 装完要重新看一眼
    runSteps(task, steps, python, repo)
    sweep(tasks)
    return describe(task)
  }

  function runSteps(task, steps, python, cwd) {
    let index = 0
    const next = () => {
      if (index >= steps.length) {
        task.status = 'done'
        task.exit = 0
        task.finishedAt = new Date().toISOString()
        return
      }
      const step = steps[index++]
      task.log.push(`$ ${python} ${step.args.join(' ')}`)
      let child
      try {
        child = spawn(python, step.args, { cwd, windowsHide: true })
      } catch (error) {
        task.status = 'failed'
        task.error = error.message
        task.log.push(error.message)
        return
      }
      const push = (prefix) => (chunk) => {
        for (const line of String(chunk).split(/\r?\n/)) {
          if (!line.trim()) continue
          task.log.push(prefix + line)
        }
        if (task.log.length > MAX_LOG_LINES) task.log.splice(0, task.log.length - MAX_LOG_LINES)
      }
      child.stdout.on('data', push(''))
      child.stderr.on('data', push(''))
      child.on('error', (error) => {
        task.status = 'failed'
        task.error = error.message
        task.log.push(error.message)
      })
      child.on('close', (code) => {
        if (code !== 0) {
          task.status = 'failed'
          task.exit = code
          task.error = `「${step.label}」退出码 ${code}`
          task.finishedAt = new Date().toISOString()
          return
        }
        task.log.push(`✓ ${step.label}`)
        next()
      })
    }
    next()
  }

  function task(id) {
    const hit = tasks.get(String(id || ''))
    return hit ? describe(hit) : null
  }

  function describe(task) {
    return {
      id: task.id, action: task.action, mode: task.mode, mirror: task.mirror,
      label: task.label, status: task.status, exit: task.exit, error: task.error,
      startedAt: task.startedAt, finishedAt: task.finishedAt,
      log: task.log.slice(-200),
    }
  }

  function sweep(table) {
    const cutoff = Date.now() - TASK_TTL_MS
    for (const [id, task] of table) {
      if (task.finishedAt && Date.parse(task.finishedAt) < cutoff) table.delete(id)
    }
  }

  return { status, start, task, mirrors: MIRRORS, modelRepo: MODEL_REPO, modelFiles: MODEL_FILES }
}
