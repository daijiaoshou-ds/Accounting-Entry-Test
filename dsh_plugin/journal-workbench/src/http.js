import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'

/** 页面用的一条 JSON 路由。前缀取长一点的固定串，撞不上 SPA 的 fallback。 */
const ROUTE = '/plugin/journal'

/** 上传上限：一份全年序时账也就 50~80MB，128MB 够用。 */
const MAX_UPLOAD = 128 * 1024 * 1024

const MIME = {
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xls': 'application/vnd.ms-excel',
  '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
  '.csv': 'text/csv; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.parquet': 'application/octet-stream',
}

/**
 * 工作台页面的数据接口。
 *
 * 为什么走 HTTP 而不是 Remote 服务：页面在浏览器里、Host 在 Node 里，两边同源，
 * 一个 `fetch('/plugin/journal/...')` 比引入一套 typert 远程调用简单得多，
 * 而且这些数据本来就是"随便刷都行"的只读视图。
 *
 * 为什么用 `handler` 而不是 `resource`：resource 路由只接 GET/HEAD（其它方法在
 * 匹配之前就被 405 挡掉），而这里有上传和提交两个 POST。所以自己接
 * `IncomingMessage`，再包成 Fetch 的 `Request` 交给同一套 `handle()`——
 * 中间那一层逻辑不用写两遍。
 *
 * 安全边界只有一条：**不信任浏览器传来的路径**。下载只认 `state.json` 里登记过的
 * 产物（`resolveDownload`），上传只写进 `_storage_jobs/.incoming/`。
 */
export function registerRoutes(ctx, { ops, settings }) {
  const server = ctx.get('webServer')
  if (!server || typeof server.register !== 'function') return () => {}
  return server.register({
    kind: 'prefix',
    path: ROUTE,
    handler: async (req, res) => {
      const response = await fromNode(ctx, ops, settings, req)
      await toNode(res, response)
    },
  })
}

/** Node 的 http 对象 → Fetch 的 Request（body 一次性读成 Buffer，上传最大 128MB）。 */
async function fromNode(ctx, ops, settings, req) {
  try {
    const method = (req.method || 'GET').toUpperCase()
    const url = `http://${req.headers.host || '127.0.0.1'}${req.url}`
    const body = method === 'GET' || method === 'HEAD' ? undefined : await readAll(req)
    const request = new Request(url, {
      method,
      headers: req.headers,
      body: body && body.length ? body : undefined,
    })
    return await handle(ctx, ops, settings, request)
  } catch (error) {
    return json({ ok: false, error: error?.message || String(error) }, 400)
  }
}

