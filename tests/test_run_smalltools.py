# -*- coding: utf-8 -*-
"""小工具（other_tools）5 个 Web 功能的功能验证脚本。

覆盖：Excel数据提取 / 文件批量整理 / Excel格式互转 / PDF索引号生成 / PDF合并分拆
在临时目录里造数据 -> 调用与原页面完全相同的入口函数 -> 断言产物内容。
"""
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
sys.path.insert(0, str(ROOT))

import pandas as pd

from other_tools.web._common import load_desktop_module

_WORK = HERE / "_work" / "smalltools"
shutil.rmtree(_WORK, ignore_errors=True)
_WORK.mkdir(parents=True, exist_ok=True)
RESULTS = []
_counts = {"PASS": 0, "FAIL": 0, "WARN": 0}


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    _counts[status] += 1
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail else ""), flush=True)
    return cond


def warn(name, detail=""):
    _counts["WARN"] += 1
    RESULTS.append(("WARN", name, detail))
    print(f"[WARN] {name}  -> {detail}", flush=True)


def section(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}", flush=True)


def noop(*a, **k):
    pass


def mkdir(p):
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ============================================================================
# 1. Excel数据提取 (column_extractor)
# ============================================================================
def test_excel_extract():
    section("1. Excel数据提取 (other_tools/modules/column_extractor.py)")
    m = load_desktop_module("column_extractor")
    d = mkdir(_WORK / "extract")
    src = mkdir(d / "input")

    df1 = pd.DataFrame({"姓名": ["张三", "李四"], "部门": ["财务", "销售"],
                        "金额": [100.5, 200.25], "备注": ["a", "b"]})
    df2 = pd.DataFrame({"姓名": ["王五"], "部门": ["采购"],
                        "金额": [50.0], "备注": ["c"]})
    df1.to_excel(src / "a.xlsx", index=False)
    df2.to_excel(src / "b.xlsx", index=False)

    # 多 sheet 文件
    with pd.ExcelWriter(src / "multi.xlsx") as w:
        df1.to_excel(w, sheet_name="资产负债表", index=False)
        df2.to_excel(w, sheet_name="利润表", index=False)

    logs = []

    def run(out_name, **kw):
        out = d / out_name
        args = dict(source_path=str(src), is_folder=True, recursive=False,
                    target_sheets=[], extract_mode="all", exact_cols=[],
                    fuzzy_cols=[], fuzzy_threshold=0.6, save_path=str(out),
                    log_func=logs.append)
        args.update(kw)
        ok, msg = m.core_process(**args)
        return ok, msg, (out if out.exists() else None)

    # 1.1 全部列 - pandas
    # 输入目录含 a.xlsx(2行) / b.xlsx(1行) / multi.xlsx(2 sheet: 2+1行) => 6 行
    EXPECT_ROWS = 6
    ok, msg, out = run("all_pandas.xlsx")
    if check("1.1 提取全部列(Pandas)", ok and out is not None, msg):
        got = pd.read_excel(out)
        check("1.1b 行数=6（含多 Sheet 全展开）", len(got) == EXPECT_ROWS, f"实际 {len(got)}")
        check("1.1c 列齐全", set(["姓名", "部门", "金额", "备注"]).issubset(set(got.columns)),
              str(list(got.columns)))
        check("1.1d 带来源溯源列", {"_来源工作簿", "_来源工作表"}.issubset(set(got.columns)),
              str(list(got.columns)))

    # 1.2 全部列 - polars
    ok, msg, out = run("all_polars.xlsx", use_polars=True)
    if check("1.2 提取全部列(Polars)", ok and out is not None, msg):
        got = pd.read_excel(out)
        check("1.2b polars 行数=6", len(got) == EXPECT_ROWS, f"实际 {len(got)}")

    # 1.3 指定列 精确匹配
    ok, msg, out = run("exact.xlsx", extract_mode="specific", exact_cols=["姓名", "金额"])
    if check("1.3 指定列-精确匹配", ok and out is not None, msg):
        got = pd.read_excel(out)
        # 指定列 + 自动附加的来源溯源列 = 4 列
        check("1.3b 只含指定列(+来源列)",
              set(got.columns) == {"姓名", "金额", "_来源工作簿", "_来源工作表"},
              str(list(got.columns)))
        check("1.3c 行数=6", len(got) == EXPECT_ROWS, f"实际 {len(got)}")
        check("1.3d 数据无损",
              sorted(got["姓名"].astype(str)) == sorted(["张三", "李四", "王五"] * 2),
              str(sorted(got["姓名"].astype(str))))

    # 1.4 指定列 模糊匹配
    ok, msg, out = run("fuzzy.xlsx", extract_mode="specific", fuzzy_cols=["金额"])
    if check("1.4 指定列-模糊匹配", ok and out is not None, msg):
        got = pd.read_excel(out)
        check("1.4b 命中金额列", "金额" in got.columns, str(list(got.columns)))

    # 1.5 sheet 过滤
    ok, msg, out = run("sheet.xlsx", target_sheets=["利润表"])
    if check("1.5 指定工作表过滤", ok and out is not None, msg):
        got = pd.read_excel(out)
        check("1.5b 仅利润表 1 行", len(got) == 1, f"实际 {len(got)}")

    # 1.6 CSV 输出
    ok, msg, out = run("out.csv", is_csv=True)
    check("1.6 CSV 输出", ok and out is not None, msg)
    if out and out.exists():
        enc_ok = False
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                pd.read_csv(out, encoding=enc)
                enc_ok = True
                break
            except Exception:
                pass
        check("1.6b CSV 可被页面 _read_output 读回", enc_ok)

    # 1.7 raw_merge 堆叠模式
    ok, msg, out = run("raw.xlsx", raw_merge_mode=True)
    check("1.7 原封不动堆叠模式", ok and out is not None, msg)

    # 1.8 表头起始行
    src2 = mkdir(d / "input2")
    with pd.ExcelWriter(src2 / "hdr.xlsx") as w:
        pd.DataFrame([["报表", None, None], ["姓名", "金额", "部门"],
                      ["张三", 1, "财务"]]).to_excel(w, index=False, header=False)
    out = d / "hdr_out.xlsx"
    ok, msg = m.core_process(str(src2), True, False, [], "all", [], [], 0.6,
                             str(out), logs.append, header_start_row=2)
    if check("1.8 表头起始行=2", ok and out.exists(), msg):
        got = pd.read_excel(out)
        check("1.8b 表头正确识别", "姓名" in got.columns, str(list(got.columns)))

    # 1.9 空目录
    empty = mkdir(d / "empty")
    ok, msg = m.core_process(str(empty), True, False, [], "all", [], [], 0.6,
                             str(d / "none.xlsx"), logs.append)
    check("1.9 空目录返回友好错误", (not ok) and "未找到" in msg, f"ok={ok} msg={msg}")


