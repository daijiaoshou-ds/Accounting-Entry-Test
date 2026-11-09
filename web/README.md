# web —— 序时账清洗前端（React + Vite）

只覆盖「清洗 + 复核」两页，其余功能仍在 Streamlit。两个前端**共存**：
Streamlit 8501 不动，本前端调 API 8502，同一份数据、同一套业务逻辑。

## 开发

```bash
# 1) 起后端（另开一个窗口）
./venv/Scripts/python.exe -m api.run_api --port 8502      # 或双击 start_api.bat

# 2) 起前端
cd web
npm install          # 首次
npm run dev          # http://localhost:5173
```

Vite 把 `/api` 代理到 `127.0.0.1:8502`（见 `vite.config.ts`），所以前端代码里
不用写域名，也没有 CORS 问题。

## 生产（单端口）

```bash
cd web && npm run build        # 产出 web/dist
./venv/Scripts/python.exe -m api.run_api --port 8502
```

`api/app.py` 末尾会把 `web/dist` 挂到 `/`，所以浏览器直接开
<http://127.0.0.1:8502> 就是成品；API 仍在同一个端口的 `/api/*` 下。

> 改了前端要重新 `npm run build`——后端只做静态托管，不会自动重建。

## 目录

```
src/
  main.tsx / App.tsx     外壳：顶部状态、tab、最近任务
  api.ts                 后端契约的唯一出口（fetch + 错误归一）
  types.ts               与 api/app.py 对应的类型
  styles.css             设计变量 + 组件样式（无 UI 框架）
  pages/
    CleanPage.tsx        上传 → 字段映射 → 进度 → 结果概览 + 导出
    ReviewPage.tsx       复核分页 / 桶筛选 / 未审数据下载
```

## UI 冒烟（可选）

```bash
npx playwright install chromium      # 一次性，约 120MB
node scripts/ui_smoke.cjs            # 需要后端已在 8502 运行
```

跑完在系统临时目录留 4 张截图（空态 / 映射 / 结果 / 复核），并打印 JS 错误数。
脚本默认用 `chromium-1254/chrome-win64/chrome.exe`，可用 `CHROME_PATH` 覆盖。

## 约定

1. **不写死业务常量**：桶列表来自 `/api/buckets`，字段来自 `/api/fields`，
   置信度档位来自返回的 `置信度` 字段。模型重训、桶表调整都不该改前端。
2. **没有"纠错"功能**：4.0 删除了程序学习回路。人工确认结论走
   「未审数据 → 复核 → 金标准 → 重训」，前端只提供 `/unreviewed` 下载。
3. **大表不进浏览器**：结果只走 `/rows` 分页（凭证级，每页 50），
   要看全量就点导出，别把几十万行塞进 DOM。
4. **错误只显示后端给的 message**：`api.ts` 把 `{error:{code,message}}` 归一成
   `ApiError`，页面直接展示，不自己编文案。
