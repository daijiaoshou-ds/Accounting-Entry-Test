// -*- coding: utf-8 -*-
/**
 * `journal_next` 的输出形状：**给全貌、只展开前 10 组**。
 *
 * 用户提的问题：500 条待办、分 20 条一批地发，AI 只能一批批看，没法按主题（研发支出、
 * 光刻板）把 100 条一起处理。改成：全貌（多少组/多少张、桶与置信度分布）+ 前 10 组细节
 * + 全部条目的文件路径，让 AI 有机会"先分组再批量交"。
 *
 *     node _dev/tests/test_format_next.mjs
 */
import { formatNext } from '../../dsh_plugin/journal-workbench/src/format.js'

const failures = []
const check = (ok, message) => { if (!ok) failures.push(message) }

const items = Array.from({ length: 12 }, (_, i) => ({
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

const text = formatNext({
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
      help: '逐组确认桶',
      items,             // CLI 只内联前 20 条；这里给全部，模拟 request 全量
    },
  },
  state: { feature_name: '序时账清洗' },
  stages: [{ id: 'review_bucket', title: '复核业务分类', status: 'awaiting', count: 12 }],
  request: { items, value_enum: ['研发', '采购'] },
  requestPath: 'D:\\jobs\\job1\\reviews\\review_bucket.request.json',
  running: null,
})

check(text.includes('待办全貌：12 组 / 36 张凭证'), '没有给全貌（组数/张数）')
check(text.includes('按模型桶：研发 18 · 采购 18'), '没有按模型桶的分布')
check(text.includes('前 10 组') || text.includes('只展开前 10 组'), '没说明只展开前 10 组')
check((text.match(/^\[\d+\]/gm) || []).length === 10, '展开的条目数不是 10')
check(text.includes('review_bucket.request.json'), '没给全量条目的路径')
check(text.includes('不必按批走'), '没告诉它可以按主题分组、一次交很多条')
check(text.includes('还有 2 组没展开'), '没说明剩下多少组没展开')
check(text.includes('张数：3 张同判'), '条目里没写"一条覆盖几张"')

console.log(failures.length
  ? `\n${failures.map((item) => `FAIL ${item}`).join('\n')}`
  : '✅ journal_next 给全貌、展开前 10 组、给全量路径')
process.exit(failures.length ? 1 : 0)
