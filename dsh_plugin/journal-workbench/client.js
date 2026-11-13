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
        tab: '审计工作台',
        title: '审计工作台',
        newJob: '＋ 新建任务',
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
        reviewChoice: '这一批怎么处理：可以让这个任务的 AI 复核，也可以不复核、直接按原判出结果。',
        waive: '不复核，直接用',
        stop: '■ 中止',
        stopping: '中止中…',
        stopped: '已中止',
        stalled: '这个运行的进程已经不在了（被中止过，或者插件重启过），它不会再往前走。',
        stalledHint: '可以重试（已跑完的阶段不会重算），或者中止它、重新开始。',
        wake: 'AI 复核这批',
        waked: '已交给这个任务的 AI 复核会话，它会接手这批待办',
        wakeFailed: '叫不醒复核会话（可能还没建起来）。再点一次，或让任务自己的会话接手。',
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
        taskNewHint: '先起个名字，再把这份账传上来；创建时会解析一次（认列、读表、算科目组合），之后四个功能都直接用这份解析结果',
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
      },
      en: {
        tab: 'Journal',
        title: 'Journal workbench',
        newJob: '+ New task',
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
        reviewChoice: 'Two ways out of this stop: hand the batch to this task\'s AI reviewer, or skip review and use the model\'s own answers.',
        waive: 'Skip review, use as is',
        stop: '■ Stop',
        stopping: 'Stopping…',
        stopped: 'Stopped',
        stalled: 'The process behind this run is gone (stopped earlier, or the plugin restarted), so it will not move on.',
        stalledHint: 'Retry (finished stages are not recomputed), or stop it and start over.',
        wake: 'AI review this batch',
        waked: 'Handed over to this task\'s review session; it will take these items',
        wakeFailed: 'Could not wake the review session. Try again, or use "Copy for AI"',
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
        taskNewHint: 'Name it, then upload the ledger; it is parsed once on creation (columns, table, subject combos) and every feature reads that result',
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
      if (status === 'aborted') return t('stopped')
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
    const SIDE_DEFAULT = 288
    const SIDE_KEY = 'jw.sideWidth'
    const COLLAPSE_KEY = 'jw.sideCollapsed'

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

    /** 右栏收没收起：记在本地，下次进来还是上次那个状态。 */
    function readSideCollapsed() {
      try {
        return window.localStorage?.getItem(COLLAPSE_KEY) === '1'
      } catch {
        return false
      }
    }

    // ── 样式（只引主题 token；`color-mix` 用来做 hover / 淡色底）──────────

    const CSS = `/* 会计分录工作台 · 界面层
 *
 * 只描述外观与页面的展开/收起：类名、组件、接口与任务逻辑仍由 client.js 决定。
 * 颜色全部从 DSH 主题 token 推导（color-mix），深浅主题自动跟随；
 * DSH 浅色主题的边框 token 近乎透明，所以线自己按标签色算，不直接用 border-l1。
 * 功能卡按序循环五种低饱和色，加功能不会花，也不会一片同色。 */

.jw-root{
  --jw-ink:var(--dsw-alias-label-primary);
  --jw-dim:var(--dsw-alias-label-secondary);
  --jw-line:color-mix(in srgb,var(--jw-ink) 11%,transparent);
  --jw-line-strong:color-mix(in srgb,var(--jw-ink) 20%,transparent);
  --jw-surface:var(--dsw-alias-bg-layer-1);
  --jw-sunken:color-mix(in srgb,var(--jw-ink) 3.5%,var(--dsw-alias-bg-base));
  --jw-brand:color-mix(in srgb,#4c63c4 92%,var(--jw-ink) 8%);
  --jw-brand-soft:color-mix(in srgb,var(--jw-brand) 9%,transparent);
  --jw-good:var(--dsw-alias-state-success-primary);
  --jw-warn:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 78%,var(--jw-dim));
  --jw-bad:var(--dsw-alias-state-error-primary);
  --jw-r:14px;
  --jw-shadow:0 1px 2px color-mix(in srgb,var(--jw-ink) 5%,transparent);
  --jw-lift:0 2px 6px color-mix(in srgb,var(--jw-ink) 7%,transparent),
    0 18px 36px -28px color-mix(in srgb,var(--jw-ink) 45%,transparent);
  --jw-ease:cubic-bezier(.22,1,.36,1);
  display:flex;flex-direction:column;height:100%;min-height:0;
  background:var(--dsw-alias-bg-base);color:var(--jw-ink);
  font-size:14px;line-height:1.65;letter-spacing:.005em;
}

/* ──  Basics  ─────────────────────────────────────────────────────── */
.jw-spacer{flex:1;min-width:8px}
.jw-muted{color:var(--jw-dim)}
.jw-small{font-size:12.5px}
.jw-mono{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:12px}
.jw-ok{color:var(--jw-good)}
.jw-bad{color:var(--jw-bad)}
.jw-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.jw-stack{display:flex;flex-direction:column;gap:14px}
.jw-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:16px}

/* ──  页头：标题带一枚小印记，底边一道渐变收口  ─────────────────── */
.jw-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex:none;
  padding:14px 22px;border-bottom:1px solid var(--jw-line);
  background:var(--dsw-alias-bg-base);position:relative;z-index:2}
.jw-head::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,var(--jw-brand),transparent 46%);opacity:.5;
  pointer-events:none}
.jw-title{margin:0;font-size:20px;font-weight:680;letter-spacing:-.02em;line-height:1.35}
.jw-title::before{content:'';display:inline-block;width:9px;height:9px;margin-right:10px;
  border-radius:3px;vertical-align:2px;
  background:linear-gradient(135deg,var(--jw-brand),
    color-mix(in srgb,#2f8f88 78%,var(--jw-brand) 22%));
  box-shadow:0 0 0 3px var(--jw-brand-soft)}
.jw-head>.jw-muted.jw-small{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);
  font-size:12px;color:var(--jw-dim);opacity:.82;
  padding-left:11px;margin-left:3px;border-left:1px solid var(--jw-line-strong);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:40%}

/* ──  控件：统一高度与圆角，hover 只给一点点颜色  ───────────────── */
.jw-btn{font:inherit;font-size:13px;font-weight:500;line-height:1.5;
  min-height:33px;padding:4px 14px;border-radius:9px;
  border:1px solid var(--jw-line-strong);background:var(--jw-surface);color:var(--jw-ink);
  cursor:pointer;white-space:nowrap;
  transition:border-color .18s var(--jw-ease),background .18s var(--jw-ease),
    color .18s var(--jw-ease),box-shadow .18s var(--jw-ease),transform .18s var(--jw-ease),opacity .18s}
.jw-btn:hover:enabled{border-color:color-mix(in srgb,var(--jw-brand) 55%,var(--jw-line-strong));color:var(--jw-brand)}
.jw-btn:active:enabled{transform:translateY(1px)}
.jw-btn:disabled{opacity:.42;cursor:not-allowed}
.jw-btn-primary{background:var(--jw-brand);border-color:transparent;color:#fff;font-weight:600;
  box-shadow:0 1px 2px color-mix(in srgb,var(--jw-brand) 35%,transparent),
    0 8px 18px -12px color-mix(in srgb,var(--jw-brand) 85%,transparent)}
.jw-btn-primary:hover:enabled{background:color-mix(in srgb,var(--jw-brand) 88%,var(--jw-ink) 12%);
  border-color:transparent;color:#fff}
.jw-btn-primary:disabled{background:var(--jw-sunken);border-color:var(--jw-line);
  color:var(--jw-dim);box-shadow:none;opacity:1}
.jw-btn-ghost{background:transparent;border-color:transparent;color:var(--jw-dim)}
.jw-btn-ghost:hover:enabled{background:var(--jw-sunken);border-color:transparent;color:var(--jw-ink)}
.jw-btn-tiny{min-height:27px;padding:2px 9px;font-size:12.5px;border-radius:8px}
.jw-btn-hint{font-size:11.5px;opacity:.6;margin-left:6px}
.jw-btn-hint.ok{color:var(--jw-good);opacity:1}
.jw-select,.jw-input{font:inherit;font-size:13px;min-height:33px;padding:4px 10px;
  border-radius:9px;border:1px solid var(--jw-line-strong);outline:none;
  background:var(--jw-surface);color:var(--jw-ink);max-width:100%;
  transition:border-color .18s var(--jw-ease),box-shadow .18s var(--jw-ease)}
.jw-select:focus,.jw-input:focus{border-color:var(--jw-brand);
  box-shadow:0 0 0 3px var(--jw-brand-soft)}
.jw-table .jw-select,.jw-table .jw-input{min-height:0;padding:3px 8px;font-size:12.5px}

/* ──  分区标题：短竖线 + 一条延伸的细线，给内容一个边界  ─────────── */
.jw-section{display:flex;align-items:center;gap:10px;margin:8px 0 0;
  font-size:12px;font-weight:650;letter-spacing:.12em;color:var(--jw-dim)}
.jw-section::before{content:'';width:3px;height:13px;border-radius:2px;flex:none;
  background:linear-gradient(180deg,var(--jw-brand),
    color-mix(in srgb,#2f8f88 70%,var(--jw-brand) 30%))}
.jw-section::after{content:'';flex:1;height:1px;background:var(--jw-line)}

/* ──  面：卡片、侧栏卡、提示块  ───────────────────────────────────── */
.jw-card{background:var(--jw-surface);border:1px solid var(--jw-line);
  border-radius:var(--jw-r);padding:18px 20px;box-shadow:var(--jw-shadow)}
.jw-card>header{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.jw-card h3{margin:0;font-size:15px;font-weight:660;letter-spacing:.005em}
.jw-split{flex:1;min-height:0;display:flex;align-items:stretch;
  background:var(--jw-sunken);position:relative}
.jw-main{flex:1;min-width:0;overflow:auto;padding:22px clamp(18px,2.2vw,30px) 56px;
  display:flex;flex-direction:column;gap:14px}
.jw-split.collapsed .jw-main{padding-left:clamp(24px,3vw,44px);
  padding-right:clamp(24px,3vw,44px)}
.jw-gutter{flex:none;width:13px;cursor:col-resize;position:relative;background:transparent}
.jw-gutter::before{content:'';position:absolute;left:50%;top:20px;bottom:20px;width:1px;
  transform:translateX(-50%);background:var(--jw-line);
  transition:background .18s var(--jw-ease),width .18s var(--jw-ease)}
.jw-gutter:hover::before,.jw-gutter.dragging::before{width:2px;background:var(--jw-brand)}
.jw-side{flex:none;overflow:auto;padding:22px 20px 40px 10px;
  display:flex;flex-direction:column;gap:12px}
.jw-side-card{background:var(--jw-surface);border:1px solid var(--jw-line);
  border-radius:var(--jw-r);padding:16px 14px;box-shadow:var(--jw-shadow);
  display:flex;flex-direction:column;gap:12px}
.jw-side-head{display:flex;align-items:center;gap:7px;min-width:0;
  padding-bottom:11px;border-bottom:1px solid var(--jw-line)}
.jw-side-title{font-size:14.5px;font-weight:660;letter-spacing:.005em}
.jw-side-head .jw-btn-tiny{min-width:27px;padding:0;font-size:15px;line-height:1;
  border-radius:8px;color:var(--jw-dim)}

/* 任务栏的开合：顶栏一个常驻开关，栏头一个「收起」 */
.jw-side-toggle{display:inline-flex;align-items:center;gap:6px;min-height:31px;
  padding:3px 10px 3px 9px;font-size:12.5px;color:var(--jw-dim)}
.jw-side-toggle .jw-toggle-icon{font-size:13px;line-height:1;margin-top:-1px}
.jw-side-toggle .jw-count{display:inline-flex;align-items:center;justify-content:center;
  min-width:18px;height:18px;padding:0 5px;border-radius:9px;font-size:11.5px;
  background:var(--jw-sunken);color:var(--jw-dim)}
.jw-side-toggle.on{color:var(--jw-brand);
  border-color:color-mix(in srgb,var(--jw-brand) 40%,transparent);
  background:var(--jw-brand-soft)}
.jw-side-toggle.on .jw-count{background:var(--jw-surface);color:var(--jw-brand)}

/* ──  标签与状态：一颗小圆点比一整块颜色轻得多  ─────────────────── */
.jw-chip{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;font-weight:500;
  line-height:1.7;padding:1px 9px;border-radius:999px;white-space:nowrap;
  border:1px solid var(--jw-line);background:var(--jw-sunken);color:var(--jw-dim)}
.jw-chip::before{content:'';width:5px;height:5px;border-radius:50%;
  background:currentColor;opacity:.8;flex:none}
.jw-chip.on{color:var(--jw-good);border-color:color-mix(in srgb,var(--jw-good) 35%,transparent);
  background:color-mix(in srgb,var(--jw-good) 9%,transparent)}
.jw-chip.warn{color:var(--jw-warn);border-color:color-mix(in srgb,var(--jw-warn) 38%,transparent);
  background:color-mix(in srgb,var(--jw-warn) 10%,transparent)}
.jw-chip.bad{color:var(--jw-bad);border-color:color-mix(in srgb,var(--jw-bad) 35%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 9%,transparent)}
.jw-tag{display:inline-flex;align-items:center;font-size:12px;line-height:1.7;
  padding:0 7px;border-radius:6px;border:1px solid var(--jw-line);
  background:var(--jw-sunken);color:var(--jw-dim);white-space:nowrap}
.jw-tag.on{color:var(--jw-good);border-color:color-mix(in srgb,var(--jw-good) 40%,transparent);
  background:color-mix(in srgb,var(--jw-good) 9%,transparent)}
.jw-banner{position:relative;border:1px solid var(--jw-line);border-radius:10px;
  padding:10px 12px 10px 15px;font-size:13px;line-height:1.6;
  background:var(--jw-sunken);color:var(--jw-dim)}
.jw-banner::before{content:'';position:absolute;left:0;top:8px;bottom:8px;width:3px;
  border-radius:0 2px 2px 0;background:var(--jw-line-strong)}
.jw-banner.err{color:var(--jw-bad);border-color:color-mix(in srgb,var(--jw-bad) 32%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 7%,transparent)}
.jw-banner.err::before{background:var(--jw-bad)}
.jw-banner.ok{color:var(--jw-good);border-color:color-mix(in srgb,var(--jw-good) 32%,transparent);
  background:color-mix(in srgb,var(--jw-good) 8%,transparent)}
.jw-banner.ok::before{background:var(--jw-good)}

/* ──  环境卡：一条横向的状态条，两格规格并排，不占半屏  ─────────── */
.jw-env{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 10px;
  padding:15px 18px;position:relative;overflow:hidden}
.jw-env::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:var(--jw-brand);opacity:.55;
  transition:background .2s var(--jw-ease)}
.jw-env:has(.jw-chip.on)::before{background:var(--jw-good)}
.jw-env>header{grid-column:1/-1;align-items:center;margin-bottom:11px}
.jw-env>header .jw-btn-ghost{border-color:var(--jw-line);background:var(--jw-surface);color:var(--jw-ink)}
.jw-env>header .jw-btn-ghost:hover:enabled{border-color:var(--jw-brand);
  background:var(--jw-brand-soft);color:var(--jw-brand)}
.jw-env>.jw-env-line{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);
  font-size:12.5px;line-height:1.5;color:var(--jw-dim);background:var(--jw-sunken);
  border:1px solid var(--jw-line);border-radius:9px;padding:8px 11px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ──  功能卡：横向紧凑一块，一行能放四个，加功能只多一行  ─────────
   高度约 70px：图标在左、名字与状态在右；颜色按序循环，靠左一条竖线认功能。 */
.jw-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
  gap:8px}
.jw-tile{--jw-tone:var(--jw-brand);
  position:relative;display:flex;flex-direction:row;align-items:center;gap:8px;
  text-align:left;min-height:0;max-width:300px;padding:12px 11px 12px 14px;overflow:hidden;
  border:1px solid var(--jw-line);border-radius:12px;
  background:var(--jw-surface);color:var(--jw-ink);font:inherit;cursor:pointer;
  box-shadow:var(--jw-shadow);
  transition:border-color .2s var(--jw-ease),box-shadow .2s var(--jw-ease),
    transform .2s var(--jw-ease),background .2s var(--jw-ease),opacity .2s}
.jw-tile::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:var(--jw-tone);opacity:.85}
.jw-tile:hover{transform:translateY(-2px);box-shadow:var(--jw-lift);
  border-color:color-mix(in srgb,var(--jw-tone) 45%,var(--jw-line))}
.jw-tile:hover .jw-tile-icon{background:color-mix(in srgb,var(--jw-tone) 18%,transparent)}
.jw-tile:focus-visible{outline:2px solid var(--jw-brand);outline-offset:2px}
.jw-tile.off{opacity:.6}
.jw-tile.on{border-color:color-mix(in srgb,var(--jw-brand) 55%,transparent);
  background:color-mix(in srgb,var(--jw-brand) 4%,var(--jw-surface))}
.jw-tile-icon{width:28px;height:28px;flex:none;border-radius:8px;
  display:flex;align-items:center;justify-content:center;
  font-size:15px;font-weight:650;margin:0;
  background:color-mix(in srgb,var(--jw-tone) 12%,transparent);color:var(--jw-tone);
  border:1px solid color-mix(in srgb,var(--jw-tone) 22%,transparent);
  transition:background .2s var(--jw-ease)}
.jw-tile-body{flex:1;display:flex;flex-direction:column;align-items:flex-start;
  gap:4px;min-width:0}
.jw-tile-title{font-size:14px;font-weight:650;letter-spacing:.005em;line-height:1.35;
  max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.jw-tile-body .jw-chip{margin-top:0;font-size:12px;padding:1px 8px}
/* 五个一循环的低饱和色：加功能也不至于整页一个颜色 */
.jw-tile:nth-child(5n+2){--jw-tone:color-mix(in srgb,#2f8f88 88%,var(--jw-ink) 12%)}
.jw-tile:nth-child(5n+3){--jw-tone:color-mix(in srgb,#8a63b8 88%,var(--jw-ink) 12%)}
.jw-tile:nth-child(5n+4){--jw-tone:color-mix(in srgb,#c08a3e 88%,var(--jw-ink) 12%)}
.jw-tile:nth-child(5n+5){--jw-tone:color-mix(in srgb,#4a86b8 88%,var(--jw-ink) 12%)}
/* 「还没建任务 / 依赖未完成」不是错误：颜色只留给状态胶囊 */
.jw-tile.tone-warn .jw-tile-icon{background:color-mix(in srgb,var(--jw-tone) 12%,transparent);
  color:var(--jw-tone);border-color:color-mix(in srgb,var(--jw-tone) 22%,transparent)}
.jw-tile.tone-good{--jw-tone:var(--jw-good)}
.jw-tile.tone-bad{--jw-tone:var(--jw-bad)}

/* ──  右栏任务：当前任务一条竖线，其余只有细边框  ───────────────── */
.jw-tasks{display:flex;flex-direction:column;gap:9px}
.jw-taskrow{position:relative;display:flex;flex-direction:column;gap:6px;
  padding:11px 12px 11px 14px;border-radius:12px;overflow:hidden;
  border:1px solid var(--jw-line);background:var(--dsw-alias-bg-base);
  cursor:pointer;transition:border-color .18s var(--jw-ease),
    background .18s var(--jw-ease),box-shadow .18s var(--jw-ease)}
.jw-taskrow:hover{border-color:var(--jw-line-strong);box-shadow:var(--jw-shadow)}
.jw-taskrow.on{border-color:color-mix(in srgb,var(--jw-brand) 48%,transparent);
  background:color-mix(in srgb,var(--jw-brand) 5%,var(--jw-surface))}
.jw-taskrow.on::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:var(--jw-brand)}
/* 窄栏里任务名不该被操作按钮挤成省略号：让按钮组另起一行 */
.jw-taskrow-head{display:flex;align-items:center;gap:6px;min-width:0;
  flex-wrap:wrap;row-gap:7px}
.jw-taskrow-head>.jw-spacer{flex-basis:100%;height:0;min-width:0}
.jw-taskrow-name{font-size:14px;font-weight:660;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.jw-taskrow-meta{font-size:12.5px;line-height:1.55;color:var(--jw-dim);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.jw-runs{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.jw-runchip{display:inline-flex;align-items:center;gap:5px;font:inherit;font-size:12px;
  padding:2px 8px;border-radius:999px;border:1px solid var(--jw-line);
  background:var(--jw-sunken);color:var(--jw-dim);cursor:pointer;
  transition:border-color .18s var(--jw-ease),color .18s var(--jw-ease),
    background .18s var(--jw-ease)}
.jw-runchip:hover{border-color:var(--jw-brand);color:var(--jw-brand);
  background:var(--jw-brand-soft)}
.jw-runchip.done{color:var(--jw-good);border-color:color-mix(in srgb,var(--jw-good) 35%,transparent)}
.jw-runchip.await{color:var(--jw-warn);border-color:color-mix(in srgb,var(--jw-warn) 38%,transparent)}
.jw-runchip.failed{color:var(--jw-bad);border-color:color-mix(in srgb,var(--jw-bad) 35%,transparent)}

/* ──  空状态：一张纸、一句话、一个明确的动作  ───────────────────── */
.jw-empty{display:flex;flex-direction:column;align-items:center;gap:11px;
  padding:24px 14px 18px;border:1px dashed var(--jw-line-strong);border-radius:12px;
  text-align:center;color:var(--jw-dim);font-size:13px;line-height:1.65;
  background:var(--jw-sunken)}
.jw-empty::before{content:'';width:32px;height:36px;flex:none;border-radius:8px;
  border:1.5px solid color-mix(in srgb,var(--jw-brand) 45%,var(--jw-line-strong));
  background:
    linear-gradient(var(--jw-line-strong),var(--jw-line-strong)) 8px 10px/15px 1.5px no-repeat,
    linear-gradient(var(--jw-line-strong),var(--jw-line-strong)) 8px 16px/15px 1.5px no-repeat,
    linear-gradient(var(--jw-line-strong),var(--jw-line-strong)) 8px 22px/9px 1.5px no-repeat,
    var(--jw-surface);
  box-shadow:0 6px 16px -12px color-mix(in srgb,var(--jw-brand) 70%,transparent);
  margin-bottom:2px}
.jw-empty>div:first-of-type{color:var(--jw-ink);font-size:13.5px;
  font-weight:500;max-width:230px}
.jw-empty .jw-btn{width:100%;margin-top:4px}

/* ──  阶段链 / 配置步骤 / 表单  ───────────────────────────────────── */
.jw-stages{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.jw-stage{display:inline-flex;align-items:center;gap:5px;white-space:nowrap;
  border:1px solid var(--jw-line);background:var(--jw-sunken);
  border-radius:999px;padding:3px 11px;font-size:12.5px}
.jw-stage.done{border-color:color-mix(in srgb,var(--jw-good) 38%,transparent);
  background:color-mix(in srgb,var(--jw-good) 9%,transparent);color:var(--jw-good)}
.jw-stage.awaiting{border-color:color-mix(in srgb,var(--jw-warn) 45%,transparent);
  background:color-mix(in srgb,var(--jw-warn) 10%,transparent);color:var(--jw-warn)}
.jw-stage.failed{border-color:color-mix(in srgb,var(--jw-bad) 38%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 9%,transparent);color:var(--jw-bad)}
.jw-stage.skipped{opacity:.55}
.jw-dot{font-size:11px;color:var(--jw-dim)}
.jw-dot.on{color:var(--jw-good)}
.jw-tabs{display:flex;gap:2px;padding:0 16px;border-bottom:1px solid var(--jw-line);
  flex:none;overflow-x:auto}
.jw-tab{font:inherit;font-size:13px;padding:9px 15px;border:0;
  border-bottom:2px solid transparent;background:transparent;
  color:var(--jw-dim);cursor:pointer;white-space:nowrap;
  transition:color .18s var(--jw-ease),border-color .18s var(--jw-ease)}
.jw-tab:hover{color:var(--jw-ink)}
.jw-tab.on{color:var(--jw-brand);border-bottom-color:var(--jw-brand);font-weight:600}
.jw-drop{display:block;border:1px dashed var(--jw-line-strong);border-radius:12px;
  padding:22px;text-align:center;cursor:pointer;background:var(--jw-sunken);
  font-size:13.5px;
  transition:border-color .18s var(--jw-ease),background .18s var(--jw-ease)}
.jw-drop:hover{border-color:var(--jw-brand);background:var(--jw-brand-soft)}
.jw-map{display:grid;grid-template-columns:minmax(96px,140px) minmax(0,1fr) auto;
  gap:8px 10px;align-items:center}
.jw-map .jw-req{color:var(--jw-bad);margin-left:2px}
.jw-modes{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.jw-mode{display:flex;flex-direction:column;gap:3px;text-align:left;padding:12px 14px;
  border-radius:12px;border:1px solid var(--jw-line);background:var(--jw-sunken);
  color:var(--jw-ink);font:inherit;cursor:pointer;
  transition:border-color .18s var(--jw-ease),box-shadow .18s var(--jw-ease)}
.jw-mode strong{font-size:13.5px}
.jw-mode:hover{border-color:var(--jw-line-strong)}
.jw-mode.on{border-color:var(--jw-brand);background:var(--jw-brand-soft);
  box-shadow:0 0 0 3px var(--jw-brand-soft)}
.jw-step{display:flex;gap:10px;padding:10px 0;border-bottom:1px dashed var(--jw-line);
  align-items:flex-start}
.jw-step:last-child{border-bottom:0;padding-bottom:0}
.jw-step-body{min-width:0;display:flex;flex-direction:column;gap:2px}
.jw-step-title{font-weight:600;font-size:13px}
.jw-step-no{width:23px;height:23px;flex:none;border-radius:50%;display:flex;
  align-items:center;justify-content:center;font-size:12px;
  border:1px solid var(--jw-line-strong);color:var(--jw-dim);background:var(--jw-sunken)}
.jw-step-no.ok{border-color:color-mix(in srgb,var(--jw-good) 50%,transparent);
  color:var(--jw-good);background:color-mix(in srgb,var(--jw-good) 10%,transparent)}
.jw-confirm{display:flex;gap:8px;align-items:center;flex-wrap:wrap;
  margin-top:12px;padding:10px 12px;border-radius:10px;
  background:var(--jw-sunken);border:1px dashed var(--jw-line-strong);font-size:13px}
.jw-choice{margin-top:14px;padding:12px 14px;border-radius:10px;
  border:1px dashed var(--jw-line-strong);background:var(--jw-sunken)}

/* ──  选功能 / 上传 / 结果里的块  ────────────────────────────────── */
.jw-picks{display:flex;flex-direction:column;gap:9px}
.jw-pick{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;
  align-items:center;width:100%;text-align:left;padding:11px 14px;border-radius:12px;
  border:1px solid var(--jw-line);background:var(--jw-surface);
  color:var(--jw-ink);font:inherit;cursor:pointer;
  transition:border-color .18s var(--jw-ease),box-shadow .18s var(--jw-ease),
    background .18s var(--jw-ease),opacity .18s}
.jw-pick:hover:not(.off){border-color:color-mix(in srgb,var(--jw-brand) 50%,var(--jw-line))}
.jw-pick.on{border-color:var(--jw-brand);background:var(--jw-brand-soft);
  box-shadow:0 0 0 3px var(--jw-brand-soft)}
.jw-pick.off{opacity:.55;cursor:not-allowed}
.jw-pick-name{font-size:13.5px;font-weight:650}
.jw-pick-desc{grid-column:1/-1;font-size:12.5px;line-height:1.5;color:var(--jw-dim)}
.jw-pick-note{grid-column:1/-1;font-size:12px;color:var(--jw-warn)}
.jw-bars{display:flex;flex-direction:column;gap:5px}
.jw-bar{display:grid;grid-template-columns:minmax(72px,120px) minmax(0,1fr) 56px;
  gap:8px;align-items:center;font-size:12.5px}
.jw-bar-track{height:7px;border-radius:4px;background:var(--jw-sunken);overflow:hidden}
.jw-bar-fill{display:block;height:100%;border-radius:4px;
  background:var(--jw-brand);opacity:.85}
.jw-stat{display:inline-flex;flex-direction:column;gap:1px;min-width:96px;
  padding:8px 13px;border-radius:10px;border:1px solid var(--jw-line);
  background:var(--jw-sunken)}
.jw-stat b{font-size:18px;font-variant-numeric:tabular-nums}
.jw-log,.jw-code{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);
  font-size:12px;color:var(--jw-dim);background:var(--jw-sunken);
  border:1px solid var(--jw-line);border-radius:9px}
.jw-log{margin:0;padding:9px 11px;max-height:180px;overflow:auto;white-space:pre-wrap}
.jw-code{display:block;padding:8px 11px;white-space:pre-wrap;word-break:break-all;
  flex:1;min-width:0}
.jw-env-row{display:grid;grid-template-columns:minmax(120px,180px) minmax(0,1fr) auto;
  gap:4px 12px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--jw-line);
  font-size:13px}
.jw-env-row:last-child{border-bottom:0}

/* ──  复核表：表头吸附、按内容自适应，不锁列宽  ───────────────────
   锁了就会顶出卡片（格子有 padding，算不准）。只保两件必须的：
   凭证号不折行（折了就看不出是哪张）、依据框别被压成一条缝。 */
.jw-tablewrap{max-height:58vh;overflow:auto;background:var(--jw-surface);
  border:1px solid var(--jw-line);border-radius:12px;
  scrollbar-width:thin;scrollbar-color:var(--jw-line-strong) transparent}
.jw-table{width:100%;border-collapse:collapse;font-size:13px}
.jw-table th,.jw-table td{border-bottom:1px solid var(--jw-line);
  padding:7px 9px;text-align:left;vertical-align:top}
.jw-table td:first-child .jw-mono{white-space:nowrap}
.jw-table td:nth-child(3){min-width:150px}
/* 现状一列是「桶 + 置信度 + 概率」的短词，字变大后不能被挤成竖排 */
.jw-table td:nth-child(4){min-width:92px;white-space:nowrap}
.jw-table td:nth-child(6){min-width:106px}
.jw-table td:nth-child(6) .jw-input{width:100%;min-width:84px}
.jw-table th{position:sticky;top:0;z-index:1;background:var(--jw-sunken);
  font-weight:600;font-size:12.5px;white-space:nowrap}
.jw-table tr:last-child td{border-bottom:0}
.jw-table td.jw-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.jw-table tbody tr:nth-child(even){background:color-mix(in srgb,var(--jw-ink) 2%,transparent)}
.jw-table tbody tr:hover{background:var(--jw-brand-soft)}
.jw-entries{margin:0;padding-left:15px;color:var(--jw-dim);white-space:pre-wrap;font-size:12.5px}
.jw-entries li{list-style:disc}`

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

    function ReviewTable({ review, submitted, t, onSubmit, onWaive, onWake, busy }) {
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
            className: 'jw-btn',
            disabled: !!busy || changed.length === 0,
            onClick: () => onSubmit(changed),
          }, `${t('submit')} (${changed.length})`),
          review.truncated && h('span', { className: 'jw-muted jw-small' },
            `${review.total - filtered.length} ${t('more')}`)),

        // 停到复核位不会自动叫人：要么点这里让 AI 接手，要么直接往下走
        h('div', { className: 'jw-choice' },
          h('div', { className: 'jw-muted jw-small' }, t('reviewChoice')),
          h('div', { className: 'jw-row', style: { marginTop: 8 } },
            h('button', {
              className: 'jw-btn jw-btn-primary',
              disabled: !!busy,
              onClick: onWake,
            }, t('wake')),
            h('button', {
              className: 'jw-btn',
              disabled: !!busy,
              onClick: onWaive,
            }, t('waive')))))
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
        className: `jw-tile${state.disabled ? ' off' : ''}${active ? ' on' : ''}`
          + `${state.tone ? ` tone-${state.tone}` : ' tone-ready'}`,
        title: [feature.description, state.note].filter(Boolean).join(' · '),
        onClick: onOpen,
      },
      h('span', { className: 'jw-tile-icon', 'aria-hidden': 'true' }, feature.name.slice(0, 1)),
      h('span', { className: 'jw-tile-body' },
        h('span', { className: 'jw-tile-title' }, feature.name),
        h('span', { className: chipClass(state) }, state.chip)))
    }

    // ── 右栏：任务 ──────────────────────────────────────────────────────
    /**
     * 环境卡：配一次就不动的东西，放主区左上角，不占右栏（右栏全是任务）。
     */
    function EnvCard({ t, env, onOpen }) {
      const ok = !!env?.ready?.model
      const torch = env?.packages?.torch?.version
      return h('section', { className: 'jw-card jw-env' },
        h('header', null,
          h('h3', null, t('envTab')),
          h('span', { className: `jw-chip${ok ? ' on' : ' warn'}` },
            !env ? t('connectingShort') : ok ? t('envReady') : t('envNotReady')),
          h('span', { className: 'jw-spacer' }),
          h('button', { className: 'jw-btn jw-btn-ghost', onClick: onOpen },
            ok ? t('open') : t('fix'))),
        h('div', { className: 'jw-env-line' },
          `Python ${env?.pythonVersion || '—'} · ${env?.venv?.exists ? 'venv ✓' : 'venv ✗'}`),
        h('div', { className: 'jw-env-line' },
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
    function TaskRail({ api, t, tasks, features, activeId, onChanged, onUse, onOpenFeature, onNew, onCollapse, onError }) {
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
          // 点一下整栏收掉，主区拿回全部宽度；顶栏那个开关负责再叫回来
          h("button", {
            className: "jw-btn jw-btn-ghost jw-btn-tiny",
            onClick: () => onCollapse?.(),
            title: t("collapse"),
            "aria-label": t("collapse"),
          }, "»"),
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
        return { chip: t('envNotReady'), chipOn: false, disabled: true, startDisabled: true, note: t('cardEnv'), tone: 'warn' }
      }
      if (!task) {
        return { chip: t('cardNoTask'), chipOn: false, disabled: true, startDisabled: true, note: t('cardNoTaskHint'), tone: 'warn' }
      }
      const runs = task.runs || {}
      const missing = (FEATURE_DEPS[feature.id] || []).filter((dep) => runs[dep]?.status !== 'done')
      if (missing.length) {
        const names = missing.map((dep) => (features || []).find((item) => item.id === dep)?.name || dep)
        return {
          chip: t('cardBlocked'), chipOn: false, disabled: true, startDisabled: true,
          note: `${t('cardDeps')}${names.join('、')}`, tone: 'warn',
        }
      }
      const run = runs[feature.id]
      if (!run) return { chip: t('cardReady'), chipOn: false, disabled: false, startDisabled: false, note: '', tone: '' }
      // 没跑完（刚建 / 在跑 / 等复核）就不要再点第二个：后端的会挡，但让按钮先说话。
      // 注意只挡「开始处理」，不挡点开这个功能——等复核时正是要进去看。
      const active = run.status === 'created' || run.status === 'running'
        || run.status === 'awaiting_review'
      return {
        chip: statusWord(t, run.status),
        chipOn: run.status === 'done',
        disabled: false,
        startDisabled: active,
        note: run.awaiting ? `${t('awaiting')}：${run.awaiting}` : '',
        // 等你复核也是"要你动手"，和失败一样该有色；只有"在跑"留中性
        tone: run.status === 'failed' ? 'bad'
          : run.status === 'done' ? 'good'
            : active || run.status === 'aborted' ? 'warn' : '',
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
      // 右栏也可以整个收掉：账跑完、只想点功能的时候，把宽度全让给主区
      const [sideCollapsed, setSideCollapsed] = useState(readSideCollapsed)
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
          const list = data.jobs || []
          setJobs(list)
          setRepo(data.repo || '')
          setBooting(false)
          setError('')
          bootRef.current.done = true
          return list
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
            return null
          }
          setError(caught.message)
          return null
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

      // 轮询：有东西在算就快一点（1.5s），闲着就慢（5s）。
      // 功能之间的依赖（"清洗跑完现金流才能开始"）也从这份结果里算，
      // 所以清洗一跑完，下一个 tick 就能开始——不用等下一次别的东西刷新。
      useEffect(() => {
        let stopped = false
        let timer = null
        const tick = async () => {
          if (stopped) return
          const list = await loadJobs()
          if (stopped) return
          const busy = (list || []).some((item) => item.status === 'running'
            || item.status === 'created')
          timer = window.setTimeout(tick, busy ? 1500 : 5000)
        }
        timer = window.setTimeout(tick, 5000)
        return () => {
          stopped = true
          if (timer) window.clearTimeout(timer)
        }
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

      // 收起状态也要记住：下次进页面还是窄/宽的那个选择
      useEffect(() => {
        try {
          window.localStorage?.setItem(COLLAPSE_KEY, sideCollapsed ? '1' : '0')
        } catch {
          // 存不了就算了，下次默认展开
        }
      }, [sideCollapsed])

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

      /**
       * 每个任务"跑过哪些功能、到哪一步"——从 `jobs` 现算，不等 `/tasks`。
       *
       * `/tasks` 只在建/删任务、交结论、中止时拉一次；靠它的话"清洗跑完了，
       * 现金流还显示不能开始"，要等下一次拉任务才知道。`jobs` 是每 1.5~5 秒
       * 轮询的，永远比它新——依赖状态用它算，就不会有那段空等。
       *
       * 老运行（没有 task 归属的）从 `/tasks` 带的 runs 里留着，不被覆盖掉。
       */
      const viewTasks = useMemo(() => tasks.map((doc) => {
        const runs = { ...(doc.runs || {}) }
        for (const job of jobs) {
          if (job.task !== doc.id) continue
          const prev = runs[job.feature]
          if (!prev || String(job.created_at) > String(prev.created_at)) {
            runs[job.feature] = job
          }
        }
        return { ...doc, runs }
      }), [tasks, jobs])

      const task = viewTasks.find((item) => item.id === taskId) || viewTasks[0] || null

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

      /**
       * 中止：先杀进程再改状态（后端保证顺序），这里只管按下去。
       *
       * 中止之后 `epoch` 加一，轮询按新状态重新开始——aborted 不是"还在跑"，
       * 轮询会自己停下来，不会留一个永远转的圈。
       */
      const stop = useCallback(async () => {
        if (!jobId) return
        setBusy(true)
        setError('')
        try {
          await api.post('/stop', { job_id: jobId })
          setNotice(t('stopped'))
          setEpoch((value) => value + 1)
          loadJobs()
          if (task) loadTasks(task.id)
        } catch (caught) {
          setError(caught.message)
        } finally {
          setBusy(false)
        }
      }, [api, jobId, loadJobs, loadTasks, t, task])

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
        openFeature(featureId, viewTasks.find((item) => item.id === ownerId) || null)
      }

      /** 建任务：起名字 + 配账，落在首页之前的那一步。 */
      const openCreate = () => {
        setView('new')
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
        // 没有全局「开始处理」：开始是功能自己的事，进到哪个功能就只跑哪个
        h('button', {
          className: 'jw-btn',
          onClick: () => openCreate(),
        }, t('newJob')),
        h('button', {
          className: 'jw-btn jw-btn-ghost',
          onClick: () => { loadJobs(); loadTasks(); loadEnv(true) },
        }, t('refresh')),
        // 常驻开关：右栏收起后就靠它把任务列表叫回来
        h('button', {
          className: `jw-btn jw-btn-ghost jw-side-toggle${sideCollapsed ? ' on' : ''}`,
          title: sideCollapsed ? t('taskTab') : t('collapse'),
          'aria-expanded': sideCollapsed ? 'false' : 'true',
          onClick: () => setSideCollapsed((value) => !value),
        },
        h('span', { className: 'jw-toggle-icon', 'aria-hidden': 'true' },
          sideCollapsed ? '»' : '«'),
        h('span', null, t('taskTab')),
        tasks.length > 0 && h('span', { className: 'jw-count' }, String(tasks.length))))

      const body = []
      body.push(booting && !error && h('div', { className: 'jw-banner' }, t('connecting')))
      body.push(error && h('div', { className: 'jw-banner err' }, error))
      body.push(notice && !error && h('div', { className: 'jw-banner ok' }, notice))

      if (view === 'home') {
        // 主区 = 环境 + 功能。右栏全是任务：切任务是换一份账，跟功能不是一回事。
        body.push(h(EnvCard, { t, env, onOpen: () => setView('env') }))
        body.push(h('div', { className: 'jw-section', style: { margin: '4px 0 0' } }, t('sectionFeature')))
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

      /** 在这个功能里开工：只用当前任务的账跑这一个功能，不给人「还能选别的」的错觉。 */
      const startFeature = async (featureId) => {
        if (!task) return
        setBusy(true)
        setError('')
        try {
          const data = await api.post('/start', {
            feature: featureId,
            file: task.file,
            mapping: task.mapping || {},
            task: task.id,
          })
          setJobId(data.job_id)
          setEpoch((value) => value + 1)
          loadJobs()
          loadTasks(task.id)
        } catch (error) {
          setError(`${t('start')}: ${error.message}`)
        } finally {
          setBusy(false)
        }
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
          // 在跑就给一个停的出口。以前只能关页面，进程还在吃 GPU。
          (job?.status === 'running' || job?.stalled) && h('button', {
            className: 'jw-btn jw-btn-ghost',
            disabled: !!busy,
            onClick: stop,
          }, busy ? t('stopping') : t('stop')),
          h('button', {
            className: 'jw-btn jw-btn-primary',
            disabled: state.disabled || state.startDisabled || !!busy,
            onClick: () => startFeature(currentFeature.id),
          }, busy ? t('starting') : t('startRun'))),
        !job && !tabJobs.length && state.note
          && h('div', { className: 'jw-banner err' }, state.note),
        !job && tabJobs.length > 0 && h('div', { className: 'jw-muted jw-small' }, t('empty')),
        job && h(JobView, {
          job, chain, review, api, t, busy,
          onDecide: decide, onWaive: waive, onWake: wake,
          onResume: resume, onStop: stop,
        })))
      }

      return h('div', { className: 'jw-root' }, h(StyleTag, null), head,
        h('div', { className: `jw-split${sideCollapsed ? ' collapsed' : ''}` },
          h('main', { className: 'jw-main' },
            // body 是个动态数组：不补 key，React 每轮都警告一遍
            body.filter(Boolean).map((node, index) => React.cloneElement(node, {
              key: node.key || `block-${index}`,
            }))),
          // 收起后连分隔条一起拿掉：该占满就占满，不留一条假边框
          !sideCollapsed && h('div', {
            className: `jw-gutter${dragOn ? ' dragging' : ''}`,
            role: 'separator',
            'aria-orientation': 'vertical',
            title: t('resizeHint'),
            onPointerDown: startDrag,
            onDoubleClick: () => setSideW(SIDE_DEFAULT),
          }),
          !sideCollapsed && h('aside', { className: 'jw-side', style: { width: sideW } },
            h(TaskRail, {
              api, t, tasks: viewTasks, features,
              activeId: task?.id || '',
              onChanged: async (selectId) => { await loadTasks(selectId); await loadJobs() },
              onUse: (id) => setTaskId(id),
              onOpenFeature: openFeatureFor,
              onNew: openCreate,
              onCollapse: () => setSideCollapsed(true),
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
    function JobView({ job, chain, review, api, t, busy, onDecide, onWaive, onWake, onResume, onStop }) {
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
          // 状态还说"正在跑"、后台却没有这个进程：多半是被中止过或插件重启过。
          // 不写清楚，页面就一直转圈，人只会以为"卡了"。
          job.stalled && h('div', { className: 'jw-banner err', style: { marginTop: 10 } },
            t('stalled'),
            h('div', { className: 'jw-small' }, t('stalledHint'))),
          (job.status === 'failed' || job.status === 'aborted' || job.stalled)
            && h('div', { className: 'jw-row', style: { marginTop: 8 } },
              h('button', { className: 'jw-btn', disabled: !!busy, onClick: onResume }, t('resume')),
              job.stalled && h('button', {
                className: 'jw-btn jw-btn-ghost', disabled: !!busy, onClick: onStop,
              }, t('stop'))),
          job.status === 'running' && !job.stalled && h('div', { className: 'jw-row', style: { marginTop: 8 } },
            h('button', { className: 'jw-btn jw-btn-ghost', disabled: !!busy, onClick: onStop },
              t('stop'))),
          job.status === 'aborted' && h('div', { className: 'jw-muted jw-small', style: { marginTop: 10 } },
            `${t('stopped')}。${t('stalledHint')}`),
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
        }),

        job.status === 'done' && h(ResultPanel, { job, t, api }),

        !job.running && !['awaiting_review', 'done', 'failed', 'aborted'].includes(job.status)
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

      // 两处都挂：
      //   · 左侧边栏一个图标（主区整页）——工作台不属于某个对话，它是账自己的地方；
      //   · 对话里一个页签——就在当前会话里看，不用先离开对话。
      // 用的是同一个组件，数据、任务、进度完全同一份；区别只在进的门。
      ctx.slots.inject('main', () => ctx.slots.register({
        name: 'main',
        key: NS,
        locale: NS,
      }, Workbench))

      ctx.slots.inject('conversation.view', () => ctx.slots.register({
        name: 'conversation.view',
        id: 'journal',
        order: 40,
        label: () => translate('tab'),
        locale: NS,
      }, Workbench))

      ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
        name: 'sidebar.panellist',
        id: NS,
        order: 30,
        label: () => translate('tab'),
        locale: NS,
      }, function JournalPanelIcon(props) {
        const size = typeof props?.size === 'number' ? props.size : 18
        return h('svg', {
          width: size,
          height: size,
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: 1.6,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
        },
        h('path', { d: 'M5 3h11l3 4v14H5z' }),
        h('path', { d: 'M9 9h6M9 13h6M9 17h4' }))
      }))
    }

    return {
      name: NS,
      inject: ['slots', 'locale'],
      apply,
      // 离线渲染测试用（浏览器里没人读它）：没有浏览器时靠它验证组件不炸。
      __internals: { Workbench, JobView, Stages, ReviewTable, ResultPanel, EnvPanel,
        TaskCreate, TaskRail, EnvCard, FeatureTile, Detail,
        featureState, chipClass, DICT, translate },
    }
  },
})
