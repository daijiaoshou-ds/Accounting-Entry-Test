const STATUS_ICON = {
  done: '✓',
  skipped: '↷',
  awaiting: '⏸',
  running: '●',
  failed: '✕',
  pending: '○',
}

/** 工具结果的硬上限：再长就该分批（`limit` / `journal_report`）。 */
export const MAX_OUTPUT = 12_000

export function clamp(text) {
  const value = String(text ?? '')
  if (value.length <= MAX_OUTPUT) return value
  return `${value.slice(0, MAX_OUTPUT)}\n…（结果已截断，还有 ${value.length - MAX_OUTPUT} 字；`
    + '调小 limit 或用 journal_report 看汇总）'
}

const num = (value) => (typeof value === 'number' && Number.isFinite(value)
  ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  : '—')

/**
 * 阶段时间线。
 *
 * 状态来自 `state.json`（`jobflow` 每跑完一个阶段写一次），所以这条线跟
 * python 进程不在一起也能看——这正是"进度不用问 Python"的原因。
 */
export function stageLine(stages) {
  if (!Array.isArray(stages) || !stages.length) return '阶段：（还没开始）'
  const parts = stages.map((stage) => {
    const icon = STATUS_ICON[stage.status] || '○'
    let text = `${stage.title} ${icon}`
    if (typeof stage.elapsed === 'number' && stage.elapsed > 0) text += ` ${stage.elapsed}s`
    if (stage.count) text += `（${stage.count} 条）`
    if (stage.note && stage.status !== 'done') text += `「${stage.note}」`
    return text
  })
  return `阶段：${parts.join(' → ')}`
}

export function formatFeatures(payload) {
  const features = payload?.features || []
  if (!features.length) return '没有注册任何功能。'
  const lines = [`可用功能（${features.length}）`]
  for (const feature of features) {
    lines.push('')
    lines.push(`${feature.id} · ${feature.name}${feature.description ? ` —— ${feature.description}` : ''}`)
    const requires = (feature.requires || []).join('、') || '（无）'
    lines.push(`  必需字段：${requires}`)
    const stages = (feature.stages || []).map((stage) => (
      stage.actor === 'ai' ? `${stage.title}(复核)` : `${stage.title}(程序)`
    ))
    lines.push(`  阶段：${stages.join(' → ')}`)
    if ((feature.accepts_upstream || []).length) {
      lines.push(`  可接上游：${feature.accepts_upstream.join('、')}（用 journal_start 的 upstream）`)
    }
  }
  return lines.join('\n')
}

export function formatInspect(payload) {
  const fields = payload?.fields || []
  const lines = [
    `${payload.filename} · ${num(payload.rows)} 行 · ${(payload.columns || []).length} 列 · 引擎 ${payload.engine || '?'}`,
    '',
    `字段映射（功能 ${payload.feature}）`,
  ]
  for (const field of fields) {
    const mark = field.column ? '✓' : (field.required ? '✗' : '·')
    const required = field.required ? '（必需）' : ''
    lines.push(`  ${mark} ${field.label}${required} ← ${field.column || '（未识别）'}`)
  }
  if ((payload.unmapped || []).length) {
    const rest = payload.unmapped.slice(0, 8).join('、')
    const more = payload.unmapped.length > 8 ? ` 等 ${payload.unmapped.length} 列` : ''
    lines.push('', `没被认领的列（不影响，导出时原样保留）：${rest}${more}`)
  }
  lines.push('')
  if (payload.mapping_complete) {
    lines.push('必需字段都认到了，可以直接 journal_start。')
  } else {
    lines.push(`缺必需字段：${(payload.missing_labels || payload.missing || []).join('、')}。`
      + '确认过对应列后，在 journal_start 的 mapping 里指定。')
  }
  return lines.join('\n')
}

export function formatStart(payload) {
  return [
    `任务 ${payload.job_id} 已建：${payload.feature_name || payload.feature} · `
    + `${payload?.input?.filename || ''}`,
    '程序阶段在后台跑，用 journal_next 看进度（1 万行约 4 分钟，6 万行更久）。',
  ].join('\n')
}

export function formatInbox(payload) {
  const jobs = payload?.jobs || []
  if (!jobs.length) return '没有在等复核或失败的任务。'
  const lines = [`在等复核 / 失败的任务（${jobs.length}）`]
  for (const job of jobs) {
    const where = job.awaiting
      ? `停在 ${job.awaiting}${job.awaiting_count ? `（${job.awaiting_count} 条）` : ''}`
      : ''
    lines.push(`- ${job.job_id} · ${job.feature_name || job.feature} · ${job.filename}`
      + ` · ${job.status}${where ? ` · ${where}` : ''} · ${job.created_at || ''}`)
  }
  return lines.join('\n')
}

