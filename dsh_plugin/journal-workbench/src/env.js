import { spawn, execFile } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'

import { managedPython, managedPythonRoot } from './settings.js'

/**
 * 环境配置：插件自己不带模型、也不带 Python 和那些库，别人的机器上得先装。
 *
 * 这里做三件事，真正的安装都交给 python / pip / modelscope 去跑：
 *   ① status()  看清现在缺什么（缺什么说什么，不猜）
 *   ② start()   点一下按钮 → 后台任务（下载安装包 / pip / modelscope）
 *   ③ task(id)  进度与日志（页面轮询）；cancel(id) 中止
 *
 * 两条原则：
 *   · **已经装好的东西不再装一遍**（除非界面上勾了"强制重装"）——点错了一下
 *     不该让人等十分钟；
 *   · **每一步都能中止**（taskkill 整棵进程树），装错了不用关整个应用。
 */

export const MIRRORS = [
  { id: 'aliyun', name: '阿里云', url: 'https://mirrors.aliyun.com/pypi/simple/' },
  { id: 'tsinghua', name: '清华大学', url: 'https://pypi.tuna.tsinghua.edu.cn/simple' },
  { id: 'official', name: 'PyPI 官方', url: 'https://pypi.org/simple' },
]

/** 模型发布在 ModelScope。 */
export const MODEL_REPO = 'daijiaoshou/hajishou-V2.0'
const MODEL_DIR = join('summary_cleaner', 'nn', '_storage')
const MODEL_FILES = [
  join('fine_tuned', 'model.safetensors'),
  'finance_classifier.pt',
  'subject_to_index.json',
  'index_to_bucket.json',
]

/**
 * 托管的 Python 解释器。
 *
 * 用 python.org 的安装包而不是"绿色版"：它能在 Windows 上静默装到指定目录、
 * 自带 pip、且是最常见的发行版，出了问题用户搜得到。URL 是 python.org 的固定
 * 路径，版本号一改就换一行。
 */
