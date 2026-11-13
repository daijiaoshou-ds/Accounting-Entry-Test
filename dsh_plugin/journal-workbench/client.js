/* 会计分录工作台 —— 浏览器半边（`conversation.view` 里的一个页签）。
 *
 * 它只做「人的界面」这一件事：选功能、传账、确认字段映射、看阶段时间线、
 * 在复核表里改几条、下载产物。程序阶段在 Host 后台跑，AI 复核由同一个会话的
 * agent 在对话里做——两边写的是同一份 `decisions/<stage>.json`。
 *
 * 没有编译步骤：手写 `React.createElement`（`h`），因为这份代码装在一个
 * 不可构建的目录里，改一次就要能立刻被 loader 读到。
 */
window.__ModuleLoader__.load({
  id: 'dsh-journal-workbench',
  factory(require) {
    const React = require('react')
    const { useCallback, useEffect, useMemo, useRef, useState } = React
    const h = React.createElement

    const NS = 'dsh-journal-workbench'
    const API = '/plugin/journal'

    /** 哪些功能要模型（torch + 四件交付物）；其余只要有基础库就能跑。 */
    const NEEDS_MODEL = new Set(['cleaning', 'cashflow'])

    /**
     * 功能之间的依赖：谁要等谁跑完。卡片置灰、建任务时不给选，都看这张表。
     *
     * 现金流要清洗判的桶；分录测试要清洗的科目归一化 + 对方科目的配对。
     * 对方科目和清洗自己不带前置。
     */
    const FEATURE_DEPS = {
      contra: [],
      cleaning: [],
      cashflow: ['cleaning'],
      entry_test: ['cleaning', 'contra'],
    }

    // ── 文案 ──────────────────────────────────────────────────────────────
    // 注册进 Client 的 locale 服务；其中 zh 同时充当兜底（键缺了也别渲染成空）。
    const DICT = {
      zh: {
        tab: '会计分录',
        title: '会计分录工作台',
        newJob: '＋ 新建任务',
        newTaskTitle: '跑一个功能',
        newTaskHint: '选一个功能，用当前任务的账跑一次；复核、结果、产物都在那个功能里看。',
        newTaskPick: '要跑的功能',
        newTaskFlow: '会依次经过',
        noRuns: '这个功能在这份账上还没跑过。',
        startRun: '▶ 开始处理',
        collapse: '收起任务栏',
        fieldsAuto: '字段读表时自动识别',
        refresh: '刷新',
        job: '任务',
        none: '（还没有任务）',
        feature: '功能',
        file: '序时账',
        chooseFile: '点击选择文件，或把账拖到这里',
        orPath: '或者用本机路径（免上传）',
        localPath: '例如 case/序时账1.xlsx',
        inspect: '读表并确认字段',
        inspecting: '读表中…',
        mapping: '字段映射',
        mappingHint: '必需字段认不全就没法开始',
        required: '必需',
        start: '开始',
        starting: '建任务中…',
        upstream: '接着已完成的清洗任务跑',
        upstreamNone: '（不接上游，重新读表分类）',
        stages: '阶段',
        running: '正在跑',
        awaiting: '等你复核',
        done: '已完成',
        failed: '失败',
        pending: '未开始',
        skipped: '跳过',
        todo: '条待办',
        changed: '已改',
        reviewTag: '复核',
        submittedN: '已交 {n} 条',
        submit: '提交改判',
        waive: '这批不改（弃权）',
        copyPrompt: '复制给 AI',
        copied: '已复制',
        wake: '叫 AI 复核',
        waked: '已通知对话里的 AI，它会接手这批待办',
        wakeFailed: '没有可叫醒的会话（任务不是在这个会话里发起的），用「复制给 AI」',
        resume: '重试（resume）',
        download: '下载',
        result: '结果',
        noFiles: '还没有产物',
        bucket: '业务桶分布',
        confidence: '置信度',
        overrides: '复核改判',
        rows: '行',
        vouchers: '凭证',
        empty: '选一个任务，或新建一个。',
        connecting: '正在连接工作台（应用刚启动时要等几秒）…',
        envTab: '环境配置',
        envTitle: '环境配置',
        envHint: '插件本身不带模型和 Python 库。第一次用先在这里装好，再去用上面的功能。',
        envRecheck: '重新检测',
        envBase: '安装基础依赖',
        envNn: '安装模型依赖',
        envModel: '下载模型',
        envMode: '安装模式',
        envCpu: 'CPU（体积小，够用）',
        envGpu: 'GPU（要显卡，包大得多）',
        envMirror: '安装源',
        envReady: '可以用了',
        envNotReady: '还不能用',
        envPackages: '依赖包',
        envModelFiles: '模型交付物',
        envLog: '安装日志',
        envRunning: '正在装…',
        envNone: '还没开始。',
        envNeedSetup: '环境还没配好，先去「环境配置」把缺的装上。',
        envGoSetup: '去配环境',
        envModelNote: '模型来自 ModelScope：daijiaoshou/hajishou-V2.0',
        envBlockers: '还缺：',
        envSteps: '配置步骤',
        envPython: '安装 Python',
        envVenv: '创建虚拟环境',
        envAlready: '已就绪',
        envConfirmTip: '即将开始：',
        envStart: '开始',
        envCancel: '取消',
        envStop: '中止',
        envCancelled: '已中止',
        envDo_python: '下载并安装 Python 3.13.5（约 28MB）',
        envDo_venv: '在项目里创建 venv 虚拟环境',
        envDo_base: '安装基础依赖（requirements.txt，约 2 分钟）',
        envDo_nn: '安装模型版依赖（torch + transformers 等，按上面的模式与源）',
        envDo_model: '从 ModelScope 下载模型 daijiaoshou/hajishou-V2.0',
        back: '返回',
        sectionConfig: '配置',
        sectionFeature: '功能',
        resizeHint: '拖动调整宽度，双击复位',
        open: '打开',
        fix: '去配置',
        connectingShort: '检测中',
        cardEnv: '环境还没配好',
        cardNoTask: '还没建任务',
        cardNoTaskHint: '先去「新建任务」建一个，它的账就是这几个功能要用的',
        cardBlocked: '依赖未完成',
        cardDeps: '需要先完成：',
        cardReady: '可以开始',
        taskTab: '任务',
        taskNew: '新建任务',
        taskNewHint: '先起个名字，再把这份账传上来；之后四个功能都在这份账上跑',
        taskList: '已有任务',
        taskCurrent: '当前任务',
        taskNone: '还没有任务',
        taskNoneHint: '一个任务装一份账，四个功能共用它；要换账就再建一个任务。',
        taskCreate: '创建任务',
        creating: '创建中…',
        taskName: '任务名',
        taskNameHint: '例如：11 月账',
        taskRuns: '跑过的功能',
        taskDeleteNote: '删掉「{name}」？它的账和 {n} 次运行记录会一起移进 _storage_jobs/.trash，页面上删不回来。',
        reviewSession: 'AI 复核会话',
        needTask: '还没有任务。先建一个：它会装一份序时账，四个功能都在这份账上跑。',
        useTask: '当前任务',
        use: '用它',
        inUse: '当前',
        delete: '删除',
        save: '保存',
        combo: '科目组合',
        comboNone: '组合还没算',
        comboRun: '重算组合',
        search: '搜索待办',
        more: '条未显示',
        why: '为什么要看',
        entries: '分录',
        current: '现状',
        modelBucket: '模型桶',
        confidenceCol: '置信度',
        changeTo: '改为',
        note: '依据（可选）',
        keep: '不改（按默认走）',
        loadFailed: '读取失败',
        copiedLine: '帮我接着跑任务 {job}（{feature}），停在「{stage}」，{count} 条待复核。',
      },
      en: {
        tab: 'Journal',
        title: 'Journal workbench',
        newJob: '+ New task',
        newTaskTitle: 'Run a feature',
        newTaskHint: 'Pick one feature to run on the current task. Review, results and files live inside that feature.',
        newTaskPick: 'Feature to run',
        newTaskFlow: 'Runs through',
        noRuns: 'This feature has not run on this ledger yet.',
        startRun: '▶ Start',
        collapse: 'Collapse task list',
        fieldsAuto: 'columns auto-detected on read',
        refresh: 'Refresh',
        job: 'Task',
        none: '(no tasks yet)',
        feature: 'Feature',
        file: 'Ledger',
        chooseFile: 'Click to choose a file, or drop the ledger here',
        orPath: 'Or use a local path (no upload)',
        localPath: 'e.g. case/journal1.xlsx',
        inspect: 'Read and confirm fields',
        inspecting: 'Reading…',
        mapping: 'Field mapping',
        mappingHint: 'Required fields must all be mapped to start',
        required: 'required',
        start: 'Start',
        starting: 'Creating…',
        upstream: 'Continue from a finished cleaning task',
        upstreamNone: '(do not reuse upstream)',
        stages: 'Stages',
        running: 'Running',
        awaiting: 'Awaiting review',
        done: 'Done',
        failed: 'Failed',
        pending: 'Pending',
        skipped: 'Skipped',
        todo: 'to review',
        changed: 'changed',
        reviewTag: 'Review',
        submittedN: 'Submitted {n}',
        submit: 'Submit changes',
        waive: 'No changes (waive)',
        copyPrompt: 'Copy for AI',
        copied: 'Copied',
        wake: 'Wake the AI',
        waked: 'Notified the AI in this session; it will take over these items',
        wakeFailed: 'No session to wake (this task was not started here); use "Copy for AI"',
        resume: 'Retry (resume)',
        download: 'Download',
        result: 'Result',
        noFiles: 'No artifacts yet',
        bucket: 'Bucket distribution',
        confidence: 'Confidence',
        overrides: 'Overrides',
        rows: 'rows',
        vouchers: 'vouchers',
        empty: 'Pick a task, or create one.',
        connecting: 'Connecting to the workbench (a few seconds right after the app starts)…',
        envTab: 'Setup',
        envTitle: 'Environment',
        envHint: 'The plugin ships without models or Python packages. Install them here once, then use the features above.',
        envRecheck: 'Re-check',
        envBase: 'Install base packages',
        envNn: 'Install model packages',
        envModel: 'Download model',
        envMode: 'Mode',
        envCpu: 'CPU (small, enough)',
        envGpu: 'GPU (needs a graphics card, much larger)',
        envMirror: 'Index',
        envReady: 'Ready',
        envNotReady: 'Not ready',
        envPackages: 'Packages',
        envModelFiles: 'Model files',
        envLog: 'Install log',
        envRunning: 'Installing…',
        envNone: 'Not started.',
        envNeedSetup: 'Environment is not ready. Install what is missing under "Setup" first.',
        envGoSetup: 'Open Setup',
        envModelNote: 'Model: daijiaoshou/hajishou-V2.0 on ModelScope',
        envBlockers: 'Missing: ',
        envSteps: 'Setup steps',
        envPython: 'Install Python',
        envVenv: 'Create venv',
        envAlready: 'ready',
        envConfirmTip: 'About to run: ',
        envStart: 'Start',
        envCancel: 'Cancel',
        envStop: 'Abort',
        envCancelled: 'Aborted',
        envDo_python: 'download and install Python 3.13.5 (~28MB)',
        envDo_venv: 'create a venv inside the project',
        envDo_base: 'install base requirements (~2 minutes)',
        envDo_nn: 'install model packages (torch + transformers, per mode and index above)',
        envDo_model: 'download daijiaoshou/hajishou-V2.0 from ModelScope',
        back: 'Back',
        sectionConfig: 'Setup',
        sectionFeature: 'Features',
        resizeHint: 'Drag to resize, double-click to reset',
        open: 'Open',
        fix: 'Fix',
        connectingShort: 'checking',
        cardEnv: 'Environment not ready',
        cardNoTask: 'No task yet',
        cardNoTaskHint: 'Create a task first — its ledger is what every feature reads',
        cardBlocked: 'Dependencies pending',
        cardDeps: 'Finish first: ',
        cardReady: 'Ready',
        taskTab: 'Tasks',
        taskNew: 'New task',
        taskNewHint: 'Name it, then upload the ledger; all four features run on that one ledger',
        taskList: 'Tasks',
        taskCurrent: 'Current task',
        taskNone: 'No task yet',
        taskNoneHint: 'One task holds one ledger and all four features share it. Another ledger means another task.',
        taskCreate: 'Create task',
        creating: 'Creating…',
        taskName: 'Task name',
        taskNameHint: 'e.g. November ledger',
        taskRuns: 'Runs',
        taskDeleteNote: 'Delete “{name}”? Its ledger and {n} run(s) move to _storage_jobs/.trash — not undoable from the page.',
        reviewSession: 'AI review session',
        needTask: 'No task yet. Create one: it holds a ledger, and every feature runs on it.',
        useTask: 'Current task',
        use: 'Use',
        inUse: 'active',
        delete: 'Delete',
        save: 'Save',
        combo: 'Subject combos',
        comboNone: 'combos not computed',
        comboRun: 'Recompute combos',
        search: 'Search items',
        more: 'not shown',
        why: 'Why review',
        entries: 'Entries',
        current: 'Current',
        modelBucket: 'Model bucket',
        confidenceCol: 'Confidence',
        changeTo: 'Change to',
        note: 'Reason (optional)',
        keep: 'keep default',
        loadFailed: 'Load failed',
        copiedLine: 'Continue task {job} ({feature}), stopped at "{stage}" with {count} items to review.',
      },
    }

    let translate = (key) => DICT.zh[key] ?? key

    function fill(template, vars) {
      return String(template).replace(/\{(\w+)\}/g, (_, k) => (k in vars ? String(vars[k]) : ''))
    }

    // ── HTTP ──────────────────────────────────────────────────────────────

    function qs(params) {
      const parts = []
      for (const [key, value] of Object.entries(params || {})) {
        if (value === undefined || value === null || value === '') continue
        parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
      }
      return parts.length ? `?${parts.join('&')}` : ''
    }

    function makeApi(getSessionId) {
      async function read(response) {
        let data = null
        try {
          data = await response.json()
        } catch {
          throw new Error(`服务返回的不是 JSON（HTTP ${response.status}）`)
        }
        if (!response.ok || data?.ok === false) {
          throw new Error(data?.error || `HTTP ${response.status}`)
        }
        return data
      }

      return {
        get(path, params) {
          return fetch(`${API}${path}${qs({ ...params, sid: getSessionId() })}`, {
            headers: { accept: 'application/json' },
          }).then(read)
        },
        post(path, payload) {
          return fetch(`${API}${path}`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ ...payload, sid: getSessionId() }),
          }).then(read)
        },
        upload(file) {
          return fetch(`${API}/upload${qs({ name: file.name, sid: getSessionId() })}`, {
            method: 'POST',
            headers: { 'content-type': 'application/octet-stream' },
            body: file,
          }).then(read)
        },
        downloadUrl(jobId, key) {
          return `${API}/download${qs({ id: jobId, key, sid: getSessionId() })}`
        },
      }
    }

    // ── 小工具 ────────────────────────────────────────────────────────────

    const STATUS_ICON = {
      done: '✓', skipped: '↷', awaiting: '⏸', running: '●', failed: '✕', pending: '○',
    }

    function statusWord(t, status) {
      if (status === 'done') return t('done')
      if (status === 'awaiting_review') return t('awaiting')
      if (status === 'running' || status === 'created') return t('running')
      if (status === 'failed') return t('failed')
      return t('pending')
    }

    function fmtNumber(value) {
      if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
      return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
    }

    function fmtBytes(bytes) {
      if (!bytes) return ''
      if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`
      return `${Math.round(bytes / 1024)}KB`
    }

    /**
     * 右侧栏宽度：默认 300，夹在 [260, 460]，记在 localStorage 里——拖过的人
     * 下次进这个页面还是那么宽。
     */
    const SIDE_MIN = 260
    const SIDE_MAX = 460
    const SIDE_DEFAULT = 300
    const SIDE_KEY = 'jw.sideWidth'

    const clampSide = (value) => {
      const n = Number(value)
      if (!Number.isFinite(n)) return SIDE_DEFAULT
      return Math.min(SIDE_MAX, Math.max(SIDE_MIN, Math.round(n)))
    }

    function readSideWidth() {
      try {
        const saved = window.localStorage?.getItem(SIDE_KEY)
        return saved ? clampSide(saved) : SIDE_DEFAULT
      } catch {
        return SIDE_DEFAULT      // 隐私模式/localStorage 被禁：用默认值，别炸
      }
    }

    // ── 样式（只引主题 token；`color-mix` 用来做 hover / 淡色底）──────────

    const CSS = `
.jw-root{display:flex;flex-direction:column;height:100%;min-height:0;background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font-size:13px;line-height:1.55}
.jw-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--dsw-alias-border-l1);flex:none}
.jw-title{margin:0;font-size:15px;font-weight:600}
.jw-spacer{flex:1;min-width:8px}
.jw-muted{color:var(--dsw-alias-label-secondary)}
.jw-small{font-size:12px}
.jw-mono{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:11.5px}
.jw-card{background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l1);border-radius:10px;padding:10px 12px}
.jw-card>header{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.jw-card h3{margin:0;font-size:13px;font-weight:600}
.jw-btn{font:inherit;font-size:12px;line-height:1.6;padding:3px 10px;border-radius:6px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);cursor:pointer}
.jw-btn:hover:enabled{border-color:var(--dsw-alias-label-secondary)}
.jw-btn:disabled{opacity:.45;cursor:default}
.jw-btn-primary{background:var(--dsw-alias-brand-primary);border-color:transparent;color:#fff;font-weight:600}
.jw-btn-primary:hover:enabled{filter:brightness(1.08)}
.jw-select,.jw-input{font:inherit;font-size:12px;padding:3px 7px;border-radius:6px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);max-width:100%}
.jw-stages{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.jw-stage{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);border-radius:999px;padding:2px 9px;font-size:12px;white-space:nowrap}
.jw-stage.done{border-color:color-mix(in srgb,var(--dsw-alias-state-success-primary) 45%,transparent)}
.jw-stage.awaiting{border-color:var(--dsw-alias-state-warn-primary);color:var(--dsw-alias-state-warn-primary)}
.jw-stage.failed{border-color:var(--dsw-alias-state-error-primary);color:var(--dsw-alias-state-error-primary)}
.jw-stage.skipped{opacity:.6}
.jw-dot{font-size:10px}
.jw-banner{border-radius:8px;padding:7px 10px;font-size:12px;border:1px solid}
.jw-banner.err{color:var(--dsw-alias-state-error-primary);border-color:color-mix(in srgb,var(--dsw-alias-state-error-primary) 45%,transparent);background:color-mix(in srgb,var(--dsw-alias-state-error-primary) 8%,transparent)}
.jw-banner.ok{color:var(--dsw-alias-state-success-primary);border-color:color-mix(in srgb,var(--dsw-alias-state-success-primary) 40%,transparent);background:color-mix(in srgb,var(--dsw-alias-state-success-primary) 8%,transparent)}
.jw-drop{display:block;border:1px dashed var(--dsw-alias-border-l2);border-radius:10px;padding:18px;text-align:center;cursor:pointer;background:var(--dsw-alias-bg-layer-2)}
.jw-drop:hover{border-color:var(--dsw-alias-brand-primary)}
.jw-map{display:grid;grid-template-columns:minmax(96px,140px) minmax(0,1fr) auto;gap:6px 10px;align-items:center}
.jw-map .jw-req{color:var(--dsw-alias-state-error-primary);margin-left:2px}
.jw-tablewrap{max-height:58vh;overflow:auto;border:1px solid var(--dsw-alias-border-l1);border-radius:8px}
.jw-table{width:100%;border-collapse:collapse;font-size:12px;min-width:820px}
.jw-table th,.jw-table td{border-bottom:1px solid var(--dsw-alias-border-l1);padding:6px 8px;text-align:left;vertical-align:top}
.jw-table th:nth-child(3),.jw-table td:nth-child(3){min-width:190px}
.jw-table td:first-child .jw-mono{white-space:nowrap}
.jw-table th{position:sticky;top:0;z-index:1;background:var(--dsw-alias-bg-layer-2);font-weight:600;white-space:nowrap}
.jw-table tr:last-child td{border-bottom:0}
.jw-table td.jw-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.jw-entries{margin:0;padding-left:14px;color:var(--dsw-alias-label-secondary);white-space:pre-wrap}
.jw-entries li{list-style:disc}
.jw-tag{display:inline-block;border:1px solid var(--dsw-alias-border-l2);border-radius:4px;padding:0 5px;font-size:11px;color:var(--dsw-alias-label-secondary)}
.jw-tag.on{color:var(--dsw-alias-state-success-primary);border-color:color-mix(in srgb,var(--dsw-alias-state-success-primary) 45%,transparent)}
.jw-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.jw-bars{display:flex;flex-direction:column;gap:4px}
.jw-bar{display:grid;grid-template-columns:minmax(72px,120px) minmax(0,1fr) 56px;gap:8px;align-items:center;font-size:12px}
.jw-bar-track{height:8px;border-radius:4px;background:var(--dsw-alias-bg-layer-2);overflow:hidden}
.jw-bar-fill{display:block;height:100%;background:var(--dsw-alias-brand-primary);opacity:.75}
.jw-stat{display:inline-flex;flex-direction:column;gap:1px;padding:6px 12px;border-radius:8px;background:var(--dsw-alias-bg-layer-2);min-width:96px}
.jw-stat b{font-size:15px;font-variant-numeric:tabular-nums}
.jw-log{margin:0;padding:8px 10px;border-radius:8px;background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:11.5px;max-height:180px;overflow:auto;white-space:pre-wrap}
.jw-code{display:block;padding:7px 10px;border-radius:6px;background:var(--dsw-alias-bg-layer-2);font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:11.5px;white-space:pre-wrap;word-break:break-all;flex:1;min-width:0}
.jw-tabs{display:flex;gap:2px;padding:0 16px;border-bottom:1px solid var(--dsw-alias-border-l1);flex:none;overflow-x:auto}
.jw-tab{font:inherit;font-size:12.5px;padding:7px 14px;border:0;border-bottom:2px solid transparent;background:transparent;color:var(--dsw-alias-label-secondary);cursor:pointer;white-space:nowrap}
.jw-tab:hover{color:var(--dsw-alias-label-primary)}
.jw-tab.on{color:var(--dsw-alias-label-primary);border-bottom-color:var(--dsw-alias-brand-primary);font-weight:600}
.jw-ok{color:var(--dsw-alias-state-success-primary)}
.jw-bad{color:var(--dsw-alias-state-error-primary)}
.jw-env-row{display:grid;grid-template-columns:minmax(120px,180px) minmax(0,1fr) auto;gap:4px 12px;align-items:baseline;padding:3px 0;border-bottom:1px solid var(--dsw-alias-border-l1)}
.jw-env-row:last-child{border-bottom:0}

