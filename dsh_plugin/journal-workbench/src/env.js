import { spawn, execFile } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'

import { managedPython, managedPythonRoot } from './settings.js'
import { loadLayout } from './layout.js'

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

/** 模型发布在 ModelScope。目录不写死：问 `journal env` 要 `model_dir`。 */
export const MODEL_REPO = 'daijiaoshou/hajishou-V2.0'
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
/** 安装包源：官方优先，国内网络下华为云镜像更快（是同一份文件，不是重打包）。 */
export const PYTHON_SOURCES = [
  `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-amd64.exe`,
  `https://mirrors.huaweicloud.com/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-amd64.exe`,
]

/**
 * 基础依赖：没有它们连读表都跑不了。
 *
 * 这份名单 = 运行时真实 import 到的 + parquet 落盘要用的 pyarrow。以前还带了
 * scikit-learn / streamlit，前者现在没 import、后者是旧界面的，装上去只是多等
 * 一分钟；改名要和 requirements*.txt 一起改（两边对不上，环境页会说"缺"）。
 */
const BASE_PACKAGES = ['pandas', 'numpy', 'pyarrow', 'python-calamine', 'openpyxl']
const NN_PACKAGES = ['transformers', 'safetensors', 'modelscope']

/**
 * CPU / GPU 两种模式的差别只有 torch 的轮子：
 *   CPU  走 PyTorch 官方 cpu 源，122MB，装完 540MB
 *   GPU  走 pip 镜像里的 CUDA 版，下 2.4GB、装完 4GB+
 * 其余依赖一样。所以界面上按模式列出"会装什么"，别让人以为没区别。
 */
export const TORCH_CPU_INDEX = 'https://download.pytorch.org/whl/cpu'
/** CPU 轮子的备用源：官方站国内时快时慢，交大镜像同版本。 */
export const TORCH_CPU_MIRRORS = [
  TORCH_CPU_INDEX,
  'https://mirror.sjtu.edu.cn/pytorch-wheels/cpu',
]

const MAX_LOG_LINES = 400
const TASK_TTL_MS = 6 * 60 * 60 * 1000

