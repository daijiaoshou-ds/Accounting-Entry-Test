import { spawn, execFile } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, renameSync, writeFileSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { basePythonOf, managedPython, managedPythonRoot, venvPythonIn } from './settings.js'
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

/**
 * 探测失败要怎么讲。
 *
 * 一律说成"没装"会把人带偏：有人就是照着这句话去重装了一个本来完好的环境，
 * 因为解释器在、依赖也在，只是那个目录不让 venv 的启动器读 `pyvenv.cfg`。
 * 三种情况分开说：
 *   ① 根本没有解释器（spawn ENOENT）→ 确实可以在这里装一个
 *   ② `pyvenv.cfg` 在、启动器却说读不到 → 路径被权限/沙箱挡住，挪位置就好
 *   ③ 别的失败 → 原样给退出码 + stderr 首行，别替它下结论
 */
export function probeVerdict({ python, probe, venvDir = '', venvTarget = '', cfgExists = false }) {
  if (probe?.ok) return { ok: true, detail: `v${probe.python} · ${python}`, hint: '' }
  if (probe?.missing) return { ok: false, detail: '没装 Python（可以在这里装一个）', hint: '' }

  const raw = String(probe?.error || '').replace(/\s+/g, ' ').trim()
  if (cfgExists && /pyvenv\.cfg/i.test(raw)) {
    return {
      ok: false,
      detail: `解释器在 ${python}，但它读不到同目录的 pyvenv.cfg——文件是齐的，这个路径被挡住了`,
      hint: venvDir
        ? `venv 在 ${venvDir}。点「迁移虚拟环境」挪到 ${venvTarget} 即可，依赖不用重装。`
        : '',
    }
  }
  const exit = probe?.exit === undefined || probe?.exit === null ? '' : `（退出码 ${probe.exit}）`
  return { ok: false, detail: `探测失败${exit}：${raw.slice(0, 200) || '没有输出'}`, hint: '' }
}

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

    const venv = settings.venvFor
      ? settings.venvFor(repo)
      : { create: join(repo, 'venv'), found: '', python: '', elsewhere: false }
    const venvOk = !!venv.found
    // venv 目录里 `pyvenv.cfg` 在不在：在的话，"启动器说读不到它"就只能是路径被挡住了
    const cfgExists = venvOk && existsSync(join(venv.found, 'pyvenv.cfg'))
    // 文件齐、启动器却读不到：这不是"坏了"，是这个位置不准它读（见 venvHome 的注释）
    const venvBlocked = venvOk && cfgExists && /pyvenv\.cfg/i.test(String(probe.error || '')) && !probe.ok
    const verdict = repoOk
      ? probeVerdict({ python, probe, venvDir: venv.found, venvTarget: venv.create, cfgExists })
      : { ok: false, detail: probe.error || '', hint: '' }

    const venvStep = {
      id: 'venv', label: '虚拟环境（venv）', ok: venvOk && !!probe.ok,
      detail: !venvOk
        ? `还没有 venv（会建在 ${venv.create}）`
        : !probe.ok
          ? `venv 在 ${venv.found}，但里面的解释器起不来（见上一步）`
          : venv.found,
      hint: venvOk && !probe.ok
        ? (venv.elsewhere
          ? `点「迁移虚拟环境」把它挪到 ${venv.create}，依赖不用重装`
          : '就地重写启动器与 pyvenv.cfg 即可（依赖不用重装）')
        : '',
    }

    // 六步走：解释器 → 虚拟环境 → 基础依赖 → 模型依赖 → 模型文件 → 基座模型
    const steps = [
      { id: 'python', label: 'Python 解释器', ok: verdict.ok, detail: verdict.detail, hint: verdict.hint },
      venvStep,
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
      venv: {
        path: venv.found || venv.create,
        create: venv.create,
        found: venv.found,
        exists: venvOk,
        ok: venvOk && !!probe.ok,
        elsewhere: !!venv.elsewhere,
        blocked: venvBlocked,
      },
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
      // 安装包版本是"能装到哪个版本"，跟上面探测到的实际版本不是一回事——
      // 以前两边共用 pythonVersion，界面显示的永远是 3.13.5（哪怕跑的是 3.13.16）
      pythonInstaller: PYTHON_VERSION,
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
        resolve({ ok: false, missing: error.code === 'ENOENT', error: error.message })
        return
      }
      child.stdout.on('data', (chunk) => { out += chunk })
      child.stderr.on('data', (chunk) => { err += chunk })
      // ENOENT = 这个路径下根本没有解释器（和"有，但起不来"是两件事）
      child.on('error', (error) => resolve({ ok: false, missing: error.code === 'ENOENT', error: error.message }))
      child.on('close', (code) => {
        if (code !== 0) {
          resolve({
            ok: false,
            exit: code,
            error: err.trim().split('\n').slice(-3).join(' ') || `退出码 ${code}`,
          })
          return
        }
        try {
          resolve({ ok: true, ...JSON.parse(out.trim().split('\n').pop()) })
        } catch {
          resolve({ ok: false, exit: code, error: `探测输出读不懂：${out.slice(-200)}` })
        }
      })
    })
  }

  /**
   * 把 venv 从被挡住的位置搬到数据目录（不重装依赖）。
   *
   * 只做改名（同一块盘上是瞬时的），然后在老位置留一个目录联接：老位置可能还被
   * 配置里的 python 路径、任务记录、venv 自己的脚本引用着，联接让它们接着能用。
   * 搬完当场再探测一次，成了就说成了，没成就直说"venv 本身坏了，得重建"。
   */
  async function migrateVenv(task, { from, to, repo }) {
    return await moveVenv(task, {
      from,
      to,
      probe: (python) => runProbe(python, repo),
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

    // 依赖/模型都要用"跑得起来的解释器"装：解释器起不来就先修解释器，
    // 别在这儿抛一句"缺少 Python"让人自己猜
    if (action !== 'python' && action !== 'venv' && !current.pythonOk) {
      throw new Error(current.venv?.blocked
        ? '解释器起不来，挡住的是 venv 那个路径：先点「迁移虚拟环境」，再回来装依赖'
        : '解释器起不来：先把「Python 解释器」「虚拟环境」两步修好，再回来装依赖')
    }

    if (action === 'python') {
      // 解释器在、只是 venv 那条路径被挡住时，装一个新 Python 不会被选中
      // （pythonFor 优先用 venv 里的那个），只会让人白等十分钟
      if (current.venv?.blocked) {
        return {
          skipLabel: '不用装 Python：解释器在，被挡住的是 venv 的路径——点「迁移虚拟环境」（依赖不重装）',
          steps: [],
        }
      }
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
      const target = current.venv?.create || join(repo, 'venv')
      const found = current.venv?.found || ''
      // 能跑就别折腾：venv 在老位置但一直好用，不强行搬家
      if (found && current.pythonOk) {
        return { skipLabel: `虚拟环境已经能用了（${found}）`, steps: [{ label: '虚拟环境', satisfied: true }] }
      }

      if (found && current.venv?.elsewhere) {
        return {
          skipLabel: '虚拟环境已经能用了',
          steps: [{
            label: `把 venv 迁到数据目录（${target}）：改名 + 目录联接，依赖不重装`,
            satisfied: false,
            run: async (task) => await migrateVenv(task, { from: found, to: target, repo }),
          }],
        }
      }

      // 没有 venv，或者它就在该在的地方却起不来：`python -m venv` 就地重写启动器与
      // pyvenv.cfg，site-packages 留着——这一步不下载任何依赖。
      const rebuild = current.pythonOk
        ? current.python
        : (basePythonOf(found) || managedPython() || (process.platform === 'win32' ? 'python.exe' : 'python3'))
      return {
        skipLabel: '虚拟环境已经能用了',
        steps: [{
          label: found ? '就地修复 venv（重写启动器与 pyvenv.cfg，依赖不重装）' : `创建 venv（${target}）`,
          satisfied: false,
          cmd: rebuild,
          args: ['-m', 'venv', target],
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

/**
 * 把 venv 挪到新位置，并在老位置留一个目录联接（不重装依赖）。
 *
 * 只改名：同一块盘上是瞬时的；跨盘（老位置在 D 盘）才退化成整份复制，那时旧目录
 * 先留着、不建联接——复制出来的 venv 里的脚本还指着老路径，删旧目录之前得人工确认。
 * 搬完用传进来的 `probe` 再验一次，报告里给结论（不在日志里假装成功）。
 */
export async function moveVenv(task, { from, to, probe }) {
  if (!from || !to) throw new Error('不知道该把 venv 从哪搬到哪')
  if (!existsSync(venvPythonIn(from))) throw new Error(`这里没有 venv：${from}`)
  mkdirSync(dirname(to), { recursive: true })
  if (existsSync(to)) throw new Error(`目标位置已经有东西，没敢动：${to}`)

  let moved = false
  try {
    renameSync(from, to)
    moved = true
    task.log.push(`✓ 已改名：${from} → ${to}`)
  } catch (error) {
    task.log.push(`× 改名失败：${error.message}——改为整份复制（约 1GB，要等一会儿）`)
    cpSync(from, to, { recursive: true })
    task.log.push(`✓ 已复制到 ${to}（老目录 ${from} 先留着，确认没问题可以删）`)
  }

  if (moved) {
    const link = await makeJunction(from, to)
    if (link.ok) task.log.push(`✓ 老位置留下目录联接：${from} → ${to}`)
    else task.log.push(`· 目录联接没建起来（${link.reason || '未知原因'}），不影响使用`)
  }

  const result = await probe(venvPythonIn(to))
  if (result?.ok) task.log.push(`✓ 迁移后解释器正常：Python ${result.python}`)
  else {
    task.log.push(`✗ 迁移后仍然起不来：${String(result?.error || '').slice(0, 160)}`)
    task.log.push('· 换位置没解决就是 venv 本身坏了：点「创建虚拟环境」就地重写启动器（依赖不重装）')
  }
  return result
}

/** Windows 目录联接（`mklink /J`）：不需要管理员权限，读起来跟真目录一样。 */
function makeJunction(link, target) {
  return new Promise((resolve) => {
    if (process.platform !== 'win32') {
      resolve({ ok: false, reason: '不是 Windows' })
      return
    }
    execFile('cmd', ['/c', 'mklink', '/J', link, target], { windowsHide: true }, (error, stdout, stderr) => {
      resolve({ ok: !error, reason: error ? String(stderr || stdout || error.message).trim() : '' })
    })
  })
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