/* ── 视觉：把"能看"抬到"好看"。全部走主题 token，深色模式自动跟着变 ── */
.jw-root{background:var(--dsw-alias-bg-base);font-size:13px;letter-spacing:.005em}
.jw-head{padding:14px 20px;gap:10px;border-bottom:1px solid var(--dsw-alias-border-l1)}
.jw-title{font-size:15px;font-weight:650;letter-spacing:.01em;margin:0}
.jw-tabs{padding:0 16px;gap:2px;border-bottom:1px solid var(--dsw-alias-border-l1)}
.jw-tab{padding:9px 15px;border-radius:9px 9px 0 0;font-size:12.5px;transition:background .15s,color .15s}
.jw-tab:hover{background:var(--dsw-alias-bg-layer-2)}
.jw-tab.on{background:var(--dsw-alias-bg-layer-2);border-bottom-color:var(--dsw-alias-brand-primary)}
.jw-stack{display:flex;flex-direction:column;gap:12px}
.jw-card{border-radius:14px;padding:14px 16px;background:var(--dsw-alias-bg-layer-1);
  box-shadow:0 1px 2px rgba(0,0,0,.04),0 10px 30px -22px rgba(0,0,0,.35)}
.jw-card>header{gap:10px}
.jw-card h3{font-size:13.5px;font-weight:650;margin:0}
.jw-section{font-size:11px;font-weight:600;letter-spacing:.09em;color:var(--dsw-alias-label-secondary);
  margin:0 0 10px}