const PROBE = `
import json, sys, importlib.metadata as md
names = ${JSON.stringify([...BASE_PACKAGES, ...NN_PACKAGES, 'torch'])}
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
    // 布局（模型目录等）问引擎要，不在这里拼路径；问不到就明说，不猜
    const layout = repoOk ? await loadLayout({ repo, python }) : null
    const probe = repoOk ? await runProbe(python, repo) : { ok: false, error: `这里没有 journal.py：${repo}` }
    const packages = {}
    for (const name of [...BASE_PACKAGES, ...NN_PACKAGES, 'torch']) {
      const version = probe.packages?.[name] || null
      packages[name] = { ok: !!version, version }
    }

    const modelDir = layout?.modelDir || ''
    const files = modelDir
      ? MODEL_FILES.map((name) => ({ name, ok: existsSync(join(modelDir, name)) }))
      : []
    const modelOk = !!modelDir && files.length > 0 && files.every((file) => file.ok)
    // 基座 BGE（1.6GB）第一次分类时才用到；不预先下好，第一本账会静默等十几分钟
    const bgeOk = !!layout?.modelCache && bgeCached(layout.modelCache)

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
        detail: !modelDir ? '读不到引擎布局（journal env 没跑起来）'
          : modelOk ? MODEL_REPO : `缺 ${files.filter((f) => !f.ok).map((f) => f.name).join('、')}` },
      { id: 'bge', label: '基座模型（BGE）', ok: bgeOk,
        detail: !layout?.modelCache ? '读不到引擎布局'
          : bgeOk ? 'bge-large-zh 已在缓存' : '缺（约 1.6GB，第一次分类时才用，会在后台等很久）' },
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
      model: { dir: modelDir, files, ok: modelOk, bge: bgeOk, repo: MODEL_REPO },
      steps,
      modes: {
        cpu: { ready: baseOk && torchOk && nnPkgsOk, detail: 'torch CPU 版（约 122MB，装完 540MB）' },
        gpu: { ready: baseOk && torchOk && cudaOk && nnPkgsOk,
          detail: cudaOk ? `torch CUDA 版（当前 CUDA ${probe.torch_cuda}）` : 'torch CUDA 版（约 2.4GB；当前是 CPU 版，装 CUDA 版才能用显卡）' },
      },
      ready: { basic: baseOk, model: baseOk && torchOk && nnPkgsOk && modelOk && bgeOk },
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

    // 已经有一件在装：直接返回它。两次 pip 抢同一个 venv 会把环境弄坏，
    // 而人离开页面再回来，看到的也应该还是同一件。
    const live = latest()
    if (live && live.status === 'running' && !force) return live

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
        // CPU 轮子两个源挨着试：官方站国内时快时慢，交大镜像同版本。
        run: mode === 'cpu'
          ? async (task) => {
            let lastError = null
            for (const index of TORCH_CPU_MIRRORS) {
              try {
                await spawnStep(task, python, ['-m', 'pip', 'install', 'torch',
                  '--index-url', index], repo)
                return
              } catch (error) {
                lastError = error
                task.log.push(`× ${index}：${error.message}`)
              }
            }
            throw lastError || new Error('torch 装不上')
          }
          : cmd(pip(['torch', '-i', mirror.url])),
      }
      const restStep = {
        label: '安装模型版依赖（requirements-nn.txt）',
        satisfied: NN_PACKAGES.every((name) => current.packages[name]?.ok),
        ...cmd(pip(['-r', 'requirements-nn.txt', '-i', mirror.url])),
      }
      return { skipLabel: `${mode === 'cpu' ? 'CPU' : 'GPU'} 模式的模型依赖已经齐了`, steps: [torchStep, restStep] }
    }

    if (action === 'model') {
      const target = current.model?.dir || ''
      if (!target) throw new Error('读不到引擎布局（journal env 没跑起来），不知道该把模型下到哪')
      return {
        skipLabel: `模型 ${MODEL_REPO} 与基座 BGE 都已经在了`,
        steps: [{
          label: `下载模型 ${MODEL_REPO}（ModelScope）`,
          satisfied: current.model.ok,
          ...cmd([python, ['-c',
            'from modelscope import snapshot_download;'
            + ` snapshot_download(${JSON.stringify(MODEL_REPO)}, local_dir=${JSON.stringify(target)})`]]),
        }, {
          // 基座 1.6GB：不预下的话，第一本账跑分类时会静默等十几分钟
          label: '下载基座模型 BGE（约 1.6GB）',
          satisfied: current.model.bge,
          ...cmd([python, ['-c',
            'from core.nn.model_loader import resolve_model_dir;'
            + ' print(resolve_model_dir("BAAI/bge-large-zh-v1.5"))']]),
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

    let buffer = null
    let lastError = ''
    for (const url of PYTHON_SOURCES) {
      task.log.push(`下载 ${url}`)
      try {
        const response = await fetch(url)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        buffer = Buffer.from(await response.arrayBuffer())
        break
      } catch (error) {
        lastError = error.message
        task.log.push(`× ${url}：${error.message}`)
      }
    }
    if (!buffer) throw new Error(`Python 安装包下载失败：${lastError}`)
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

  /** 跑一条命令并等它结束：0 = 成功，非 0 抛错（调用方决定重试还是失败）。 */
  function spawnStep(task, command, args, repo) {
    return new Promise((resolve, reject) => {
      task.log.push(`$ ${command} ${(args || []).join(' ')}`)
      const child = spawn(command, args || [], { cwd: repo, windowsHide: true })
      task.pid = child.pid
      const push = (chunk) => {
        for (const line of String(chunk).split(/\r?\n/)) {
          if (!line.trim()) continue
          task.log.push(line)
        }
        if (task.log.length > MAX_LOG_LINES) {
          task.log.splice(0, task.log.length - MAX_LOG_LINES)
        }
      }
      child.stdout.on('data', push)
      child.stderr.on('data', push)
      child.on('error', reject)
      child.on('close', (code) => {
        task.pid = null
        if (task.status === 'cancelled') {
          reject(new Error('已中止'))
          return
        }
        if (code === 0) resolve()
        else reject(Object.assign(new Error(`退出码 ${code}`), { code }))
      })
    })
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
        if (step.run) await step.run(task)
        else {
          const command = step.cmd
          if (!command) throw new Error('这一步没有可执行的命令（缺少 Python？）')
          await spawnStep(task, command, step.args || [], repo)
        }
        task.log.push(`✓ ${step.label}`)
        next()
      } catch (error) {
        if (task.status === 'cancelled') return
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

  /** 最近一个任务：页面重新打开时靠它接上进度（安装本身一直在 Host 里跑）。 */
  function latest() {
    let best = null
    for (const item of tasks.values()) {
      if (!best || Date.parse(item.startedAt) > Date.parse(best.startedAt)) best = item
    }
    return best ? describe(best) : null
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

  return { status, start, task, latest, cancel, mirrors: MIRRORS, modelRepo: MODEL_REPO, modelFiles: MODEL_FILES }
}

/**
 * BGE 缓存在不在：两种布局都认——引擎自己下的 `BAAI__bge-large-zh-v1.5/`，
 * 和 modelscope 快照目录 `models/BAAI--bge-.../snapshots/`。
 */
function bgeCached(dir) {
  return existsSync(join(dir, 'BAAI__bge-large-zh-v1.5', 'config.json'))
    || existsSync(join(dir, 'models', 'BAAI--bge-large-zh-v1.5', 'snapshots'))
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
