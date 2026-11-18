import { runJournal } from './cli.js'

/**
 * 引擎的目录布局只问一次：`journal env`。
 *
 * 以前插件自己拼这些路径（`join(repo, 'core', 'nn', '_storage')`、桶表、
 * `features/<id>/review-guide.md`）。后果是引擎挪一次目录就得改一遍 JS，而
 * **改漏了不报错**：桶表读不到只是复核提示里少一段定义，模型目录读不到只是
 * 环境卡显示"缺文件"。目录布局的真相在 Python 那边（`jobflow/cli.py` 的
 * `env`），从这里问一次就够——插件不需要知道 `core/` 下面长什么样。
 *
 * 缓存：成功 10 分钟、失败 30 秒。失败短一点，因为"环境还没装好"是暂时的，
 * 装好之后不该等十分钟才认。
 */
const OK_TTL_MS = 10 * 60 * 1000
const FAIL_TTL_MS = 30 * 1000

const cache = new Map()

/**
 * @returns {Promise<null | {repo:string, jobsDir:string, modelDir:string,
 *   modelCache:string, taxonomy:string, reviewGuides:Object, features:string[],
 *   python:string, pythonVersion:string}>}
 *   `null` = 问不到（没有 `env` 子命令 / Python 跑不起来），调用方自行降级。
 */
export async function loadLayout({ repo, python }) {
  if (!repo) return null
  const key = `${repo}|${python || ''}`
  const hit = cache.get(key)
  if (hit && Date.now() < hit.expires) return hit.value

  let value = null
  try {
    const result = await runJournal({ python, repo, args: ['env'], timeoutMs: 30_000 })
    if (result.ok && result.value && result.value.ok) value = normalize(result.value, repo)
  } catch {
    // 老引擎没有 `env` / 解释器没装：降级，不抛
  }
  cache.set(key, { value, expires: Date.now() + (value ? OK_TTL_MS : FAIL_TTL_MS) })
  return value
}

/** 装了新版本、挪过目录之后想立刻重问，就调这个（不调也会在 TTL 后自己过期）。 */
export function clearLayout(repo) {
  if (!repo) {
    cache.clear()
    return
  }
  for (const key of [...cache.keys()]) {
    if (key.startsWith(`${repo}|`)) cache.delete(key)
  }
}

function normalize(raw, repo) {
  const text = (v) => (typeof v === 'string' && v.trim() ? v.trim() : '')
  const guides = {}
  for (const [name, file] of Object.entries(raw.review_guides || {})) {
    if (text(file)) guides[name] = text(file)
  }
  return {
    repo: text(raw.repo) || repo,
    jobsDir: text(raw.jobs_dir),
    modelDir: text(raw.model_dir),
    modelCache: text(raw.model_cache),
    taxonomy: text(raw.taxonomy),
    reviewGuides: guides,
    features: Array.isArray(raw.features) ? raw.features.map(String) : [],
    python: text(raw.python),
    pythonVersion: text(raw.python_version),
  }
}