# ============================================================================
# 2. 文件批量整理 (file_batch_tool)
# ============================================================================
def test_file_batch():
    section("2. 文件批量整理 (other_tools/modules/file_batch_tool.py)")
    m = load_desktop_module("file_batch_tool")
    d = mkdir(_WORK / "filebatch")
    src = mkdir(d / "files")
    for n in ["1.txt", "2.txt", "3.txt"]:
        (src / n).write_text(f"content {n}", encoding="utf-8")
    mkdir(src / "sub")

    # 2.1 生成清单
    tpl = d / "清单.xlsx"
    logs = []
    try:
        res = m.generate_excel_template(str(src), str(tpl), logs.append)
    except Exception as e:
        check("2.1 生成文件清单 Excel", False, f"抛异常 {type(e).__name__}: {e}")
        return
    if check("2.1 生成文件清单 Excel", tpl.exists(), f"return={res!r}"):
        df = pd.read_excel(tpl)
        need = ["原文件夹名称", "原文件名", "文件路径", "新文件夹名称", "新文件名"]
        check("2.1b 清单含固定 5 列", all(c in df.columns for c in need), str(list(df.columns)))
        check("2.1c 扫描到 3 个文件", len(df) == 3, f"实际 {len(df)}")

        # 2.2 重命名 + 移动（默认移动模式）
        df["新文件夹名称"] = "归档"
        df["新文件名"] = ["a" + str(i + 1) + ".txt" for i in range(len(df))]
        plan = d / "plan.xlsx"
        df.to_excel(plan, index=False)
        plogs = []
        try:
            r2 = m.process_files_from_excel(str(plan), str(src), plogs.append)
            check("2.2 按清单移动+重命名", (src / "归档" / "a1.txt").exists(),
                  f"return={r2!r} log_tail={plogs[-2:]}")
            check("2.2b 原位置已清空(移动语义)", not (src / "1.txt").exists())
        except Exception as e:
            check("2.2 按清单移动+重命名", False, f"抛异常 {type(e).__name__}: {e}")

        # 2.3 复制模式
        src3 = mkdir(d / "files3")
        (src3 / "x.txt").write_text("x", encoding="utf-8")
        tpl3 = d / "清单3.xlsx"
        m.generate_excel_template(str(src3), str(tpl3), logs.append)
        df3 = pd.read_excel(tpl3)
        df3["新文件夹名称"] = "copy"
        plan3 = d / "plan3.xlsx"
        df3.to_excel(plan3, index=False)
        try:
            m.process_files_from_excel(str(plan3), str(src3), logs.append, is_copy_mode=True)
            check("2.3 复制模式保留原文件",
                  (src3 / "x.txt").exists() and (src3 / "copy" / "x.txt").exists())
        except Exception as e:
            check("2.3 复制模式保留原文件", False, f"抛异常 {type(e).__name__}: {e}")

        # 2.4 自动避重（同名不覆盖）
        src4 = mkdir(d / "files4")
        (src4 / "y.txt").write_text("1", encoding="utf-8")
        mkdir(src4 / "dest")
        (src4 / "dest" / "y.txt").write_text("OLD", encoding="utf-8")
        tpl4 = d / "清单4.xlsx"
        m.generate_excel_template(str(src4), str(tpl4), logs.append)
        df4 = pd.read_excel(tpl4)
        df4 = df4[df4["原文件名"] == "y.txt"].copy()
        df4["新文件夹名称"] = "dest"
        plan4 = d / "plan4.xlsx"
        df4.to_excel(plan4, index=False)
        try:
            m.process_files_from_excel(str(plan4), str(src4), logs.append, is_copy_mode=True)
            old_kept = (src4 / "dest" / "y.txt").read_text(encoding="utf-8") == "OLD"
            new_files = [p.name for p in (src4 / "dest").iterdir()]
            check("2.4 同名自动避重（不覆盖）", old_kept and len(new_files) >= 2,
                  f"dest={new_files}")
        except Exception as e:
            check("2.4 同名自动避重（不覆盖）", False, f"抛异常 {type(e).__name__}: {e}")