.jw-btn{border-radius:8px;padding:5px 12px;transition:border-color .15s,background .15s,opacity .15s}
.jw-btn:disabled{opacity:.55;cursor:not-allowed}
.jw-btn-primary{background:var(--dsw-alias-brand-primary);color:#fff;border-color:transparent}
.jw-btn-primary:disabled{opacity:.45}
.jw-banner{border-radius:10px;padding:9px 12px;font-size:12.5px}
.jw-step{display:flex;gap:10px;padding:9px 0;border-bottom:1px dashed var(--dsw-alias-border-l1);align-items:flex-start}
.jw-step:last-child{border-bottom:0;padding-bottom:0}
.jw-step-body{min-width:0;display:flex;flex-direction:column;gap:2px}
.jw-step-title{font-weight:600;font-size:12.5px}
.jw-step-no{width:22px;height:22px;flex:none;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:11px;border:1px solid var(--dsw-alias-border-l2);color:var(--dsw-alias-label-secondary);
  background:var(--dsw-alias-bg-layer-2)}
.jw-step-no.ok{border-color:color-mix(in srgb,var(--dsw-alias-state-success-primary) 55%,transparent);
  color:var(--dsw-alias-state-success-primary);
  background:color-mix(in srgb,var(--dsw-alias-state-success-primary) 12%,transparent)}
.jw-modes{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.jw-mode{display:flex;flex-direction:column;gap:3px;text-align:left;padding:11px 13px;border-radius:12px;
  border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);
  font:inherit;cursor:pointer;transition:border-color .15s,box-shadow .15s}
.jw-mode strong{font-size:13px}
.jw-mode.on{border-color:var(--dsw-alias-brand-primary);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--dsw-alias-brand-primary) 14%,transparent)}
.jw-dot{font-size:11px;color:var(--dsw-alias-label-secondary)}
.jw-dot.on{color:var(--dsw-alias-state-success-primary)}
.jw-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.jw-btn-hint{font-size:10.5px;opacity:.6;margin-left:6px}
.jw-btn-hint.ok{color:var(--dsw-alias-state-success-primary);opacity:1}
.jw-confirm{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px;padding:9px 12px;
  border-radius:10px;background:var(--dsw-alias-bg-layer-2);border:1px dashed var(--dsw-alias-border-l2);font-size:12.5px}
.jw-tag{border-radius:999px;padding:1px 8px}
.jw-table td{border-bottom:1px solid var(--dsw-alias-border-l1)}
.jw-table tbody tr:hover{background:var(--dsw-alias-bg-layer-2)}
.jw-tablewrap{border-radius:12px}
.jw-stat{border:1px solid var(--dsw-alias-border-l1)}

.jw-chip{display:inline-block;font-size:10.5px;line-height:1.7;padding:0 8px;border-radius:999px;
  border:1px solid var(--dsw-alias-border-l2);color:var(--dsw-alias-label-secondary)}
.jw-chip.on{color:var(--dsw-alias-state-success-primary);
  border-color:color-mix(in srgb,var(--dsw-alias-state-success-primary) 45%,transparent);
  background:color-mix(in srgb,var(--dsw-alias-state-success-primary) 10%,transparent)}
.jw-btn-ghost{background:transparent;border-color:var(--dsw-alias-border-l1)}
.jw-btn-tiny{padding:2px 8px;font-size:11.5px}

/* ── 新建任务：选功能 + 任务行 ── */
.jw-picks{display:flex;flex-direction:column;gap:8px}
.jw-pick{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 10px;align-items:center;
  width:100%;text-align:left;padding:10px 13px;border-radius:12px;
  border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1);
  color:var(--dsw-alias-label-primary);font:inherit;cursor:pointer;
  transition:border-color .12s,box-shadow .12s,background .12s}
.jw-pick:hover:not(.off){border-color:var(--dsw-alias-brand-primary)}
.jw-pick.on{border-color:var(--dsw-alias-brand-primary);
  background:color-mix(in srgb,var(--dsw-alias-brand-primary) 6%,var(--dsw-alias-bg-layer-1));
  box-shadow:0 0 0 3px color-mix(in srgb,var(--dsw-alias-brand-primary) 13%,transparent)}
.jw-pick.off{opacity:.6;cursor:not-allowed}
.jw-pick-name{font-size:13px;font-weight:650}
.jw-pick-desc{grid-column:1/-1;font-size:11.5px;line-height:1.5;color:var(--dsw-alias-label-secondary)}
.jw-pick-note{grid-column:1/-1;font-size:11px;color:var(--dsw-alias-state-warn-primary)}
.jw-chip.warn{color:var(--dsw-alias-state-warn-primary);
  border-color:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 45%,transparent);
  background:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 10%,transparent)}
.jw-chip.bad{color:var(--dsw-alias-state-error-primary);
  border-color:color-mix(in srgb,var(--dsw-alias-state-error-primary) 45%,transparent);
  background:color-mix(in srgb,var(--dsw-alias-state-error-primary) 10%,transparent)}

/* ── 主壳：主区（功能 / 详情）+ 右侧栏（环境 + 任务），中间可以拖 ── */
.jw-split{flex:1;min-height:0;display:flex;align-items:stretch}
.jw-main{flex:1;min-width:0;overflow:auto;padding:16px 18px 48px;
  display:flex;flex-direction:column;gap:12px}
/* ── 功能：一个功能一个框，一行四个 ── */
.jw-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
@media (max-width:1080px){.jw-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.jw-tile{display:flex;flex-direction:column;gap:7px;align-items:flex-start;text-align:left;
  padding:12px 13px;border-radius:12px;border:1px solid var(--dsw-alias-border-l1);
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;
  cursor:pointer;transition:border-color .12s,box-shadow .12s,transform .12s}
.jw-tile:hover{border-color:var(--dsw-alias-brand-primary);transform:translateY(-1px);
  box-shadow:0 10px 24px -20px rgba(0,0,0,.55)}
.jw-tile.off{opacity:.62}
.jw-tile.on{border-color:var(--dsw-alias-brand-primary);
  background:color-mix(in srgb,var(--dsw-alias-brand-primary) 5%,var(--dsw-alias-bg-layer-1))}
.jw-tile-title{font-size:13.5px;font-weight:650;letter-spacing:.01em}
.jw-gutter{flex:none;width:11px;cursor:col-resize;position:relative;background:transparent}
.jw-gutter::before{content:'';position:absolute;left:50%;top:0;bottom:0;width:1px;
  transform:translateX(-50%);background:var(--dsw-alias-border-l1);transition:background .12s,width .12s}
.jw-gutter:hover::before,.jw-gutter.dragging::before{width:2px;background:var(--dsw-alias-brand-primary)}
.jw-side{flex:none;overflow:auto;padding:16px 14px 24px 6px;
  display:flex;flex-direction:column;gap:10px}
.jw-side-card{background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l1);
  border-radius:14px;padding:11px 12px;display:flex;flex-direction:column;gap:8px}
.jw-side-head{display:flex;align-items:center;gap:6px;min-width:0}
.jw-side-title{font-size:12.5px;font-weight:650;letter-spacing:.01em}
.jw-side-line{font-size:11.5px;line-height:1.5;color:var(--dsw-alias-label-secondary);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ── 右栏里的任务（窄，一个任务一块）── */
.jw-tasks{display:flex;flex-direction:column;gap:8px}
.jw-taskrow{display:flex;flex-direction:column;gap:5px;padding:9px 10px;border-radius:11px;
  border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-base);cursor:pointer;
  transition:border-color .12s,background .12s}
.jw-taskrow:hover{border-color:var(--dsw-alias-border-l2)}
.jw-taskrow.on{border-color:color-mix(in srgb,var(--dsw-alias-brand-primary) 55%,transparent);
  background:color-mix(in srgb,var(--dsw-alias-brand-primary) 5%,var(--dsw-alias-bg-layer-1))}
.jw-taskrow-head{display:flex;align-items:center;gap:6px;min-width:0}
.jw-taskrow-name{font-size:12.5px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.jw-taskrow-meta{font-size:11.5px;line-height:1.45;color:var(--dsw-alias-label-secondary)}
.jw-runs{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}
.jw-runchip{display:inline-flex;align-items:center;gap:5px;font:inherit;font-size:11px;
  padding:1px 7px;border-radius:999px;border:1px solid var(--dsw-alias-border-l2);
  background:transparent;color:var(--dsw-alias-label-secondary);cursor:pointer;
  transition:border-color .12s,color .12s,background .12s}
.jw-runchip:hover{border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-label-primary)}
.jw-runchip.done{color:var(--dsw-alias-state-success-primary);
  border-color:color-mix(in srgb,var(--dsw-alias-state-success-primary) 45%,transparent)}
.jw-runchip.await{color:var(--dsw-alias-state-warn-primary);
  border-color:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 50%,transparent)}
.jw-runchip.failed{color:var(--dsw-alias-state-error-primary);
  border-color:color-mix(in srgb,var(--dsw-alias-state-error-primary) 45%,transparent)}
.jw-empty{display:flex;flex-direction:column;align-items:center;gap:8px;padding:18px 8px;
  border:1px dashed var(--dsw-alias-border-l2);border-radius:12px;text-align:center;
  color:var(--dsw-alias-label-secondary);font-size:12px;line-height:1.5}
