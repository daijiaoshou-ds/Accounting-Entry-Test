# -*- coding: utf-8 -*-
"""小工具 —— 真实 UI 交互测试（AppTest 点按钮 + 假上传）

覆盖 5 个工具 × 两种来源（📁 上传文件 / 🗂️ 上传文件夹）：
「上传 -> 点按钮 -> 后台任务 -> 结果区 / 下载」全链路，并校验
  ① 页面无 st.error、无异常；
  ② 出现下载按钮（浏览器下载式输出）；
  ③ 产物落在系统临时目录，项目目录里不留任何产物。
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # 项目根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from _fixtures import drive, start_tool_app  # noqa: E402

from other_tools.web._common import work_dir  # noqa: E402

FAILS = []
PASSES = []
TOOLS = ["excel_extract", "file_batch", "xls_convert", "pdf_indexer", "pdf_merger"]
RUN_LABEL = {
    "excel_extract": "开始提取",
    "file_batch": "生成文件清单",
    "xls_convert": "开始转换",
    "pdf_indexer": "添加索引",
    "pdf_merger": "开始合并",
}
SRC_RADIO_KEY = {
    "excel_extract": "ex__mode",
    "file_batch": "fb_gen__mode",
    "xls_convert": "xc__mode",
    "pdf_indexer": "idx__mode",
    "pdf_merger": "pm__mode",
}


def ok(m):
    PASSES.append(m)
    print(f"[PASS] {m}", flush=True)


def bad(m):
    FAILS.append(m)
    print(f"[FAIL] {m}", flush=True)


def start(tool, mode):
    """启动 AppTest 会话（mode: files / folder）"""
    return start_tool_app(tool, mode)


def check_result(at, name, expect_download=True):
    """统一校验：无 error / 无异常 / 有成功提示 / 有下载按钮"""
    if at.exception:
        bad(f"{name}: 页面抛异常 -> {str(at.exception[0].value)[:400]}")
        return False
    errs = [e.value for e in at.error]
    if errs:
        bad(f"{name}: 页面出现 error -> {errs[:2]}")
        for c in at.code:
            if "Traceback" in c.value:
                print("    traceback:", c.value[:900])
        return False
    succ = [s.value for s in at.success]
    dls = list(at.get("download_button"))
    print(f"    success={succ[:1]} download={[getattr(d, 'label', '?') for d in dls]}")
    if expect_download and not dls:
        bad(f"{name}: 未出现下载按钮（可能任务失败）")
        return False
    ok(f"{name}: 交互成功（{succ[:1]}）")
    return True


def no_project_pollution(name):
    """产物不应写进项目目录（历史实现写在 other_tools/web/_work）"""
    stale = ROOT / "other_tools" / "web" / "_work"
    if stale.exists():
        bad(f"{name}: 项目目录里出现产物 -> {stale}")
        return False
    return True


def select_folder_mode(at, key):
    """把「数据来源」切到「上传文件夹」"""
    at.radio(key=key).set_value("🗂️ 上传文件夹")
    at.run()
    if at.exception:
        bad(f"切换文件夹模式异常: {str(at.exception[0].value)[:300]}")
        return False
    return True


def case(tool, mode):
    """单个工具 + 单个来源模式的完整交互"""
    tag = f"{tool}[{'文件夹' if mode == 'folder' else '文件'}]"
    at = start(tool, mode)
    if at.exception:
        bad(f"{tag}: 首轮渲染异常 {str(at.exception[0].value)[:400]}")
        return
    if not any(r.label == "数据来源" for r in at.radio):
        bad(f"{tag}: 找不到「数据来源」选项（应支持上传文件/上传文件夹）")
        return
    if mode == "folder" and not select_folder_mode(at, SRC_RADIO_KEY[tool]):
        return
    if tool == "xls_convert":
        at.selectbox(key="xc_mode_idx").set_value(1)   # xlsx -> xls
        at.run()

    at, err = drive(at, RUN_LABEL[tool])
    if err:
        bad(f"{tag}: {err}")
        return
    check_result(at, tag)
    no_project_pollution(tag)

    # PDF 合并：顺带校验产物落在临时目录
    if tool == "pdf_merger":
        outs = sorted(work_dir("pdf_merger").rglob("合并结果*.pdf"))
        if outs:
            ok(f"{tag}: 合并产物在临时目录 -> {outs[0].name}")
        else:
            bad(f"{tag}: 临时目录里找不到合并产物")


def file_batch_execute(mode):
    """文件批量整理特有链路：① 生成清单 -> 改清单 -> ② 执行整理（校验真的整理了）"""
    import pandas as pd
    tag = f"file_batch[{mode}][执行整理]"
    at = start("file_batch", mode)
    if mode == "folder" and not select_folder_mode(at, "fb_gen__mode"):
        return
    at, err = drive(at, "生成文件清单")
    if err:
        bad(f"{tag}: 生成清单失败 {err}")
        return

    # 模拟用户在下载的清单里填「新文件夹名称」，再上传回执（这里直接改工作目录里的清单）
    manifest = work_dir("file_batch") / "文件清单.xlsx"
    if not manifest.exists():
        bad(f"{tag}: 清单文件没生成到临时目录")
        return
    df = pd.read_excel(manifest, dtype=str).fillna("")
    if "文件路径" not in df.columns or not all(not str(v).strip().startswith("C:") for v in df["文件路径"]):
        bad(f"{tag}: 清单里的文件路径应是相对路径 -> {df['文件路径'].tolist()[:3]}")
        return
    df["新文件夹名称"] = "已整理"
    df.to_excel(manifest, index=False)

    for cb in at.checkbox:                 # 复制模式：保留原文件
        if "复制模式" in cb.label:
            cb.set_value(True)
    at.run()
    at, err = drive(at, "执行整理")
    if err:
        bad(f"{tag}: {err}")
        return
    errs = [e.value for e in at.error]
    if errs:
        bad(f"{tag}: error={errs[:2]}")
        return
    in_dir = work_dir("file_batch") / "input"
    sources = sorted(p.name for p in in_dir.rglob("*")
                     if p.is_file() and "已整理" not in p.parts)
    copied = sorted(p.name for p in (in_dir / "已整理").rglob("*") if p.is_file())
    if not copied or copied != sources:
        bad(f"{tag}: 整理结果不符合预期 -> 复制到已整理={copied}，原文件={sources}")
        return
    moved = sorted(str(p.relative_to(in_dir)) for p in in_dir.rglob("*") if p.is_file())
    dls = [getattr(d, "label", "?") for d in at.get("download_button")]
    ok(f"{tag}: 成功，产物={moved}，下载={dls}")
    no_project_pollution(tag)


def main():
    print("=== 5 个小工具 × 上传文件 / 上传文件夹 ===\n", flush=True)
    for tool in TOOLS:
        for mode in ("files", "folder"):
            print(f"\n--- {tool} / {mode} ---", flush=True)
            case(tool, mode)

    print("\n--- 文件批量整理：执行整理链路 ---", flush=True)
    for mode in ("files", "folder"):
        file_batch_execute(mode)

    print()
    print(f"汇总: PASS={len(PASSES)}  FAIL={len(FAILS)}")
    for f in FAILS:
        print("  [FAIL]", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
