/**
 * venv 放哪儿 —— 环境页"探测失败"的一半根因在这儿。
 *
 * 踩过的坑：打包版的 venv 以前建在 `%LOCALAPPDATA%\dsh-auditor\engine` 里，而 DSH
 * 的 Windows 沙箱会给这个目录打 ACL；venv 的启动器在里面读不到自己的 `pyvenv.cfg`，
 * 报 `failed to locate pyvenv.cfg`，页面却只说"没装 Python"——于是人去重装了一个
 * 本来完好的环境。现在：venv 建到数据目录（engine 的兄弟），老位置的还能接着用，
 * 真被挡住时给一条"迁移"的出路（只改名 + 目录联接，依赖不重装）。
 *
 *     node _dev/tests/test_venv_paths.mjs
 */
import { lstatSync, mkdirSync, mkdtempSync, readdirSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const scratch = mkdtempSync(join(tmpdir(), 'jw-venv-'))
// settings.js 在函数里读 LOCALAPPDATA，所以导入之后再改也来得及
process.env.LOCALAPPDATA = scratch

const { basePythonOf, engineHome, makeSettings, venvHome, venvPythonIn } = await import('../../dsh_plugin/journal-workbench/src/settings.js')
const { moveVenv, probeVerdict } = await import('../../dsh_plugin/journal-workbench/src/env.js')

const failures = []
const check = (ok, message) => { if (!ok) failures.push(message) }
const near = (a, b) => String(a).toLowerCase() === String(b).toLowerCase()

/** 造一个"看起来像 venv"的目录：有启动器就算（真跑起来不是这里要验的）。 */
function fakeVenv(dir, { cfg = true, home = '' } = {}) {
  mkdirSync(join(dir, 'Scripts'), { recursive: true })
  writeFileSync(join(dir, 'Scripts', 'python.exe'), '')
  if (cfg) writeFileSync(join(dir, 'pyvenv.cfg'), `home = ${home || join(scratch, 'base')}\nversion = 3.13.5\n`)
  return dir
}

// 先拆联接再删：直接递归删目录会顺着联接删到目标里去
function rmTree(dir) {
  let stat
  try { stat = lstatSync(dir) } catch { return }
  if (stat.isSymbolicLink()) { try { rmdirSync(dir) } catch { /* 已没了 */ } return }
  if (!stat.isDirectory()) { try { unlinkSync(dir) } catch { /* 已没了 */ } return }
  for (const name of readdirSync(dir)) rmTree(join(dir, name))
  try { rmdirSync(dir) } catch { /* 已没了 */ }
}

const settings = makeSettings({ get: () => undefined }, {})
const engine = engineHome()
mkdirSync(engine, { recursive: true })

// ── ① 全新机器：venv 该建在数据目录，不在引擎目录里 ──────────────────
{
  const plan = settings.venvFor(engine)
  check(near(plan.create, venvHome()), `全新机器 venv 该建在 ${venvHome()}，实际 ${plan.create}`)
  check(!near(plan.create, join(engine, 'venv')), 'venv 不能再建在 engine/ 里面（沙箱会挡住）')
  check(plan.found === '' && plan.elsewhere === false, '还没有 venv 时 found 该是空的')
  check(venvPythonIn(plan.create) === '', '没有 venv 时不该解析出解释器')
}

// ── ② 老机器：venv 还在 engine 里，得能接着用，并提示迁移 ────────────
{
  const legacy = fakeVenv(join(engine, 'venv'))
  const home = join(scratch, 'base')
  const plan = settings.venvFor(engine)
  check(near(plan.found, legacy), `老位置的 venv 该被认出来，实际 ${plan.found}`)
  check(plan.elsewhere === true, '老位置的 venv 要标 elsewhere（页面据此把按钮改成「迁移」）')
  check(near(settings.pythonFor(engine), join(legacy, 'Scripts', 'python.exe')),
    'pythonFor 还得指向老 venv，不然等于把人家环境当没装')
  check(near(basePythonOf(legacy), join(home, 'python.exe')) !== true, '基座解释器不存在时不该瞎指')

  // 基座解释器真的在：能从 pyvenv.cfg 里读出来（重建 venv 要用它）
  mkdirSync(home, { recursive: true })
  writeFileSync(join(home, 'python.exe'), '')
  check(near(basePythonOf(legacy), join(home, 'python.exe')), 'pyvenv.cfg 的 home 该被解析出来')
}

// ── ③ 数据目录里有了 venv：优先用它，不再用 engine 里的 ──────────────
{
  fakeVenv(venvHome())
  const plan = settings.venvFor(engine)
  check(near(plan.found, venvHome()), '数据目录的 venv 该优先于 engine 里的')
  check(plan.elsewhere === false, '就在该在的地方，不该标 elsewhere')
  check(near(settings.pythonFor(engine), join(venvHome(), 'Scripts', 'python.exe')), 'pythonFor 该指向新位置')
}

// ── ④ 开发版（项目根就是仓库根）：venv 就地，跟命令行同一份 ───────────
{
  const project = join(scratch, 'project')
  fakeVenv(join(project, 'venv'))
  mkdirSync(project, { recursive: true })
  const plan = settings.venvFor(project)
  check(near(plan.create, join(project, 'venv')), '开发版 venv 该建在仓库根')
  check(near(plan.found, join(project, 'venv')), '开发版该认出仓库里的 venv')
  check(plan.elsewhere === false, '开发版不该要求迁移')
}

// ── ⑤ 探测失败怎么说：装没装、被挡住、还是别的问题 ──────────────────
{
  const ok = probeVerdict({ python: 'C:/py/python.exe', probe: { ok: true, python: '3.13.16' } })
  check(ok.ok && ok.detail.includes('3.13.16') && ok.detail.includes('python.exe'), '探测成功就该报版本和路径')

  const missing = probeVerdict({ python: 'python.exe', probe: { ok: false, missing: true, error: 'spawn ENOENT' } })
  check(!missing.ok && missing.detail.includes('没装'), '根本没解释器才说"没装"')

  const blocked = probeVerdict({
    python: 'C:/data/engine/venv/Scripts/python.exe',
    probe: { ok: false, exit: 103, error: 'failed to locate pyvenv.cfg: C:\\data\\engine\\venv' },
    venvDir: 'C:/data/engine/venv',
    venvTarget: 'C:/data/venv',
    cfgExists: true,
  })
  check(!blocked.ok, '被挡住时还是红')
  check(blocked.detail.includes('pyvenv.cfg') && blocked.detail.includes('读不到'), '要说清是"读不到 pyvenv.cfg"，不是"没装"')
  check(blocked.hint.includes('迁移') && blocked.hint.includes('C:/data/venv'), '要给出迁移这条路')

  // 文件其实不在：那就是 venv 真坏了，不能赖沙箱
  const gone = probeVerdict({
    python: 'C:/data/x/Scripts/python.exe',
    probe: { ok: false, exit: 103, error: 'failed to locate pyvenv.cfg: C:\\data\\x' },
    venvDir: 'C:/data/x',
    venvTarget: 'C:/data/venv',
    cfgExists: false,
  })
  check(gone.detail.includes('103') && gone.hint === '', '没 pyvenv.cfg 就走"通用失败"，带上退出码')

  const generic = probeVerdict({ python: 'py', probe: { ok: false, exit: 1, error: 'Traceback ... ModuleNotFoundError' } })
  check(generic.detail.includes('退出码 1') && generic.detail.includes('ModuleNotFoundError'), '别的失败要原样带上退出码与首行')
}

// ── ⑥ 迁移：只改名 + 目录联接，搬完当场验一次 ────────────────────────
{
  const from = fakeVenv(join(scratch, 'legacy', 'venv'))
  const to = join(scratch, 'moved', 'venv')
  const task = { log: [] }
  const probed = []
  const result = await moveVenv(task, {
    from, to,
    probe: async (python) => { probed.push(python); return { ok: true, python: '3.13.16' } },
  })

  check(result?.ok === true, '迁移后探测成功要如实返回')
  check(near(venvPythonIn(to), join(to, 'Scripts', 'python.exe')), `迁移后解释器该在新位置：${venvPythonIn(to)}`)
  check(probed.length === 1 && near(probed[0], join(to, 'Scripts', 'python.exe')), '探测该打在新位置上')
  check(task.log.some((line) => line.includes('迁移后解释器正常')), '日志要写清迁移后能不能跑')
  if (process.platform === 'win32') {
    check(lstatSync(from).isSymbolicLink(), '老位置该留下目录联接')
    check(near(venvPythonIn(from), join(from, 'Scripts', 'python.exe')), '通过联接也该读得到解释器')
  }

  // 目标已经有东西时不许动
  let threw = false
  try {
    await moveVenv({ log: [] }, { from: to, to: from, probe: async () => ({ ok: true }) })
  } catch {
    threw = true
  }
  check(threw, '目标已存在时必须停下，不能覆盖别人的目录')
}

rmTree(scratch)

if (failures.length) {
  console.error(`❌ ${failures.length} 项不符：`)
  for (const message of failures) console.error(`   · ${message}`)
  process.exit(1)
}
console.log('✅ venv 位置、探测措辞、迁移都对上了（6 组）')