# ============================================================================
# 3. Excel格式互转 (xls_to_xlsx)
# ============================================================================
def test_xls_convert():
    section("3. Excel格式互转 (other_tools/modules/xls_to_xlsx.py)")
    m = load_desktop_module("xls_to_xlsx")
    d = mkdir(_WORK / "convert")
    df = pd.DataFrame({"科目": ["库存现金", "银行存款"], "金额": [1.0, 2.0]})

    # 3.1 xlsx -> xls
    s1 = d / "src1.xlsx"
    df.to_excel(s1, index=False)
    o1 = d / "out1.xls"
    try:
        ok, msg = m.convert_xlsx_to_xls_logic(str(s1), str(o1))
        check("3.1 xlsx -> xls", ok and o1.exists(), msg)
        if o1.exists():
            back = pd.read_excel(o1)
            check("3.1b xls 内容正确", list(back["科目"]) == list(df["科目"]), str(back.to_dict("list")))
    except Exception as e:
        check("3.1 xlsx -> xls", False, f"抛异常 {type(e).__name__}: {e}")

    # 3.2 xls -> xlsx（用刚生成的 xls）
    if o1.exists():
        o2 = d / "out2.xlsx"
        try:
            ok, msg = m.convert_xls_to_xlsx_logic(str(o1), str(o2))
            check("3.2 xls -> xlsx", ok and o2.exists(), msg)
            if o2.exists():
                back = pd.read_excel(o2)
                check("3.2b 内容正确", list(back["科目"]) == list(df["科目"]))
        except Exception as e:
            check("3.2 xls -> xlsx", False, f"抛异常 {type(e).__name__}: {e}")

    # 3.3 xlsx -> xlsm
    o3 = d / "out3.xlsm"
    try:
        ok, msg = m.convert_format_change_logic(str(s1), str(o3))
        check("3.3 xlsx -> xlsm", ok and o3.exists(), msg)
    except Exception as e:
        check("3.3 xlsx -> xlsm", False, f"抛异常 {type(e).__name__}: {e}")

    # 3.4 csv -> xlsx
    c1 = d / "in.csv"
    df.to_csv(c1, index=False, encoding="utf-8-sig")
    o4 = d / "out4.xlsx"
    try:
        ok, msg = m.convert_csv_to_xlsx_logic(str(c1), str(o4))
        check("3.4 csv -> xlsx", ok and o4.exists(), msg)
        if o4.exists():
            back = pd.read_excel(o4)
            check("3.4b csv 内容正确", "科目" in back.columns and len(back) == 2, str(list(back.columns)))
    except Exception as e:
        check("3.4 csv -> xlsx", False, f"抛异常 {type(e).__name__}: {e}")

    # 3.5 get_unique_dest_path 避重
    (d / "dup.xlsx").write_text("", encoding="utf-8")
    p = m.get_unique_dest_path(str(d), "dup.xlsx")
    check("3.5 get_unique_dest_path 自动避重", Path(p).name != "dup.xlsx", Path(p).name)

    # 3.6 core_process_folder 批量（页面 folder 分支）
    fs = mkdir(d / "folder_src")
    pd.DataFrame({"a": [1, 2]}).to_excel(fs / "f1.xlsx", index=False)
    pd.DataFrame({"a": [3]}).to_excel(fs / "f2.xlsx", index=False)
    fo = mkdir(d / "folder_out")
    try:
        res = m.core_process_folder(str(fs), str(fo), 1, False, False, False, lambda *a: None)
        produced = sorted(p.name for p in fo.iterdir() if p.is_file())
        check("3.6 文件夹批处理 xlsx->xls", len(produced) == 2, f"产物={produced} res={str(res)[:60]}")
    except Exception as e:
        check("3.6 文件夹批处理 xlsx->xls", False, f"抛异常 {type(e).__name__}: {e}")


