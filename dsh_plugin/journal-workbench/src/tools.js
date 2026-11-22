/**
 * 6 个工具 —— agent 侧的 `journal` 命令。
 *
 * 每个工具只做两件事：校验参数、调 `ops` 排版。真正的动作在 `lib/ops.js`，
 * M2 的页面路由会复用同一批函数（一个操作只实现一次）。
 *
 * 失败一律**当成文本返回**（"失败：…"）而不是抛异常：抛出去的消息在界面里
 * 只剩一行通用错误，而模型需要看到"表里没有这一列""取值不在 value_enum 里"
 * 这种能直接改的具体原因。
 */
export function createTools(ops) {
  return [
    textTool({
      name: 'journal_features',
      description: '列出可用的账务功能（序时账清洗、现金流量表编写…）与每个功能的阶段链、必需字段。'
        + '阶段里标「复核」的那一步会停下来等结论。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    }, (args, agent) => ops.features(agent)),

    textTool({
      name: 'journal_inspect',
      description: '读一份序时账，报列名、建议的字段映射、必需字段缺哪些。建任务前先看这个，'
        + '确认后用 journal_start 的 mapping 传回。不加载模型。',
      parameters: {
        type: 'object',
        properties: {
          file: { type: 'string', description: '序时账文件路径（绝对路径，或相对项目根）' },
          feature: { type: 'string', description: '功能 id，决定必需字段；默认 cleaning' },
        },
        required: ['file'],
        additionalProperties: false,
      },
    }, (args, agent) => ops.inspect(args, agent)),

    textTool({
      name: 'journal_start',
      description: '建任务并开始跑，立刻返回 job_id。程序阶段在后台跑（1 万行约 4 分钟），'
        + '跑到第一个复核位停下；进度用 journal_next 看。',
      parameters: {
        type: 'object',
        properties: {
          feature: { type: 'string', description: '功能 id（journal_features 列出的）' },
          file: { type: 'string', description: '序时账文件路径；给了 upstream 时可以不给' },
          upstream: {
            type: 'string',
            description: '接着哪个已完成任务的 job_id 跑，跳过读表与分类；给了就不必再给 file',
          },
          mapping: {
            type: 'object',
            description: '字段映射，canonical 名 → 列名，例如 {"voucher_id":"凭证字号"}；来自 journal_inspect',
          },
          max_review: { type: 'integer', description: '每个复核阶段最多给多少条待办，默认 100' },
          task: {
            type: 'string',
            description: '归属哪个任务（页面上的任务 id，task_…）。页面建任务时会给；'
              + '不给自己单独跑，运行记录就不进任何任务',
          },
        },
        required: ['feature'],
        additionalProperties: false,
      },
    }, (args, agent) => ops.start(args, agent)),

    textTool({
      name: 'journal_next',
      description: '任务现在该谁做什么：给 job_id 时返回阶段链、下一步与待办条目'
        + '（每条带整张凭证的分录）；不给时列出在等复核或失败的任务。'
        + '任务在跑着的时候，这一次调用会等最多 40 秒，等它落到下一个复核位。',
      parameters: {
        type: 'object',
        properties: {
          job_id: { type: 'string', description: 'job_id（页面上的 task_… 也能用，会自动找它最近一次运行）' },
          limit: { type: 'integer', description: '这次内联多少条待办，默认 20，最多 60' },
          offset: { type: 'integer', description: '从第几条开始内联（翻页看后面的待办，只读）' },
        },
        additionalProperties: false,
      },
    }, (args, agent) => ops.next(args, agent)),

    textTool({
      name: 'journal_todos',
      description: '只读的待办分页：offset/limit 翻页看完全部待办，不改变任务状态，'
        + '也不需要先提交前一批。等复核的任务用它把 100 多条一次看完再决定交哪些。',
      parameters: {
        type: 'object',
        properties: {
          job_id: { type: 'string', description: 'job_id（task_… 也行）' },
          stage: { type: 'string', description: '阶段 id；省略＝当前在等复核的那一段' },
          offset: { type: 'integer', description: '从第几条开始，默认 0' },
          limit: { type: 'integer', description: '这次给多少条，默认 20，最多 200' },
          q: { type: 'string', description: '关键词过滤（摘要在内），可选' },
        },
        required: ['job_id'],
        additionalProperties: false,
      },
    }, (args, agent) => ops.todos(args, agent)),

    textTool({
      name: 'journal_lessons',
      description: '把这次复核学到的"怎么判"追加进经验文件（每个功能一份，跨任务共享）。'
        + '复核会话没有文件工具时，用它代替手写。只写判据，不要写某张凭证判成了什么。',
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: '一条经验，一句话说清判据或容易踩的坑' },
          job_id: { type: 'string', description: '从哪个任务推功能（与 feature 二选一）' },
          feature: { type: 'string', description: '功能 id，如 cleaning' },
        },
        required: ['text'],
        additionalProperties: false,
      },
    }, async (args, agent) => {
      const result = await ops.lessons(args, agent)
      return `已记进 ${result.file}\n（经验跨任务共享；下次复核会先读它。）`
    }),

    textTool({
      name: 'journal_submit',
      description: '交一批复核结论，然后让任务在后台继续。返回采纳与驳回明细；'
        + '被驳回的条目不会自动重试，按 reason 改后重交。',
      parameters: {
        type: 'object',
        properties: {
          job_id: { type: 'string' },
          stage: { type: 'string', description: '阶段 id；省略＝当前等待复核的阶段' },
          decisions: {
            type: 'array',
            description: '只写要改的条目；不写的条目保持模型原判',
            items: {
              type: 'object',
              properties: {
                key: { type: 'string', description: '待办里的 key（凭证号）' },
                value: { type: 'string', description: 'journal_next 报出的合法取值之一' },
                note: { type: 'string', description: '判断依据，可选' },
              },
              required: ['key', 'value'],
              additionalProperties: false,
            },
          },
          waive: { type: 'boolean', description: 'true＝这批不改，剩下的保持原判' },
        },
        required: ['job_id'],
        additionalProperties: false,
      },
    }, (args, agent) => ops.submit(args, agent)),

    textTool({
      name: 'journal_report',
      description: '任务结果摘要：规模、业务桶分布、置信度、复核改判数、产物路径。任务跑完后用它汇报。',
      parameters: {
        type: 'object',
        properties: { job_id: { type: 'string' } },
        required: ['job_id'],
        additionalProperties: false,
      },
    }, (args, agent) => ops.report(args, agent)),
  ]
}

function textTool(definition, run) {
  return {
    name: definition.name,
    description: definition.description,
    parameters: definition.parameters,
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: String(value ?? '') }],
    },
    execute: async (args, exec) => {
      try {
        return await run(args || {}, exec?.agent)
      } catch (error) {
        return `失败：${error?.message || String(error)}`
      }
    },
  }
}
