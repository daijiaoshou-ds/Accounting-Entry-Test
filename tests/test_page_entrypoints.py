# -*- coding: utf-8 -*-
"""页面级入口测试（第二轮）：直接调用 other_tools/web/*.py 里页面的 _run/_run_* 函数，
传入与 Streamlit 上传流程**完全相同的实参类型**（save_uploaded() 返回 Path、work_dir() 返回 Path），
验证「点按钮 -> 后台任务」这段真实交互链路。

第一轮测试的漏洞：直接调用 modules/*.py 的桌面函数并传 str 路径，绕过了页面层，
因此没能暴露 Path/str 类型不匹配与输出目录未创建这两类缺陷。
"""
import os
import shutil
import sys
import traceback
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
sys.path.insert(0, str(ROOT))

import pandas as pd

from other_tools.web._common import (
    save_uploaded, reset_work_dir, work_dir,
)
from other_tools.web import excel_extract, file_batch, xls_convert, pdf_indexer, pdf_merger

_WORK = HERE / "_work" / "pages"
shutil.rmtree(_WORK, ignore_errors=True)
_WORK.mkdir(parents=True, exist_ok=True)

RESULTS = []
_counts = {"PASS": 0, "FAIL": 0}


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    _counts[st] += 1
    RESULTS.append((st, name, detail))
    print(f"[{st}] {name}" + (f"  -> {detail}" if detail else ""), flush=True)


def section(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}", flush=True)


class FakeUpload:
    """模拟 st.file_uploader 返回的 UploadedFile：name + getbuffer()"""

    def __init__(self, path: Path):
        self.name = path.name
        self._b = path.read_bytes()

    def getbuffer(self):
        return memoryview(self._b)

    def getvalue(self):
        return self._b


def call_run(fn, *args, label=""):
    """调用页面后台任务函数，捕获异常，返回 (ok, result_or_error, logs)"""
    logs = []
    stop = threading.Event()
    try:
        r = fn(logs.append, stop, *args)
        return True, r, logs
    except Exception as e:
        return False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}", logs


def mk_pdf(path: Path, pages=2, text="T"):
    import fitz
    d = fitz.open()
    for i in range(pages):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"{text}{i + 1}", fontsize=14)
    d.save(str(path))
    d.close()


# ============================================================================
section("A. Excel数据提取 —— 页面 _run（上传流程，真实类型）")
d = _WORK / "excel"
src = d / "src.xlsx"
src.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame({"姓名": ["张三", "李四"], "金额": [1.0, 2.0]}).to_excel(src, index=False)

# 完全复刻 excel_extract.show_page 里 run_clicked 分支：调用页面自己的 prepare_run()
in_dir, save_path = excel_extract.prepare_run(is_csv=False)
saved = save_uploaded([FakeUpload(src)], in_dir)
print(f"  save_uploaded 返回类型: {[type(x).__name__ for x in saved]}")
print(f"  work_dir() 返回类型  : {type(work_dir('excel_extract')).__name__}")
print(f"  save_path = {save_path}")
print(f"  output 目录是否存在（调用前）: {Path(save_path).parent.exists()}")

ok, res, logs = call_run(
    excel_extract._run,
    str(in_dir), True, False, [], "all", [], [], 0.6, save_path,
    False, False, False, 1, False,
)
check("A1 Excel数据提取 页面 _run 不抛异常", ok, str(res)[:400] if not ok else "")
if ok:
    check("A2 Excel数据提取 返回成功", bool(res[0]) if isinstance(res, tuple) else False,
          f"返回={res}")
    check("A3 Excel数据提取 产物已生成", Path(save_path).exists(),
          f"{save_path} exists={Path(save_path).exists()} | 日志尾={logs[-3:]}")

# A4 CSV 输出分支（页面 is_csv=True 时走另一条保存逻辑）
in_dir_csv, save_csv = excel_extract.prepare_run(is_csv=True)
saved_csv = save_uploaded([FakeUpload(src)], in_dir_csv)
ok, res, logs = call_run(
    excel_extract._run,
    str(in_dir_csv), True, False, [], "all", [], [], 0.6, save_csv,
    False, True, False, 1, False,
)
check("A4 Excel数据提取 CSV输出分支 不抛异常", ok, str(res)[:300] if not ok else "")
if ok:
    check("A5 Excel数据提取 CSV 产物已生成", Path(save_csv).exists(),
          f"{save_csv} exists={Path(save_csv).exists()}")

