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
      // 管线：对方科目 → 清洗 → 现金流 / 分录测试
      // 清洗接对方科目，所以交付物里一行同时有「业务分类」和「对方科目」；
      // 现金流与分录测试都只接清洗——它们要的东西清洗已经带出来了，不必各接两个上游。
      contra: [],
      cleaning: ['contra'],
      cashflow: ['cleaning'],
      entry_test: ['cleaning'],
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
        // ── 分层 / 新建任务（域在前，功能在后）──
        pickDomain: '你要处理什么数据？',
        pickDomainHint: '一份数据配一次；同一层下的功能都从这份数据起跑',
        uploadTo: '上传{name}',
        changeDomain: '换一层',
        reqDone: '必需字段已识别',
        reqMissing: '还缺',
        expand: '展开调整',
        fold: '收起',
        unusedCols: '列未使用',
        unusedHint: '不参与计算，导出时原样保留',
        soon: '未接入',
        soonHint: '这个功能还没接入，先把位置占上',
        domainNoFeature: '这一层还没有可用的功能，配好的数据暂时没人能跑',
        newJobShort: '＋ 新建',
        domainCount: '{n} 个功能',
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
        usesUpstream: '接着「{name}」的结果跑，已经做过的不再重做',
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
        // ── 产物预览 ──
        preview: '预览',
        previewLoading: '正在读这张表…',
        previewFail: '读不出这张表',
        previewRetry: '再试一次',
        previewSearch: '搜索这张表',
        previewEmpty: '这张表没有数据',
        previewNone: '没搜到「{q}」',
        previewRange: '第 {a}–{b} 行 / 共 {n} 行',
        previewHits: '命中 {n} 行',
        previewTruncated: '表太大，预览只含前 {n} 行（原表 {m} 行）；完整内容用下载',
        previewReadonly: '只读 · 点单元格复制',
        previewCopied: '已复制：{text}',
        reportNote: '报告就在下面，看完用 PDF 归档',
        reportFrame: '会计分录测试报告',
        detailFile: '序时账（含异常标记）',
        // ── 配账：科目对应（归一化之后仍不是标准科目的写法）──
        alignTitle: '先对一下科目',
        alignHint: '这些写法不是标准一级科目（客户名、银行账户名、临时科目…）。挑一个标准科目，'
          + '点确认就写进全局别名表——以后别的账里同样的写法也按它归一。不确认也能往下跑，'
          + '只是这些行少了科目信号。',
        alignVouchers: '{n} 张凭证',
        alignPick: '—— 选标准科目 ——',
        alignApply: '写入别名表并重新识别',
        alignSkip: '先不管，直接开始',
        rows: '行',
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
        taskDeleteNote: '永久删除「{name}」？它的账、{n} 次运行记录和产物会一起删掉，不能恢复。',
        trashLeft: '回收站残留 {n} 项 · {size}',
        trashNote: '旧版本删任务时留下的；现在删除是直接删干净，不会再往这里放东西',
        trashPurge: '清空',
        trashPurgeSure: '确认清空',
        trashPurged: '已清空回收站，释放 {size}',
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
        pickDomain: 'What data are you working on?',
        pickDomainHint: 'Configure it once; every feature in that layer runs on it',
        uploadTo: 'Upload {name}',
        changeDomain: 'Switch layer',
        reqDone: 'Required fields found',
        reqMissing: 'Still missing',
        expand: 'Adjust',
        fold: 'Done',
        unusedCols: 'columns unused',
        unusedHint: 'not used in the calculation, kept as-is on export',
        soon: 'Not wired',
        soonHint: 'This feature is not wired up yet',
        domainNoFeature: 'No usable feature in this layer yet, so a task would have nothing to run',
        newJobShort: '+ New',
        domainCount: '{n} features',
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
        usesUpstream: 'Runs on the results of "{name}" — finished steps are not redone',
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
        preview: 'Preview',
        previewLoading: 'Reading the sheet…',
        previewFail: 'Could not read this sheet',
        previewRetry: 'Try again',
        previewSearch: 'Search this sheet',
        previewEmpty: 'This sheet is empty',
        previewNone: 'No match for "{q}"',
        previewRange: 'Rows {a}–{b} of {n}',
        previewHits: '{n} matches',
        previewTruncated: 'Too large: preview holds the first {n} of {m} rows. Download for the rest',
        previewReadonly: 'Read-only · click a cell to copy',
        previewCopied: 'Copied: {text}',
        reportNote: 'The report is below; take the PDF for the file',
        reportFrame: 'Journal entry testing report',
        detailFile: 'Ledger with flags (Excel)',
        alignTitle: 'Map unknown subjects',
        alignHint: 'These names are not standard level-1 subjects (customer names, bank accounts, '
          + 'temporary accounts...). Pick the standard subject they belong to and confirm — the '
          + 'mapping goes into the global alias table, so other ledgers with the same wording fold '
          + 'the same way. You can also skip it and run as is; those rows just lose the subject signal.',
        alignVouchers: '{n} vouchers',
        alignPick: '— pick a standard subject —',
        alignApply: 'Save mapping and re-read',
        alignSkip: 'Skip, start anyway',
        rows: 'rows',
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
        taskDeleteNote: 'Delete “{name}” for good? Its ledger, {n} run(s) and artifacts are removed — this cannot be undone.',
        trashLeft: 'Old trash: {n} item(s) · {size}',
        trashNote: 'Left over from older versions. Deleting a task now removes it outright.',
        trashPurge: 'Empty',
        trashPurgeSure: 'Empty it',
        trashPurged: 'Trash emptied, {size} freed',
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

      const get = (path, params) => fetch(`${API}${path}${qs({ ...params, sid: getSessionId() })}`, {
        headers: { accept: 'application/json' },
      }).then(read)

      return {
        get,
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
        /** 同一份产物的"打开"版本：不带头部的 attachment，给 iframe 用。 */
        openUrl(jobId, key) {
          return `${API}/download${qs({ id: jobId, key, sid: getSessionId(), inline: 1 })}`
        },
        /** 产物预览的一窗数据：`sheet` 给空就落在这份文件的第一张表。 */
        preview(jobId, key, params) {
          return get('/preview', { id: jobId, key, ...params })
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

    /**
     * 一个运行的轮询间隔（0 = 不用再问了）。
     *
     * `created` 必须算"还在跑"：`journal create` 建完就返回，python 真正写下
     * `running` 还要一两秒（起进程 + import + 读表），而页面第一次问恰好落在
     * 这中间。以前没算 created → 间隔 0 → 轮询停住，页面永远停在"刚建好"那一页，
     * 只能退出功能再进来才刷新（用户看到的就是"页面滞后"）。
     */
    function pollDelay(status) {
      if (status === 'created') return 500
      if (status === 'running') return 1000
      if (status === 'awaiting_review') return 4000
      return 0
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
  --jw-line:color-mix(in srgb,var(--jw-ink) 9%,transparent);
  --jw-line-strong:color-mix(in srgb,var(--jw-ink) 16%,transparent);
  --jw-surface:var(--dsw-alias-bg-layer-1);
  --jw-sunken:color-mix(in srgb,var(--jw-ink) 3.5%,var(--dsw-alias-bg-base));
  --jw-brand:color-mix(in srgb,#4c63c4 92%,var(--jw-ink) 8%);
  --jw-brand-soft:color-mix(in srgb,var(--jw-brand) 9%,transparent);
  /* 状态色都掺一点中性灰：不复读系统色，也不会在浅底上发刺 */
  --jw-good:color-mix(in srgb,var(--dsw-alias-state-success-primary) 88%,var(--jw-dim) 12%);
  --jw-warn:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 75%,var(--jw-dim) 25%);
  --jw-bad:color-mix(in srgb,var(--dsw-alias-state-error-primary) 90%,var(--jw-dim) 10%);
  --jw-r:16px;
  --jw-shadow:0 1px 2px color-mix(in srgb,var(--jw-ink) 3%,transparent),
    0 6px 16px -12px color-mix(in srgb,var(--jw-ink) 20%,transparent);
  --jw-lift:0 2px 6px color-mix(in srgb,var(--jw-ink) 5%,transparent),
    0 20px 40px -28px color-mix(in srgb,var(--jw-ink) 32%,transparent);
  --jw-ease:cubic-bezier(.33,0,.2,1);
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
.jw-title::before{content:'';display:inline-block;width:10px;height:10px;margin-right:11px;
  border-radius:50%;vertical-align:1px;
  background:linear-gradient(135deg,var(--jw-brand),
    color-mix(in srgb,#2f8f88 65%,var(--jw-brand) 35%));
  box-shadow:0 0 0 4px var(--jw-brand-soft)}
.jw-head>.jw-muted.jw-small{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);
  font-size:12px;color:var(--jw-dim);opacity:.82;
  padding-left:11px;margin-left:3px;border-left:1px solid var(--jw-line-strong);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:40%}

/* ──  控件：统一高度与圆角，hover 只给一点点颜色  ───────────────── */
.jw-btn{font:inherit;font-size:13px;font-weight:500;line-height:1.5;
  min-height:33px;padding:4px 14px;border-radius:10px;
  border:1px solid var(--jw-line-strong);background:var(--jw-surface);color:var(--jw-ink);
  cursor:pointer;white-space:nowrap;
  transition:border-color .22s var(--jw-ease),background .22s var(--jw-ease),
    color .22s var(--jw-ease),box-shadow .22s var(--jw-ease),
    transform .22s var(--jw-ease),opacity .22s}
.jw-btn:hover:enabled{border-color:color-mix(in srgb,var(--jw-brand) 38%,var(--jw-line-strong));
  background:color-mix(in srgb,var(--jw-brand) 5%,var(--jw-surface));color:var(--jw-brand)}
.jw-btn:active:enabled{transform:translateY(1px)}
.jw-btn:disabled{opacity:.45;cursor:not-allowed}
.jw-btn-primary{background:linear-gradient(180deg,
  color-mix(in srgb,var(--jw-brand) 86%,#fff 14%),var(--jw-brand));
  border-color:transparent;color:#fff;font-weight:600;
  box-shadow:0 1px 2px color-mix(in srgb,var(--jw-brand) 25%,transparent),
    0 10px 20px -14px color-mix(in srgb,var(--jw-brand) 70%,transparent)}
.jw-btn-primary:hover:enabled{background:linear-gradient(180deg,
  color-mix(in srgb,var(--jw-brand) 78%,#fff 22%),
  color-mix(in srgb,var(--jw-brand) 94%,var(--jw-ink) 6%));
  border-color:transparent;color:#fff}
.jw-btn-primary:disabled{background:var(--jw-sunken);border-color:var(--jw-line);
  color:var(--jw-dim);box-shadow:none;opacity:1}
.jw-btn-ghost{background:transparent;border-color:transparent;color:var(--jw-dim)}
.jw-btn-ghost:hover:enabled{background:var(--jw-sunken);border-color:transparent;
  color:var(--jw-ink)}
.jw-btn-tiny{min-height:27px;padding:2px 9px;font-size:12.5px;border-radius:9px}
.jw-btn-icon{width:24px;min-width:24px;padding:0;font-size:14px;line-height:1;
  display:inline-flex;align-items:center;justify-content:center}
/* 破坏性操作：平时是淡红的「可以点」，确认后变成实心红「真的要删」 */
.jw-btn-danger{color:var(--jw-bad);
  border-color:color-mix(in srgb,var(--jw-bad) 26%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 6%,transparent)}
.jw-btn-danger:hover:enabled{color:var(--jw-bad);
  border-color:color-mix(in srgb,var(--jw-bad) 46%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 12%,transparent)}
.jw-btn-danger-solid{background:linear-gradient(180deg,
  color-mix(in srgb,var(--jw-bad) 88%,#fff 12%),var(--jw-bad));
  border-color:transparent;color:#fff;font-weight:600;
  box-shadow:0 1px 2px color-mix(in srgb,var(--jw-bad) 30%,transparent),
    0 8px 16px -12px color-mix(in srgb,var(--jw-bad) 70%,transparent)}
.jw-btn-danger-solid:hover:enabled{background:linear-gradient(180deg,
  color-mix(in srgb,var(--jw-bad) 82%,#fff 18%),
  color-mix(in srgb,var(--jw-bad) 94%,var(--jw-ink) 6%));
  border-color:transparent;color:#fff}
.jw-btn-hint{font-size:11.5px;opacity:.6;margin-left:6px}
.jw-btn-hint.ok{color:var(--jw-good);opacity:1}
.jw-select,.jw-input{font:inherit;font-size:13px;min-height:33px;padding:4px 10px;
  border-radius:9px;border:1px solid var(--jw-line-strong);outline:none;
  background:var(--jw-surface);color:var(--jw-ink);max-width:100%;
  transition:border-color .22s var(--jw-ease),box-shadow .22s var(--jw-ease)}
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
  transition:background .22s var(--jw-ease),width .22s var(--jw-ease)}
.jw-gutter:hover::before,.jw-gutter.dragging::before{width:2px;background:var(--jw-brand)}
.jw-side{flex:none;overflow:auto;padding:22px 20px 40px 10px;
  display:flex;flex-direction:column;gap:12px}
.jw-side-card{background:var(--jw-surface);border:1px solid var(--jw-line);
  border-radius:var(--jw-r);padding:16px 14px;box-shadow:var(--jw-shadow);
  display:flex;flex-direction:column;gap:12px}
.jw-side-head{display:flex;align-items:center;gap:7px;min-width:0;
  padding-bottom:11px;border-bottom:1px solid var(--jw-line)}
.jw-side-title{font-size:14.5px;font-weight:660;letter-spacing:.005em}
.jw-side-head .jw-btn-tiny{min-width:28px;padding:0;font-size:15px;line-height:1;
  border-radius:9px;color:var(--jw-dim);
  background:var(--jw-sunken);border-color:var(--jw-line)}
.jw-side-head .jw-btn-tiny:hover:enabled{color:var(--jw-brand);
  border-color:color-mix(in srgb,var(--jw-brand) 40%,transparent);
  background:var(--jw-brand-soft)}

/* 任务栏的开合：顶栏一个常驻开关，栏头一个「收起」 */
.jw-side-toggle{display:inline-flex;align-items:center;gap:6px;min-height:31px;
  padding:3px 10px 3px 9px;font-size:12.5px;color:var(--jw-dim);
  background:var(--jw-sunken);border-color:var(--jw-line)}
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
/* 「当前任务」是身份，不是状态：用品牌色，才不会被绿色当成"成功" */
.jw-chip.current{color:var(--jw-brand);
  border-color:color-mix(in srgb,var(--jw-brand) 32%,transparent);
  background:var(--jw-brand-soft)}
.jw-tag{display:inline-flex;align-items:center;font-size:12px;line-height:1.7;
  padding:0 7px;border-radius:6px;border:1px solid var(--jw-line);
  background:var(--jw-sunken);color:var(--jw-dim);white-space:nowrap}
.jw-tag.on{color:var(--jw-good);border-color:color-mix(in srgb,var(--jw-good) 40%,transparent);
  background:color-mix(in srgb,var(--jw-good) 9%,transparent)}
.jw-banner{position:relative;border:1px solid var(--jw-line);border-radius:12px;
  padding:10px 13px 10px 15px;font-size:13px;line-height:1.6;
  background:var(--jw-sunken);color:var(--jw-dim)}
.jw-banner::before{content:'';position:absolute;left:0;top:9px;bottom:9px;width:3px;
  border-radius:0 2px 2px 0;background:var(--jw-line-strong)}
.jw-banner.err{color:var(--jw-bad);font-weight:500;
  border-color:color-mix(in srgb,var(--jw-bad) 38%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 9%,transparent)}
.jw-banner.err::before{background:var(--jw-bad)}
.jw-banner.ok{color:var(--jw-good);border-color:color-mix(in srgb,var(--jw-good) 32%,transparent);
  background:color-mix(in srgb,var(--jw-good) 8%,transparent)}
.jw-banner.ok::before{background:var(--jw-good)}

/* ──  环境卡：一条横向的状态条，两格规格并排，不占半屏  ─────────── */
.jw-env{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 10px;
  padding:15px 18px;position:relative;overflow:hidden}
.jw-env::before{content:'';position:absolute;left:0;top:16px;bottom:16px;width:2px;
  border-radius:2px;opacity:.6;
  background:linear-gradient(180deg,var(--jw-brand),
    color-mix(in srgb,var(--jw-brand) 20%,transparent))}
.jw-env:has(.jw-chip.on)::before{background:linear-gradient(180deg,var(--jw-good),
  color-mix(in srgb,var(--jw-good) 20%,transparent))}
.jw-env:has(.jw-chip.warn)::before{background:linear-gradient(180deg,var(--jw-warn),
  color-mix(in srgb,var(--jw-warn) 20%,transparent))}
.jw-env>header{grid-column:1/-1;align-items:center;margin-bottom:11px}
.jw-env>header .jw-btn-ghost{border-color:var(--jw-line);background:var(--jw-surface);color:var(--jw-ink)}
.jw-env>header .jw-btn-ghost:hover:enabled{border-color:var(--jw-brand);
  background:var(--jw-brand-soft);color:var(--jw-brand)}
.jw-env>.jw-env-line{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);
  font-size:12.5px;line-height:1.5;color:var(--jw-dim);background:var(--jw-sunken);
  border:1px solid var(--jw-line);border-radius:9px;padding:8px 11px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── 功能层：一层 = 一份可共用的数据 ────────────────────────────────
   四个功能的簇画一条细分隔标题；单例层（solo）标题退成灰色小字、不画框——
   层是数据概念，不该在页面上先占一个空盒子。 */
.jw-domain{margin-top:18px}
.jw-domain-head{display:flex;align-items:center;gap:9px;margin:0 0 9px;
  padding-bottom:7px;border-bottom:1px solid var(--jw-line);min-width:0}
.jw-domain.solo .jw-domain-head{border-bottom:1px solid var(--jw-line)}
.jw-domain-name{font-size:13.5px;font-weight:660;letter-spacing:.005em;white-space:nowrap}
.jw-domain.solo .jw-domain-name{font-size:12.5px;font-weight:500;color:var(--jw-dim)}
.jw-domain-count{font-size:11.5px;color:var(--jw-dim);padding:1px 7px;
  border-radius:999px;background:var(--jw-sunken);border:1px solid var(--jw-line);
  white-space:nowrap}
.jw-domain-desc{font-size:12.5px;color:var(--jw-dim);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;min-width:0}
.jw-domain-head .jw-btn-tiny{min-height:24px;padding:1px 8px;font-size:11.5px}

/* 新建任务第一步：选一层（选完才决定上传什么） */
.jw-domcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.jw-domcard{display:flex;flex-direction:column;gap:4px;text-align:left;
  padding:14px 16px;border:1px solid var(--jw-line);border-radius:14px;
  background:var(--jw-surface);color:var(--jw-ink);font:inherit;cursor:pointer;
  box-shadow:var(--jw-shadow);
  transition:border-color .22s var(--jw-ease),box-shadow .22s var(--jw-ease),
    transform .22s var(--jw-ease)}
.jw-domcard:hover{transform:translateY(-2px);box-shadow:var(--jw-lift);
  border-color:color-mix(in srgb,var(--jw-brand) 45%,var(--jw-line))}
.jw-domcard.off{opacity:.62}
.jw-domcard.off:hover{transform:none;box-shadow:var(--jw-shadow);border-color:var(--jw-line)}
.jw-domcard-name{font-size:14px;font-weight:650}
.jw-domcard-desc{font-size:12.5px;line-height:1.55;color:var(--jw-dim)}
.jw-domcard-meta{font-size:12px;color:var(--jw-dim);margin-top:3px}

/* ──  功能卡：横向紧凑一块，一行能放四个，加功能只多一行  ─────────
   高度约 70px：图标在左、名字与状态在右；颜色按序循环，靠左一条竖线认功能。
   **列数写死 4 列**：成本计算单独一层、只有一张卡，用 auto-fit 的话它会占满
   整行（被 max-width 截住后又比别的卡宽一截），写死 4 列它就落在第一列、
   和上面那排一样宽。窄屏再退回 auto-fit。 */
.jw-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
@media (max-width:820px){.jw-grid{grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}}
.jw-tile{--jw-tone:var(--jw-brand);
  position:relative;display:flex;flex-direction:row;align-items:center;gap:8px;
  text-align:left;min-height:0;max-width:300px;padding:12px 11px 12px 15px;overflow:hidden;
  border:1px solid var(--jw-line);border-radius:14px;
  background:var(--jw-surface);color:var(--jw-ink);font:inherit;cursor:pointer;
  box-shadow:var(--jw-shadow);
  transition:border-color .2s var(--jw-ease),box-shadow .2s var(--jw-ease),
    transform .2s var(--jw-ease),background .2s var(--jw-ease),opacity .2s}
.jw-tile::before{content:'';position:absolute;left:0;top:10px;bottom:10px;width:2px;
  border-radius:2px;opacity:.85;
  background:linear-gradient(180deg,var(--jw-tone),
    color-mix(in srgb,var(--jw-tone) 30%,transparent))}
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
.jw-tasks{display:flex;flex-direction:column;gap:8px}
/* 旧回收站的残留提示：一行小字 + 一个"清空"，平时不出现 */
.jw-trash{display:flex;align-items:center;gap:8px;margin-top:10px;padding-top:8px;
  border-top:1px dashed var(--jw-line);font-size:11.5px;color:var(--jw-dim)}
.jw-taskrow{position:relative;display:flex;flex-direction:column;gap:5px;
  padding:9px 10px 9px 13px;border-radius:12px;overflow:hidden;
  border:1px solid var(--jw-line);background:var(--dsw-alias-bg-base);
  cursor:pointer;transition:border-color .22s var(--jw-ease),
    background .22s var(--jw-ease),box-shadow .22s var(--jw-ease)}
.jw-taskrow:hover{border-color:var(--jw-line-strong);box-shadow:var(--jw-shadow)}
.jw-taskrow.on{border-color:color-mix(in srgb,var(--jw-brand) 42%,transparent);
  background:color-mix(in srgb,var(--jw-brand) 5%,var(--jw-surface))}
.jw-taskrow.on::before{content:'';position:absolute;left:0;top:12px;bottom:12px;width:2px;
  border-radius:2px;background:linear-gradient(180deg,var(--jw-brand),
    color-mix(in srgb,var(--jw-brand) 25%,transparent))}
/* 点了删除、正等你确认的那一张：整卡转淡红，别让人删错 */
.jw-taskrow.doomed{border-color:color-mix(in srgb,var(--jw-bad) 45%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 5%,var(--jw-surface))}
.jw-taskrow.doomed::before{content:'';position:absolute;left:0;top:12px;bottom:12px;width:2px;
  border-radius:2px;background:var(--jw-bad)}
/* 名字与操作同一行：名字自己省略，按钮不换行——一张卡只占三行 */
.jw-taskrow-head{display:flex;align-items:center;gap:5px;min-width:0;flex-wrap:wrap;
  row-gap:5px}
.jw-taskrow-head>.jw-spacer{flex:1;min-width:4px}
.jw-taskrow-name{flex:0 1 auto;min-width:0;font-size:13.5px;font-weight:660;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.jw-taskrow .jw-chip{font-size:11.5px;padding:0 7px;gap:4px}
.jw-taskrow .jw-btn-tiny{min-height:24px;padding:1px 7px;font-size:11.5px;border-radius:7px}
.jw-taskrow .jw-btn-icon{width:24px;min-width:24px;padding:0;font-size:14px}
.jw-taskrow-meta{font-size:12px;line-height:1.5;color:var(--jw-dim);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.jw-runs{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}
.jw-runchip{display:inline-flex;align-items:center;gap:5px;font:inherit;font-size:11.5px;
  padding:1px 7px;border-radius:999px;border:1px solid var(--jw-line);
  background:var(--jw-sunken);color:var(--jw-dim);cursor:pointer;
  transition:border-color .22s var(--jw-ease),color .22s var(--jw-ease),
    background .22s var(--jw-ease)}
/* 状态不靠颜色独扛：一颗符号就能读懂，色弱也分得清 */
.jw-runchip::before{content:'·';flex:none;font-size:12px;line-height:1;opacity:.8}
.jw-runchip.done::before{content:'✓'}
.jw-runchip.await::before{content:'!'}
.jw-runchip.failed::before{content:'✕'}
.jw-runchip:hover{border-color:var(--jw-brand);color:var(--jw-brand);
  background:var(--jw-brand-soft)}
/* 任务卡里的状态是这一栏最先要读到的东西：给底色和字重，不只是描边 */
.jw-runchip.done{color:var(--jw-good);font-weight:600;
  border-color:color-mix(in srgb,var(--jw-good) 30%,transparent);
  background:color-mix(in srgb,var(--jw-good) 8%,transparent)}
.jw-runchip.await{color:var(--jw-warn);font-weight:600;
  border-color:color-mix(in srgb,var(--jw-warn) 34%,transparent);
  background:color-mix(in srgb,var(--jw-warn) 10%,transparent)}
.jw-runchip.failed{color:var(--jw-bad);font-weight:600;
  border-color:color-mix(in srgb,var(--jw-bad) 30%,transparent);
  background:color-mix(in srgb,var(--jw-bad) 8%,transparent)}

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
  transition:color .22s var(--jw-ease),border-color .22s var(--jw-ease)}
.jw-tab:hover{color:var(--jw-ink)}
.jw-tab.on{color:var(--jw-brand);border-bottom-color:var(--jw-brand);font-weight:600}
.jw-drop{display:block;border:1px dashed var(--jw-line-strong);border-radius:14px;
  padding:22px;text-align:center;cursor:pointer;background:var(--jw-sunken);
  font-size:13.5px;
  transition:border-color .22s var(--jw-ease),background .22s var(--jw-ease)}
.jw-drop:hover{border-color:var(--jw-brand);background:var(--jw-brand-soft)}
.jw-map{display:grid;grid-template-columns:minmax(96px,140px) minmax(0,1fr) auto;
  gap:8px 10px;align-items:center}
.jw-map .jw-req{color:var(--jw-bad);margin-left:2px}
/* 配置页第一行：已认出多少 / 还缺什么 / 展开调整。默认折叠，人只看结论。 */
.jw-maphead{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:11px}
.jw-unused{margin-top:12px;font-size:12.5px;line-height:1.7;color:var(--jw-dim)}
.jw-unused b{font-weight:500;color:var(--jw-ink)}
.jw-modes{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.jw-mode{display:flex;flex-direction:column;gap:3px;text-align:left;padding:12px 14px;
  border-radius:14px;border:1px solid var(--jw-line);background:var(--jw-sunken);
  color:var(--jw-ink);font:inherit;cursor:pointer;
  transition:border-color .22s var(--jw-ease),box-shadow .22s var(--jw-ease)}
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
  margin-top:12px;padding:10px 12px;border-radius:12px;
  background:var(--jw-sunken);border:1px dashed var(--jw-line-strong);font-size:13px}
.jw-choice{margin-top:14px;padding:12px 14px;border-radius:12px;
  border:1px dashed var(--jw-line-strong);background:var(--jw-sunken)}

/* ──  选功能 / 上传 / 结果里的块  ────────────────────────────────── */
.jw-picks{display:flex;flex-direction:column;gap:9px}
.jw-pick{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;
  align-items:center;width:100%;text-align:left;padding:11px 14px;border-radius:14px;
  border:1px solid var(--jw-line);background:var(--jw-surface);
  color:var(--jw-ink);font:inherit;cursor:pointer;
  transition:border-color .22s var(--jw-ease),box-shadow .22s var(--jw-ease),
    background .22s var(--jw-ease),opacity .18s}
.jw-pick:hover:not(.off){border-color:color-mix(in srgb,var(--jw-brand) 50%,var(--jw-line))}
.jw-pick.on{border-color:var(--jw-brand);background:var(--jw-brand-soft);
  box-shadow:0 0 0 3px var(--jw-brand-soft)}
.jw-pick.off{opacity:.55;cursor:not-allowed}
.jw-pick-name{font-size:13.5px;font-weight:650}
.jw-pick-desc{grid-column:1/-1;font-size:12.5px;line-height:1.5;color:var(--jw-dim)}
.jw-pick-note{grid-column:1/-1;font-size:12px;color:var(--jw-warn)}
.jw-log,.jw-code{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);
  font-size:12px;color:var(--jw-dim);background:var(--jw-sunken);
  border:1px solid var(--jw-line);border-radius:10px}
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
.jw-table tbody tr:hover{background:color-mix(in srgb,var(--jw-brand) 6%,transparent)}
.jw-entries{margin:0;padding-left:15px;color:var(--jw-dim);white-space:pre-wrap;font-size:12.5px}
.jw-entries li{list-style:disc}

/* ──  产物预览：表头吸附、行号固定、格子截断  ─────────────────────
   为什么不用上面那张复核表：那是"列固定、行几十条"的编辑表。这里列数由文件
   决定、行可能四万，要的是 Excel 的读法——横向能滚、表头不跟着跑、数字右对齐。
   滚动容器只有一个，表头与行号靠 sticky 固定在里面。 */
.jw-sheet{margin-top:14px;border:1px solid var(--jw-line);border-radius:12px;
  overflow:hidden;background:var(--jw-surface)}
.jw-sheet-files{display:flex;gap:6px;flex-wrap:wrap;padding:8px 10px 0}
.jw-sheet-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  padding:6px 8px 0;background:var(--jw-sunken);border-bottom:1px solid var(--jw-line)}
.jw-sheet-tabs{display:flex;gap:3px;flex-wrap:wrap;min-width:0}
.jw-sheet-tab{font:inherit;font-size:12.5px;line-height:1.5;padding:4px 10px;
  border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0;
  background:transparent;color:var(--jw-dim);cursor:pointer;white-space:nowrap}
.jw-sheet-tab:hover{color:var(--jw-ink)}
.jw-sheet-tab.on{background:var(--jw-surface);border-color:var(--jw-line);
  color:var(--jw-ink);font-weight:600}
.jw-sheet-tab b{font-weight:500;color:var(--jw-dim);font-variant-numeric:tabular-nums}
.jw-sheet-head .jw-input{width:190px;margin-left:auto;margin-bottom:5px}
.jw-sheet-scroll{height:clamp(300px,56vh,640px);overflow:auto;overflow-anchor:none;
  scrollbar-width:thin;scrollbar-color:var(--jw-line-strong) transparent}
.jw-sheet table{border-collapse:separate;border-spacing:0;font-size:12.5px;
  font-variant-numeric:tabular-nums}
/* 行高锁死 26px（与 client.js 的 PREVIEW_ROW_H 一致）：虚拟滚动的留白靠它算。
   padding 2 + line-height 22 = 26，字号再大就撑破——改字号要一起改这两个值。 */
.jw-sheet td,.jw-sheet th{padding:5px 9px;text-align:left;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:260px}
.jw-sheet td{padding:2px 9px;line-height:22px;
  box-shadow:inset 0 -1px 0 var(--jw-line)}
.jw-sheet tbody tr{height:26px}
.jw-sheet tbody tr.jw-sheet-padrow td{box-shadow:none;padding:0;border:0}
.jw-sheet thead th{position:sticky;top:0;z-index:2;background:var(--jw-sunken);
  color:var(--jw-dim);font-size:12px;font-weight:600;
  border-bottom:1px solid var(--jw-line-strong)}
.jw-sheet .jw-rn{position:sticky;left:0;z-index:1;background:var(--jw-surface);
  color:var(--jw-dim);font-size:11px;text-align:right;user-select:none;
  border-right:1px solid var(--jw-line)}
.jw-sheet thead .jw-rn{z-index:3;background:var(--jw-sunken)}
.jw-sheet tbody tr:hover td{background:color-mix(in srgb,var(--jw-brand) 5%,transparent)}
.jw-sheet td.jw-num{text-align:right}
.jw-sheet td.was-copied{background:color-mix(in srgb,var(--jw-good) 16%,transparent)}
.jw-sheet-foot{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  padding:7px 10px;background:var(--jw-sunken);border-top:1px solid var(--jw-line);
  font-size:12px;color:var(--jw-dim)}
.jw-sheet-msg{padding:22px 12px;text-align:center;font-size:12.5px;color:var(--jw-dim)}
.jw-sheet-msg.error{color:var(--jw-bad)}
/* 报告：iframe 铺满卡片宽度，高度跟窗口走；脚注说明"PDF 是归档件" */
.jw-report{margin-top:14px;border:1px solid var(--jw-line);border-radius:12px;
  overflow:hidden;background:var(--jw-surface)}
.jw-report-frame{display:block;width:100%;height:clamp(420px,72vh,980px);
  border:0;background:var(--jw-surface)}
.jw-report-foot{display:flex;align-items:center;gap:10px;padding:7px 10px;
  background:var(--jw-sunken);border-top:1px solid var(--jw-line)}`

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
          // 有改动才亮：这一颗是表格里唯一的"落地"动作
          h('button', {
            className: 'jw-btn jw-btn-primary',
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

    /** 能预览的产物：和 Host 的名单一致（`src/preview.js`）。parquet 只给下载。 */
    const PREVIEW_EXT = new Set(['xlsx', 'xlsm', 'xlsb', 'xls', 'ods', 'csv'])

    /**
     * 一页多少行：滚动到哪儿取哪一块（500 行 ≈ 100KB，本机是毫秒级）。
     * 页大一点换请求少一点，但拖滚动条时多余的行也要跟着读，500 是个折中。
     */
    const PREVIEW_CHUNK = 500

    /**
     * 行高，单位 px。**必须与 CSS 里 `.jw-sheet tbody tr` 的 height 一致**：
     * 虚拟滚动的上下留白全靠它算，改了样式不改这里，滚到底会差出几十行。
     */
    const PREVIEW_ROW_H = 26

    function extOf(name) {
      const hit = /\.([A-Za-z0-9]+)$/.exec(String(name || ''))
      return hit ? hit[1].toLowerCase() : ''
    }

    /** 显示宽度：中文占两格，列宽才不会把「摘要」压成一条缝。 */
    function textWidth(text) {
      let width = 0
      for (const char of String(text ?? '')) width += char.charCodeAt(0) > 255 ? 2 : 1
      return width
    }

    /**
     * 列宽与对齐：按表头 + 前几十行的内容估，数字列右对齐。
     *
     * 用样本而不是全量：四万行只为了量列宽扫一遍不划算，而且前 40 行已经能看出
     * "这一列是金额还是摘要"。超过 30 字的内容截断——要看全文点一下复制。
     */
    function columnLayout(header, sample) {
      return (header || []).map((name, index) => {
        let wide = textWidth(name)
        let numeric = 0
        let seen = 0
        for (const row of sample || []) {
          const text = String(row?.[index] ?? '')
          if (!text) continue
          seen += 1
          wide = Math.max(wide, Math.min(30, textWidth(text)))
          if (/^-?[\d,]+(\.\d+)?$/.test(text)) numeric += 1
        }
        return {
          width: Math.max(64, Math.min(260, Math.round(wide * 7) + 18)),
          numeric: seen > 0 && numeric / seen > 0.7,
        }
      })
    }

    /**
     * 结果页的表格预览。只读、只认 `state.json` 里登记过的产物。
     *
     * 数据按页要（`PREVIEW_CHUNK` 行一页），**只渲当前看得见的那几十行**：
     * 四万行乘二十几列整块进 DOM，浏览器先卡死再说；只渲可见窗口，滚动条仍然
     * 按整表长度画，"滚到第 3 万行"和"滚到第 3 行"一样顺。
     *
     * 搜索不在已加载的页里找，而是交给 Host 在整张表上找——否则"搜不到"只是
     * 因为你没滚到那儿。命中行的行号仍是原表位置（从 1 重数会对着 Excel 找不着）。
     *
     * 换 sheet 要重新读文件（一次 1~2 秒），所以有「正在读这张表…」；
     * 同一次会话里再切回来是毫秒级，Host 侧按文件缓存着。
     */
    function SheetPreview({ job, api, t }) {
      const files = useMemo(
        () => (job.files || []).filter((file) => PREVIEW_EXT.has(extOf(file.filename))),
        [job.files],
      )
      const defaultKey = useMemo(() => {
        const first = files.find((file) => file.key === 'result') || files[0]
        return first ? first.key : ''
      }, [files])

      const [picked, setPicked] = useState('')
      const [sheet, setSheet] = useState('')
      const [meta, setMeta] = useState(null)   // { sheets, sheet, header, truncated }
      const [pages, setPages] = useState({})   // 页码 → { rows, rowIndexes }
      const [total, setTotal] = useState(0)
      const [range, setRange] = useState(null) // 眼下看得见的行区间（1 基）
      const [error, setError] = useState('')
      const [query, setQuery] = useState('')
      const [needle, setNeedle] = useState('')
      const [copied, setCopied] = useState(null)
      const [nonce, setNonce] = useState(0)
      const token = useRef(0)                  // 换表/换搜索：在飞的旧请求作废
      const pending = useRef(new Set())

      const activeKey = picked || defaultKey

      // 换产物、换表、换搜索条件：已读的页全丢掉，从头开始
      useEffect(() => {
        token.current += 1
        pending.current.clear()
        setPages({})
        setTotal(0)
        setMeta(null)
        setRange(null)
        setError('')
        setCopied(null)
      }, [job.job_id, activeKey, sheet, needle, nonce])

      // 搜索防抖：敲一个字发一次请求没必要，停手 320ms 再问
      useEffect(() => {
        const value = query.trim()
        if (value === needle) return undefined
        const timer = window.setTimeout(() => setNeedle(value), 320)
        return () => window.clearTimeout(timer)
      }, [query, needle])

      /** 要第 `index` 页。同一页不重复要；答案回来时按 token 判断还算不算数。 */
      const loadPage = useCallback(async (index) => {
        if (!activeKey || pending.current.has(index)) return
        pending.current.add(index)
        const stamp = token.current
        try {
          const res = await api.preview(job.job_id, activeKey, {
            sheet, offset: index * PREVIEW_CHUNK, limit: PREVIEW_CHUNK, q: needle,
          })
          if (stamp !== token.current) return
          const view = res.preview || {}
          setMeta({
            sheets: view.sheets || [],
            sheet: view.sheet || '',
            header: view.header || [],
            truncated: !!view.truncated,
          })
          setTotal(Number(view.total) || 0)
          setPages((prev) => ({
            ...prev,
            [index]: { rows: view.rows || [], rowIndexes: view.rowIndexes || null },
          }))
        } catch (err) {
          if (stamp === token.current) setError(err?.message || String(err))
        } finally {
          // 只清理自己那一轮：reset 已经清过空集合，这里再删会把新一轮的同号页
          // 标记成"没在要"，于是重复发请求
          if (stamp === token.current) pending.current.delete(index)
        }
      }, [api, job.job_id, activeKey, sheet, needle])

      useEffect(() => {
        if (!activeKey) return undefined
        loadPage(0)
        return undefined
        // nonce：按了「再试一次」要真的再要一次，不能因为 loadPage 没变就跳过
      }, [loadPage, activeKey, nonce])

      // 行区间只在真的变了的时候回传，否则每滚 1px 都要父组件重画一遍
      const keepRange = useCallback((next) => {
        setRange((prev) => (prev && prev.from === next.from && prev.to === next.to ? prev : next))
      }, [])

      const copy = useCallback((rowIndex, colIndex, text) => {
        const value = String(text ?? '')
        if (!value) return
        try {
          navigator.clipboard?.writeText(value)
        } catch {
          // 没有剪贴板权限就算了，别把点单元格变成报错
        }
        setCopied({ row: rowIndex, col: colIndex, text: value })
        window.setTimeout(() => setCopied(null), 1400)
      }, [])

      const current = meta?.sheet || sheet
      const sheets = meta?.sheets || []
      const header = meta?.header || []
      // 列宽按第一页估，够用了；真要逐页量，滚动时会一顿一顿地改列宽
      const firstPage = pages[0]
      const layout = useMemo(
        () => columnLayout(header, firstPage?.rows?.slice(0, 40) || []),
        [header, firstPage],
      )
      // 页签名上是原表行数；被闸门砍过时两者不一样，页脚要说清楚
      const realRows = sheets.find((item) => item.name === current)?.rows

      // 早返回必须在所有 hook 之后：产物列表从"有"变"没有"时 hook 数量一变就炸
      if (!files.length) return null

      let body = null
      if (error && !meta) {
        body = h('div', { className: 'jw-sheet-msg error' },
          `${t('previewFail')}：${error}`,
          h('div', { style: { marginTop: 8 } },
            h('button', {
              className: 'jw-btn jw-btn-ghost',
              onClick: () => setNonce((n) => n + 1),
            }, t('previewRetry'))))
      } else if (!meta) {
        body = h('div', { className: 'jw-sheet-msg' }, t('previewLoading'))
      } else if (!total) {
        body = h('div', { className: 'jw-sheet-msg' },
          needle ? fill(t('previewNone'), { q: needle }) : t('previewEmpty'))
      } else {
        body = h(SheetTable, {
          // 换表/换搜索时重挂一次：滚动位置回到表头，而不是停在上一张表的第 3 万行
          key: `${current}|${needle}|${nonce}`,
          pages,
          total,
          header,
          layout,
          copied,
          onCopy: copy,
          onNeed: loadPage,
          onRange: keepRange,
        })
      }

      return h('div', { className: 'jw-sheet' },
        files.length > 1 && h('div', { className: 'jw-sheet-files' },
          files.map((file) => h('button', {
            key: file.key,
            className: `jw-btn ${file.key === activeKey ? 'jw-btn-primary' : 'jw-btn-ghost'}`,
            onClick: () => setPicked(file.key),
          }, file.filename))),

        h('div', { className: 'jw-sheet-head' },
          h('div', { className: 'jw-sheet-tabs' },
            sheets.map((item) => h('button', {
              key: item.name,
              className: `jw-sheet-tab${item.name === current ? ' on' : ''}`,
              title: item.name,
              onClick: () => setSheet(item.name),
            }, item.name, h('b', null, ` ${fmtNumber(item.rows)}`)))),
          h('input', {
            className: 'jw-input',
            placeholder: t('previewSearch'),
            value: query,
            onChange: (event) => setQuery(event.target.value),
          })),

        body,

        meta && h('div', { className: 'jw-sheet-foot' },
          needle
            ? h('span', null, fill(t('previewHits'), { n: fmtNumber(total) }))
            : range && h('span', null, fill(t('previewRange'), {
              a: fmtNumber(range.from), b: fmtNumber(range.to), n: fmtNumber(total),
            })),
          meta.truncated && h('span', null, fill(t('previewTruncated'), {
            n: fmtNumber(total), m: fmtNumber(realRows || total),
          })),
          h('span', { className: 'jw-spacer' }),
          copied
            ? h('span', { className: 'jw-ok' }, fill(t('previewCopied'), {
              text: copied.text.length > 30 ? `${copied.text.slice(0, 30)}…` : copied.text,
            }))
            : h('span', null, t('previewReadonly')),
          error && meta && h('span', { className: 'jw-bad' }, error)))
    }

    /**
     * 虚拟滚动的窗口：滚到哪儿，该渲哪几行。
     *
     * 抽成纯函数是为了能验："滚到底看不看得到最后一行""上下留白加起来等不等于
     * 整表高度"这类错，在浏览器里几乎发现不了（只会觉得滚动条短了一点），
     * 但算术上是能断言的。
     */
    function windowOf({ scrollTop, boxH, headH, total, rowH = PREVIEW_ROW_H, overscan = 10 }) {
      const scrolled = Math.max(0, scrollTop - headH)
      const visible = Math.ceil(boxH / rowH) + overscan * 2
      // 起点不能越过"最后一屏"：否则人会滚进一片没有行的空白（拖滚动条时最容易撞上）
      const maxFirst = Math.max(0, total - visible)
      const first = Math.min(maxFirst, Math.max(0, Math.floor(scrolled / rowH) - overscan))
      const last = Math.min(total, first + visible)
      return {
        first,
        last,
        topPad: first * rowH,
        bottomPad: Math.max(0, (total - last) * rowH),
      }
    }

    /**
     * 表格本体：虚拟滚动，只渲看得见的那几十行。
     *
     * 做法是老实的"上下留白"：tbody 前面一排空行撑出 `起点 × 行高`，后面一排撑出
     * 剩下的高度，滚动条长度因此等于整表长度；真正渲染的行只有窗口那一段。
     *
     * 行高必须与 CSS 里的 `.jw-sheet tbody tr` 一致（`PREVIEW_ROW_H`）。格子之间
     * 那道线用 inset 阴影画、不用 border：border 会让每行实高比设定值多 1px，
     * 四万行之后滚动条就对不上了。
     */
    function SheetTable({ pages, total, header, layout, copied, onCopy, onNeed, onRange }) {
      const boxRef = useRef(null)
      const [scrollTop, setScrollTop] = useState(0)
      const [boxH, setBoxH] = useState(420)
      const [headH, setHeadH] = useState(32)

      // 容器高度由 CSS 定（clamp），这里量一次，并跟着窗口尺寸变
      useEffect(() => {
        const box = boxRef.current
        if (!box) return undefined
        const sync = () => {
          setBoxH(box.clientHeight || 420)
          setHeadH(box.querySelector('thead')?.offsetHeight || 32)
        }
        sync()
        if (typeof ResizeObserver === 'function') {
          const observer = new ResizeObserver(sync)
          observer.observe(box)
          return () => observer.disconnect()
        }
        window.addEventListener('resize', sync)
        return () => window.removeEventListener('resize', sync)
      }, [])

      const { first, last, topPad, bottomPad } = windowOf({ scrollTop, boxH, headH, total })

      // 窗口落到哪几页就取哪几页：拖到第 3 万行只读那一块，不从头读
      useEffect(() => {
        if (!total) return
        const from = Math.floor(first / PREVIEW_CHUNK)
        const to = Math.floor(Math.max(first, last - 1) / PREVIEW_CHUNK)
        for (let page = from; page <= to; page += 1) onNeed?.(page)
      }, [first, last, total, onNeed])

      useEffect(() => {
        if (!total) return
        onRange?.({ from: first + 1, to: last })
      }, [first, last, total, onRange])

      /** 第 `index` 行：没读到的页先空着，等它自己回来填上。 */
      const rowAt = (index) => {
        const page = pages[Math.floor(index / PREVIEW_CHUNK)]
        if (!page) return null
        const local = index % PREVIEW_CHUNK
        return {
          row: page.rows[local] || [],
          number: page.rowIndexes ? page.rowIndexes[local] : index,
        }
      }

      const rows = []
      for (let index = first; index < last; index += 1) {
        const hit = rowAt(index)
        const number = hit ? hit.number : index
        const row = hit ? hit.row : []
        rows.push(h('tr', { key: index },
          h('td', { className: 'jw-rn' }, fmtNumber(number + 1)),
          layout.map((col, colIndex) => h('td', {
            key: colIndex,
            className: [
              col.numeric ? 'jw-num' : '',
              copied && copied.row === number && copied.col === colIndex ? 'was-copied' : '',
            ].filter(Boolean).join(' ') || undefined,
            title: row[colIndex] || '',
            onClick: () => onCopy?.(number, colIndex, row[colIndex]),
          }, row[colIndex] ?? ''))))
      }

      const span = header.length + 1

      return h('div', {
        className: 'jw-sheet-scroll',
        ref: boxRef,
        onScroll: (event) => setScrollTop(event.currentTarget.scrollTop),
      },
      h('table', null,
        h('colgroup', null,
          h('col', { style: { width: 52 } }),
          layout.map((col, index) => h('col', { key: index, style: { width: col.width } }))),
        h('thead', null, h('tr', null,
          h('th', { className: 'jw-rn' }, '#'),
          header.map((name, index) => h('th', {
            key: index,
            title: name,
            className: layout[index]?.numeric ? 'jw-num' : undefined,
          }, name)))),
        h('tbody', null,
          topPad > 0 && h('tr', { key: 'pad-top', className: 'jw-sheet-padrow' },
            h('td', { colSpan: span, style: { height: topPad } })),
          rows,
          bottomPad > 0 && h('tr', { key: 'pad-bottom', className: 'jw-sheet-padrow' },
            h('td', { colSpan: span, style: { height: bottomPad } })))))
    }

    /**
     * 结果页：**只有预览和下载**。
     *
     * 统计条（凭证/行/改判/置信度）、业务桶分布、facts.summary 都拿掉了：那些数
     * 是"表里本来就有"的汇总，看一眼表就知道，摆两遍只是让人多划两屏。
     * 功能之间的结果形态也因此统一——不管哪个功能，跑完都是同一件事：
     * 这里有一份产物，看，或者拿走。
     */
    function ResultPanel({ job, t, api }) {
      const files = job.files || []
      // 报告优先：这份产物的正文就是它，别让人先点下载
      const report = files.find((file) => extOf(file.filename) === 'html')
      // 表格产物沿用 result 这个标签；报告的两个文件按后缀写，否则会出现
      // 「下载 result」和「下载 pdf」并排，让人分不清哪个是可读的报告
      // 报告类产物按后缀标（"下载 html" / "下载 pdf"）；标记表按用途标——
      // 否则并排出现「下载 result」「下载 detail」谁也认不出去哪个
      const labelOf = (file) => {
        const ext = extOf(file.filename)
        if (ext === 'html' || ext === 'pdf') return ext
        if (file.key === 'detail') return t('detailFile')
        return file.label
      }
      return h('div', { className: 'jw-card' },
        h('header', null,
          h('h3', null, t('result')),
          h('span', { className: 'jw-muted jw-small' },
            `${job.feature_name} · ${job.input?.filename || ''}`),
          files.length
            ? h('span', { className: 'jw-spacer' })
            : null,
          files.map((file) => h('a', {
            key: file.key,
            className: 'jw-btn',
            href: api.downloadUrl(job.job_id, file.key),
            download: file.filename,
          }, `${t('download')} ${labelOf(file)}${file.bytes ? ` · ${fmtBytes(file.bytes)}` : ''}`))),

        report
          ? h(ReportFrame, { api, t, job, file: report })
          : files.length
            ? h(SheetPreview, { job, api, t })
            : h('p', { className: 'jw-muted jw-small', style: { margin: 0 } }, t('noFiles')))
    }

    /**
     * HTML 报告的现场版：一个 iframe，直接读 `/download?...&inline=1`。
     *
     * 为什么不复用表格预览器：报告是**文档**（三节、段落、宽表），不是一张可
     * 分页的表；用 iframe 算是把"报告怎么写"整个交还给生成方，插件不再认识它的
     * 结构。`sandbox` 置空是刻意的：报告是静态 HTML，不需要脚本、表单、跳转，
     * 关掉它们之后，就算产物被谁换成了一个坏页面，也翻不出浪。
     */
    function ReportFrame({ api, t, job, file }) {
      return h('div', { className: 'jw-report' },
        h('iframe', {
          className: 'jw-report-frame',
          title: t('reportFrame'),
          src: api.openUrl(job.job_id, file.key),
          sandbox: '',
          loading: 'lazy',
        }),
        h('div', { className: 'jw-report-foot' },
          h('span', { className: 'jw-muted jw-small' },
            `${file.filename}${file.bytes ? ` · ${fmtBytes(file.bytes)}` : ''}`),
          h('span', { className: 'jw-spacer' }),
          h('span', { className: 'jw-muted jw-small' }, t('reportNote'))))
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
            h('button', { className: 'jw-btn jw-btn-danger', onClick: stop }, t('envStop')))),

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
          // 没配好时「去配置」是这一页唯一该点的东西：给它主按钮的分量
          h('button', { className: `jw-btn${ok ? ' jw-btn-ghost' : ' jw-btn-primary'}`,
            onClick: onOpen }, ok ? t('open') : t('fix'))),
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
    function TaskCreate({ api, t, domains, features, initialDomain, onCreated, onError, setBusy, busy }) {
      // 层先定，文件后传：层决定"上传的是什么"（序时账 / 采购明细），
      // 也决定读表时看哪几个字段（域契约，5–8 个，不是所有功能的并集）。
      const [domainId, setDomainId] = useState(String(initialDomain || ""))
      const [uploaded, setUploaded] = useState(null)
      const [inspect, setInspect] = useState(null)
      const [mapping, setMapping] = useState({})
      const [name, setName] = useState("")
      const [working, setWorking] = useState("")
      // 默认折叠：正常情况下一行"必需 4/4 已识别"就够了，不该让人先看一屏下拉
      const [adjust, setAdjust] = useState(false)
      // 配账时归一化之后仍不是标准一级科目的写法：先让人对一遍，再往下跑
      const [align, setAlign] = useState(null)
      const inputRef = useRef(null)

      const domain = (domains || []).find((item) => item.id === domainId) || null
      const input = domain?.inputs?.[0] || null
      // 这一层有没有能跑的功能：没有就不让建，免得建出一份没人能跑的数据
      const ready = (features || []).some(
        (item) => item.domain === domainId && item.available !== false)

      const reset = () => {
        setUploaded(null)
        setInspect(null)
        setMapping({})
        setAdjust(false)
        if (inputRef.current) inputRef.current.value = ""
      }

      const pick = useCallback(async (file) => {
        if (!file || !domain) return
        setWorking(t("inspecting"))
        onError("")
        try {
          const up = await api.upload(file)
          setUploaded(up)
          // 名字默认取文件名（去掉扩展名）：多数人就叫"序时账1"
          setName((current) => current || String(up.name || "").replace(/\.(xlsx|xls|xlsm|csv)$/i, ""))
          const data = await api.post("/inspect", { file: up.path, domain: domain.id })
          setInspect(data.inspect)
          setMapping(data.inspect.suggested_mapping || {})
          // 缺字段就直接展开：还要再点一次才看见缺什么，等于没提示
          setAdjust((data.inspect.missing || []).length > 0)
        } catch (error) {
          setInspect(null)
          onError(`${t("loadFailed")}: ${error.message}`)
        } finally {
          setWorking("")
        }
      }, [api, domain, onError, t])

      const create = useCallback(async () => {
        if (!uploaded || !domain) return
        setBusy(true)
        onError("")
        try {
          const data = await api.post("/tasks", {
            file: uploaded.path, name: name || uploaded.name, mapping, domain: domain.id,
          })
          reset()
          setName("")
          // 归一化之后还有对不上的科目：先停下让人对一遍（这一步改的是全局别名表，
          // 所以只在配账时问，跑起来之后再问就晚了）
          if ((data.task.unknown || []).length) {
            setAlign({ task: data.task, standard: data.task.standard || [], picks: {} })
            return
          }
          await onCreated(data.task)
        } catch (error) {
          onError(`${t("taskCreate")}: ${error.message}`)
        } finally {
          setBusy(false)
        }
      }, [api, uploaded, name, mapping, domain, onCreated, onError, setBusy, t])

      // 把选好的对应关系写进别名表，然后重新解析这份账（还剩的继续对）
      const applySubjects = useCallback(async () => {
        if (!align) return
        const pairs = Object.fromEntries(
          Object.entries(align.picks).filter(([, target]) => target))
        if (!Object.keys(pairs).length) return
        setBusy(true)
        onError("")
        try {
          const data = await api.post("/tasks/subjects", { id: align.task.id, pairs })
          if ((data.task.unknown || []).length) {
            setAlign({
              ...align, task: data.task,
              standard: data.task.standard || align.standard, picks: {},
            })
          } else {
            setAlign(null)
            await onCreated(data.task)
          }
        } catch (error) {
          onError(`${t("taskCreate")}: ${error.message}`)
        } finally {
          setBusy(false)
        }
      }, [align, api, onCreated, onError, setBusy, t])

      const skipAlign = useCallback(() => {
        const task = align?.task
        setAlign(null)
        if (task) onCreated(task)
      }, [align, onCreated])

      // ── 还没选层：先问"你要处理什么数据" ──
      // 这一问不是为了限定能用哪些功能，而是把字段空间裁到这一层的几个字段。
      if (!domain) {
        return h("div", { className: "jw-card" },
          h("header", null,
            h("h3", null, t("pickDomain")),
            h("span", { className: "jw-muted jw-small" }, t("pickDomainHint"))),
          h("div", { className: "jw-domcards" }, (domains || []).map((item) => {
            const live = (features || []).some(
              (feature) => feature.domain === item.id && feature.available !== false)
            return h("button", {
              key: item.id,
              className: `jw-domcard${live ? "" : " off"}`,
              onClick: () => { setDomainId(item.id); reset(); setName("") },
            },
            h("span", { className: "jw-domcard-name" }, item.name),
            item.description && h("span", { className: "jw-domcard-desc" }, item.description),
            h("span", { className: "jw-domcard-meta" },
              `${fill(t("uploadTo"), { name: item.inputs?.[0]?.label || t("file") })}`
              + ` · ${fill(t("domainCount"), { n: (item.features || []).length })}`),
            !live && h("span", { className: "jw-domcard-meta" }, t("soon")))
          })))
      }

      // ── 科目对不上：先对一遍，再让它去跑 ──
      if (align) {
        const rows = align.task.unknown || []
        const picked = Object.values(align.picks || {}).filter(Boolean).length
        return h("div", { className: "jw-card" },
          h("header", null,
            h("h3", null, t("alignTitle")),
            h("span", { className: "jw-muted jw-small" }, align.task.name),
            h("span", { className: "jw-spacer" }),
            h("span", { className: "jw-chip warn" }, String(rows.length))),
          h("p", { className: "jw-muted jw-small", style: { marginTop: 0 } }, t("alignHint")),
          rows.map((item) => h("div", {
            key: item.name, className: "jw-row", style: { marginBottom: 8 },
          },
          h("span", {
            style: { minWidth: 140 },
            title: (item.raw_names || []).join(" / "),
          }, item.name),
          h("span", { className: "jw-muted jw-small", style: { minWidth: 90 } },
            fill(t("alignVouchers"), { n: item.vouchers })),
          h("select", {
            className: "jw-select",
            value: align.picks[item.name] || "",
            onChange: (event) => setAlign((current) => ({
              ...current,
              picks: { ...current.picks, [item.name]: event.target.value },
            })),
          },
          h("option", { value: "" }, t("alignPick")),
          (align.standard || []).map((name) => h("option", { key: name, value: name }, name))))),
          h("div", { className: "jw-actions" },
            h("button", {
              className: "jw-btn jw-btn-primary",
              disabled: !!busy || !picked,
              onClick: applySubjects,
            }, busy ? t("creating") : t("alignApply")),
            h("button", {
              className: "jw-btn jw-btn-ghost", disabled: !!busy, onClick: skipAlign,
            }, t("alignSkip"))))
      }

      const required = inspect?.required || []
      const optional = inspect?.optional || []
      const unused = inspect?.unused || []
      const columns = inspect?.columns || []
      const preview = inspect?.preview || []
      // 缺哪些：以"当前选择"为准，服务端给的 status 只是初值（人改过就不准了）
      const miss = required.filter((item) => !mapping[item.key])
      // 样例跟着选择走：选了别的列也能立刻看到那列长什么样
      const samplesOf = (column) => {
        if (!column) return ""
        const values = preview.map((row) => row[column])
          .filter((value) => value !== null && value !== undefined && String(value) !== "nan")
        return values.slice(0, 2).map((value) => String(value)).join(" / ")
      }
      const fieldRow = (item) => h(React.Fragment, { key: item.key },
        h("span", { className: "jw-muted jw-small" }, item.label,
          item.required && h("span", { className: "jw-req" }, "*")),
        h("select", {
          className: "jw-select",
          value: mapping[item.key] || "",
          onChange: (event) => {
            const value = event.target.value
            setMapping((current) => {
              const next = { ...current }
              if (value) next[item.key] = value
              else delete next[item.key]
              return next
            })
          },
        },
        h("option", { value: "" }, "—"),
        columns.map((column) => h("option", { key: column, value: column }, column))),
        h("span", { className: "jw-muted jw-small" },
          samplesOf(mapping[item.key]) || (item.samples || []).slice(0, 2).join(" / ")))

      return h("div", { className: "jw-card" },
        h("header", null,
          h("h3", null, t("taskNew")),
          h("span", { className: "jw-muted jw-small" }, domain.name),
          h("span", { className: "jw-spacer" }),
          h("button", {
            className: "jw-btn jw-btn-ghost jw-btn-tiny",
            onClick: () => { setDomainId(""); reset(); setName("") },
          }, t("changeDomain"))),

        // 这一层没有能跑的功能（成本计算还没接入）：说清楚，别让人配完才发现没处用
        !ready && h("div", { className: "jw-banner", style: { marginBottom: 10 } },
          t("domainNoFeature")),

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
            // 上传标的由层的输入角色生成，前端不写死"序时账"
            h("div", null, fill(t("uploadTo"), { name: input?.label || t("file") })),
            h("div", { className: "jw-muted jw-small" },
              input?.hint || ".xlsx / .xls / .csv")),
          working && h("div", { className: "jw-muted jw-small", style: { marginTop: 8 } }, working)),

        inspect && h("div", null,
          h("div", { className: "jw-row", style: { marginBottom: 10 } },
            h("strong", null, inspect.filename),
            h("span", { className: "jw-muted jw-small" },
              `${fmtNumber(inspect.rows)} ${t("rows")} · ${columns.length} 列`),
            h("span", { className: "jw-spacer" }),
            h("button", {
              className: "jw-btn jw-btn-ghost",
              onClick: () => { setInspect(null); setUploaded(null) },
            }, t("cancel"))),

          // 配置页第一行就是结论：认全了就不展开，缺了自动展开
          h("div", { className: "jw-maphead" },
            h("span", { className: `jw-chip${miss.length ? " warn" : " on"}` },
              miss.length
                ? `${t("reqMissing")} ${miss.length}`
                : `${t("reqDone")} ${required.length}/${required.length}`),
            miss.length > 0 && h("span", { className: "jw-muted jw-small" },
              miss.map((item) => item.label).join("、")),
            h("span", { className: "jw-spacer" }),
            h("button", {
              className: "jw-btn jw-btn-ghost jw-btn-tiny",
              onClick: () => setAdjust((value) => !value),
            }, adjust ? t("fold") : t("expand"))),

          // 表单只放"要确认的"：必需字段，加上人主动展开时才出现的可选字段
          (adjust || miss.length > 0) && h("div", { className: "jw-map" },
            required.concat(adjust ? optional : []).map(fieldRow)),

          // 其余列默认"不参与"——这就是配置量不随列数增长的原因
          unused.length > 0 && h("div", { className: "jw-unused" },
            h("b", null, `${unused.length} ${t("unusedCols")}`), "：",
            unused.slice(0, 8).map((item) => item.column).join("、"),
            unused.length > 8 ? ` 等 ${unused.length} 列` : "",
            ` —— ${t("unusedHint")}`),

          h("div", { className: "jw-actions" },
            h("button", {
              className: "jw-btn jw-btn-primary",
              disabled: miss.length > 0 || !!working || !!busy || !ready,
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
    function TaskRail({ api, t, tasks, features, trash, activeId, onChanged, onUse, onOpenFeature, onNew, onCollapse, onError }) {
      const [confirmId, setConfirmId] = useState("")
      const [trashSure, setTrashSure] = useState(false)
      const [purged, setPurged] = useState("")
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

      // 清空旧回收站：这是"删掉那堆已经没人能恢复的东西"，所以也要点两下
      const purge = useCallback(async () => {
        try {
          const data = await api.post("/tasks/trash/purge", {})
          setTrashSure(false)
          setPurged(fmtBytes(data.purged?.bytes || 0) || "0KB")
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
          : h("div", { className: "jw-tasks" }, tasks.map((task) => {
            // 只列跑过的功能：没跑过的四个「—」是同一句话重复四遍，白占两行
            const runFeatures = features.filter((feature) => (task.runs || {})[feature.id])
            return h("div", {
              key: task.id,
              className: `jw-taskrow${task.id === activeId ? " on" : ""}`
                + `${confirmId === task.id ? " doomed" : ""}`,
              // 这个任务的 AI 复核在自己那个会话里跑（数据隔离）；会话 id 挂在提示上
              title: task.session ? `${t("reviewSession")}：${task.session}` : undefined,
              onClick: () => onUse(task.id),
            },
            h("div", { className: "jw-taskrow-head" },
              h("span", { className: "jw-taskrow-name" }, task.name),
              task.id === activeId && h("span", { className: "jw-chip current" }, t("inUse")),
              h("span", { className: "jw-spacer" }),
              // 低频操作收成一个小符号，把横向空间让给任务名；文案留在提示里
              h("button", {
                className: "jw-btn jw-btn-ghost jw-btn-tiny jw-btn-icon",
                title: `${t("comboRun")} · ${t("combo")}`,
                "aria-label": t("comboRun"),
                onClick: stop(() => prepare(task.id)),
              }, "↻"),
              confirmId === task.id
                ? h(React.Fragment, null,
                  h("button", {
                    className: "jw-btn jw-btn-tiny jw-btn-danger-solid",
                    onClick: stop(() => remove(task.id)),
                  }, `${t("delete")} ✓`),
                  h("button", {
                    className: "jw-btn jw-btn-ghost jw-btn-tiny",
                    onClick: stop(() => setConfirmId("")),
                  }, t("cancel")))
                : h("button", {
                  className: "jw-btn jw-btn-ghost jw-btn-tiny jw-btn-danger",
                  onClick: stop(() => setConfirmId(task.id)),
                }, t("delete"))),

            // 一行装完：文件名 + 行数；组合数与字段数退到悬停里，不再各占一行
            h("div", {
              className: "jw-taskrow-meta",
              title: `${taskLine(t, task)} · ${comboLine(task)}`,
            }, `${task.filename}${rowsOf(task)
              ? ` · ${fmtNumber(rowsOf(task))} ${t("rows")}` : ""}`),

            runFeatures.length > 0 && h("div", { className: "jw-runs" },
              runFeatures.map((feature) => {
                const run = task.runs[feature.id]
                // 名字留全，状态退成一颗颜色点：完整状态在悬停里
                return h("button", {
                  key: feature.id,
                  className: `jw-runchip${toneOf(run)}`,
                  title: `${feature.name}：${statusWord(t, run.status)}`,
                  onClick: stop(() => onOpenFeature(task.id, feature.id)),
                }, feature.name)
              })))
          })),

        // 旧版本留下的 .trash：不当回收站用，只报个数、给一次清空
        purged
          ? h("div", { className: "jw-trash" },
            h("span", { className: "jw-ok" }, fill(t("trashPurged"), { size: purged })))
          : trash?.count > 0 && h("div", { className: "jw-trash" },
            h("span", { title: t("trashNote") },
              fill(t("trashLeft"), { n: trash.count, size: fmtBytes(trash.bytes) || "0KB" })),
            h("span", { className: "jw-spacer" }),
            trashSure
              ? h("button", {
                className: "jw-btn jw-btn-tiny jw-btn-danger-solid",
                onClick: purge,
              }, t("trashPurgeSure"))
              : h("button", {
                className: "jw-btn jw-btn-ghost jw-btn-tiny jw-btn-danger",
                onClick: () => setTrashSure(true),
              }, t("trashPurge"))))
    }

    // ── 一个功能现在能不能跑 ────────────────────────────────────────────
    /**
     * 判断顺序：环境 → 任务（有没有账） → 上游依赖 → 自己跑到哪了。
     *
     * 抽成纯函数是因为功能框、"跑一个功能"面板、功能页的横幅要说同一句话——
     * 三处各写一遍，迟早出现"框里说能跑、面板说不能"。
     */
    function featureState(t, feature, { env, task, features }) {
      // 占位功能（成本计算）：卡片留在层里，但不给"能点"的错觉。建任务那边用
      // 同一个判据（`available`）挡住，两处口径一致。
      if (feature.available === false) {
        return {
          chip: t('soon'), chipOn: false, disabled: true, startDisabled: true,
          note: t('soonHint'), tone: 'warn',
        }
      }
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
      // 功能层（域）：纯数据，决定分组顺序，也决定"新建任务"时上传什么
      const [domains, setDomains] = useState([])
      const [createDomain, setCreateDomain] = useState('')
      const [jobs, setJobs] = useState([])
      const [tasks, setTasks] = useState([])
      // 旧版本删任务留下的 .trash：页面只报个数、给一次清空，不当回收站用
      const [trash, setTrash] = useState({ count: 0, bytes: 0 })
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
          setDomains(data.domains || [])
        } catch (caught) {
          setError(caught.message)
        }
      }, [api])

      const loadTasks = useCallback(async (selectId) => {
        try {
          const data = await api.get('/tasks')
          const list = data.tasks || []
          setTasks(list)
          setTrash(data.trash || { count: 0, bytes: 0 })
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
          // **任务清单也得跟着刷**：功能卡上的"待复核/已完成"是从 `task.runs` 来的
          // （/tasks 由 jobs 现算），只刷 /jobs 的话，清洗跑完了页面还显示"等复核"，
          // 得手动点刷新才对——这就是那个 bug。
          await loadTasks()
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
      }, [loadJobs, loadTasks])

      // 切回这个页面（窗口重新聚焦、页签从后台回来）时立刻刷一次：
      // 后台的定时器会被浏览器节流，回来时不该还盯着几分钟前的状态
      useEffect(() => {
        const refresh = () => {
          if (document.visibilityState === 'hidden') return
          loadJobs()
          loadTasks()
        }
        document.addEventListener('visibilitychange', refresh)
        window.addEventListener('focus', refresh)
        return () => {
          document.removeEventListener('visibilitychange', refresh)
          window.removeEventListener('focus', refresh)
        }
      }, [loadJobs, loadTasks])

      useEffect(() => {
        if (!jobId) {
          setJob(null)
          setReview(null)
          return undefined
        }
        let stopped = false
        let misses = 0
        const tick = async () => {
          if (stopped) return
          try {
            const data = await api.get('/job', { id: jobId })
            if (stopped) return
            misses = 0
            setJob(data.job)
            if (data.job.status === 'awaiting_review') {
              const rev = await api.get('/review', { id: jobId })
              if (!stopped) setReview(rev.review)
            } else if (data.job.status !== 'running' && data.job.status !== 'created') {
              setReview(null)
            }
            const delay = pollDelay(data.job.status)
            if (delay > 0 && !stopped) timer.current = window.setTimeout(tick, delay)
          } catch (caught) {
            if (stopped) return
            setError(caught.message)
            // 抢在写 state.json 的那一瞬读、或者网络抖一下，都不该让页面停在这
            // ——以前这里不重排，一次失败就永远不动了。连续失败才放弃。
            misses += 1
            if (misses < 30) timer.current = window.setTimeout(tick, 2000)
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

      /** 建任务：先选一层（层决定上传什么），再配字段。 */
      const openCreate = (domain = '') => {
        setCreateDomain(String(domain || ''))
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
        // 有域的时候层标题自己就是分区标题；再叠一行"功能"会多出一条分隔线
        !domains.length && body.push(
          h('div', { className: 'jw-section', style: { margin: '4px 0 0' } }, t('sectionFeature')))

        const tile = (feature) => h(FeatureTile, {
          key: feature.id,
          feature,
          state: cardState(feature),
          active: false,
          onOpen: () => openFeature(feature.id),
        })

        // 按层分组：层的顺序和成员都来自后端的域契约（`journal_io/contracts.py`）。
        // 前端不维护第二份清单——否则加功能要改两处，迟早对不上。
        const seen = new Set()
        const groups = (domains.length ? domains : [{ id: '', name: t('sectionFeature'), features: [] }])
          .map((item) => {
            const items = features.filter((feature) => feature.domain === item.id
              || (!!item.features?.includes(feature.id)))
            items.forEach((feature) => seen.add(feature.id))
            return { domain: item, items }
          })
        const orphan = features.filter((feature) => !seen.has(feature.id))
        if (orphan.length) groups.push({ domain: { id: '', name: t('sectionFeature'), solo: true }, items: orphan })

        groups.filter((group) => group.items.length).forEach((group) => {
          body.push(h('section', {
            className: `jw-domain${group.domain.solo ? ' solo' : ''}`,
            key: group.domain.id || 'other',
          },
          h('div', { className: 'jw-domain-head' },
            h('span', { className: 'jw-domain-name' }, group.domain.name),
            h('span', { className: 'jw-domain-count' },
              fill(t('domainCount'), { n: group.items.length })),
            group.domain.description
              && h('span', { className: 'jw-domain-desc' }, group.domain.description),
            h('span', { className: 'jw-spacer' }),
            // 层内直接新建：上传标的就是这一层的数据，不用再选一次
            h('button', {
              className: 'jw-btn jw-btn-ghost jw-btn-tiny',
              onClick: () => openCreate(group.domain.id || ''),
            }, t('newJobShort'))),
          h('div', { className: 'jw-grid' }, group.items.map(tile))))
        })
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
          domains, features,
          initialDomain: createDomain,
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
        // 这个功能会接谁的结果：功能自己声明（`accepts_upstream`），那份跑完了就接
        const up = (currentFeature.accepts_upstream || [])
          .map((id) => ({
            id,
            name: (features.find((item) => item.id === id) || {}).name || id,
            run: task?.runs?.[id],
          }))
          .find((item) => item.run?.status === 'done') || null
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
            className: 'jw-btn jw-btn-ghost jw-btn-danger',
            disabled: !!busy,
            onClick: stop,
          }, busy ? t('stopping') : t('stop')),
          h('button', {
            className: 'jw-btn jw-btn-primary',
            disabled: state.disabled || state.startDisabled || !!busy,
            onClick: () => startFeature(currentFeature.id),
          }, busy ? t('starting') : t('startRun'))),
        up && h('div', { className: 'jw-muted jw-small' },
          fill(t('usesUpstream'), { name: up.name })),
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
              api, t, tasks: viewTasks, features, trash,
              activeId: task?.id || '',
              onChanged: async (selectId) => {
                await loadTasks(selectId)
                const list = await loadJobs()
                // 删掉的任务连同它名下的运行记录一起没了：详情还挂在那条 job 上的话，
                // 轮询会一直报"找不到任务"，所以退回首页
                if (jobId && !(list || []).some((item) => item.job_id === jobId)) {
                  setJobId('')
                  setView('home')
                }
              },
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
                className: 'jw-btn jw-btn-ghost jw-btn-danger', disabled: !!busy, onClick: onStop,
              }, t('stop'))),
          job.status === 'running' && !job.stalled && h('div', { className: 'jw-row', style: { marginTop: 8 } },
            h('button', { className: 'jw-btn jw-btn-ghost jw-btn-danger', disabled: !!busy, onClick: onStop },
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
        SheetPreview, SheetTable, ReportFrame, windowOf, columnLayout, textWidth, TaskCreate, TaskRail, EnvCard,
        FeatureTile, Detail, featureState, chipClass, pollDelay, DICT, translate },
    }
  },
})