# ============================================================================
# 4. PDF索引号生成 (pdf_indexer)
# ============================================================================
def _make_pdf(path, pages=3, rotate=None, text="TEST"):
    import fitz
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"{text} page {i + 1}", fontsize=14)
        if rotate:
            pg.set_rotation(rotate)
    doc.save(str(path))
    doc.close()


def test_pdf_indexer():
    section("4. PDF索引号生成 (other_tools/modules/pdf_indexer.py)")
    d = mkdir(_WORK / "pdfidx")
    font = "C:\\Windows\\Fonts\\simsun.ttc"
    if not os.path.exists(font):
        warn("4.x 系统无 simsun.ttc，跳过索引测试")
        return
    m = load_desktop_module("pdf_indexer")

    for rot in (None, 90, 180, 270):
        p = d / f"doc_{rot}.pdf"
        _make_pdf(p, pages=3, rotate=rot)
        logs = []
        try:
            ok, msg = m.add_index_to_pdf(str(p), font, logs.append)
        except Exception as e:
            check(f"4.1 打索引 rotate={rot}", False, f"抛异常 {type(e).__name__}: {e}")
            continue
        if not check(f"4.1 打索引 rotate={rot}", ok, f"{msg} | {logs[-1:]}"):
            continue
        import fitz
        doc = fitz.open(str(p))
        texts = [pg.get_text() for pg in doc]
        page_ok = all(("1/3" in t or f"{i + 1}/3" in t) for i, t in enumerate(texts))
        check(f"4.1b rotate={rot} 每页含页码 n/3", page_ok,
              " / ".join(t.strip().replace("\n", "|")[:40] for t in texts))
        check(f"4.1c rotate={rot} 旋转元数据保留",
              doc[0].rotation == (rot or 0), f"rotation={doc[0].rotation}")
        doc.close()

    # CropBox 偏移
    import fitz
    p = d / "crop.pdf"
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((72, 100), "CROP", fontsize=14)
    pg.set_cropbox(fitz.Rect(50, 50, 500, 700))
    doc.save(str(p))
    doc.close()
    logs = []
    ok, msg = m.add_index_to_pdf(str(p), font, logs.append)
    if check("4.2 CropBox 偏移页打索引", ok, f"{msg} | {logs[-1:]}"):
        doc = fitz.open(str(p))
        t = doc[0].get_text()
        check("4.2b CropBox 页含页码", "1/1" in t, t.strip()[:60])
        doc.close()

    # 窄页 / 超长文件名
    p = d / ("很长的文件名" * 12 + ".pdf")
    _make_pdf(p, pages=1)
    logs = []
    ok, msg = m.add_index_to_pdf(str(p), font, logs.append)
    if check("4.3 超长文件名/窄页", ok, f"{msg} | {logs[-1:]}"):
        doc = fitz.open(str(p))
        t = doc[0].get_text()
        check("4.3b 超长文件名仍保留页码", "1/1" in t, t.strip().replace("\n", "|")[:80])
        doc.close()

    # 批量入口 batch_process_pdf（页面未用，仅确认可调用性/签名兼容）
    p2 = d / "batchtest.pdf"
    _make_pdf(p2, pages=2)
    try:
        import inspect
        sig = inspect.signature(m.batch_process_pdf)
        check("4.4 batch_process_pdf 签名可解析", True, str(sig))
    except Exception as e:
        check("4.4 batch_process_pdf 签名可解析", False, str(e))


