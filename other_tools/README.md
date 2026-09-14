# other_tools —— 小工具集（Web 版）

原「基米工具箱」桌面版（CustomTkinter）里的办公工具，已移植为 Streamlit 页面。
**桌面版入口与未移植的模块已清理**，这里只保留 Web 版在用的东西。

## 目录结构

```
other_tools/
├── ui.py                  # 工具注册表 TOOLS_REGISTRY + 二级目录页 + 详情页分发
├── web/                   # 5 个工具的 Streamlit 页面
│   ├── _common.py         # 公共设施：上传/下载、后台任务、临时目录、字体探测
│   ├── excel_extract.py   # Excel数据提取
│   ├── file_batch.py      # 文件批量整理
│   ├── xls_convert.py     # Excel格式互转
│   ├── pdf_indexer.py     # PDF索引号生成
│   └── pdf_merger.py      # PDF合并/分拆/转换
└── modules/               # 原桌面版的纯逻辑函数（无 GUI 依赖，页面直接复用）
    ├── column_extractor.py / file_batch_tool.py / xls_to_xlsx.py
    ├── pdf_indexer.py / pdf_merger.py
    └── path_manager.py    # 资源路径（assets/ 目录）
```

## 页面 ↔ 底层对应关系

| 工具 | 页面（`web/`） | 底层入口（`modules/`） |
|---|---|---|
| Excel数据提取 | `excel_extract.py` | `column_extractor.core_process()`（Pandas / Polars 双引擎） |
| 文件批量整理 | `file_batch.py` | `file_batch_tool.generate_excel_template()` / `process_files_from_excel()` |
| Excel格式互转 | `xls_convert.py` | `xls_to_xlsx.convert_xls_to_xlsx_logic()` 等 5 个转换函数 |
| PDF索引号生成 | `pdf_indexer.py` | `pdf_indexer.add_index_to_pdf()` |
| PDF合并/分拆/转换 | `pdf_merger.py` | `pdf_merger.core_merge_process()` + `convert_*_to_pdf()` |

## 输入 / 输出约定（重要）

- **输入**：`_common.pick_source()` 统一渲染「📁 上传文件 / 🗂️ 上传文件夹」二选一。
  文件夹用 Streamlit 原生目录上传（浏览器 `webkitdirectory`），递归包含子文件夹；
  上传文件名形如 `所选文件夹/子目录/a.xlsx`，由 `_common.save_uploaded()` 在临时目录里还原出同样的层级。
- **工作目录**：`%TEMP%\ka_toolbox_work\<tool_id>`（`_common.work_dir()`），每次运行前清空。
  **不往项目目录写产物，也不改动用户上传的原文件。**
- **输出**：一律用 `_common.offer_downloads()` 交给浏览器下载 —— 单个文件直接下载，多个文件打包 zip
  （`make_zip_bytes(base_dir=...)` 会保留目录层级），与「序时账清洗」的输出方式一致。
- **后台任务**：`_common.start_task/poll_task/stop_task` + `show_logs()`，日志实时刷新、可中断。

## 新增一个工具的步骤

1. 在 `modules/` 里放纯逻辑函数（不要 import `customtkinter`/`tkinter`；页面用桩模块兜底，但纯逻辑不应依赖 GUI）。
2. 在 `web/` 里写 `<id>.py`，实现 `show_page(tool: dict)`，用 `_common` 的上传/下载/任务设施。
3. 在 `ui.py` 的 `TOOLS_REGISTRY` 里加一条：`id` / `icon` / `name` / `desc` / `features` / `page_module`。
4. 在 `tests/_fixtures.py` 的 `fixtures_for()` 里补该工具的样例文件，测试会自动覆盖到它。

## 附：PDF索引号生成的历史修复记录（2026-02-18）

**原问题**：部分页面打不上索引（旋转页、CropBox 偏移页、窄页/长文件名）。

| 问题场景 | 原代码行为 | 修复方案（`modules/pdf_indexer.py`） |
|---|---|---|
| 页面 `/Rotate` 90°/270°/180° | 按显示矩形算坐标，文字却写进 MediaBox 坐标系，索引越界/被裁/位置颠倒 | 显示坐标 → MediaBox 坐标按旋转角 4 分支显式换算 + 用 `write_text(matrix=)` 校正文字方向与落点 |
| CropBox 原点 ≠ (0,0)（如 WPS/裁剪过的 PDF） | 整条索引画到可视区之外，完全不可见 | 换算公式直接基于 `page.cropbox` 参与计算 |
| 窄页 / 超长文件名 | x 坐标为负，索引 80% 被裁掉 | 自动缩字号（下限 8pt），仍放不下则截断文件名、保留 `n/N` 页码 |
| 坐标换算依赖库内部行为 | 依赖 `TextWriter` 隐式补偿（实测 1.26.6 只做部分平移补偿，旋转场景错误） | 在代码里预消掉自动 `cm` 补偿，落点由显式矩阵完全控制 |

回归覆盖见 `tests/test_run_smalltools.py` 第 4 节（旋转 0/90/180/270 × CropBox 偏移 × 窄页）。
升级 PyMuPDF 后建议重跑该脚本。
