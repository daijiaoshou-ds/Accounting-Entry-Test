import { statSync } from 'node:fs'
import { dirname, extname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { JournalError, runPython } from './cli.js'

/** 解析脚本就在本目录：它只做"文件 → 文字矩阵"，分页/搜索在这里做。 */
const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), 'sheet_preview.py')

/** 能预览的扩展名。别的（pdf/图片/未知）只给下载，不装作能看。 */
const PREVIEWABLE = new Set(['.xlsx', '.xlsm', '.xlsb', '.xls', '.ods', '.csv'])

/**
 * 内存闸门。解析一次整张表常驻，所以两头都要限：格子总数（列多时行数没意义）
 * 与行数（一张几十万行的 csv）。
 *
 * 42,683×22 ≈ 94 万格，正好在闸门内——一期账的交付物不需要截断；
 * 全年账的 6 万行报表才会碰到上限，那时页面会明说"只预览前 N 行"。
 */
const MAX_CELLS = 1_200_000
const MAX_ROWS = 60_000

/** 缓存几个文件：切 sheet 不用重读，切任务才换。一本账三个 sheet 都待在同一份文件里。 */
const CACHE_FILES = 2

/** 一次给多少行：够铺满两屏，滚动/翻页时再要。 */
const DEFAULT_LIMIT = 300
const MAX_LIMIT = 2_000

/** 读 6 万行要一两秒，给足余量；真卡住就别让请求永远挂着。 */
const PARSE_TIMEOUT_MS = 120_000
const PARSE_MAX_BYTES = 128 * 1024 * 1024

export function isPreviewable(filename) {
  return PREVIEWABLE.has(extname(String(filename || '')).toLowerCase())
}

/**
 * 产物预览。
 *
 * 三件事：**认路径**（只认 `state.json` 里登记过的产物，复用 `resolveDownload`，
 * 不信任浏览器传来的路径）、**读一次**（整张表解析进内存，按文件 LRU 留两份）、
 * **切窗口**（分页与搜索都在内存里做，不再起 python）。
 *
 * 为什么整张表进内存而不是每次读一遍：翻页/搜索是高频动作，一次 python 启动
 * 加解析要 1.5s，翻三页人就走了。整进内存后翻页是毫秒级，代价是一份表几十兆
 * ——所以有闸门，也有 LRU。
 */
export function createPreview({ ops, settings }) {
  /** key = 路径 + mtime + 大小：文件被重跑覆盖后旧缓存自然失效，不用手动清。 */
  const cache = new Map()

  function touch(key) {
    const entry = cache.get(key)
    if (!entry) return null
    cache.delete(key)
    cache.set(key, entry)
    return entry
  }

  function keep(key, entry) {
    cache.set(key, entry)
    while (cache.size > CACHE_FILES) {
      const oldest = cache.keys().next().value
      cache.delete(oldest)
    }
    return entry
  }

  async function parse({ python, repo, path, sheet }) {
    const args = ['--file', path, '--max-cells', String(MAX_CELLS), '--max-rows', String(MAX_ROWS)]
    if (sheet) args.push('--sheet', sheet)
    const result = await runPython({
      python,
      cwd: repo,
      script: SCRIPT,
      args,
      timeoutMs: PARSE_TIMEOUT_MS,
      maxCapture: PARSE_MAX_BYTES,
    })
    if (!result.ok) throw new JournalError(`读不了这个表：${result.error}`)
    const value = result.value || {}
    if (value.ok === false) throw new JournalError(value.error || '读表失败')
    return value
  }

  /**
   * 一张表 → 一个窗口。
   *
   * `q` 非空时窗口落在**命中行**上（不然搜完还得自己翻到第 3 万行），
   * `total` 也跟着变成命中数——页脚说"共 N 行"才不会说谎。
   *
   * 无论哪种情况都带上 `rowIndexes`：搜索结果是挑出来的行，不还原原始行号的话，
   * 页面左边那列会从 1 重新数，人对着 Excel 找就找不着了。
   */
  function view(entry, { sheet, offset, limit, q }) {
    const data = entry.data.get(sheet)
    if (!data) throw new JournalError(`这张表还没读出来：${sheet}`)
    const header = data.rows[0] || []
    const body = data.rows.slice(1)

    const needle = String(q || '').trim().toLowerCase()
    let source = body
    let indexes = null
    if (needle) {
      const hits = []
      for (let i = 0; i < body.length; i += 1) {
        const row = body[i]
        for (let c = 0; c < row.length; c += 1) {
          if (String(row[c]).toLowerCase().includes(needle)) {
            hits.push(i)
            break
          }
        }
      }
      source = hits.map((i) => body[i])
      indexes = hits
    }

    const start = Math.max(0, Math.min(Number(offset) || 0, source.length))
    const size = Math.max(1, Math.min(Number(limit) || DEFAULT_LIMIT, MAX_LIMIT))
    const page = source.slice(start, start + size)
    return {
      sheets: entry.sheets,
      sheet,
      header,
      rows: page,
      rowIndexes: indexes ? indexes.slice(start, start + size) : null,
      offset: start,
      total: source.length,
      truncated: !!data.truncated,
      q: needle ? String(q).trim() : '',
    }
  }

  /** 页面上那条 `GET /preview`。 */
  async function sheetView({ job_id, key: fileKey, sheet, offset, limit, q }, agent) {
    const hit = await ops.resolveDownload({ job_id, key: fileKey }, agent)
    if (!isPreviewable(hit.filename)) {
      throw new JournalError(`${hit.filename} 不支持预览，用下载打开`)
    }

    const info = statSync(hit.path)
    const key = `${hit.path}|${info.mtimeMs}|${info.size}`
    let entry = touch(key)
    if (!entry) {
      const repo = await settings.repoFor(agent)
      entry = keep(key, {
        path: hit.path,
        filename: hit.filename,
        sheets: [],
        data: new Map(),
      })
      await fill(entry, { python: settings.pythonFor(repo), repo, sheet: '' })
    }

    const wanted = String(sheet || '').trim()
    const known = entry.data.has(wanted) || (!wanted && entry.data.size > 0)
    if (!known) {
      const repo = await settings.repoFor(agent)
      await fill(entry, { python: settings.pythonFor(repo), repo, sheet: wanted })
    }

    const active = wanted && entry.data.has(wanted) ? wanted : firstSheet(entry)
    const payload = view(entry, { sheet: active, offset, limit, q })
    return {
      ...payload,
      file: { key: fileKey, name: entry.filename, bytes: info.size },
    }
  }

  /** 补一次解析：整份表进缓存，页签的行列数也跟着更新。 */
  async function fill(entry, { python, repo, sheet }) {
    const parsed = await parse({ python, repo, path: entry.path, sheet })
    entry.sheets = Array.isArray(parsed.sheets) ? parsed.sheets : []
    const name = parsed.sheet
    entry.data.set(name, {
      rows: Array.isArray(parsed.rows) ? parsed.rows : [],
      truncated: !!parsed.truncated,
    })
  }

  function firstSheet(entry) {
    return entry.data.keys().next().value || ''
  }

  return { sheetView, isPreviewable }
}
