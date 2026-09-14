# tests —— 回归测试

全部是**独立脚本**（不依赖 pytest），每个脚本末尾都会打印 `汇总: PASS=n FAIL=m` 并用退出码表示成败。

```bat
:: 一键全跑
venv\Scripts\python.exe -X utf8 tests\run_all.py

:: 只跑某几类（按标题关键词过滤）
venv\Scripts\python.exe -X utf8 tests\run_all.py 小工具 银行

:: 单个脚本
venv\Scripts\python.exe -X utf8 tests\test_tools_ui_interaction.py
```

## 脚本清单

| 脚本 | 覆盖内容 |
|---|---|
| `test_run_smalltools.py` | 5 个小工具的**纯逻辑**（直接调 `other_tools/modules/*.py`）：列提取双引擎、清单生成/执行、5 种格式互转、PDF 索引（旋转/裁剪/窄页）、PDF 合并分拆转换 |
| `test_run_bankmatcher.py` | 银行流水匹配纯逻辑：字段识别、清洗、对方科目、多级匹配、月度差异、日期/金额解析边界 |
| `test_page_entrypoints.py` | **页面级入口**：以与 Streamlit 上传流程完全相同的实参类型调用 `other_tools/web/*.py` 的 `_run*`（曾经在这里漏掉 Path/str 类型不匹配与输出目录未建的问题） |
| `test_tools_ui_interaction.py` | **真实 UI 交互**（AppTest 点真按钮 + 假上传）：5 个工具 ×「上传文件 / 上传文件夹」，校验成功提示、下载按钮、产物落在系统临时目录 |
| `test_folder_upload.py` | 上传文件夹的细节：相对层级还原、路径穿越拦截、重名覆盖开关、zip 保层级、原样打包、stop_event 中断、页面已无「本机路径」输入 |
| `test_bank_ui_e2e.py` | 银行流水匹配全流程 UI（上传 → 字段映射 → 配对 → 核对 → 导出） |
| `test_navigation.py` | 从 `app.py` 走首页 → 5 个一级模块 → 小工具 → 5 个详情页的渲染链路 |
| `test_nav_clicks.py` | 首页/侧边栏按钮的真实点击流转与未知 id 兜底 |
| `probe_bank_behavior.py` | 诊断探针（不判成败）：把对方科目分析在真实场景下的行为打印出来，便于人工核对 |

## 约定

- **测试数据**统一放 `tests/_work/`（gitignore），由 `_fixtures.py` 按需生成，跑测试不会往项目里写产物。
- **AppTest 驱动脚本**（`_apptest_*.py`）只做一件事：把 `st.file_uploader` 换成假实现并打开对应的工具页，供测试用 `AppTest.from_file` 驱动。
  - `_apptest_tool.py`：环境变量 `TOOL_UNDER_TEST`（工具 id）+ `UPLOAD_MODE`（`files` / `folder`）。
- 小工具的工作目录在**系统临时目录**（`%TEMP%\ka_toolbox_work\<tool_id>`），测试通过
  `other_tools.web._common.work_dir()` 定位产物，不要硬编码路径。
- 项目根用 `Path(__file__).resolve().parents[1]` 取，别用 CWD（脚本从哪个目录启动都要能跑）。