# ============================================================================
# 5. PDF合并/分拆/转换 (pdf_merger)
# ============================================================================
def test_pdf_merger():
    section("5. PDF合并/分拆/转换 (other_tools/modules/pdf_merger.py)")
    d = mkdir(_WORK / "pdfmerge")
    m = load_desktop_module("pdf_merger")

    # 5.1 合并（非递归）
    f1 = mkdir(d / "m1")
    _make_pdf(f1 / "a.pdf", pages=2, text="AAA")
    _make_pdf(f1 / "b.pdf", pages=3, text="BBB")
    logs = []
    try:
        msg = m.core_merge_process(str(f1), False, False, False, False, False, 1, logs.append)
    except Exception as e:
        check("5.1 合并两个 PDF", False, f"抛异常 {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return
    merged = f1 / "合并结果.pdf"
    check("5.1 合并两个 PDF", merged.exists(), f"msg={msg}")
    if merged.exists():
        import fitz
        doc = fitz.open(str(merged))
        check("5.1b 合并页数=5", doc.page_count == 5, f"实际 {doc.page_count}")
        check("5.1c 合并内容含两文件", "AAA" in doc[0].get_text() and "BBB" in doc[3].get_text())
        doc.close()

    # 5.2 按步长分拆
    f2 = mkdir(d / "m2")
    _make_pdf(f2 / "x.pdf", pages=5, text="XXX")
    logs2 = []
    msg2 = m.core_merge_process(str(f2), False, False, False, False, True, 2, logs2.append)
    parts = sorted(p.name for p in f2.iterdir() if p.name.startswith("合并结果"))
    check("5.2 分拆为按步长文件", len(parts) == 3, f"parts={parts} msg={msg2}")
    if len(parts) == 3:
        import fitz
        sizes = []
        for p in parts:
            doc = fitz.open(str(f2 / p))
            sizes.append(doc.page_count)
            doc.close()
        check("5.2b 分卷页数 2/2/1", sizes == [2, 2, 1], str(sizes))

    # 5.3 幂等：再次合并不吃掉上次产物
    logs3 = []
    m.core_merge_process(str(f1), False, False, False, False, False, 1, logs3.append)
    produced = sorted(p.name for p in f1.iterdir() if p.name.startswith("合并结果"))
    check("5.3 重复运行不误删旧产物（命名加序号）", len(produced) == 2, str(produced))

    # 5.4 递归子文件夹
    f3 = mkdir(d / "m3")
    mkdir(f3 / "sub")
    _make_pdf(f3 / "top.pdf", pages=1)
    _make_pdf(f3 / "sub" / "deep.pdf", pages=1)
    logs4 = []
    m.core_merge_process(str(f3), True, False, False, False, False, 1, logs4.append)
    merged3 = f3 / "合并结果.pdf"
    if check("5.4 递归合并", merged3.exists()):
        import fitz
        doc = fitz.open(str(merged3))
        check("5.4b 递归含 2 页", doc.page_count == 2, f"实际 {doc.page_count}")
        doc.close()

    # 5.5 空目录
    f4 = mkdir(d / "m4")
    msg5 = m.core_merge_process(str(f4), False, False, False, False, False, 1, lambda *a: None)
    check("5.5 空目录返回友好提示", "未找到" in str(msg5), str(msg5))

    # 5.6 图片转 PDF 合并（需要 assets/fonts/simsun.ttc 资源；Web 页面有 _ensure_font_asset 兜底）
    f5 = mkdir(d / "m5")
    from PIL import Image
    Image.new("RGB", (400, 300), (255, 255, 255)).save(f5 / "img.png")

    # 5.6a 冷启动（假设 assets/fonts/simsun.ttc 不存在，模拟首次部署）
    # 模块自身不复制字体资源，属于已知限制；Web 页面在点「开始合并」前会 _ensure_font_asset()
    # 兜底（见 5.6b），所以这里只记为 WARN 而不是缺陷。
    font_rel = ROOT / "assets" / "fonts" / "simsun.ttc"
    backup = None
    if font_rel.exists():
        backup = font_rel.read_bytes()
        font_rel.unlink()
    try:
        logs6 = []
        msg6 = m.core_merge_process(str(f5), False, True, False, False, False, 1, logs6.append)
        cold_ok = (f5 / "合并结果.pdf").exists()
        if cold_ok:
            check("5.6a 冷启动图片转PDF(无字体资源)", True, "模块自身也能兜底")
        else:
            warn("5.6a 冷启动图片转PDF(无字体资源)",
                 f"模块自身报错（msg={msg6}）；Web 页面已用 _ensure_font_asset() 在运行前兜底")

        # 5.6b 页面兜底 _ensure_font_asset() 后重试
        import importlib
        page = importlib.import_module("other_tools.web.pdf_merger")
        fp = page._ensure_font_asset()
        logs6b = []
        msg6b = m.core_merge_process(str(f5), False, True, False, False, False, 1, logs6b.append)
        got = sorted(p.name for p in f5.iterdir() if p.name.startswith("合并结果"))
        check("5.6b 页面兜底复制字体后图片转PDF成功",
              bool(fp) and len(got) >= 1, f"font={fp} msgs={msg6b} 产物={got}")

    finally:
        if backup is not None:
            font_rel.parent.mkdir(parents=True, exist_ok=True)
            font_rel.write_bytes(backup)
        else:
            # 测试首次部署场景，清理测试期间生成的字体，避免污染被测环境
            if font_rel.exists():
                font_rel.unlink()
            if font_rel.parent.exists() and not any(font_rel.parent.iterdir()):
                font_rel.parent.rmdir()


# ============================================================================
# 6. 页面导入 + streamlit AppTest 冒烟
# ============================================================================
def _apptest(script_name, **kwargs):
    """用 AppTest.from_file 跑 AppTest（from_function 会写系统 tmp，可能被沙箱拒绝）"""
    from streamlit.testing.v1 import AppTest
    app_file = HERE / f"_apptest_{script_name}.py"
    return AppTest.from_file(str(app_file), default_timeout=90).run()


def test_pages_render():
    section("6. Streamlit 页面渲染冒烟（AppTest）")
    try:
        from streamlit.testing.v1 import AppTest  # noqa: F401
    except Exception as e:
        warn("6.0 streamlit.testing 不可用", str(e))
        return

    # 6.1 小工具二级目录页
    at = _apptest("tools_home")
    if at.exception:
        check("6.1 小工具目录页无异常", False, str(at.exception[0].value)[:300])
    else:
        check("6.1 小工具目录页无异常", True)
        labels = [b.label for b in at.button]
        check("6.1b 5 个工具入口按钮存在",
              sum(1 for l in labels if l.startswith("打开")) == 5, str(labels))

    # 6.2 五个工具详情页
    for tid, name in [("excel_extract", "Excel数据提取"), ("file_batch", "文件批量整理"),
                      ("xls_convert", "Excel格式互转"), ("pdf_indexer", "PDF索引号生成"),
                      ("pdf_merger", "PDF合并/分拆/转换")]:
        at = AppTest.from_file(str(HERE / "_apptest_tool_detail.py"), default_timeout=90)
        at.session_state["current_tool"] = tid
        at.run()
        if at.exception:
            check(f"6.2 详情页 {name}", False, str(at.exception[0].value)[:300])
        else:
            errs = [e.value for e in at.error]
            check(f"6.2 详情页 {name}", not errs, str(errs)[:200])

    # 6.3 银行流水匹配页
    at = _apptest("bank")
    if at.exception:
        check("6.3 银行流水匹配页无异常", False, str(at.exception[0].value)[:400])
    else:
        check("6.3 银行流水匹配页无异常", True)
        check("6.3b 未上传时给出提示", any("上传" in i.value for i in at.info),
              str([i.value[:40] for i in at.info]))


# ============================================================================
if __name__ == "__main__":
    print(f"work dir: {_WORK}", flush=True)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    suites = [
        ("extract", test_excel_extract),
        ("filebatch", test_file_batch),
        ("convert", test_xls_convert),
        ("pdfidx", test_pdf_indexer),
        ("pdfmerge", test_pdf_merger),
        ("pages", test_pages_render),
    ]
    for key, fn in suites:
        if only and only != key:
            continue
        try:
            fn()
        except Exception as e:
            check(f"{key} 套件整体异常", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

    print("\n" + "=" * 78)
    print(f"汇总: PASS={_counts['PASS']}  FAIL={_counts['FAIL']}  WARN={_counts['WARN']}")
    print("=" * 78)
    for st_, name, detail in RESULTS:
        if st_ != "PASS":
            print(f"  [{st_}] {name} :: {detail[:300]}")
    keep = os.environ.get("KEEP_WORK") == "1"
    if not keep:
        shutil.rmtree(_WORK, ignore_errors=True)
    else:
        print(f"work dir kept: {_WORK}")