# A6 文件夹上传分支（上传副本保留子目录层级，递归扫描）
d = _WORK / "excel_folder"
fsrc = d / "src"; fsrc.mkdir(parents=True, exist_ok=True)
pandas_df = pd.DataFrame({"c": [1, 2, 3]})
excel_folder_src = fsrc / "子目录" / "y.xlsx"
excel_folder_src.parent.mkdir(parents=True, exist_ok=True)
pandas_df.to_excel(excel_folder_src, index=False)
fout = d / "out.xlsx"
ok, res, logs = call_run(
    excel_extract._run,
    str(fsrc), True, True, [], "all", [], [], 0.6, str(fout),
    False, False, False, 1, False,
)
check("A6 Excel数据提取 文件夹上传（含子目录）分支 不抛异常", ok, str(res)[:300] if not ok else "")
if ok:
    check("A7 Excel数据提取 文件夹产物已生成", fout.exists(), f"exists={fout.exists()}")

# ============================================================================
section("B. 文件批量整理 —— 页面 _run_generate / _run_process（真实类型）")
d = _WORK / "filebatch"
files_dir = d / "files"
files_dir.mkdir(parents=True, exist_ok=True)
for n in ("1.txt", "2.txt"):
    (files_dir / n).write_text(n, encoding="utf-8")

wd2 = reset_work_dir("file_batch")
list_path = str(wd2 / "文件清单.xlsx")
print(f"  清单输出路径 = {list_path}; work 目录存在={wd2.exists()}")
ok, res, logs = call_run(file_batch._run_generate, str(files_dir), list_path)
check("B1 生成文件清单 _run_generate 不抛异常", ok, str(res)[:300] if not ok else "")
if ok:
    check("B2 清单已生成", Path(list_path).exists(), f"exists={Path(list_path).exists()}")

# 执行整理（用生成的清单）
if Path(list_path).exists():
    df = pd.read_excel(list_path)
    df["新文件夹名称"] = "归档"
    plan = d / "plan.xlsx"
    df.to_excel(plan, index=False)
    ok, res, logs = call_run(file_batch._run_process, str(plan), str(files_dir), True, False)
    check("B3 执行整理 _run_process 不抛异常", ok, str(res)[:300] if not ok else "")
    if ok:
        check("B4 文件已复制到 归档/", (files_dir / "归档" / "1.txt").exists(),
              f"目录={[p.name for p in files_dir.iterdir()]}")

# ============================================================================
section("C. Excel格式互转 —— 页面 _run_upload（真实类型）")
d = _WORK / "convert"
csrc = d / "src.xlsx"
csrc.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame({"a": [1, 2]}).to_excel(csrc, index=False)

wd3 = reset_work_dir("xls_convert")
in3 = wd3 / "input"; in3.mkdir(exist_ok=True)
out3 = wd3 / "output"; out3.mkdir(exist_ok=True)
saved3 = save_uploaded([FakeUpload(csrc)], in3)
print(f"  saved 类型: {[type(x).__name__ for x in saved3]}, out_dir={out3}")
# mode_index=1 => xlsx -> xls
ok, res, logs = call_run(xls_convert._run_upload, saved3, 1, str(out3))
check("C1 Excel格式互转 _run_upload 不抛异常", ok, str(res)[:400] if not ok else "")
if ok:
    outs = list(out3.iterdir())
    check("C2 转换产物已生成", len(outs) == 1, f"产物={[p.name for p in outs]}")
    check("C3 返回信息含成功计数", "成功" in str(res), str(res))

# 文件夹上传分支：保留子目录层级 + 原样打包非目标格式
d2 = _WORK / "convert_folder"
fsrc = d2 / "src"; fsrc.mkdir(parents=True, exist_ok=True)
pd.DataFrame({"b": [1]}).to_excel(fsrc / "x.xlsx", index=False)
(fsrc / "说明.txt").write_text("非目标格式", encoding="utf-8")
in3b = wd3 / "input_folder"; in3b.mkdir(exist_ok=True)
out3b = wd3 / "output_folder"; out3b.mkdir(exist_ok=True)
ffiles = save_uploaded([FakeUpload(fsrc / "x.xlsx"), FakeUpload(fsrc / "说明.txt")], in3b)
ok, res, logs = call_run(xls_convert._run_upload, ffiles, 1, str(out3b), str(in3b), True)
check("C4 Excel格式互转 文件夹上传分支 不抛异常", ok, str(res)[:400] if not ok else "")
if ok:
    outs = sorted(p.name for p in out3b.rglob("*") if p.is_file())
    check("C5 文件夹上传：转换结果 + 原样打包的非目标文件",
          outs == ["x.xls", "说明.txt"], f"out={outs}")