export function formatNext({ next, state, stages, request, requestPath, running }) {
  const lines = []
  const name = state?.feature_name || state?.feature || ''
  lines.push(`任务 ${next.job_id}${name ? ` · ${name}` : ''} · ${next.status}`)
  lines.push(stageLine(stages))

  if (running) {
    lines.push('', `程序正在跑：${running.label}（已 ${running.seconds}s）`)
    if (running.log?.length) {
      lines.push('进度（stderr 末尾）：')
      for (const line of running.log) lines.push(`  ${line}`)
    }
    lines.push('', '还在跑，等一会儿再 journal_next。')
    return lines.join('\n')
  }

  if (next.status === 'failed') {
    lines.push('', `失败了：${next.error || '（没有 error 字段）'}`)
    lines.push('修好后用 journal_start 重新建任务，或让用户跑 journal resume。')
    return lines.join('\n')
  }

  const action = next.next || {}

  if (action.running) {
    lines.push('', '状态是 running，但不是本插件在跑（可能是命令行或网页起的，也可能是进程已经没了）。')
    lines.push('等它跑完再 journal_next；确认进程已经不在了，就让用户 `journal resume`。')
    return lines.join('\n')
  }

  if (action.actor === 'ai') {
    lines.push('', `下一步：AI · ${action.title || action.stage}（${action.stage}）`
      + ` · ${action.count} 条待办${action.truncated ? '（已截断）' : ''}`)
    if (action.help) lines.push(`判据：${action.help}`)
    const values = request?.value_enum || []
    if (values.length) {
      lines.push(`合法取值（value_enum，共 ${values.length}）：${values.join('、')}`)
    }
    lines.push('交结论用 journal_submit；一条都不改就用 waive。')
    const items = action.items || []
    const full = Array.isArray(request?.items) ? request.items : items

    // 全貌：先让调用方一眼看到"这批到底是什么形状"，再给前几条细节。
    // 只看前 10 条细节、但知道总共有多少组、大头在哪类桶上——比"20 条一批"
    // 更接近人看账的方式，也够它决定要不要写脚本按模板分组。
    if (full.length > 0) {
      const vouchers = full.reduce((sum, item) => sum + (Number(item['张数']) || 1), 0)
      lines.push('', `待办全貌：${full.length} 组 / ${vouchers} 张凭证`
        + (full.length > 10 ? '（下面只展开前 10 组）' : ''))
      const byBucket = topCounts(full, (item) => item['模型桶'] || item['业务分类'])
      if (byBucket) lines.push(`  按模型桶：${byBucket}`)
      const byConf = topCounts(full, (item) => item['置信度'])
      if (byConf) lines.push(`  按置信度：${byConf}`)
      if (requestPath && full.length > 10) {
        lines.push(`  全部条目（含每组的张数/样张）：${requestPath}`)
        lines.push('  —— 不必按批走：先按摘要模板/科目组合分组，一次可以交很多条。')
      }
    }

    if (items.length) lines.push('')
    items.slice(0, 10).forEach((item, index) => {
      lines.push(...itemBlock(item, index + 1))
    })
    if (full.length > 10) {
      lines.push('', `还有 ${full.length - 10} 组没展开（不是"下一批"，是同一批的其余部分）：`
        + `${requestPath || '见任务目录的 reviews/<stage>.request.json'}。`)
    }
    return lines.join('\n')
  }

  if (action.actor === 'none' || next.status === 'done') {
    lines.push('', '已完成。用 journal_report 拿结果摘要与产物路径。')
    return lines.join('\n')
  }

  lines.push('', `下一步：${action.action || '继续跑'}（程序）`)
  lines.push('后台跑完会自动停；再调一次 journal_next 看结果。')
  return lines.join('\n')
}

/**
 * 一条待办。
 *
 * 顺序即有意的：**分录（对方科目）> 摘要 > 模型 Top3**。只给"一个科目 + 一个
 * 金额"时，两条「银行存款 / 32,000」在纸上完全一样，是对方科目让它们不同。
 *
 * 没在这里列名字的字段一律按「字段名：值」原样输出（数组逐行）。新功能往待办里
 * 加字段不用改插件——`对方科目分析` 的「候选方案」、`会计分录测试` 的「风险等级」
 * 都是这么出来的。
 */
const KNOWN_FIELDS = new Set([
  'key', '凭证号', '为什么要看', '原因', 'help', '模式',
  '模型桶', '业务分类', '置信度', '校准概率', '模型Top3',
  '科目', '金额', '摘要', '组内张数', '张数', '样张', '分录',
])