function readAll(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

async function toNode(res, response) {
  const headers = {}
  response.headers.forEach((value, key) => {
    headers[key] = value
  })
  const body = Buffer.from(await response.arrayBuffer())
  headers['content-length'] = String(body.length)
  res.writeHead(response.status, headers)
  res.end(body)
}

async function handle(ctx, ops, settings, request) {
  const url = new URL(request.url, 'http://dsh.local')
  const route = url.pathname.slice(ROUTE.length).replace(/\/+$/, '') || '/'
  const method = (request.method || 'GET').toUpperCase()

  try {
    const sid = url.searchParams.get('sid') || ''
    const agent = resolveAgent(ctx, sid)

    if (method === 'GET' && route === '/jobs') {
      const info = await ops.overview(agent)
      return json({ ok: true, repo: info.repo, jobs: info.jobs })
    }

    if (method === 'GET' && route === '/job') {
      return json({ ok: true, job: await ops.jobView({ job_id: param(url, 'id') }, agent) })
    }

    if (method === 'GET' && route === '/review') {
      const review = await ops.reviewView({
        job_id: param(url, 'id'),
        stage: url.searchParams.get('stage') || '',
        offset: url.searchParams.get('offset') || 0,
        limit: url.searchParams.get('limit') || 200,
        q: url.searchParams.get('q') || '',
      }, agent)
      return json({ ok: true, review })
    }

    if (method === 'GET' && route === '/features') {
      return json({ ok: true, features: (await ops.featuresRaw(agent)).features || [] })
    }

    if (method === 'GET' && route === '/download') {
      const hit = await ops.resolveDownload(
        { job_id: param(url, 'id'), key: param(url, 'key') },
        agent,
      )
      const data = readFileSync(hit.path)
      return new Response(data, {
        headers: {
          'content-type': MIME[extname(hit.filename)] || 'application/octet-stream',
          'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(hit.filename)}`,
          'content-length': String(data.length),
          'cache-control': 'no-store',
        },
      })
    }

    // 上传的 body 是文件字节，不能当 JSON 解析：先分流再读体
    if (method === 'POST' && route === '/upload') {
      return json(await upload(request, settings, url, agent))
    }

    if (method === 'POST') {
      const payload = await body(request)
      const who = resolveAgent(ctx, payload.sid || sid)

      if (route === '/inspect') {
        return json({ ok: true, inspect: await ops.inspectRaw(payload, who) })
      }
      if (route === '/start') {
        const value = await ops.startRaw(payload, who)
        await cleanupIncoming(payload.file, settings, who)
        return json({ ok: true, job_id: value.job_id, input: value.input })
      }
      if (route === '/decide') {
        const { value, jobId } = await ops.submitRaw(payload, who)
        return json({ ok: true, receipt: value, job_id: jobId })
      }
      if (route === '/resume') {
        return json({ ok: true, ...(await ops.resume(payload, who)) })
      }
    }

    return json({ ok: false, error: `未知接口 ${method} ${route}` }, 404)
  } catch (error) {
    return json({ ok: false, error: error?.message || String(error) }, 400)
  }
}

async function upload(request, settings, url, agent) {
  const raw = basename(param(url, 'name') || 'upload.xlsx')
  const data = Buffer.from(await request.arrayBuffer())
  if (!data.length) throw new Error('文件是空的')
  if (data.length > MAX_UPLOAD) throw new Error('文件超过 128MB，改用"本机路径"建任务')

  // 每份上传一个子目录，**文件名保持原样**：`Job.create` 会把源文件按原名复制进
  // 任务目录，导出产物也叫 `<原名>_业务分类.xlsx`。要是这里加时间戳前缀，
  // 用户下载到的就是 `1794478443002_序时账1_业务分类.xlsx`。
  const dir = join(await incomingDir(settings, agent), String(Date.now()))
  mkdirSync(dir, { recursive: true })
  const name = raw.replace(/[\\/:*?"<>|]/g, '_')
  const target = join(dir, name)
  writeFileSync(target, data)
  return { ok: true, path: target, name, bytes: data.length }
}

async function incomingDir(settings, agent) {
  const repo = await settings.repoFor(agent)
  return join(settings.jobsFor(repo), '.incoming')
}

/** `create` 已经把原件复制进任务目录了，临时文件（连同它自己的目录）没用了。 */
async function cleanupIncoming(file, settings, agent) {
  const path = String(file || '')
  if (!path) return
  try {
    const dir = await incomingDir(settings, agent)
    if (!path.startsWith(dir)) return
    rmSync(dirname(path), { recursive: true, force: true })
  } catch {
    // 已经被删/没用过，忽略
  }
}

function resolveAgent(ctx, sessionId) {
  if (!sessionId) return undefined
  try {
    return ctx.get('agents')?.get?.(sessionId)
  } catch {
    return undefined
  }
}

async function body(request) {
  const text = await request.text()
  if (!text) return {}
  try {
    return JSON.parse(text)
  } catch {
    throw new Error('请求体不是合法 JSON')
  }
}

function param(url, key) {
  return url.searchParams.get(key) || ''
}

function extname(file) {
  const hit = /\.[A-Za-z0-9]+$/.exec(String(file || ''))
  return hit ? hit[0].toLowerCase() : ''
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  })
}