export const PYTHON_VERSION = '3.13.5'
const PYTHON_INSTALLER = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-amd64.exe`

/** 基础依赖：没有它们连读表都跑不了。 */
const BASE_PACKAGES = ['pandas', 'numpy', 'pyarrow', 'python-calamine', 'openpyxl', 'scikit-learn']
const NN_PACKAGES = ['transformers', 'safetensors', 'modelscope']

/**
 * CPU / GPU 两种模式的差别只有 torch 的轮子：
 *   CPU  走 PyTorch 官方 cpu 源，122MB，装完 540MB
 *   GPU  走 pip 镜像里的 CUDA 版，下 2.4GB、装完 4GB+
 * 其余依赖一样。所以界面上按模式列出"会装什么"，别让人以为没区别。
 */
export const TORCH_CPU_INDEX = 'https://download.pytorch.org/whl/cpu'

const MAX_LOG_LINES = 400
const TASK_TTL_MS = 6 * 60 * 60 * 1000

const PROBE = `
import json, sys, importlib.metadata as md
names = ${JSON.stringify([...BASE_PACKAGES, ...NN_PACKAGES, 'torch', 'streamlit'])}
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

    const repoOk = existsSync(join(repo, 'journal.py'))
    const probe = repoOk ? await runProbe(python, repo) : { ok: false, error: `这里没有 journal.py：${repo}` }
    const packages = {}
    for (const name of [...BASE_PACKAGES, ...NN_PACKAGES, 'torch']) {
      const version = probe.packages?.[name] || null
      packages[name] = { ok: !!version, version }
    }

    const modelDir = join(repo, MODEL_DIR)
    const files = MODEL_FILES.map((name) => ({ name, ok: existsSync(join(modelDir, name)) }))
    const modelOk = files.every((file) => file.ok)

    const baseOk = repoOk && probe.ok && BASE_PACKAGES.every((name) => packages[name].ok)
    const nnPkgsOk = repoOk && probe.ok && NN_PACKAGES.every((name) => packages[name].ok)
    const torchOk = packages.torch.ok
    const cudaOk = !!probe.torch_cuda

    const venvPython = join(repo, 'venv', 'Scripts', 'python.exe')
    const venvOk = existsSync(venvPython) || existsSync(join(repo, 'venv', 'bin', 'python'))

    // 五步走：解释器 → 虚拟环境 → 基础依赖 → 模型依赖 → 模型文件
    const steps = [
      { id: 'python', label: 'Python 解释器', ok: probe.ok,
        detail: probe.ok ? `v${probe.python} · ${python}` : (managedPython() ? '托管解释器没跑起来' : '没装（可以在这里装一个）') },
      { id: 'venv', label: '项目虚拟环境', ok: venvOk, detail: venvOk ? join(repo, 'venv') : '没有 venv（建议建一个，别装进系统 Python）' },
      { id: 'base', label: '基础依赖', ok: baseOk,
        detail: baseOk ? `${BASE_PACKAGES.length} 个包齐` : `缺 ${BASE_PACKAGES.filter((n) => !packages[n].ok).join('、')}` },
      { id: 'nn', label: '模型依赖', ok: nnPkgsOk && torchOk,
        detail: !torchOk ? '缺 torch'
          : !nnPkgsOk ? `缺 ${NN_PACKAGES.filter((n) => !packages[n].ok).join('、')}`
            : `torch ${packages.torch.version}${cudaOk ? ` · CUDA ${probe.torch_cuda}` : ' · CPU 版'}` },
      { id: 'model', label: '模型文件', ok: modelOk,
        detail: modelOk ? MODEL_REPO : `缺 ${files.filter((f) => !f.ok).map((f) => f.name).join('、')}` },
    ]

    const blockers = steps.filter((step) => !step.ok)
      .map((step) => `${step.label}：${step.detail}`)
    if (!repoOk) blockers.unshift(`没找到项目：${repo}`)

    const value = {
      repo,
      python,
      pythonVersion: probe.python || '',
      pythonOk: !!probe.ok,
      probeError: probe.error || '',
      managedPython: { root: managedPythonRoot(), path: managedPython(), installed: !!managedPython() },
      venv: { path: join(repo, 'venv'), python: venvPython, exists: venvOk },
      packages,
      torchCuda: probe.torch_cuda || '',
      model: { dir: modelDir, files, ok: modelOk, repo: MODEL_REPO },
      steps,
      modes: {
        cpu: { ready: baseOk && torchOk && nnPkgsOk, detail: 'torch CPU 版（约 122MB，装完 540MB）' },
        gpu: { ready: baseOk && torchOk && cudaOk && nnPkgsOk,
          detail: cudaOk ? `torch CUDA 版（当前 CUDA ${probe.torch_cuda}）` : 'torch CUDA 版（约 2.4GB；当前是 CPU 版，装 CUDA 版才能用显卡）' },
      },
      ready: { basic: baseOk, model: baseOk && torchOk && nnPkgsOk && modelOk },
      blockers,
      mirrors: MIRRORS,
      modelRepo: MODEL_REPO,
      pythonVersion: PYTHON_VERSION,
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
        } catch {
          resolve({ ok: false, error: `探测输出读不懂：${out.slice(-200)}` })
        }
      })
    })
  }

  // ── ② 起任务 ─────────────────────────────────────────────────────────

  /**
   * @param {object} spec
   * @param {'python'|'venv'|'base'|'nn'|'model'} spec.action
   * @param {'cpu'|'gpu'} spec.mode    nn 用：装哪种 torch
   * @param {string} spec.mirror       mirrors 里的 id
   * @param {boolean} spec.force       已经齐了也重装
   */
  async function start(agent, spec = {}) {
    const repo = await settings.repoFor(agent)
    const action = String(spec.action || '').trim()
    const mode = spec.mode === 'gpu' ? 'gpu' : 'cpu'
    const mirror = MIRRORS.find((m) => m.id === spec.mirror) || MIRRORS[0]
    const force = !!spec.force

    if (!existsSync(join(repo, 'journal.py'))) throw new Error(`这里没有 journal.py：${repo}`)

    const current = await status(agent, { fresh: true })
    const plan = buildPlan({ action, mode, mirror, repo, current })
    // 只装缺的：已经装好的步骤不再跑（除非勾了强制重装）
    const steps = force ? plan.steps : plan.steps.filter((step) => !step.satisfied)
    if (!steps.length) {
      // 已经满足了：不装，直接给一条"没做事"的任务，别让人以为在跑
      const id = newTaskId()
      tasks.set(id, {
        id, action, mode, mirror: mirror.id, label: plan.skipLabel,
        status: 'done', exit: 0, error: '', log: [`✓ ${plan.skipLabel}`],
        startedAt: new Date().toISOString(), finishedAt: new Date().toISOString(),
      })
      sweep(tasks)
      return describe(tasks.get(id))
    }

    const id = newTaskId()
    const task = {
      id, action, mode, mirror: mirror.id,
      label: steps.map((s) => s.label).join(' → '),
      status: 'running', log: [], startedAt: new Date().toISOString(),
      finishedAt: '', exit: null, error: '', pid: null,
    }
    tasks.set(id, task)
    cached.at = 0
    runSteps(task, steps, repo)
    sweep(tasks)
    return describe(task)
  }

  function buildPlan({ action, mode, mirror, repo, current }) {
    const python = current.pythonOk ? current.python : managedPython()
    const pip = (args) => [python, ['-m', 'pip', 'install', ...args]]

    if (action === 'python') {
      return {
        skipLabel: `已经有 Python ${current.pythonVersion || ''}，不用装`,
        steps: [{
          label: `下载并安装 Python ${PYTHON_VERSION}（约 28MB）`,
          satisfied: current.pythonOk,
          run: async (task) => await bootstrapPython(task),
        }],
      }
    }

    if (action === 'venv') {
      return {
        skipLabel: '虚拟环境已经建好了',
        steps: [{
          label: '创建 venv 虚拟环境',
          satisfied: current.venv.exists,
          cmd: current.pythonOk ? current.python : python,
          args: ['-m', 'venv', join(repo, 'venv')],
        }],
      }
    }

    if (action === 'base') {
      return {
        skipLabel: '基础依赖已经齐了',
        steps: [{
          label: '安装基础依赖（requirements.txt）',
          satisfied: current.steps.find((s) => s.id === 'base')?.ok,
          ...cmd(pip(['-r', 'requirements.txt', '-i', mirror.url])),
        }],
      }
    }

    if (action === 'nn') {
      const torchStep = {
        label: mode === 'cpu' ? '安装 torch（CPU 轮子，约 122MB）' : '安装 torch（CUDA 版，约 2.4GB）',
        satisfied: mode === 'cpu' ? current.packages.torch.ok : (current.packages.torch.ok && !!current.torchCuda),
        ...cmd(pip(mode === 'cpu'
          ? ['torch', '--index-url', TORCH_CPU_INDEX]
          : ['torch', '-i', mirror.url])),
      }
      const restStep = {
        label: '安装模型版依赖（requirements-nn.txt）',
        satisfied: NN_PACKAGES.every((name) => current.packages[name]?.ok),
        ...cmd(pip(['-r', 'requirements-nn.txt', '-i', mirror.url])),
      }
      return { skipLabel: `${mode === 'cpu' ? 'CPU' : 'GPU'} 模式的模型依赖已经齐了`, steps: [torchStep, restStep] }
    }

    if (action === 'model') {
      return {
        skipLabel: `模型 ${MODEL_REPO} 已经在了`,
        steps: [{
          label: `下载模型 ${MODEL_REPO}（ModelScope）`,
          satisfied: current.model.ok,
          ...cmd([python, ['-m', 'modelscope', 'download', MODEL_REPO, '--local_dir', MODEL_DIR]]),
        }],
      }
    }

    throw new Error(`不认识的安装动作：${action}`)
  }

  function cmd(pair) {
    return { cmd: pair[0], args: pair[1] }
  }

  /** 下载 python.org 的安装包并静默装到托管目录。 */
  async function bootstrapPython(task) {
    const root = managedPythonRoot()
    mkdirSync(root, { recursive: true })
    const installer = join(root, `python-${PYTHON_VERSION}-amd64.exe`)

    task.log.push(`下载 ${PYTHON_INSTALLER}`)
    const response = await fetch(PYTHON_INSTALLER)
    if (!response.ok) throw new Error(`下载失败：HTTP ${response.status}`)
    const buffer = Buffer.from(await response.arrayBuffer())
    writeFileSync(installer, buffer)
    task.log.push(`已下载 ${(buffer.length / 1024 / 1024).toFixed(1)}MB，开始静默安装…`)

    await new Promise((resolve, reject) => {
      execFile(installer, [
        '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0',
        'Include_test=0', 'Include_pip=1', 'Include_doc=0', 'Include_tcltk=0',
        `TargetDir=${root}`,
      ], { windowsHide: true }, (error) => (error ? reject(error) : resolve()))
    })
    try { rmSync(installer, { force: true }) } catch { /* 删不掉也不影响 */ }
    task.log.push(`✓ 已装到 ${root}`)
  }

  function runSteps(task, steps, repo) {
    let index = 0
    const next = async () => {
      if (index >= steps.length) {
        task.status = 'done'
        task.exit = 0
        task.finishedAt = new Date().toISOString()
        return
      }
      const step = steps[index++]
      task.log.push(`▶ ${step.label}`)
      try {
        if (step.run) {
          await step.run(task)
          next()
          return
        }
        const pythonForStep = step.cmd
        if (!pythonForStep) throw new Error('这一步没有可执行的命令（缺少 Python？）')
        task.log.push(`$ ${pythonForStep} ${(step.args || []).join(' ')}`)
        const child = spawn(pythonForStep, step.args || [], { cwd: repo, windowsHide: true })
        task.pid = child.pid
        const push = (chunk) => {
          for (const line of String(chunk).split(/\r?\n/)) {
            if (!line.trim()) continue
            task.log.push(line)
          }
          if (task.log.length > MAX_LOG_LINES) task.log.splice(0, task.log.length - MAX_LOG_LINES)
        }
        child.stdout.on('data', push)
        child.stderr.on('data', push)
        child.on('error', (error) => {
          task.status = 'failed'
          task.error = error.message
          task.log.push(error.message)
        })
        child.on('close', (code) => {
          task.pid = null
          if (task.status === 'cancelled') return
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
      } catch (error) {
        task.status = 'failed'
        task.error = error.message
        task.log.push(`✗ ${error.message}`)
        task.finishedAt = new Date().toISOString()
      }
    }
    next()
  }

  // ── ③ 状态 / 中止 ────────────────────────────────────────────────────

  function task(id) {
    const hit = tasks.get(String(id || ''))
    return hit ? describe(hit) : null
  }

  function cancel(id) {
    const hit = tasks.get(String(id || ''))
    if (!hit || hit.status !== 'running') return false
    hit.status = 'cancelled'
    hit.finishedAt = new Date().toISOString()
    hit.log.push('已中止')
    if (hit.pid) killTree(hit.pid)
    return true
  }

  function describe(task) {
    return {
      id: task.id, action: task.action, mode: task.mode, mirror: task.mirror,
      label: task.label, status: task.status, exit: task.exit, error: task.error,
      startedAt: task.startedAt, finishedAt: task.finishedAt,
      log: (task.log || []).slice(-200),
    }
  }

  function newTaskId() {
    return `env_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
  }

  function sweep(table) {
    const cutoff = Date.now() - TASK_TTL_MS
    for (const [id, task] of table) {
      if (task.finishedAt && Date.parse(task.finishedAt) < cutoff) table.delete(id)
    }
  }

  return { status, start, task, cancel, mirrors: MIRRORS, modelRepo: MODEL_REPO, modelFiles: MODEL_FILES }
}

/** 掐掉整棵进程树：pip 会再起子进程，只杀父进程会把子进程留成孤儿。 */
function killTree(pid) {
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true })
    } else {
      process.kill(pid, 'SIGTERM')
    }
  } catch {
    // 进程已经没了
  }
}