# ============================================================================
section("D. PDF索引号生成 —— 页面 _run（真实类型，用户报错点）")
d = _WORK / "pdfidx"
psrc = d / "doc.pdf"
psrc.parent.mkdir(parents=True, exist_ok=True)
mk_pdf(psrc, pages=2)

wd4 = reset_work_dir("pdf_indexer")
in4 = wd4 / "input"; in4.mkdir(exist_ok=True)
out4 = wd4 / "output"; out4.mkdir(exist_ok=True)
saved4 = save_uploaded([FakeUpload(psrc)], in4)
print(f"  saved 类型: {[type(x).__name__ for x in saved4]}")
font = "C:\\Windows\\Fonts\\simsun.ttc" if os.path.exists("C:\\Windows\\Fonts\\simsun.ttc") else ""

ok, res, logs = call_run(pdf_indexer._run, saved4, font, out4)
check("D1 PDF索引 _run 不抛异常", ok, (str(res)[:500] if not ok else ""))
if not ok:
    print("      ↑ 这就是用户遇到的 TypeError: unsupported operand type(s) for +: 'WindowsPath' and 'str'")
if ok:
    check("D2 索引产物已生成", len(list(out4.iterdir())) == 1,
          f"产物={[p.name for p in out4.iterdir()]} | 返回={res}")
    if list(out4.iterdir()):
        import fitz
        doc = fitz.open(str(next(out4.iterdir())))
        t = doc[0].get_text()
        check("D3 索引文字已写入", "1/2" in t, t.strip().replace("\n", "|")[:60])
        doc.close()

# ============================================================================
section("E. PDF合并 —— 页面 _run_merge 上传分支（真实类型）")
d = _WORK / "pdfmerge"
msrc = d / "a.pdf"
msrc.parent.mkdir(parents=True, exist_ok=True)
mk_pdf(msrc, pages=2)
mk_pdf(d / "b.pdf", pages=1)

wd5 = reset_work_dir("pdf_merger")
in5 = wd5 / "input"; in5.mkdir(exist_ok=True)
saved5 = save_uploaded([FakeUpload(msrc), FakeUpload(d / "b.pdf")], in5)
print(f"  saved 类型: {[type(x).__name__ for x in saved5]}, in_dir={in5}")
ok, res, logs = call_run(
    pdf_merger._run_merge, str(in5), False, False, False, False, False, 1,
)
check("E1 PDF合并 _run_merge(上传) 不抛异常", ok, str(res)[:500] if not ok else "")
if ok:
    outs = [p for p in in5.iterdir() if p.name.startswith("合并结果")]
    check("E2 合并产物已生成", len(outs) == 1, f"产物={[p.name for p in outs]}")
    if outs:
        import fitz
        doc = fitz.open(str(outs[0]))
        check("E3 合并页数=3", doc.page_count == 3, f"实际 {doc.page_count}")
        doc.close()

# 本地文件夹分支
d3 = _WORK / "pdfmerge_folder"
fd = d3 / "pdfs"; fd.mkdir(parents=True, exist_ok=True)
mk_pdf(fd / "1.pdf", pages=1)
mk_pdf(fd / "2.pdf", pages=2)
ok, res, logs = call_run(
    pdf_merger._run_merge, str(fd), False, False, False, False, False, 1,
)
check("E4 PDF合并 _run_merge(文件夹) 不抛异常", ok, str(res)[:400] if not ok else "")
if ok:
    check("E5 文件夹合并产物", (fd / "合并结果.pdf").exists(),
          f"目录={[p.name for p in fd.iterdir()]}")

# ============================================================================
print("\n" + "=" * 78)
print(f"汇总: PASS={_counts['PASS']}  FAIL={_counts['FAIL']}")
print("=" * 78)
for st, name, detail in RESULTS:
    if st == "FAIL":
        print(f"  [FAIL] {name} :: {detail[:300]}")