function itemBlock(item, index) {
  const lines = [`[${index}] ${item.key}`]
  const why = item['为什么要看'] || item['原因'] || item.help
  if (why) lines.push(`  为什么要看：${why}`)
  const bucket = item['模型桶'] || item['业务分类']
  const conf = item['置信度']
  const prob = item['校准概率']
  if (bucket) {
    const tail = [conf, prob].filter((v) => v !== undefined && v !== null && v !== '').join(' ')
    lines.push(`  模型桶：${bucket}${tail ? `（${tail}）` : ''}`)
  }
  if (item['模型Top3']) lines.push(`  备选：${item['模型Top3']}`)
  const subject = item['科目']
  const amount = item['金额']
  if (subject) lines.push(`  科目：${subject}${amount !== undefined ? ` · 金额 ${num(amount)}` : ''}`)
  if (item['摘要']) lines.push(`  摘要：${String(item['摘要']).slice(0, 120)}`)
  if (item['张数'] && Number(item['张数']) > 1) {
    const samples = Array.isArray(item['样张']) ? item['样张'].slice(0, 3).join('、') : ''
    lines.push(`  张数：${item['张数']} 张同判（同科目组合、摘要去数字后相同）`
      + `${samples ? `，如 ${samples}` : ''}`)
  } else if (item['组内张数'] && Number(item['组内张数']) > 1) {
    lines.push(`  组内张数：${item['组内张数']}（同模板，判一条即可）`)
  }
  const entries = item['分录']
  if (Array.isArray(entries) && entries.length) {
    lines.push('  分录：')
    for (const line of entries) lines.push(`    ${line}`)
  } else if (entries === undefined) {
    lines.push('  分录：（这批待办是旧格式，没有分录；让用户 journal resume 一次会重新生成）')
  }
  for (const [name, value] of Object.entries(item || {})) {
    if (KNOWN_FIELDS.has(name) || value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) {
      lines.push(`  ${name}：`)
      for (const row of value) lines.push(`    ${row}`)
    } else {
      lines.push(`  ${name}：${typeof value === 'number' ? num(value) : value}`)
    }
  }
  return lines
}

/** 某个字段的取值分布（前 6 个，按张数累加）：`研发 120 · 采购 90`。 */
function topCounts(items, pick) {
  const counts = new Map()
  for (const item of items) {
    const key = String(pick(item) || '').trim()
    if (!key) continue
    const size = Number(item['张数']) || 1
    counts.set(key, (counts.get(key) || 0) + size)
  }
  if (!counts.size) return ''
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([key, n]) => `${key} ${n}`)
    .join(' · ')
}

export function formatSubmit(payload, { resumed, jobId }) {  const lines = []
  if (payload.waived) {
    lines.push('这批按"不改"提交：剩下的在结果里标「需复核」。')
  } else {
    lines.push(`已交 ${payload.accepted ?? 0} 条`
      + (payload.rejected?.length ? `，驳回 ${payload.rejected.length} 条` : '，没有驳回'))
  }
  if (payload.rejected?.length) {
    for (const item of payload.rejected.slice(0, 20)) {
      lines.push(`  ✗ ${item.key}：${item.reason}`)
    }
    if (payload.rejected.length > 20) lines.push(`  …另有 ${payload.rejected.length - 20} 条`)
  }
  lines.push(resumed
    ? `任务 ${jobId} 在后台继续跑，用 journal_next 看进度或下一批。`
    : `任务 ${jobId} 已收下结论（没有继续跑）。`)
  return lines.join('\n')
}

export function formatReport(payload) {
  const stages = payload?.stages || []
  const result = payload?.result || {}
  const lines = [
    `任务 ${payload.job_id} · ${payload.feature_name || payload.feature} · ${payload.status}`,
    `输入：${payload?.input?.filename || '?'}${payload?.input?.rows ? ` · ${num(payload.input.rows)} 行` : ''}`,
    stageLine(stages),
  ]
  const bucket = result.bucket_counts || {}
  const conf = result.confidence_dist || {}
  if (Object.keys(bucket).length) {
    const top = Object.entries(bucket).sort((a, b) => b[1] - a[1]).slice(0, 12)
    lines.push('', `业务桶分布（按凭证）：${top.map(([k, v]) => `${k} ${num(v)}`).join(' · ')}`)
  }
  if (Object.keys(conf).length) {
    lines.push(`置信度：${Object.entries(conf).map(([k, v]) => `${k} ${num(v)}`).join(' · ')}`)
  }
  if (typeof result.bucket_overrides === 'number' && result.bucket_overrides > 0) {
    lines.push(`复核改判：${num(result.bucket_overrides)} 条`)
  }
  // 功能自己写的结果摘要（各功能的事实字段差得远，插件不认字段名）
  if (Array.isArray(result.summary) && result.summary.length) {
    lines.push('')
    for (const line of result.summary) lines.push(`· ${line}`)
  }
  const outputs = payload?.outputs || {}
  const files = Object.values(outputs).filter(Boolean)
  if (files.length) {
    lines.push('', '产物：')
    for (const file of files) lines.push(`  ${file}`)
  } else {
    lines.push('', '产物：还没有（任务没跑到导出阶段）。')
  }
  const next = payload?.next || {}
  if (next.actor && next.actor !== 'none') {
    lines.push('', `下一步：${next.actor} · ${next.stage || ''} ${next.action || ''}`.trim())
  }
  return lines.join('\n')
}