`

    // ── 组件 ──────────────────────────────────────────────────────────────

    function StyleTag() {
      return h('style', { 'data-plugin': NS }, CSS)
    }

    function Stages({ chain, t }) {
      if (!chain.length) return null
      return h('div', { className: 'jw-stages' },
        chain.map((stage) => {
          const status = stage.status || 'pending'
          const label = status === 'awaiting' ? t('awaiting') : statusWord(t, mapStatus(status))
          return h('span', {
            key: stage.id,
            className: `jw-stage ${status}`,
            title: stage.note || '',
          },
          h('span', { className: 'jw-dot' }, STATUS_ICON[status] || '○'),
          h('span', null, stage.title || stage.id),
          stage.actor === 'ai' && h('span', { className: 'jw-tag' }, t('reviewTag')),
          stage.count ? h('span', { className: 'jw-muted' }, `${stage.count} ${t('todo')}`) : null,
          stage.elapsed ? h('span', { className: 'jw-muted jw-small' }, `${stage.elapsed}s`) : null,
          status === 'failed' ? h('span', { className: 'jw-muted jw-small' }, label) : null)
        }))
    }

    function mapStatus(status) {
      if (status === 'awaiting') return 'awaiting'
      if (status === 'done') return 'done'
      if (status === 'skipped') return 'skipped'
      if (status === 'failed') return 'failed'
      if (status === 'running') return 'running'
      return 'pending'
    }

    // 待办里已经在表格上单独展示的字段；其余字段（对方科目分析的「候选方案」、
    // 分录测试的「风险等级」…）按「名字：值」原样放在「分录」下面，新功能加字段
    // 不用再改这张表。
    const REVIEW_KNOWN = new Set(['key', '凭证号', '为什么要看', '原因', 'help', '模式',
      '模型桶', '业务分类', '置信度', '校准概率', '模型Top3',
      '科目', '金额', '摘要', '组内张数', '分录'])

    const currentValue = (item) => String(item?.['模型桶'] || item?.['业务分类'] || '')

    function extraFields(item) {
      return Object.entries(item || {}).filter(([name, value]) =>
        !REVIEW_KNOWN.has(name) && value !== null && value !== undefined && value !== '')
    }

    /** 单值渲染：数字带千分位，其余原样（金额按原样打印会读错位）。 */
    function cellValue(value) {
      return typeof value === 'number' ? fmtNumber(value) : String(value)
    }

    function ReviewTable({ review, submitted, t, onSubmit, onWaive, onCopy, onWake, busy }) {
      const [edits, setEdits] = useState({})
      const [query, setQuery] = useState('')

      useEffect(() => {
        setEdits({})
      }, [review?.stage, review?.job_id])

      const items = review?.items || []
      const filtered = useMemo(() => {
        const needle = query.trim().toLowerCase()
        if (!needle) return items
        return items.filter((item) => Object.values(item || {})
          .some((value) => String(value ?? '').toLowerCase().includes(needle)))
      }, [items, query])

      const changed = useMemo(() => {
        const current = {}
        for (const item of items) current[String(item.key)] = currentValue(item)
        const out = []
        for (const [key, edit] of Object.entries(edits)) {
          if (!edit?.value) continue
          // 选回原判就别交：多一条同值 decision 只会让审计文件变长
          if (edit.value === current[key]) continue
          out.push({ key, value: edit.value, note: edit.note || '' })
        }
        return out
      }, [edits, items])

      if (!review || !review.stage) return null

      return h('div', { className: 'jw-card' },
        h('header', null,
          h('h3', null, t('awaiting')),
          h('span', { className: 'jw-muted jw-small' }, `${review.title || review.stage}`),
          h('span', { className: 'jw-tag' }, `${review.total} ${t('todo')}`),
          h('span', { className: 'jw-spacer' }),
          h('input', {
            className: 'jw-input',
            placeholder: t('search'),
            value: query,
            onChange: (event) => setQuery(event.target.value),
            style: { width: 160 },
          })),

        review.help && h('p', { className: 'jw-muted jw-small', style: { whiteSpace: 'pre-wrap', marginTop: 0 } },
          review.help),

        h('div', { className: 'jw-tablewrap' },
          h('table', { className: 'jw-table' },
            h('thead', null, h('tr', null,
              h('th', null, review.key_name || 'key'),
              h('th', null, t('why')),
              h('th', null, t('entries')),
              h('th', null, t('current')),
              h('th', null, t('changeTo')),
              h('th', null, t('note')))),
            h('tbody', null, filtered.map((item) => {
              const key = String(item.key)
              const done = submitted[key]
              const edit = edits[key] || {}
              const selected = edit.value || submitted[key] || ''
              const now = currentValue(item)
              return h('tr', { key },
                h('td', null,
                  h('div', { className: 'jw-mono' }, key),
                  done && h('span', { className: 'jw-tag on' }, `${t('changed')} ${done}`)),
                h('td', { className: 'jw-small' }, item['为什么要看'] || item['原因'] || ''),
                h('td', null,
                  Array.isArray(item['分录']) && item['分录'].length
                    ? h('ul', { className: 'jw-entries jw-small' },
                      item['分录'].map((line, index) => h('li', { key: index }, line)))
                    : h('span', { className: 'jw-muted jw-small' },
                      String(item['摘要'] || '').slice(0, 80)),
                  extraFields(item).map(([name, value]) => h('div', { key: name, className: 'jw-small' },
                    h('span', { className: 'jw-muted' }, `${name}：`),
                    Array.isArray(value)
                      ? h('ul', { className: 'jw-entries jw-small' },
                        value.map((row, index) => h('li', { key: index }, String(row))))
                      : cellValue(value)))),
                h('td', { className: 'jw-small' },
                  [now, item['置信度'],
                    item['校准概率'] != null ? String(item['校准概率']) : '']
                    .filter(Boolean).join(' ') || h('span', { className: 'jw-muted' }, '—')),
                h('td', null, h('select', {
                  className: 'jw-select',
                  value: selected,
                  onChange: (event) => {
                    const value = event.target.value
                    setEdits((current) => ({
                      ...current,
                      [key]: { value, note: current[key]?.note || '' },
                    }))
                  },
                },
                h('option', { value: '' }, t('keep')),
                (review.value_enum || []).map((value) => h('option', { key: value, value }, value)))),
                h('td', null, h('input', {
                  className: 'jw-input',
                  style: { width: 130 },
                  value: edit.note || '',
                  onChange: (event) => {
                    const note = event.target.value
                    setEdits((current) => {
                      const prev = current[key] || {}
                      if (!prev.value && !note) return current
                      return { ...current, [key]: { value: prev.value || '', note } }
                    })
                  },
                })))
            })))),

        h('div', { className: 'jw-row', style: { marginTop: 10 } },
          h('button', {
            className: 'jw-btn jw-btn-primary',
            disabled: !!busy || changed.length === 0,
            onClick: () => onSubmit(changed),
          }, `${t('submit')} (${changed.length})`),
          h('button', {
            className: 'jw-btn',
            disabled: !!busy,
            onClick: onWake,
          }, t('wake')),
          h('button', {
            className: 'jw-btn',
            disabled: !!busy,
            onClick: onWaive,
          }, t('waive')),
          h('button', {
            className: 'jw-btn jw-small',
            disabled: !!busy,
            onClick: onCopy,
          }, t('copyPrompt')),
          review.truncated && h('span', { className: 'jw-muted jw-small' },
            `${review.total - filtered.length} ${t('more')}`)))
    }

    function ResultPanel({ job, t, api }) {
      const exportStage = (job.stages || []).find((stage) => stage.id === 'export')
      const facts = exportStage?.facts || {}
      const buckets = facts.bucket_counts || {}
      const conf = facts.confidence_dist || {}
      const entries = Object.entries(buckets).sort((a, b) => b[1] - a[1])
      const max = entries.length ? entries[0][1] : 1
      const files = job.files || []

      return h('div', { className: 'jw-card' },
        h('header', null,
          h('h3', null, t('result')),
          h('span', { className: 'jw-muted jw-small' },
            `${job.feature_name} · ${job.input?.filename || ''}`)),
        h('div', { className: 'jw-row', style: { marginBottom: 10 } },
          typeof facts.vouchers === 'number'
            && h('span', { className: 'jw-stat' }, h('span', { className: 'jw-muted jw-small' }, t('vouchers')), h('b', null, fmtNumber(facts.vouchers))),
          typeof facts.rows === 'number'
            && h('span', { className: 'jw-stat' }, h('span', { className: 'jw-muted jw-small' }, t('rows')), h('b', null, fmtNumber(facts.rows))),
          typeof facts.bucket_overrides === 'number'
            && h('span', { className: 'jw-stat' }, h('span', { className: 'jw-muted jw-small' }, t('overrides')), h('b', null, fmtNumber(facts.bucket_overrides))),
          Object.keys(conf).length > 0
            && h('span', { className: 'jw-stat' },
              h('span', { className: 'jw-muted jw-small' }, t('confidence')),
              h('b', null, Object.entries(conf).map(([k, v]) => `${k} ${v}`).join(' · ')))),

        Array.isArray(facts.summary) && facts.summary.length > 0
          && h('ul', { className: 'jw-entries jw-small', style: { marginTop: 10 } },
            facts.summary.map((line, index) => h('li', { key: index }, String(line)))),

        files.length
          ? h('div', { className: 'jw-row' },
            files.map((file) => h('a', {
              key: file.key,
              className: 'jw-btn',
              href: api.downloadUrl(job.job_id, file.key),
              download: file.filename,
            }, `${t('download')} ${file.label}${file.bytes ? ` · ${fmtBytes(file.bytes)}` : ''}`)))
          : h('p', { className: 'jw-muted jw-small' }, t('noFiles')),

        entries.length > 0 && h('div', { style: { marginTop: 14 } },
          h('div', { className: 'jw-muted jw-small', style: { marginBottom: 6 } }, t('bucket')),
          h('div', { className: 'jw-bars' }, entries.slice(0, 16).map(([name, count]) => h('div', { key: name, className: 'jw-bar' },
            h('span', { title: name }, name),
            h('span', { className: 'jw-bar-track' },
              h('span', { className: 'jw-bar-fill', style: { width: `${Math.max(2, (count / max) * 100)}%` } })),
            h('span', { className: 'jw-num', style: { textAlign: 'right' } }, fmtNumber(count)))))))
    }

    /**
     * 环境配置页。
     *
     * 只做"看一眼 + 点按钮"：真正的安装在 Host 侧起子进程（pip / modelscope），
     * 页面每 1.2 秒拉一次任务日志。装完自动重新检测。
     *
     * 为什么要给 CPU / GPU 两种模式：pip 镜像里的 torch 在 Windows 上是 CUDA 版
     * （2.4GB、装完 4GB+），而只做推理的话 CPU 版只有 122MB。装错了不是"慢一点"，
     * 是白下两个 G。
     */
    /**
     * 环境配置页。
     *
     * 五步走：解释器 → 虚拟环境 → 基础依赖 → 模型依赖 → 模型文件。
     * 每一步都先看"是不是已经好了"：好了就把按钮变成灰的、写明"已就绪"，
     * 点不动——避免"点一下就把 60 个包重装一遍"。
     *
     * 真要装的时候还要再点一次「开始」：pip 一跑就是几分钟，不该因为手滑就开始。
     * 跑起来之后有「中止」，杀的是整棵进程树。
     *
     * CPU / GPU 的差别只有 torch 的轮子（122MB vs 2.4GB），所以按模式分别看
     * 就绪状态：GPU 模式还要求当前 torch 是 CUDA 版。
     */
    function EnvPanel({ api, t, env, onReload, onError }) {
      const [mode, setMode] = useState('cpu')
      const [mirror, setMirror] = useState('aliyun')
      const [task, setTask] = useState(null)
      const [confirm, setConfirm] = useState('')
      const [busy, setBusy] = useState(false)

      useEffect(() => {
        if (!task || task.status !== 'running') return undefined
        const id = window.setTimeout(async () => {
          try {
            const data = await api.get('/env/task', { id: task.id })
            setTask(data.task)
            if (data.task.status !== 'running') onReload()
          } catch {
            // 轮询失败就等下一次；任务本身在 Host 里跑，不受影响
          }
        }, 1200)
        return () => window.clearTimeout(id)
      }, [task, api, onReload])

      const run = useCallback(async (action) => {
        setBusy(true)
        setConfirm('')
        onError('')
        try {
          const data = await api.post('/env/start', { action, mode, mirror })
          setTask(data.task)
        } catch (error) {
          onError(error.message)
        } finally {
          setBusy(false)
        }
      }, [api, mode, mirror, onError])

      const stop = useCallback(async () => {
        if (!task?.id) return
        try {
          await api.post('/env/cancel', { id: task.id })
          const data = await api.get('/env/task', { id: task.id })
          setTask(data.task)
        } catch (error) {
          onError(error.message)
        }
      }, [api, task, onError])

      if (!env) {
        return h('div', { className: 'jw-card' }, h('span', { className: 'jw-muted' }, t('connecting')))
      }
      if (env.error) {
        return h('div', { className: 'jw-card' }, h('div', { className: 'jw-banner err' }, env.error))
      }

      const running = task?.status === 'running'
      const stepOf = (id) => (env.steps || []).find((step) => step.id === id) || { ok: false, detail: '' }
      const nnReady = mode === 'gpu' ? !!env.modes?.gpu?.ready : !!stepOf('nn').ok

      const actions = [
        { id: 'python', label: t('envPython'), ok: stepOf('python').ok, hint: '约 28MB' },
        { id: 'venv', label: t('envVenv'), ok: stepOf('venv').ok, hint: '' },
        { id: 'base', label: t('envBase'), ok: stepOf('base').ok, hint: '' },
        { id: 'nn', label: t('envNn'), ok: nnReady, hint: mode === 'cpu' ? '约 122MB' : '约 2.4GB' },
        { id: 'model', label: t('envModel'), ok: stepOf('model').ok, hint: '' },
      ]

      const readyTag = (ok) => h('span', { className: `jw-tag${ok ? ' on' : ''}` },
        ok ? '✓ ' + t('envReady') : '✗ ' + t('envNotReady'))

      return h('div', { className: 'jw-stack' },
        h('div', { className: 'jw-card' },
          h('header', null,
            h('h3', null, t('envTitle')),
            readyTag(!!env.ready?.model),
            h('span', { className: 'jw-spacer' }),
            h('span', { className: 'jw-muted jw-small' }, env.repo)),
          h('p', { className: 'jw-muted jw-small', style: { marginTop: 0 } }, t('envHint')),

          (env.blockers || []).length > 0 && h('div', { className: 'jw-banner err' },
            t('envBlockers') + env.blockers.join('；'))),

        h('div', { className: 'jw-card' },
          h('div', { className: 'jw-section' }, t('envSteps')),
          (env.steps || []).map((step, index) => h('div', { className: 'jw-step', key: step.id },
            h('span', { className: `jw-step-no${step.ok ? ' ok' : ''}` }, step.ok ? '✓' : String(index + 1)),
            h('div', { className: 'jw-step-body' },
              h('div', { className: 'jw-step-title' }, step.label),
              h('div', { className: 'jw-muted jw-small' }, step.detail)))))

        ,

        h('div', { className: 'jw-card' },
          h('div', { className: 'jw-section' }, t('envMode')),
          h('div', { className: 'jw-modes' },
            ['cpu', 'gpu'].map((id) => h('button', {
              key: id,
              className: `jw-mode${mode === id ? ' on' : ''}`,
              disabled: running,
              onClick: () => setMode(id),
            },
            h('strong', null, id === 'cpu' ? 'CPU' : 'GPU'),
            h('span', { className: 'jw-muted jw-small' }, env.modes?.[id]?.detail || ''),
            h('span', { className: `jw-dot${env.modes?.[id]?.ready ? ' on' : ''}` },
              env.modes?.[id]?.ready ? t('envReady') : t('envNotReady'))))),

          h('div', { className: 'jw-row', style: { marginTop: 12 } },
            h('span', { className: 'jw-muted jw-small' }, t('envMirror')),
            h('select', {
              className: 'jw-select', value: mirror, disabled: running,
              onChange: (event) => setMirror(event.target.value),
            }, (env.mirrors || []).map((item) => h('option', { key: item.id, value: item.id }, item.name)))),

          h('div', { className: 'jw-actions' },
            actions.map((action) => h('button', {
              key: action.id,
              className: `jw-btn${action.ok ? '' : ' jw-btn-primary'}`,
              disabled: busy || running || (action.ok && !confirm),
              title: action.ok ? t('envAlready') : '',
              onClick: () => setConfirm(confirm === action.id ? '' : action.id),
            }, action.label,
            action.hint && h('span', { className: 'jw-btn-hint' }, action.hint),
            action.ok && h('span', { className: 'jw-btn-hint ok' }, t('envAlready')))),

            h('button', { className: 'jw-btn', disabled: busy || running, onClick: () => onReload() },
              t('envRecheck'))),

          confirm && h('div', { className: 'jw-confirm' },
            h('span', null, `${t('envConfirmTip')}${t(`envDo_${confirm}`)}`),
            h('button', { className: 'jw-btn jw-btn-primary', disabled: busy, onClick: () => run(confirm) },
              t('envStart')),
            h('button', { className: 'jw-btn', onClick: () => setConfirm('') }, t('envCancel'))),

          running && h('div', { className: 'jw-confirm' },
            h('span', { className: 'jw-muted' }, task?.label || ''),
            h('button', { className: 'jw-btn', onClick: stop }, t('envStop')))),

        h('div', { className: 'jw-card' },
          h('div', { className: 'jw-row', style: { marginBottom: 8 } },
            h('div', { className: 'jw-section' }, t('envLog')),
            task && h('span', { className: `jw-tag${task.status === 'done' ? ' on' : ''}` },
              task.status === 'running' ? t('envRunning')
                : task.status === 'done' ? '✓' : task.status === 'cancelled' ? t('envCancelled')
                  : `✗ ${task.error || ''}`)),
          task?.log?.length
            ? h('pre', { className: 'jw-log' }, task.log.join('\n'))
            : h('span', { className: 'jw-muted jw-small' }, t('envNone')),

          h('div', { className: 'jw-muted jw-small', style: { marginTop: 10 } },
            h('div', null, `${t('envModelNote')}`),
            h('div', null, `Python ${env.pythonVersion || '—'} · venv ${env.venv?.exists ? '✓' : '✗'}`
              + ` · torch ${env.packages?.torch?.version || '—'}${env.torchCuda ? `（CUDA ${env.torchCuda}）` : ''}`))))
    }

    // ── 详情页的通用外壳：标题 + 返回 ───────────────────────────────────
    function Detail({ title, hint, t, onBack, children }) {
      return h('div', { className: 'jw-stack' },
        h('div', { className: 'jw-row' },
          h('button', { className: 'jw-btn jw-btn-ghost', onClick: onBack }, '← ' + t('back')),
          h('strong', null, title),
          hint && h('span', { className: 'jw-muted jw-small' }, hint)),
        children)
    }

    // ── 主区：功能框 ────────────────────────────────────────────────────
    /**
     * 一个功能一个框，框里只有名字和状态。
     *
     * 点开之后那一页才讲描述、阶段链和"为什么现在不能跑"，框里放这些只会让
     * 四个框挤成一堵墙。不能跑的时候也照样能点——那一页会把原因写清楚，
     * 把按钮做成死的，人只会觉得"点了没反应"。
     */
    function FeatureTile({ feature, state, active, onOpen }) {
      return h('button', {
        className: `jw-tile${state.disabled ? ' off' : ''}${active ? ' on' : ''}`,
        title: [feature.description, state.note].filter(Boolean).join(' · '),
        onClick: onOpen,
      },
      h('span', { className: 'jw-tile-title' }, feature.name),
      h('span', { className: chipClass(state) }, state.chip))
    }

    // ── 右栏：环境 + 任务 ──────────────────────────────────────────────
    /**
     * 环境卡。配一次就不动的东西，压在右栏顶部，不跟功能区抢地方。
     */
    function EnvCard({ t, env, onOpen }) {
      const ok = !!env?.ready?.model
      const torch = env?.packages?.torch?.version
      return h('section', { className: 'jw-side-card' },
        h('div', { className: 'jw-side-head' },
          h('span', { className: 'jw-side-title' }, t('envTab')),
          h('span', { className: `jw-chip${ok ? ' on' : ' warn'}` },
            !env ? t('connectingShort') : ok ? t('envReady') : t('envNotReady')),
          h('span', { className: 'jw-spacer' }),
          h('button', { className: 'jw-btn jw-btn-ghost jw-btn-tiny', onClick: onOpen },
            ok ? t('open') : t('fix'))),
        h('div', { className: 'jw-side-line' },
          `Python ${env?.pythonVersion || '—'} · ${env?.venv?.exists ? 'venv ✓' : 'venv ✗'}`),
        h('div', { className: 'jw-side-line' },
          `torch ${torch || '—'}${env?.torchCuda ? `（CUDA ${env.torchCuda}）` : ''}`))
    }

    // ── 任务：一份账 + 它名下的运行记录 ────────────────────────────────
    /**
     * 一个任务 = 一份被确认过的序时账（原件副本 + 字段映射）+ 在这份账上跑过的功能。
     *
     * 几个功能都对同一份账干活，而且互相有依赖，所以账在任务里只配一次；
     * 要换账就再建一个任务（新的 id、新的账），不在同一个任务里来回换。
     * 建任务和配账是同一步：没有账的任务没意义，半截的任务不落地。
     */

    /** 新建任务：起名字 → 传账 → 认字段 → 创建。 */
    function TaskCreate({ api, t, onCreated, onError, setBusy, busy }) {
      const [uploaded, setUploaded] = useState(null)
      const [inspect, setInspect] = useState(null)
      const [mapping, setMapping] = useState({})
      const [name, setName] = useState("")
      const [working, setWorking] = useState("")
      const inputRef = useRef(null)

      const pick = useCallback(async (file) => {
        if (!file) return
        setWorking(t("inspecting"))
        onError("")
        try {
          const up = await api.upload(file)
          setUploaded(up)
          // 名字默认取文件名（去掉扩展名）：多数人就叫"序时账1"
          setName((current) => current || String(up.name || "").replace(/\.(xlsx|xls|xlsm|csv)$/i, ""))
          const data = await api.post("/inspect", { file: up.path, feature: "task" })
          setInspect(data.inspect)
          setMapping(data.inspect.suggested_mapping || {})
        } catch (error) {
          setInspect(null)
          onError(`${t("loadFailed")}: ${error.message}`)
        } finally {
          setWorking("")
        }
      }, [api, onError, t])

      const create = useCallback(async () => {
        if (!uploaded) return
        setBusy(true)
        onError("")
        try {
          const data = await api.post("/tasks", {
            file: uploaded.path, name: name || uploaded.name, mapping,
          })
          setUploaded(null)
          setInspect(null)
          setMapping({})
          setName("")
          if (inputRef.current) inputRef.current.value = ""
          await onCreated(data.task)
        } catch (error) {
          onError(`${t("taskCreate")}: ${error.message}`)
        } finally {
          setBusy(false)
        }
      }, [api, uploaded, name, mapping, onCreated, onError, setBusy, t])

      const missing = inspect?.missing || []
      const fields = inspect?.fields || []

      return h("div", { className: "jw-card" },
        h("header", null,
          h("h3", null, t("taskNew")),
          h("span", { className: "jw-muted jw-small" }, t("taskNewHint"))),

        h("div", { className: "jw-row", style: { marginBottom: 10 } },
          h("span", { className: "jw-muted jw-small" }, t("taskName")),
          h("input", {
            className: "jw-input", style: { flex: 1, minWidth: 180, maxWidth: 320 },
            placeholder: t("taskNameHint"), value: name,
            onChange: (event) => setName(event.target.value),
          })),

        !inspect && h("div", null,
          h("label", { className: "jw-drop" },
            h("input", {
              ref: inputRef, type: "file", accept: ".xlsx,.xls,.xlsm,.csv",
              style: { display: "none" },
              onChange: (event) => {
                const file = event.target.files?.[0]
                if (file) pick(file)
              },
            }),
            h("div", null, t("chooseFile")),
            h("div", { className: "jw-muted jw-small" }, ".xlsx / .xls / .csv")),
          working && h("div", { className: "jw-muted jw-small", style: { marginTop: 8 } }, working)),

        inspect && h("div", null,
          h("div", { className: "jw-row", style: { marginBottom: 10 } },
            h("strong", null, inspect.filename),
            h("span", { className: "jw-muted jw-small" },
              `${fmtNumber(inspect.rows)} ${t("rows")} · ${(inspect.columns || []).length} 列`),
            h("span", { className: "jw-spacer" }),
            h("button", {
              className: "jw-btn jw-btn-ghost",
              onClick: () => { setInspect(null); setUploaded(null) },
            }, t("cancel"))),

          missing.length > 0 && h("div", { className: "jw-banner err", style: { marginBottom: 10 } },
            `${t("mapping")}: ${(inspect.missing_labels || missing).join("、")}`),

          h("div", { className: "jw-map" }, fields.map((field) => h(React.Fragment, { key: field.key },
            h("span", { className: "jw-muted jw-small" }, field.label,
              field.required && h("span", { className: "jw-req" }, "*")),
            h("select", {
              className: "jw-select",
              value: mapping[field.key] || "",
              onChange: (event) => {
                const value = event.target.value
                setMapping((current) => {
                  const next = { ...current }
                  if (value) next[field.key] = value
                  else delete next[field.key]
                  return next
                })
              },
            },
            h("option", { value: "" }, "—"),
            (inspect.columns || []).map((column) => h("option", { key: column, value: column }, column))),
            h("span", { className: "jw-muted jw-small" },
              (inspect.preview?.[field.key]?.samples || []).slice(0, 2).join(" / "))))),

          h("div", { className: "jw-actions" },
            h("button", {
              className: "jw-btn jw-btn-primary",
              disabled: missing.length > 0 || !!working || !!busy,
              onClick: create,
            }, busy ? t("creating") : t("taskCreate")))))
    }

    /**
     * 右栏：任务列表（窄栏里一块一个任务）。
     *
     * 一个任务 = 一份账 + 在这份账上跑过的功能。切任务是**换一份账**，不是换视图，
     * 所以每块先讲账（文件名、多少行、组合算没算），再把四个功能摆成可点的 chip——
     * 从任务直接跳进那个功能，不用先"选中"再回主区点一次。
     *
     * 删除要点两下：会连同账和运行记录一起收走，页面上找不回来。
     */
    function TaskRail({ api, t, tasks, features, activeId, onChanged, onUse, onOpenFeature, onNew, onError }) {
      const [confirmId, setConfirmId] = useState("")
      const doomed = tasks.find((item) => item.id === confirmId) || null

      const prepare = useCallback(async (id) => {
        try {
          await api.post("/tasks/prepare", { id })
          await onChanged(id)
        } catch (error) {
          onError(error.message)
        }
      }, [api, onChanged, onError])

      const remove = useCallback(async (id) => {
        try {
          await api.post("/tasks/delete", { id })
          setConfirmId("")
          await onChanged()
        } catch (error) {
          onError(error.message)
        }
      }, [api, onChanged, onError])

      const comboLine = (task) => (task.combo?.ok
        ? `${task.combo.l1_patterns} 种一级组合 · ${task.combo.l1l2_patterns} 种明细组合`
        : (task.combo?.error ? `${t("combo")}：${task.combo.error}` : t("comboNone")))

      /** 运行状态 → chip 的语气（只影响颜色，不影响文字）。 */
      const toneOf = (run) => {
        if (!run) return ""
        if (run.status === "done") return " done"
        if (run.status === "failed") return " failed"
        if (run.status === "awaiting_review") return " await"
        return " await"
      }

      const stop = (fn) => (event) => { event.stopPropagation(); fn() }

      return h("section", { className: "jw-side-card" },
        h("div", { className: "jw-side-head" },
          h("span", { className: "jw-side-title" }, t("taskTab")),
          tasks.length > 0 && h("span", { className: "jw-muted jw-small" }, String(tasks.length)),
          h("span", { className: "jw-spacer" }),
          h("button", {
            className: "jw-btn jw-btn-ghost jw-btn-tiny", onClick: onNew, title: t("taskNew"),
          }, "＋")),

        doomed && h("div", { className: "jw-banner err" },
          fill(t("taskDeleteNote"), {
            name: doomed.name,
            n: Object.keys(doomed.runs || {}).length,
          })),

        tasks.length === 0
          ? h("div", { className: "jw-empty" },
            h("div", null, t("taskNoneHint")),
            h("button", { className: "jw-btn jw-btn-primary jw-btn-tiny", onClick: onNew },
              t("newJob")))
          : h("div", { className: "jw-tasks" }, tasks.map((task) => h("div", {
            key: task.id,
            className: `jw-taskrow${task.id === activeId ? " on" : ""}`,
            // 这个任务的 AI 复核在自己那个会话里跑（数据隔离）；会话 id 挂在提示上
            title: task.session ? `${t("reviewSession")}：${task.session}` : undefined,
            onClick: () => onUse(task.id),
          },
          h("div", { className: "jw-taskrow-head" },
            h("span", { className: "jw-taskrow-name" }, task.name),
            task.id === activeId && h("span", { className: "jw-chip on" }, t("inUse")),
            h("span", { className: "jw-spacer" }),
            h("button", {
              className: "jw-btn jw-btn-ghost jw-btn-tiny",
              title: t("combo"),
              onClick: stop(() => prepare(task.id)),
            }, t("comboRun")),
            confirmId === task.id
              ? h(React.Fragment, null,
                h("button", {
                  className: "jw-btn jw-btn-tiny",
                  onClick: stop(() => remove(task.id)),
                }, `${t("delete")} ✓`),
                h("button", {
                  className: "jw-btn jw-btn-ghost jw-btn-tiny",
                  onClick: stop(() => setConfirmId("")),
                }, t("cancel")))
              : h("button", {
                className: "jw-btn jw-btn-ghost jw-btn-tiny",
                onClick: stop(() => setConfirmId(task.id)),
              }, t("delete"))),

          h("div", { className: "jw-taskrow-meta" }, `${task.filename} · ${taskLine(t, task)}`),
          h("div", { className: "jw-taskrow-meta" }, comboLine(task)),

          h("div", { className: "jw-runs" }, features.map((feature) => {
            const run = (task.runs || {})[feature.id]
            return h("button", {
              key: feature.id,
              className: `jw-runchip${toneOf(run)}`,
              title: `${feature.name}：${run ? statusWord(t, run.status) : t("noRuns")}`,
              onClick: stop(() => onOpenFeature(task.id, feature.id)),
            }, `${feature.name} ${run ? statusWord(t, run.status) : "—"}`)
          }))))))
    }

    // ── 一个功能现在能不能跑 ────────────────────────────────────────────
    /**
     * 判断顺序：环境 → 任务（有没有账） → 上游依赖 → 自己跑到哪了。
     *
     * 抽成纯函数是因为功能框、"跑一个功能"面板、功能页的横幅要说同一句话——
     * 三处各写一遍，迟早出现"框里说能跑、面板说不能"。
     */
    function featureState(t, feature, { env, task, features }) {
      const needsModel = NEEDS_MODEL.has(feature.id)
      const envOk = !env || env.error || !env.ready
        ? true
        : (needsModel ? !!env.ready.model : !!env.ready.basic)
      if (!envOk) {
        return { chip: t('envNotReady'), chipOn: false, disabled: true, note: t('cardEnv'), tone: 'warn' }
      }
      if (!task) {
        return { chip: t('cardNoTask'), chipOn: false, disabled: true, note: t('cardNoTaskHint'), tone: 'warn' }
      }
      const runs = task.runs || {}
      const missing = (FEATURE_DEPS[feature.id] || []).filter((dep) => runs[dep]?.status !== 'done')
      if (missing.length) {
        const names = missing.map((dep) => (features || []).find((item) => item.id === dep)?.name || dep)
        return {
          chip: t('cardBlocked'), chipOn: false, disabled: true,
          note: `${t('cardDeps')}${names.join('、')}`, tone: 'warn',
        }
      }
      const run = runs[feature.id]
      if (!run) return { chip: t('cardReady'), chipOn: false, disabled: false, note: '', tone: '' }
      return {
        chip: statusWord(t, run.status),
        chipOn: run.status === 'done',
        disabled: false,
        note: run.awaiting ? `${t('awaiting')}：${run.awaiting}` : '',
        tone: run.status === 'failed' ? 'bad' : run.status === 'done' ? 'good' : '',
      }
    }

    const chipClass = (state) => `jw-chip${state.chipOn ? ' on' : ''}`
      + `${state.tone === 'warn' ? ' warn' : state.tone === 'bad' ? ' bad' : ''}`

    /** 行数不在任务文件顶层，在配置时算好的组合摘要里。 */
    const rowsOf = (task) => Number(task?.combo?.rows || 0) || 0

    /** 任务的一行摘要：多少行账、字段配了几个（没配就是读表时自动认列）。 */
    function taskLine(t, task) {
      const fields = Object.keys(task?.mapping || {}).length
      return [
        rowsOf(task) ? `${fmtNumber(rowsOf(task))} ${t('rows')}` : '',
        fields ? `${fields} 个字段已配置` : t('fieldsAuto'),
      ].filter(Boolean).join(' · ')
    }

    // ── 新建任务：在功能外层决定跑哪个功能 ──────────────────────────────
    /**
     * 建"运行"这件事是任务级的：账在任务里配好、几个功能都用它，所以先在这里
     * 决定"跑什么"，再进那个功能看复核和结果。放进功能页里会把顺序倒过来
     * （先挑功能才建任务），而且还没建任务时四个功能页要各说一遍同样的话。
     */
    function NewTaskPanel({ api, t, features, task, stateOf, preset, onStarted, onError, busy, setBusy, onGoTasks }) {
      const [picked, setPicked] = useState(preset || '')
      // 没点过就默认第一个能跑的；点过的那个万一不能跑了，也回落到能跑的
      const chosen = features.find((item) => item.id === picked && !stateOf(item).disabled)
        || features.find((item) => !stateOf(item).disabled)
        || null

      const start = useCallback(async () => {
        if (!task || !chosen) return
        setBusy(true)
        onError('')
        try {
          const data = await api.post('/start', {
            feature: chosen.id,
            file: task.file,
            mapping: task.mapping || {},
            task: task.id,
          })
          onStarted(data.job_id, chosen.id)
        } catch (error) {
          onError(`${t('start')}: ${error.message}`)
        } finally {
          setBusy(false)
        }
      }, [api, task, chosen, onError, onStarted, setBusy, t])

      if (!task) {
        return h('div', { className: 'jw-card' },
          h('div', { className: 'jw-banner err' }, t('needTask')),
          h('div', { className: 'jw-actions' },
            h('button', { className: 'jw-btn jw-btn-primary', onClick: onGoTasks }, t('taskNew'))))
      }

      return h('div', { className: 'jw-card' },
        h('header', null,
          h('h3', null, t('newTaskTitle')),
          h('span', { className: 'jw-muted jw-small' }, t('newTaskHint'))),
        h('div', { className: 'jw-row', style: { marginBottom: 12 } },
          h('span', { className: 'jw-muted jw-small' }, t('useTask')),
          h('strong', null, task.name),
          h('span', { className: 'jw-muted jw-small' }, `${task.filename} · ${taskLine(t, task)}`)),

        h('div', { className: 'jw-section' }, t('newTaskPick')),
        h('div', { className: 'jw-picks' }, features.map((feature) => {
          const state = stateOf(feature)
          const on = chosen?.id === feature.id
          return h('button', {
            key: feature.id,
            className: `jw-pick${on ? ' on' : ''}${state.disabled ? ' off' : ''}`,
            onClick: () => { if (!state.disabled) setPicked(feature.id) },
          },
          h('span', { className: 'jw-pick-name' }, feature.name),
          h('span', { className: chipClass(state) }, state.chip),
          h('span', { className: 'jw-pick-desc' }, feature.description || ''),
          state.note && h('span', { className: 'jw-pick-note' }, state.note))
        })),

        h('div', { className: 'jw-actions' },
          h('button', {
            className: 'jw-btn jw-btn-primary',
            disabled: !!busy || !chosen,
            onClick: start,
          }, busy ? t('starting') : t('start')),
          // 什么都跑不了时不要瞎猜原因：每个功能行自己已经写了缺什么
          chosen && h('span', { className: 'jw-muted jw-small' },
            `${t('newTaskFlow')}：${(chosen.stages || []).map((stage) => stage.title).join(' → ')}`)))
    }

    /**
     * 首页 = 配置区 + 功能区。
     *
     * 为什么不用顶部横向页签：功能会越来越多，横排最后要点半天，还看不见层次。
     * 现在上下两层：配置（环境、序时账）在上，功能卡片在下，一行五个、多了换行。
     */
    function Workbench(props) {
      const t = translate
      const sessionId = props?.sessionId || ''
      const api = useMemo(() => makeApi(() => sessionId), [sessionId])

      const [features, setFeatures] = useState([])
      const [jobs, setJobs] = useState([])
      const [tasks, setTasks] = useState([])
      const [taskId, setTaskId] = useState('')
      const [env, setEnv] = useState(null)
      const [repo, setRepo] = useState('')
      const [view, setView] = useState('home')
      // 右栏宽度：拖一下就能给功能区让出空间，宽度记在本地
      const [sideW, setSideW] = useState(readSideWidth)
      const [taskPreset, setTaskPreset] = useState('')
      const [jobId, setJobId] = useState('')
      const [job, setJob] = useState(null)
      const [review, setReview] = useState(null)
      const [error, setError] = useState('')
      const [notice, setNotice] = useState('')
      const [busy, setBusy] = useState(false)
      const [epoch, setEpoch] = useState(0)
      const [booting, setBooting] = useState(true)
      const [dragOn, setDragOn] = useState(false)
      const timer = useRef(null)
      const bootRef = useRef({ tries: 0, done: false })

      const loadJobs = useCallback(async () => {
        try {
          const data = await api.get('/jobs')
          setJobs(data.jobs || [])
          setRepo(data.repo || '')
          setBooting(false)
          setError('')
          bootRef.current.done = true
        } catch (caught) {
          // 应用刚启动时插件的路由可能还没挂上：404 表示"还没起来"，自己重试
          if (!bootRef.current.done) {
            bootRef.current.tries += 1
            if (bootRef.current.tries <= 60) {
              window.setTimeout(() => { loadJobs() }, 1000)
            } else {
              setBooting(false)
              setError(caught.message)
            }
            return
          }
          setError(caught.message)
        }
      }, [api])

      const loadFeatures = useCallback(async () => {
        try {
          const data = await api.get('/features')
          setFeatures(data.features || [])
        } catch (caught) {
          setError(caught.message)
        }
      }, [api])

      const loadTasks = useCallback(async (selectId) => {
        try {
          const data = await api.get('/tasks')
          const list = data.tasks || []
          setTasks(list)
          setTaskId((current) => {
            const want = selectId || current
            // 删掉当前任务后要落到别的任务上，不能指着已经不存在的 id
            if (want && list.some((item) => item.id === want)) return want
            return list[0]?.id || ''
          })
        } catch (caught) {
          setError(caught.message)
        }
      }, [api])

      const loadEnv = useCallback(async (fresh) => {
        try {
          const data = await api.get('/env', fresh ? { fresh: '1' } : {})
          setEnv(data.env)
        } catch (caught) {
          setEnv({ error: caught.message })
        }
      }, [api])

      useEffect(() => {
        loadFeatures()
        loadJobs()
        loadTasks()
        loadEnv(false)
      }, [loadFeatures, loadJobs, loadTasks, loadEnv])

      useEffect(() => {
        const id = window.setInterval(loadJobs, 6000)
        return () => window.clearInterval(id)
      }, [loadJobs])

      useEffect(() => {
        if (!jobId) {
          setJob(null)
          setReview(null)
          return undefined
        }
        let stopped = false
        const tick = async () => {
          if (stopped) return
          try {
            const data = await api.get('/job', { id: jobId })
            if (stopped) return
            setJob(data.job)
            if (data.job.status === 'awaiting_review') {
              const rev = await api.get('/review', { id: jobId })
              if (!stopped) setReview(rev.review)
            } else if (data.job.status !== 'running') {
              setReview(null)
            }
            const delay = data.job.status === 'running' ? 1500
              : data.job.status === 'awaiting_review' ? 4000 : 0
            if (delay > 0 && !stopped) timer.current = window.setTimeout(tick, delay)
          } catch (caught) {
            if (!stopped) setError(caught.message)
          }
        }
        tick()
        return () => {
          stopped = true
          if (timer.current) window.clearTimeout(timer.current)
        }
      }, [api, jobId, epoch])

      // 拖完才落盘（拖动过程中写 localStorage 会卡手）
      useEffect(() => {
        try {
          window.localStorage?.setItem(SIDE_KEY, String(sideW))
        } catch {
          // 存不了就算了，下次用默认宽度
        }
      }, [sideW])

      /**
       * 拖分隔条：改的是**右栏**的宽度，所以往左拖 = 右栏变宽。
       *
       * 监听挂在 window 上，是因为鼠标很容易甩出那 11px 的条——只挂在分隔条自己
       * 身上，指针一离开就断；松手时必须摘掉，否则下一次点击会继续改宽度。
       */
      const startDrag = useCallback((event) => {
        if (event.button !== 0) return
        event.preventDefault()
        const startX = event.clientX
        const startW = sideW
        setDragOn(true)
        const move = (ev) => setSideW(clampSide(startW - (ev.clientX - startX)))
        const up = () => {
          setDragOn(false)
          window.removeEventListener('pointermove', move)
          window.removeEventListener('pointerup', up)
          window.removeEventListener('pointercancel', up)
        }
        window.addEventListener('pointermove', move)
        window.addEventListener('pointerup', up)
        window.addEventListener('pointercancel', up)
      }, [sideW])

      const task = tasks.find((item) => item.id === taskId) || tasks[0] || null

      const decide = useCallback(async (decisions) => {
        if (!review?.stage) return
        setBusy(true)
        setError('')
        try {
          const data = await api.post('/decide', { job_id: jobId, stage: review.stage, decisions })
          const receipt = data.receipt || {}
          const rejected = receipt.rejected || []
          if (rejected.length) {
            setError(rejected.map((item) => `${item.key}: ${item.reason}`).join('；'))
          } else {
            setNotice(fill(t('submittedN'), { n: receipt.accepted || 0 }))
          }
          setReview(null)
          setEpoch((value) => value + 1)
          loadTasks()
        } catch (caught) {
          setError(caught.message)
        } finally {
          setBusy(false)
        }
      }, [api, jobId, review, loadTasks])

      const waive = useCallback(async () => {
        if (!review?.stage) return
        setBusy(true)
        try {
          await api.post('/decide', { job_id: jobId, stage: review.stage, decisions: [], waive: true })
          setReview(null)
          setEpoch((value) => value + 1)
          loadTasks()
        } catch (caught) {
          setError(caught.message)
        } finally {
          setBusy(false)
        }
      }, [api, jobId, review, loadTasks])

      const resume = useCallback(async () => {
        setBusy(true)
        try {
          await api.post('/resume', { job_id: jobId })
          setEpoch((value) => value + 1)
        } catch (caught) {
          setError(caught.message)
        } finally {
          setBusy(false)
        }
      }, [api, jobId])

      const wake = useCallback(async () => {
        if (!jobId) return
        setBusy(true)
        try {
          const data = await api.post('/wake', { job_id: jobId })
          setNotice(data.woken ? t('waked') : t('wakeFailed'))
        } catch (caught) {
          setError(caught.message)
        } finally {
          setBusy(false)
        }
      }, [api, jobId, t])

      const copyLine = useCallback(async () => {
        const stage = review?.title || job?.stages?.find((item) => item.status === 'awaiting')?.title || ''
        const text = fill(t('copiedLine'), {
          job: jobId, feature: job?.feature_name || '', stage, count: review?.total ?? 0,
        })
        try {
          await navigator.clipboard.writeText(text)
          setNotice(t('copied'))
        } catch {
          setNotice(text)
        }
      }, [job, jobId, review, t])

      const chain = useMemo(() => {
        if (!job) return []
        const feature = features.find((item) => item.id === job.feature)
        const runtime = new Map((job.stages || []).map((stage) => [stage.id, stage]))
        if (!feature?.stages?.length) return job.stages || []
        return feature.stages.map((decl) => ({
          ...decl,
          ...(runtime.get(decl.id) || {}),
          title: decl.title || runtime.get(decl.id)?.title || decl.id,
        }))
      }, [features, job])

      // ── 功能卡片的状态（和「新建任务」面板共用 featureState）──
      const cardState = (feature) => featureState(t, feature, { env, task, features })

      const currentFeature = view.startsWith('feature:')
        ? features.find((item) => item.id === view.slice(8)) || null
        : null

      /** 打开某个功能；`owner` 用来从别的任务下面的 chip 直接跳过来。 */
      const openFeature = (id, owner) => {
        const which = owner || task
        setView(`feature:${id}`)
        setJobId('')
        setJob(null)
        setReview(null)
        if (which && which.id !== task?.id) setTaskId(which.id)
        // 已有运行就直接打开最近那个：只认这个任务的，别把别的任务的运行翻出来
        const run = which?.runs?.[id]
        const latest = run?.job_id
          || (which ? jobs.find((item) => item.feature === id && item.task === which.id)?.job_id : '')
          || ''
        if (latest) setJobId(latest)
      }

      const openFeatureFor = (ownerId, featureId) => {
        openFeature(featureId, tasks.find((item) => item.id === ownerId) || null)
      }

      /** 建任务：起名字 + 配账，落在首页之前的那一步。 */
      const openCreate = () => {
        setView('new')
        setJobId('')
        setJob(null)
        setReview(null)
      }

      /** 跑一个功能：用当前任务的账开工，可选一个默认功能。 */
      const openRun = (preset) => {
        setTaskPreset(preset || '')
        setView('run')
        setJobId('')
        setJob(null)
        setReview(null)
      }

      const head = h('div', { className: 'jw-head' },
        h('h2', { className: 'jw-title' }, t('title')),
        repo && h('span', { className: 'jw-muted jw-small' }, repo),
        h('span', { className: 'jw-spacer' }),
        // 当前任务放页头：所有功能都在这份账上跑，随时能看清现在是谁、也能直接切
        tasks.length > 0 && h('select', {
          className: 'jw-select',
          value: task?.id || '',
          title: t('taskCurrent'),
          onChange: (event) => setTaskId(event.target.value),
        }, tasks.map((item) => h('option', { key: item.id, value: item.id }, item.name))),
        h('button', {
          className: 'jw-btn jw-btn-primary',
          disabled: !task,
          onClick: () => openRun(''),
        }, t('startRun')),
        h('button', {
          className: 'jw-btn',
          onClick: () => openCreate(),
        }, t('newJob')),
        h('button', {
          className: 'jw-btn jw-btn-ghost',
          onClick: () => { loadJobs(); loadTasks(); loadEnv(true) },
        }, t('refresh')))

      const body = []
      body.push(booting && !error && h('div', { className: 'jw-banner' }, t('connecting')))
      body.push(error && h('div', { className: 'jw-banner err' }, error))
      body.push(notice && !error && h('div', { className: 'jw-banner ok' }, notice))

      if (view === 'home') {
        // 主区 = 功能。环境与任务在右栏：这两个是"配一次 / 切一下"的东西，
        // 不该占着每次都要点的功能的位置。
        body.push(h('div', { className: 'jw-section', style: { margin: 0 } }, t('sectionFeature')))
        body.push(h('div', { className: 'jw-grid' }, features.map((feature) => h(FeatureTile, {
          key: feature.id,
          feature,
          state: cardState(feature),
          active: false,
          onOpen: () => openFeature(feature.id),
        }))))
      }

      if (view === 'new') {
        body.push(h(Detail, {
          title: t('taskNew'),
          hint: task ? task.name : '',
          t,
          onBack: () => { setView('home'); setJobId(''); setJob(null); setReview(null) },
        },
        h(TaskCreate, {
          api, t, busy, setBusy,
          onError: setError,
          // 先用回包里的任务顶上，再拉一次清单：拉清单失败时它也已经在了
          onCreated: async (created) => {
            if (created?.id) {
              setTasks((list) => [created, ...list.filter((item) => item.id !== created.id)])
              setTaskId(created.id)
            }
            await loadTasks(created?.id)
            setView('home')
          },
        })))
      }

      if (view === 'run') {
        body.push(h(Detail, {
          title: t('newTaskTitle'),
          hint: task ? task.name : '',
          t,
          onBack: () => { setView('home'); setJobId(''); setJob(null); setReview(null) },
        },
        h(NewTaskPanel, {
          api, t, features, task, busy, setBusy,
          preset: taskPreset,
          stateOf: cardState,
          onError: setError,
          onGoTasks: () => openCreate(),
          onStarted: (id, featureId) => {
            setView(`feature:${featureId}`)
            setJobId(id)
            setEpoch((value) => value + 1)
            loadJobs()
            loadTasks(task?.id)
          },
        })))
      }

      if (view === 'env') {
        body.push(h(Detail, { title: t('envTab'), t, onBack: () => setView('home') },
          h(EnvPanel, { api, t, env, onReload: () => loadEnv(true), onError: setError })))
      }

      if (currentFeature) {
        const state = cardState(currentFeature)
        // 只列**这个任务**在这份账上跑出来的运行：全局列表里混着别的任务和
        // 命令行留下的一堆运行，看起来就像"任务删了东西还在"
        const tabJobs = task
          ? jobs.filter((item) => item.feature === currentFeature.id && item.task === task.id)
          : []
        body.push(h(Detail, {
          title: currentFeature.name,
          hint: currentFeature.description,
          t,
          onBack: () => { setView('home'); setJobId(''); setJob(null); setReview(null) },
        },
        h('div', { className: 'jw-row' },
          tabJobs.length > 0
            ? h(React.Fragment, null,
              h('span', { className: 'jw-muted jw-small' }, t('job')),
              h('select', {
                className: 'jw-select', value: jobId || tabJobs[0].job_id, style: { maxWidth: 380 },
                onChange: (event) => { setError(''); setNotice(''); setJobId(event.target.value) },
              },
              tabJobs.map((item) => h('option', { key: item.job_id, value: item.job_id },
                `${statusWord(t, item.status)} · ${String(item.created_at || '').slice(5, 16)} · ${item.job_id.slice(-6)}`))))
            : h('span', { className: 'jw-muted jw-small' }, t('noRuns')),
          h('span', { className: 'jw-spacer' }),
          h('button', {
            className: 'jw-btn jw-btn-primary',
            disabled: state.disabled,
            onClick: () => openRun(currentFeature.id),
          }, t('startRun'))),
        !job && !tabJobs.length && state.note
          && h('div', { className: 'jw-banner err' }, state.note),
        !job && tabJobs.length > 0 && h('div', { className: 'jw-muted jw-small' }, t('empty')),
        job && h(JobView, {
          job, chain, review, api, t, busy,
          onDecide: decide, onWaive: waive, onWake: wake, onCopy: copyLine, onResume: resume,
        })))
      }

      return h('div', { className: 'jw-root' }, h(StyleTag, null), head,
        h('div', { className: 'jw-split' },
          h('main', { className: 'jw-main' },
            // body 是个动态数组：不补 key，React 每轮都警告一遍
            body.filter(Boolean).map((node, index) => React.cloneElement(node, {
              key: node.key || `block-${index}`,
            }))),
          h('div', {
            className: `jw-gutter${dragOn ? ' dragging' : ''}`,
            role: 'separator',
            'aria-orientation': 'vertical',
            title: t('resizeHint'),
            onPointerDown: startDrag,
            onDoubleClick: () => setSideW(SIDE_DEFAULT),
          }),
          h('aside', { className: 'jw-side', style: { width: sideW } },
            h(EnvCard, { t, env, onOpen: () => setView('env') }),
            h(TaskRail, {
              api, t, tasks, features,
              activeId: task?.id || '',
              onChanged: async (selectId) => { await loadTasks(selectId); await loadJobs() },
              onUse: (id) => setTaskId(id),
              onOpenFeature: openFeatureFor,
              onNew: openCreate,
              onError: setError,
            }))))
    }

    /**
     * 一个任务的整块视图：头部 + 阶段链 + （在跑 / 待办 / 结果）。
     *
     * 单独抽出来是为了能离线渲染：这个仓库里没有浏览器，`Workbench` 的数据靠
     * effect 拉，SSR 不会跑 effect——只有把它做成纯组件，才能拿真账的 JSON
     * 渲染一遍确认不炸。
     */
    function JobView({ job, chain, review, api, t, busy, onDecide, onWaive, onWake, onCopy, onResume }) {
      return h(React.Fragment, null,
        h('div', { className: 'jw-card' },
          h('header', null,
            h('h3', null, job.feature_name || job.feature),
            h('span', { className: 'jw-mono jw-muted' }, job.job_id),
            h('span', { className: 'jw-tag' }, statusWord(t, job.status)),
            h('span', { className: 'jw-muted jw-small' },
              `${job.input?.filename || ''}${job.created_at ? ` · ${job.created_at}` : ''}`)),
          h(Stages, { chain: chain || job.stages || [], t }),
          job.status === 'failed' && h('div', { className: 'jw-banner err', style: { marginTop: 10 } },
            job.error || t('failed')),
          job.status === 'failed' && h('div', { className: 'jw-row', style: { marginTop: 8 } },
            h('button', { className: 'jw-btn', disabled: !!busy, onClick: onResume }, t('resume'))),
          job.running && h('div', { style: { marginTop: 10 } },
            h('div', { className: 'jw-muted jw-small' },
              `${t('running')}：${job.running.label} · ${job.running.seconds}s`),
            job.running.log?.length
              ? h('pre', { className: 'jw-log' }, job.running.log.join('\n'))
              : null)),

        job.status === 'awaiting_review' && review && h(ReviewTable, {
          review,
          submitted: review.submitted || {},
          t,
          busy,
          onSubmit: onDecide,
          onWaive,
          onWake,
          onCopy,
        }),

        job.status === 'done' && h(ResultPanel, { job, t, api }),

        !job.running && !['awaiting_review', 'done', 'failed'].includes(job.status)
          && h('div', { className: 'jw-card' },
            h('p', { className: 'jw-muted jw-small', style: { margin: 0 } }, t('empty'))))
    }

    // ── 插件入口 ──────────────────────────────────────────────────────────

    function apply(ctx) {
      ctx.effect(() => {
        try {
          const bound = ctx.locale?.bind?.(NS)
          translate = (key) => {
            const value = typeof bound === 'function' ? bound(key) : undefined
            if (typeof value === 'string' && value && value !== key) return value
            return DICT.zh[key] ?? key
          }
        } catch {
          translate = (key) => DICT.zh[key] ?? key
        }
        const register = ctx.locale?.register?.(NS, DICT)
        return () => {
          translate = (key) => DICT.zh[key] ?? key
          if (typeof register === 'function') register()
        }
      }, `${NS}: dictionary`)

      ctx.slots.inject('conversation.view', () => ctx.slots.register({
        name: 'conversation.view',
        id: 'journal',
        order: 25,
        locale: NS,
        label: () => translate('tab'),
      }, Workbench))
    }

    return {
      name: NS,
      inject: ['slots', 'locale'],
      apply,
      // 离线渲染测试用（浏览器里没人读它）：没有浏览器时靠它验证组件不炸。
      __internals: { Workbench, JobView, Stages, ReviewTable, ResultPanel, EnvPanel,
        TaskCreate, TaskRail, EnvCard, FeatureTile, NewTaskPanel, Detail,
        featureState, chipClass, DICT, translate },
    }
  },
})
