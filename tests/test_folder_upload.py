# -*- coding: utf-8 -*-
"""上传文件夹模式测试（用户要求：文件夹也要能选着上传，输出只走浏览器下载）

覆盖：
  A. save_uploaded —— 文件夹上传的相对层级还原、路径穿越拦截、重名覆盖开关
  B. make_zip_bytes(base_dir) —— 打包保留目录层级
  C. Excel格式互转：文件夹上传 + 原样打包 -> 输出目录里既有转换结果也有原样带上的其他格式
  D. PDF合并：文件夹上传 + 按页分拆 -> 生成多卷，下载为 zip
  E. PDF索引：文件夹上传 -> 产物保留子目录层级
  F. 5 个工具页面都不再有「本机路径」输入框（旧的路径模式已移除）
  G. 后台任务 stop_event 中断：设置停止后应正常收尾、不抛异常
"""
import io
import os
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # 项目根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from _fixtures import drive, fixtures_for, start_tool_app, FakeUploadedFile  # noqa: E402
from other_tools.web import _common  # noqa: E402
from other_tools.web._common import (  # noqa: E402
    reset_work_dir, save_uploaded, make_zip_bytes, work_dir,
)
from other_tools.web import pdf_indexer  # noqa: E402

FAILS = []
PASSES = []


def ok(m):
    PASSES.append(m)
    print(f"[PASS] {m}", flush=True)


def bad(m):
    FAILS.append(m)
    print(f"[FAIL] {m}", flush=True)


def check(name, cond, detail=""):
    ok(f"{name}{'：' + detail if detail else ''}") if cond else bad(f"{name} -> {detail}")
    return cond


# ============ A. save_uploaded：层级 / 安全 / 覆盖 ============
def test_save_uploaded():
    wd = reset_work_dir("__folder_upload__")
    ups = [
        FakeUploadedFile(Path(__file__), name="我的文件夹/a.txt"),
        FakeUploadedFile(Path(__file__), name="我的文件夹/子目录/深层/b.txt"),
        FakeUploadedFile(Path(__file__), name="../越权/evil.txt"),
    ]
    saved = save_uploaded(ups, wd)
    rels = sorted(str(Path(p).relative_to(wd)) for p in saved)
    check("A1 文件夹上传保留目录层级", rels == [
        str(Path("我的文件夹") / "a.txt"),
        str(Path("我的文件夹") / "子目录" / "深层" / "b.txt"),
        str(Path("越权") / "evil.txt"),
    ], str(rels))
    check("A2 越权路径不逃出目标目录",
          all(Path(p).resolve().is_relative_to(wd.resolve()) for p in saved))

    # 重名：默认避让（加序号），overwrite=True 时覆盖
    up2 = [FakeUploadedFile(Path(__file__), name="我的文件夹/a.txt")]
    s2 = save_uploaded(up2, wd)
    check("A3 同名默认加序号避让", Path(s2[0]).name == "a_0.txt", Path(s2[0]).name)
    (Path(wd) / "我的文件夹" / "a.txt").write_text("旧内容", encoding="utf-8")
    s3 = save_uploaded(up2, wd, overwrite=True)
    check("A4 overwrite=True 直接覆盖",
          Path(s3[0]).name == "a.txt" and (Path(wd) / "我的文件夹" / "a.txt").read_text(
              encoding="utf-8") != "旧内容")
    reset_work_dir("__folder_upload__")


# ============ B. 打包保留层级 ============
def test_zip_keeps_structure():
    wd = reset_work_dir("__folder_upload__")
    d = wd / "out" / "子目录"
    d.mkdir(parents=True)
    (d / "x.pdf").write_bytes(b"x")
    (wd / "out" / "y.pdf").write_bytes(b"y")
    files = _common.list_outputs(wd / "out")
    zf = zipfile.ZipFile(io.BytesIO(make_zip_bytes(files, base_dir=wd / "out")))
    names = sorted(n.replace("\\", "/") for n in zf.namelist())
    check("B1 zip 内保留相对层级", names == sorted(["y.pdf", "子目录/x.pdf"]), str(names))
    reset_work_dir("__folder_upload__")


