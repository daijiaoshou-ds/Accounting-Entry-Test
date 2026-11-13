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

    /** 页签顺序：先配环境，再按使用顺序排功能（不在表里的排后面）。 */
    const TAB_ORDER = ['contra', 'cleaning', 'cashflow', 'entry_test']

    /**
     * 功能之间的依赖：谁要等谁跑完。
     *
     * 现在只有现金流要等清洗；对方科目与分录测试的"预计依赖"还没做（等重做），
     * 所以先留空数组——等真做了再往这里加一行，前端自动把卡片置灰。
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
        open: '打开',
        fix: '去配置',
        connectingShort: '检测中',
        cardEnv: '环境还没配好',
        cardNoBook: '还没配账套',
        cardNoBookHint: '先去「序时账配置」传一份序时账',
        cardBlocked: '依赖未完成',
        cardDeps: '需要先完成：',
        cardReady: '可以开始',
        bookTab: '序时账配置',
        bookNone: '未配置',
        bookConfigure: '配置账套',
        bookTitle: '配置账套',
        bookHint: '一份账配一次，四个功能都用它；要换账就再配一个账套',
        bookList: '已有账套',
        bookNew: '配置新账套',
        bookSave: '保存账套',
        bookName: '账套名',
        needBook: '还没有账套。先去「序时账配置」上传一份序时账。',
        useBook: '用账套',
        use: '用它',
        inUse: '当前',
        delete: '删除',
        save: '保存',
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
        open: 'Open',
        fix: 'Fix',
        connectingShort: 'checking',
        cardEnv: 'Environment not ready',
        cardNoBook: 'No ledger yet',
        cardNoBookHint: 'Upload a ledger under "Ledger setup" first',
        cardBlocked: 'Dependencies pending',
        cardDeps: 'Finish first: ',
        cardReady: 'Ready',
        bookTab: 'Ledger setup',
        bookNone: 'Not configured',
        bookConfigure: 'Configure',
        bookTitle: 'Configure a ledger',
        bookHint: 'Configure once; all features use it. To use another ledger, create another book.',
        bookList: 'Configured ledgers',
        bookNew: 'Add another ledger',
        bookSave: 'Save ledger',
        bookName: 'Name',
        needBook: 'No ledger yet. Upload one under "Ledger setup" first.',
        useBook: 'Ledger',
        use: 'Use',
        inUse: 'active',
        delete: 'Delete',
        save: 'Save',
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

    // ── 样式（只引主题 token；`color-mix` 用来做 hover / 淡色底）──────────

    const CSS = `
.jw-root{display:flex;flex-direction:column;height:100%;min-height:0;background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font-size:13px;line-height:1.55}
.jw-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--dsw-alias-border-l1);flex:none}
.jw-title{margin:0;font-size:15px;font-weight:600}
.jw-spacer{flex:1;min-width:8px}
.jw-muted{color:var(--dsw-alias-label-secondary)}
.jw-small{font-size:12px}
.jw-mono{font-family:var(--ds-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:11.5px}
.jw-body{flex:1;min-height:0;overflow:auto;padding:12px 16px 56px;display:flex;flex-direction:column;gap:10px}
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
.jw-table{width:100%;border-collapse:collapse;font-size:12px}
.jw-table th,.jw-table td{border-bottom:1px solid var(--dsw-alias-border-l1);padding:6px 8px;text-align:left;vertical-align:top}
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
.jw-body{padding:16px 20px;display:flex;flex-direction:column;gap:12px;overflow:auto}
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

/* ── 首页：配置区 + 功能卡片 ── */
.jw-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.jw-grid-config{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.jw-tile{display:flex;flex-direction:column;gap:6px;align-items:flex-start;text-align:left;
  padding:14px 16px;border-radius:14px;border:1px solid var(--dsw-alias-border-l1);
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;
  cursor:pointer;transition:transform .12s,border-color .12s,box-shadow .12s;min-height:118px}
.jw-tile:hover:not(.off){border-color:var(--dsw-alias-brand-primary);transform:translateY(-1px);
  box-shadow:0 10px 24px -18px rgba(0,0,0,.5)}
.jw-tile.off{opacity:.62;cursor:not-allowed}
.jw-tile-config{background:color-mix(in srgb,var(--dsw-alias-brand-primary) 5%,var(--dsw-alias-bg-layer-1))}
.jw-tile-title{font-size:13.5px;font-weight:650;letter-spacing:.01em}
.jw-tile-desc{font-size:11.5px;line-height:1.55;color:var(--dsw-alias-label-secondary);
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.jw-tile-line{font-size:11px;color:var(--dsw-alias-label-secondary);line-height:1.5}
.jw-tile-foot{margin-top:auto;padding-top:6px;font-size:11.5px;font-weight:600;color:var(--dsw-alias-brand-primary)}
.jw-chip{display:inline-block;font-size:10.5px;line-height:1.7;padding:0 8px;border-radius:999px;
  border:1px solid var(--dsw-alias-border-l2);color:var(--dsw-alias-label-secondary)}
.jw-chip.on{color:var(--dsw-alias-state-success-primary);
  border-color:color-mix(in srgb,var(--dsw-alias-state-success-primary) 45%,transparent);
  background:color-mix(in srgb,var(--dsw-alias-state-success-primary) 10%,transparent)}
.tone-good{border-top:3px solid var(--dsw-alias-state-success-primary)}
.tone-warn{border-top:3px solid var(--dsw-alias-state-warn-primary)}
.tone-bad{border-top:3px solid var(--dsw-alias-state-error-primary)}
.jw-btn-ghost{background:transparent;border-color:var(--dsw-alias-border-l1)}
.jw-book{display:flex;gap:10px;align-items:center;padding:10px 2px;
  border-bottom:1px solid var(--dsw-alias-border-l1)}
.jw-book:last-child{border-bottom:0}
.jw-book.on{background:color-mix(in srgb,var(--dsw-alias-brand-primary) 6%,transparent);border-radius:10px;padding-left:8px;padding-right:8px}
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

    function NewJob({ api, features, jobs, t, onStarted, onError, setBusy, busy, fixedFeature }) {
      const [featureId, setFeatureId] = useState(fixedFeature || '')
      const [uploaded, setUploaded] = useState(null)
      const [localPath, setLocalPath] = useState('')
      const [inspect, setInspect] = useState(null)
      const [mapping, setMapping] = useState({})
      const [upstream, setUpstream] = useState('')
      const [working, setWorking] = useState('')
      const inputRef = useRef(null)

      const feature = features.find((item) => item.id === featureId) || features[0] || null

      useEffect(() => {
        if (fixedFeature && featureId !== fixedFeature) setFeatureId(fixedFeature)
        else if (!featureId && features.length) setFeatureId(features[0].id)
      }, [features, featureId, fixedFeature])

      const runInspect = useCallback(async (file, featureIdValue) => {
        setWorking(t('inspecting'))
        onError('')
        try {
          const data = await api.post('/inspect', { file, feature: featureIdValue })
          setInspect(data.inspect)
          setMapping(data.inspect.suggested_mapping || {})
        } catch (error) {
          setInspect(null)
          onError(`${t('loadFailed')}: ${error.message}`)
        } finally {
          setWorking('')
        }
      }, [api, onError, t])

      const onPick = useCallback(async (file) => {
        if (!file || !feature) return
        setWorking(t('inspecting'))
        try {
          const up = await api.upload(file)
          setUploaded(up)
          setLocalPath('')
          await runInspect(up.path, feature.id)
        } catch (error) {
          setWorking('')
          onError(`${t('loadFailed')}: ${error.message}`)
        }
      }, [api, feature, runInspect, t, onError])

      const start = useCallback(async () => {
        if (!feature) return
        setBusy(true)
        onError('')
        try {
          const body = { feature: feature.id, mapping }
          if (upstream) body.upstream = upstream
          else if (uploaded) body.file = uploaded.path
          else {
            if (!localPath.trim()) throw new Error(t('file'))
            body.file = localPath.trim()
          }
          const data = await api.post('/start', body)
          setUploaded(null)
          setInspect(null)
          setMapping({})
          setUpstream('')
          if (inputRef.current) inputRef.current.value = ''
          onStarted(data.job_id)
        } catch (error) {
          onError(`${t('start')}: ${error.message}`)
        } finally {
          setBusy(false)
        }
      }, [api, feature, mapping, uploaded, localPath, upstream, onStarted, onError, setBusy, t])

      const missing = useMemo(() => {
        if (!inspect) return []
        return (inspect.fields || [])
          .filter((field) => field.required && !mapping[field.key])
          .map((field) => field.label)
      }, [inspect, mapping])

      const upstreamJobs = useMemo(() => {
        if (!feature || !feature.accepts_upstream?.length) return []
        return jobs.filter((job) => feature.accepts_upstream.includes(job.feature)
          && (job.clean_status === 'done' || job.clean_status === 'skipped'))
      }, [jobs, feature])

      if (!feature) {
        return h('div', { className: 'jw-card' }, h('p', { className: 'jw-muted' }, t('loadFailed')))
      }

      return h('div', { className: 'jw-card' },
        h('header', null,
          h('h3', null, t('newJob')),
          h('span', { className: 'jw-muted jw-small' }, feature.description || ''),
          h('span', { className: 'jw-spacer' }),
          !fixedFeature && h('select', {
            className: 'jw-select',
            value: feature.id,
            onChange: (event) => {
              setFeatureId(event.target.value)
              setInspect(null)
              setUploaded(null)
            },
          }, features.map((item) => h('option', { key: item.id, value: item.id }, item.name)))),

        upstreamJobs.length > 0 && h('div', { className: 'jw-row', style: { marginBottom: 10 } },
          h('span', { className: 'jw-muted jw-small' }, t('upstream')),
          h('select', {
            className: 'jw-select',
            value: upstream,
            onChange: (event) => setUpstream(event.target.value),
          },
          h('option', { value: '' }, t('upstreamNone')),
          upstreamJobs.map((job) => h('option', { key: job.job_id, value: job.job_id },
            `${job.filename} · ${job.job_id}`)))),

        !inspect && !upstream && h('div', null,
          h('label', { className: 'jw-drop' },
            h('input', {
              ref: inputRef,
              type: 'file',
              accept: (feature.accept || ['.xlsx', '.csv']).join(','),
              style: { display: 'none' },
              onChange: (event) => {
                const file = event.target.files?.[0]
                if (file) onPick(file)
              },
            }),
            h('div', null, t('chooseFile')),
            h('div', { className: 'jw-muted jw-small' },
              `${(feature.accept || []).join(' / ')} · ${(feature.requires || []).length} 个必需字段`)),
          h('div', { className: 'jw-row', style: { marginTop: 10 } },
            h('span', { className: 'jw-muted jw-small' }, t('orPath')),
            h('input', {
              className: 'jw-input',
              style: { flex: 1, minWidth: 200 },
              placeholder: t('localPath'),
              value: localPath,
              onChange: (event) => setLocalPath(event.target.value),
            }),
            h('button', {
              className: 'jw-btn',
              disabled: !!working || !localPath.trim(),
              onClick: () => runInspect(localPath.trim(), feature.id),
            }, t('inspect')))),

        inspect && h('div', null,
          h('div', { className: 'jw-row', style: { marginBottom: 8 } },
            h('strong', null, inspect.filename),
            h('span', { className: 'jw-muted jw-small' },
              `${fmtNumber(inspect.rows)} ${t('rows')} · ${(inspect.columns || []).length} 列 · ${inspect.engine || ''}`),
            h('span', { className: 'jw-spacer' }),
            h('button', {
              className: 'jw-btn',
              onClick: () => {
                setInspect(null)
                setUploaded(null)
                setLocalPath('')
              },
            }, t('file'))),

          h('div', { style: { marginBottom: 6 } }, h('strong', null, t('mapping')),
            h('span', { className: 'jw-muted jw-small', style: { marginLeft: 8 } }, t('mappingHint'))),

          h('div', { className: 'jw-map' },
            (inspect.fields || []).map((field) => h(React.Fragment, { key: field.key },
              h('label', { className: 'jw-small' }, field.label,
                field.required ? h('span', { className: 'jw-req' }, '*') : null),
              h('select', {
                className: 'jw-select',
                value: mapping[field.key] || '',
                onChange: (event) => {
                  const next = { ...mapping }
                  if (event.target.value) next[field.key] = event.target.value
                  else delete next[field.key]
                  setMapping(next)
                },
              },
              h('option', { value: '' }, '（不映射）'),
              (inspect.columns || []).map((column) => h('option', { key: column, value: column }, column))),
              h('span', { className: 'jw-muted jw-small' }, field.required ? t('required') : '')))),

          (inspect.missing || []).length > 0 && h('div', {
            className: 'jw-banner err',
            style: { marginTop: 10 },
          }, `${t('mapping')}: ${inspect.missing_labels?.join('、') || inspect.missing.join(', ')}`),

          h('div', { className: 'jw-row', style: { marginTop: 12 } },
            h('button', {
              className: 'jw-btn jw-btn-primary',
              disabled: !!working || !!busy || missing.length > 0,
              onClick: start,
            }, working || busy ? t('starting') : t('start')),
            missing.length > 0 && h('span', { className: 'jw-muted jw-small' },
              `${t('mappingHint')}：${missing.join('、')}`))),

        upstream && h('div', { className: 'jw-row', style: { marginTop: 8 } },
          h('button', {
            className: 'jw-btn jw-btn-primary',
            disabled: !!busy,
            onClick: start,
          }, busy ? t('starting') : t('start')),
          h('span', { className: 'jw-muted jw-small' }, upstream)),

        working && h('div', { className: 'jw-row', style: { marginTop: 8 } },
          h('span', { className: 'jw-muted jw-small' }, working)))
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

    // ── 首页的两种卡片 ──────────────────────────────────────────────────
    function ConfigCard({ title, chip, chipOn, lines, action, onOpen }) {
      return h('button', { className: 'jw-tile jw-tile-config', onClick: onOpen },
        h('span', { className: 'jw-tile-title' }, title),
        h('span', { className: `jw-chip${chipOn ? ' on' : ''}` }, chip),
        (lines || []).map((line, index) => h('span', { key: index, className: 'jw-tile-line' }, line)),
        h('span', { className: 'jw-tile-foot' }, action))
    }

    function FeatureCard({ feature, state, onClick }) {
      return h('button', {
        className: `jw-tile jw-tile-feature${state.disabled ? ' off' : ''}${state.tone ? ` tone-${state.tone}` : ''}`,
        onClick,
      },
      h('span', { className: 'jw-tile-title' }, feature.name),
      h('span', { className: `jw-chip${state.chipOn ? ' on' : ''}` }, state.chip),
      h('span', { className: 'jw-tile-desc' }, feature.description || ''),
      state.note && h('span', { className: 'jw-tile-line' }, state.note))
    }

    // ── 序时账配置 ──────────────────────────────────────────────────────
    /**
     * 一个账套 = 一份被确认过的序时账（原件副本 + 字段映射）。
     *
     * 四个功能都对同一份账干活，而且互相有依赖，所以账**只在这里配一次**；
     * 要换账就再配一个账套，不在同一个账套里来回换。
     */
    function BookPanel({ api, t, books, onChanged, onUse, activeId, onError, setBusy }) {
      const [uploaded, setUploaded] = useState(null)
      const [inspect, setInspect] = useState(null)
      const [mapping, setMapping] = useState({})
      const [name, setName] = useState('')
      const [working, setWorking] = useState('')
      const inputRef = useRef(null)

      const pick = useCallback(async (file) => {
        if (!file) return
        setWorking(t('inspecting'))
        onError('')
        try {
          const up = await api.upload(file)
          setUploaded(up)
          setName(up.name || '')
          const data = await api.post('/inspect', { file: up.path, feature: 'book' })
          setInspect(data.inspect)
          setMapping(data.inspect.suggested_mapping || {})
        } catch (error) {
          setInspect(null)
          onError(`${t('loadFailed')}: ${error.message}`)
        } finally {
          setWorking('')
        }
      }, [api, onError, t])

      const save = useCallback(async () => {
        if (!uploaded) return
        setBusy(true)
        onError('')
        try {
          const data = await api.post('/books', {
            file: uploaded.path, name: name || uploaded.name, mapping,
          })
          setUploaded(null)
          setInspect(null)
          setMapping({})
          setName('')
          if (inputRef.current) inputRef.current.value = ''
          await onChanged(data.book?.id)
        } catch (error) {
          onError(`${t('save')}: ${error.message}`)
        } finally {
          setBusy(false)
        }
      }, [api, uploaded, name, mapping, onChanged, onError, setBusy, t])

      const missing = (inspect?.missing || [])
      const fields = inspect?.fields || []

      return h('div', { className: 'jw-stack' },
        books.length > 0 && h('div', { className: 'jw-card' },
          h('div', { className: 'jw-section' }, t('bookList')),
          books.map((book) => h('div', {
            key: book.id,
            className: `jw-book${book.id === activeId ? ' on' : ''}`,
          },
          h('div', { className: 'jw-step-body' },
            h('div', { className: 'jw-step-title' }, book.name),
            h('div', { className: 'jw-muted jw-small' },
              `${book.rows ? `${fmtNumber(book.rows)} 行 · ` : ''}${book.created_at?.slice(0, 19).replace('T', ' ')}`
              + ` · ${Object.keys(book.mapping || {}).length} 个字段`)),
          h('span', { className: 'jw-spacer' }),
          book.id === activeId
            ? h('span', { className: 'jw-chip on' }, t('inUse'))
            : h('button', { className: 'jw-btn', onClick: () => onUse(book.id) }, t('use')),
          h('button', {
            className: 'jw-btn jw-btn-ghost',
            onClick: async () => {
              try {
                await api.post('/books/delete', { id: book.id })
                await onChanged()
              } catch (error) {
                onError(error.message)
              }
            },
          }, t('delete'))))
        ),

        h('div', { className: 'jw-card' },
          h('div', { className: 'jw-section' }, books.length ? t('bookNew') : t('bookTitle')),
          h('p', { className: 'jw-muted jw-small', style: { marginTop: 0 } }, t('bookHint')),

          !inspect && h('div', null,
            h('label', { className: 'jw-drop' },
              h('input', {
                ref: inputRef,
                type: 'file',
                accept: '.xlsx,.xls,.xlsm,.csv',
                style: { display: 'none' },
                onChange: (event) => {
                  const file = event.target.files?.[0]
                  if (file) pick(file)
                },
              }),
              h('div', null, t('chooseFile')),
              h('div', { className: 'jw-muted jw-small' }, '.xlsx / .xls / .csv')),
            working && h('div', { className: 'jw-muted jw-small', style: { marginTop: 8 } }, working)),

          inspect && h('div', null,
            h('div', { className: 'jw-row', style: { marginBottom: 10 } },
              h('strong', null, inspect.filename),
              h('span', { className: 'jw-muted jw-small' },
                `${fmtNumber(inspect.rows)} ${t('rows')} · ${(inspect.columns || []).length} 列`),
              h('span', { className: 'jw-spacer' }),
              h('button', {
                className: 'jw-btn jw-btn-ghost',
                onClick: () => { setInspect(null); setUploaded(null) },
              }, t('cancel'))),

            missing.length > 0 && h('div', { className: 'jw-banner err', style: { marginBottom: 10 } },
              `${t('mapping')}: ${(inspect.missing_labels || missing).join('、')}`),

            h('div', { className: 'jw-map' }, fields.map((field) => h(React.Fragment, { key: field.key },
              h('span', { className: 'jw-muted jw-small' }, field.label,
                field.required && h('span', { className: 'jw-req' }, '*')),
              h('select', {
                className: 'jw-select',
                value: mapping[field.key] || '',
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
              h('option', { value: '' }, '—'),
              (inspect.columns || []).map((column) => h('option', { key: column, value: column }, column))),
              h('span', { className: 'jw-muted jw-small' },
                (inspect.preview?.[field.key]?.samples || []).slice(0, 2).join(' / '))))),

            h('div', { className: 'jw-row', style: { marginTop: 12 } },
              h('span', { className: 'jw-muted jw-small' }, t('bookName')),
              h('input', {
                className: 'jw-input', style: { flex: 1, minWidth: 160 },
                value: name, onChange: (event) => setName(event.target.value),
              }),
              h('button', {
                className: 'jw-btn jw-btn-primary',
                disabled: missing.length > 0 || !!working,
                onClick: save,
              }, t('bookSave'))))))
    }

    // ── 功能详情：用当前账套开工 ────────────────────────────────────────
    function FeatureStart({ api, t, feature, book, onStarted, onError, busy, setBusy, blocked }) {
      const start = useCallback(async () => {
        if (!book) return
        setBusy(true)
        onError('')
        try {
          const data = await api.post('/start', {
            feature: feature.id,
            file: book.file,
            mapping: book.mapping || {},
          })
          onStarted(data.job_id)
        } catch (error) {
          onError(`${t('start')}: ${error.message}`)
        } finally {
          setBusy(false)
        }
      }, [api, feature, book, onStarted, onError, setBusy, t])

      if (!book) {
        return h('div', { className: 'jw-card' },
          h('div', { className: 'jw-banner err' }, t('needBook')))
      }

      return h('div', { className: 'jw-card' },
        h('header', null,
          h('h3', null, t('newJob')),
          h('span', { className: 'jw-muted jw-small' }, feature.description || '')),
        h('div', { className: 'jw-row', style: { marginBottom: 10 } },
          h('span', { className: 'jw-muted jw-small' }, t('useBook')),
          h('strong', null, book.name),
          h('span', { className: 'jw-muted jw-small' },
            `${Object.keys(book.mapping || {}).length} 个字段已配置`)),
        blocked && h('div', { className: 'jw-banner err', style: { marginBottom: 10 } }, blocked),
        h('button', {
          className: 'jw-btn jw-btn-primary',
          disabled: !!busy || !!blocked,
          onClick: start,
        }, t('start')))
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
      const [books, setBooks] = useState([])
      const [bookId, setBookId] = useState('')
      const [env, setEnv] = useState(null)
      const [repo, setRepo] = useState('')
      const [view, setView] = useState('home')
      const [jobId, setJobId] = useState('')
      const [job, setJob] = useState(null)
      const [review, setReview] = useState(null)
      const [error, setError] = useState('')
      const [notice, setNotice] = useState('')
      const [busy, setBusy] = useState(false)
      const [epoch, setEpoch] = useState(0)
      const [booting, setBooting] = useState(true)
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

      const loadBooks = useCallback(async (selectId) => {
        try {
          const data = await api.get('/books')
          const list = data.books || []
          setBooks(list)
          setBookId((current) => selectId || current || list[0]?.id || '')
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
        loadBooks()
        loadEnv(false)
      }, [loadFeatures, loadJobs, loadBooks, loadEnv])

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

      const book = books.find((item) => item.id === bookId) || books[0] || null

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
          loadBooks()
        } catch (caught) {
          setError(caught.message)
        } finally {
          setBusy(false)
        }
      }, [api, jobId, review, loadBooks])

      const waive = useCallback(async () => {
        if (!review?.stage) return
        setBusy(true)
        try {
          await api.post('/decide', { job_id: jobId, stage: review.stage, decisions: [], waive: true })
          setReview(null)
          setEpoch((value) => value + 1)
          loadBooks()
        } catch (caught) {
          setError(caught.message)
        } finally {
          setBusy(false)
        }
      }, [api, jobId, review, loadBooks])

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

      // ── 功能卡片的状态 ──
      const envReady = (id) => {
        if (!env || env.error || !env.ready) return true
        return NEEDS_MODEL.has(id) ? !!env.ready.model : !!env.ready.basic
      }
      const runOf = (id) => book?.runs?.[id]
      const depsDone = (id) => (FEATURE_DEPS[id] || []).every((dep) => runOf(dep)?.status === 'done')
      const depNames = (id) => (FEATURE_DEPS[id] || [])
        .filter((dep) => runOf(dep)?.status !== 'done')
        .map((dep) => features.find((item) => item.id === dep)?.name || dep)

      const cardState = (feature) => {
        if (!envReady(feature.id)) {
          return { chip: t('envNotReady'), chipOn: false, disabled: true, note: t('cardEnv'), tone: 'warn' }
        }
        if (!book) {
          return { chip: t('cardNoBook'), chipOn: false, disabled: true, note: t('cardNoBookHint'), tone: 'warn' }
        }
        const miss = depNames(feature.id)
        if (miss.length) {
          return { chip: t('cardBlocked'), chipOn: false, disabled: true, note: `${t('cardDeps')}${miss.join('、')}`, tone: 'warn' }
        }
        const run = runOf(feature.id)
        if (!run) return { chip: t('cardReady'), chipOn: false, disabled: false, note: '' }
        const label = statusWord(t, run.status)
        return {
          chip: label,
          chipOn: run.status === 'done',
          disabled: false,
          note: run.awaiting ? `${t('awaiting')}：${run.awaiting}` : '',
          tone: run.status === 'failed' ? 'bad' : run.status === 'done' ? 'good' : '',
        }
      }

      const currentFeature = view.startsWith('feature:')
        ? features.find((item) => item.id === view.slice(8)) || null
        : null

      const openFeature = (id) => {
        setView(`feature:${id}`)
        setJobId('')
        setJob(null)
        setReview(null)
        // 已有任务就直接打开最近那个
        const run = book?.runs?.[id]
        if (run?.job_id) setJobId(run.job_id)
      }

      const head = h('div', { className: 'jw-head' },
        h('h2', { className: 'jw-title' }, t('title')),
        repo && h('span', { className: 'jw-muted jw-small' }, repo),
        h('span', { className: 'jw-spacer' }),
        h('button', {
          className: 'jw-btn jw-btn-ghost',
          onClick: () => { loadJobs(); loadBooks(); loadEnv(true) },
        }, t('refresh')))

      const body = []
      body.push(booting && !error && h('div', { className: 'jw-banner' }, t('connecting')))
      body.push(error && h('div', { className: 'jw-banner err' }, error))
      body.push(notice && !error && h('div', { className: 'jw-banner ok' }, notice))

      if (view === 'home') {
        const envOk = !!env?.ready?.model
        body.push(h('div', { className: 'jw-section' }, t('sectionConfig')))
        body.push(h('div', { className: 'jw-grid jw-grid-config' },
          h(ConfigCard, {
            title: t('envTab'),
            chip: !env ? t('connectingShort') : envOk ? t('envReady') : t('envNotReady'),
            chipOn: envOk,
            lines: !env ? [] : [
              `Python ${env.pythonVersion || '—'} · ${env.venv?.exists ? 'venv ✓' : 'venv ✗'}`,
              `torch ${env.packages?.torch?.version || '—'}${env.torchCuda ? `（CUDA ${env.torchCuda}）` : ''}`,
            ],
            action: envOk ? t('open') : t('fix'),
            onOpen: () => setView('env'),
          }),
          h(ConfigCard, {
            title: t('bookTab'),
            chip: book ? t('inUse') : t('bookNone'),
            chipOn: !!book,
            lines: book
              ? [book.name, `${Object.keys(book.mapping || {}).length} 个字段已配置`,
                `${Object.keys(book.runs || {}).length} 个功能跑过`]
              : [t('bookHint')],
            action: book ? t('open') : t('bookConfigure'),
            onOpen: () => setView('books'),
          })))

        body.push(h('div', { className: 'jw-section', style: { marginTop: 16 } }, t('sectionFeature')))
        body.push(h('div', { className: 'jw-grid' }, features.map((feature) => h(FeatureCard, {
          key: feature.id,
          feature,
          state: cardState(feature),
          onClick: () => { if (!cardState(feature).disabled) openFeature(feature.id) },
        }))))
      }

      if (view === 'env') {
        body.push(h(Detail, { title: t('envTab'), t, onBack: () => setView('home') },
          h(EnvPanel, { api, t, env, onReload: () => loadEnv(true), onError: setError })))
      }

      if (view === 'books') {
        body.push(h(Detail, {
          title: t('bookTab'),
          hint: book ? book.name : '',
          t,
          onBack: () => setView('home'),
        },
        h(BookPanel, {
          api, t, books, activeId: book?.id || '',
          onChanged: async (selectId) => { await loadBooks(selectId); await loadJobs() },
          onUse: (id) => setBookId(id),
          onError: setError,
          setBusy,
        })))
      }

      if (currentFeature) {
        const state = cardState(currentFeature)
        const tabJobs = jobs.filter((item) => item.feature === currentFeature.id)
        body.push(h(Detail, {
          title: currentFeature.name,
          hint: currentFeature.description,
          t,
          onBack: () => { setView('home'); setJobId(''); setJob(null); setReview(null) },
        },
        h('div', { className: 'jw-row', style: { marginBottom: 10 } },
          h('span', { className: 'jw-muted jw-small' }, t('job')),
          h('select', {
            className: 'jw-select', value: jobId, style: { maxWidth: 380 },
            onChange: (event) => { setError(''); setNotice(''); setJobId(event.target.value) },
          },
          h('option', { value: '' }, t('newJob')),
          tabJobs.map((item) => h('option', { key: item.job_id, value: item.job_id },
            `${statusWord(t, item.status)} · ${item.filename || item.job_id}`)))),
        !jobId && h(FeatureStart, {
          api, t, feature: currentFeature, book, busy, setBusy,
          onError: setError,
          blocked: state.disabled ? (state.note || state.chip) : '',
          onStarted: (id) => {
            setJobId(id)
            setEpoch((value) => value + 1)
            loadJobs()
            loadBooks(book?.id)
          },
        }),
        job && h(JobView, {
          job, chain, review, api, t, busy,
          onDecide: decide, onWaive: waive, onWake: wake, onCopy: copyLine, onResume: resume,
        })))
      }

      return h('div', { className: 'jw-root' }, h(StyleTag, null), head, h('div', { className: 'jw-body' }, body))
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
      __internals: { Workbench, JobView, Stages, ReviewTable, ResultPanel, NewJob, EnvPanel,
        BookPanel, FeatureStart, ConfigCard, FeatureCard, Detail, DICT, translate },
    }
  },
})
