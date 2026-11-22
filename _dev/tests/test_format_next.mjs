// -*- coding: utf-8 -*-
/**
 * `journal_next` 的输出形状：**给全貌、按 limit 渲染、用 journal_todos 翻页**。
 *
 * 这条契约变过一次：早先只展开前 10 组（`slice(0,10)`），`limit=60` 也只给 10 条——
 * AI 想看第 11 条就必须先提交前 10 条，"读"被"判"绑架。现在：
 *   - 收到几条就渲染几条（limit 由 CLI/工具决定）；
 *   - 没展开的部分给 `journal_todos(offset=…)` 的翻页提示，而不是"先交再看"。
 *
 *     node _dev/tests/test_format_next.mjs
 */
import { formatNext, formatTodos } from '../../dsh_plugin/journal-workbench/src/format.js'

const failures = []
const check = (ok, message) => { if (!ok) failures.push(message) }

const makeItems = (n) => Array.from({ length: n }, (_, i) => ({
  key: `记-${i + 1}号`,
  张数: 3,
  样张: [`记-${i + 1}号`, `记-${i + 100}号`],
  为什么要看: '置信度中（0.42）；同组 3 张同判',
  摘要: `研发支出-测试样品${i}`,
  分录: ['研发支出 借 1,000.00', '库存商品 贷 1,000.00'],
  模型桶: i < 6 ? '研发' : '采购',
  置信度: '中',
  校准概率: 0.42,
}))

const all = makeItems(12)
const shown = all.slice(0, 10)     // 本次内联 10 条（limit=10），其余 2 条要翻页

const text2 = formatNext({
  next: {
    job_id: 'job1',
    status: 'awaiting_review',
    feature: 'cleaning',
    stage: 'review_bucket',
    next: {
      actor: 'ai',
      stage: 'review_bucket',
      title: '复核业务分类',
      count: 12,
      item_offset: 0,
      items_returned: 10,
      help: '逐组确认桶',
      items: shown,
    },
  },
  state: { feature_name: '序时账清洗' },
  stages: [{ id: 'review_bucket', title: '复核业务分类', status: 'awaiting', count: 12 }],
  request: { items: all, value_enum: ['研发', '采购'] },
  requestPath: 'D:\\jobs\\job1\\reviews\\review_bucket.request.json',
  running: null,
})

check(text2.includes('待办全貌：12 组 / 36 张凭证'), '没有给全貌（组数/张数）')
check(text2.includes('按模型桶：研发 18 · 采购 18'), '没有按模型桶的分布')
check((text2.match(/^\[\d+\]/gm) || []).length === 10, 'limit=10 时展开的条目数不对')
check(text2.includes('本次第 1–10 组，共 12 组'), '没说明这次展开的是哪一段')
check(text2.includes('还有 2 组没展开'), '没说明剩下多少组')
check(text2.includes('journal_todos(job_id, stage, offset=10'), '没给翻页工具与 offset')
check(text2.includes('读不用先判'), '没讲清"读不用先判"')
check(text2.includes('张数：3 张同判'), '条目里没写"一条覆盖几张"')
check(text2.includes('review_bucket.request.json'), '没给全量原文路径')

// limit=60 时必须真的渲染 60 条（以前写死 10 条）
const sixty = makeItems(60)
const page = formatNext({
  next: {
    job_id: 'job1', status: 'awaiting_review', feature: 'cleaning', stage: 'review_bucket',
    next: { actor: 'ai', stage: 'review_bucket', title: '复核业务分类', count: 60,
      item_offset: 0, items_returned: 60, items: sixty },
  },
  state: { feature_name: '序时账清洗' },
  stages: [],
  request: { items: sixty, value_enum: ['研发', '采购'] },
  requestPath: '',
})
check((page.match(/^\[\d+\]/gm) || []).length === 60, 'limit=60 时没有真的渲染 60 条')

// 只读分页工具：offset 从 21 开始、页码与条目编号要跟着走
const todos = formatTodos({
  job: 'job1', stage: 'review_bucket', title: '复核业务分类',
  help: '只问方向冲突',
  items: all.slice(2, 5), total: 12, offset: 2, limit: 3,
  value_enum: ['研发', '采购'], submitted: {},
})
check(todos.includes('本次第 3–5 组'), 'journal_todos 没说清本次区间')
check(todos.includes('[3] 记-3号'), 'journal_todos 编号没有跟着 offset 走')
check(todos.includes('还有 7 组'), 'journal_todos 没给剩余条数')
check(todos.includes('只读视图'), 'journal_todos 没声明只读')

console.log(failures.length
  ? `\n${failures.map((item) => `FAIL ${item}`).join('\n')}`
  : '✅ journal_next 按 limit 渲染；journal_todos 只读翻页')
process.exit(failures.length ? 1 : 0)