# ============ C. Excel格式互转：文件夹 + 原样打包 ============
def test_xls_convert_folder():
    at = start_tool_app("xls_convert", mode="folder")
    at.radio(key="xc__mode").set_value("🗂️ 上传文件夹")
    at.run()
    at.selectbox(key="xc_mode_idx").set_value(1)      # xlsx -> xls
    at.run()
    for cb in at.checkbox:
        if "原样打包" in cb.label:
            cb.set_value(True)
    at.run()
    at, err = drive(at, "开始转换")
    if err:
        bad(f"C1 文件夹转换：{err}")
        return
    if [e.value for e in at.error]:
        bad(f"C1 文件夹转换 error -> {[e.value for e in at.error][:2]}")
        return
    outs = sorted(str(p.relative_to(work_dir("xls_convert") / "output"))
                  for p in (work_dir("xls_convert") / "output").rglob("*") if p.is_file())
    expect = sorted([str(Path("上传文件夹") / "xlsconv" / "src.xls"),
                     str(Path("上传文件夹") / "xlsconv" / "说明.txt")])
    check("C1 转换结果 + 原样打包文件都在，且保留层级", outs == expect, str(outs))


# ============ D. PDF合并：文件夹 + 分拆 ============
def test_pdf_merge_folder_split():
    at = start_tool_app("pdf_merger", mode="folder")
    at.radio(key="pm__mode").set_value("🗂️ 上传文件夹")
    at.run()
    for cb in at.checkbox:
        if "分拆" in cb.label:
            cb.set_value(True)
    at.run()
    at.number_input(key="pm_step").set_value(1)      # 每 1 页一个分卷
    at.run()
    at, err = drive(at, "开始合并")
    if err:
        bad(f"D1 文件夹合并分拆：{err}")
        return
    if [e.value for e in at.error]:
        bad(f"D1 文件夹合并分拆 error -> {[e.value for e in at.error][:2]}")
        return
    parts = sorted(p.name for p in work_dir("pdf_merger").rglob("合并结果-*.pdf"))
    dls = [getattr(d, "label", "?") for d in at.get("download_button")]
    check("D1 分拆出多个分卷", len(parts) >= 2, str(parts))
    check("D2 多文件时给出打包下载", any("打包" in str(x) for x in dls), str(dls))


# ============ E. PDF索引：文件夹层级 ============
def test_pdf_indexer_folder():
    at = start_tool_app("pdf_indexer", mode="folder")
    at.radio(key="idx__mode").set_value("🗂️ 上传文件夹")
    at.run()
    at, err = drive(at, "添加索引")
    if err:
        bad(f"E1 文件夹索引：{err}")
        return
    outs = sorted(str(p.relative_to(work_dir("pdf_indexer") / "output"))
                  for p in (work_dir("pdf_indexer") / "output").rglob("*") if p.is_file())
    check("E1 索引产物保留上传时的子目录层级",
          outs == [str(Path("上传文件夹") / "pdfidx" / "doc.pdf")], str(outs))


# ============ F. 页面已无「本机路径」输入 ============
def test_no_path_input():
    bads = []
    for tool in ("excel_extract", "file_batch", "xls_convert", "pdf_indexer", "pdf_merger"):
        at = start_tool_app(tool, mode="files")
        for t in at.text_input:
            if "路径" in str(t.label):
                bads.append(f"{tool}:{t.label}")
    check("F1 5 个工具页面都没有「路径」输入框", not bads, str(bads))


# ============ G. stop_event 中断 ============
def test_stop_event():
    import threading
    wd = reset_work_dir("pdf_indexer")
    in_dir = wd / "input"
    in_dir.mkdir(parents=True, exist_ok=True)
    pdf = fixtures_for("pdf_indexer")[0]
    (in_dir / "doc.pdf").write_bytes(pdf.getvalue())
    out_dir = wd / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    font = _common.find_cjk_font()
    if not font:
        bad("G1 未找到系统中文字体，跳过中断测试")
        return
    stop = threading.Event()
    stop.set()                       # 一开始就要求停止
    logs = []
    try:
        res = pdf_indexer._run(logs.append, stop, [str(in_dir / "doc.pdf")], font, str(out_dir),
                               str(in_dir))
        check("G1 stop_event 生效时正常收尾（不抛异常）", "成功: 0" in str(res), str(res))
    except Exception as e:  # noqa: BLE001
        bad(f"G1 stop_event 中断抛异常：{type(e).__name__}: {e}")


def main():
    print("=== 上传文件夹模式 & 下载式输出 ===\n", flush=True)
    test_save_uploaded()
    test_zip_keeps_structure()
    test_xls_convert_folder()
    test_pdf_merge_folder_split()
    test_pdf_indexer_folder()
    test_no_path_input()
    test_stop_event()
    print()
    print(f"汇总: PASS={len(PASSES)}  FAIL={len(FAILS)}")
    for f in FAILS:
        print("  [FAIL]", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
