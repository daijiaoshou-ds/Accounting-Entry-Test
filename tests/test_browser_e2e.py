# -*- coding: utf-8 -*-
"""可选：真实浏览器端到端（上传文件夹 -> 处理 -> 浏览器下载）

AppTest（test_tools_ui_interaction.py）能覆盖页面逻辑，但覆盖不到
「浏览器目录选择器 webkitdirectory」和「真的触发下载」这两件事，所以留一个真浏览器脚本。

前置条件（都不在 requirements.txt 里，属于人工验证工具）：
    pip install playwright
    本机已装 Chrome 或 Edge（脚本用 channel="chrome"，不下载浏览器二进制）
    应用已在运行，默认 http://localhost:8501

用法：
    venv\\Scripts\\python.exe -X utf8 tests\\test_browser_e2e.py
    set APP_URL=http://localhost:8531 && venv\\Scripts\\python.exe -X utf8 tests\\test_browser_e2e.py
"""
import os
import re
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from _fixtures import WORK, make_pdf, make_txt, make_xlsx  # noqa: E402

BASE = os.environ.get("APP_URL", "http://localhost:8501")
DOWNLOADS = WORK / "browser_downloads"
FAILS = []


def log(m):
    print(m, flush=True)


def check(name, cond, detail=""):
    if cond:
        log(f"[PASS] {name}" + (f"  -> {detail}" if detail else ""))
    else:
        FAILS.append(name)
        log(f"[FAIL] {name}  -> {detail}")


def set_checkbox(page, text):
    """勾选 Streamlit 复选框：点它外面那层可见的 <label>（原生 input 是视觉隐藏的）"""
    lab = page.locator('label[data-baseweb="checkbox"]').filter(has_text=re.compile(text)).first
    lab.scroll_into_view_if_needed()
    time.sleep(0.5)
    lab.click()
    time.sleep(2)


def open_tool(page, label):
    """首页 -> 小工具 -> 指定工具（每步从首页开始，避免点到侧边栏的返回按钮）"""
    for _ in range(3):
        page.goto(BASE, wait_until="domcontentloaded")
        page.get_by_text(re.compile("功能导航")).first.wait_for()
        time.sleep(3)
        page.get_by_role("button", name=re.compile("🧰")).first.click()
        try:
            page.get_by_role("button", name=re.compile("进入小工具")).wait_for(timeout=15000)
            break
        except Exception:  # noqa: BLE001
            time.sleep(3)
    else:
        raise RuntimeError("进不去小工具页")
    page.get_by_role("button", name=re.compile("进入小工具")).click()
    time.sleep(2)
    page.get_by_role("button", name=re.compile(label)).click()
    time.sleep(2)
    page.get_by_text("数据来源").first.wait_for()


def pick_folder(page, folder, nth=0):
    """切到「上传文件夹」并用真实目录设置 webkitdirectory 输入框"""
    page.get_by_text("🗂️ 上传文件夹", exact=True).nth(nth).click()
    time.sleep(2)
    inp = page.locator('input[type="file"][webkitdirectory]').nth(nth)
    if inp.count() == 0:
        raise RuntimeError("页面上没有带 webkitdirectory 的上传控件")
    inp.set_input_files(str(folder))
    time.sleep(4)


def wait_and_download(page, wait_text, out_name):
    deadline = time.time() + 180
    while time.time() < deadline:
        if re.search(wait_text, page.inner_text("body")):
            break
        time.sleep(1)
    else:
        return None
    with page.expect_download() as dl:
        page.get_by_text(re.compile(wait_text)).first.click()
    d = dl.value
    target = DOWNLOADS / out_name
    d.save_as(str(target))
    return target


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("跳过：未安装 playwright（pip install playwright 后可跑真实浏览器验证）")
        return 0

    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    conv = WORK / "browser_fixtures" / "凭证"
    make_xlsx(conv / "a.xlsx")
    make_txt(conv / "备注.txt", "非目标格式")
    make_xlsx(conv / "子目录" / "b.xlsx")
    pdfs = WORK / "browser_fixtures" / "合同"
    make_pdf(pdfs / "1.pdf", 1)
    make_pdf(pdfs / "2.pdf", 2)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("BROWSER_CHANNEL", "chrome"), headless=True)
        page = browser.new_context(accept_downloads=True).new_page()
        page.set_default_timeout(60000)

        # 1) Excel数据提取：文件夹（含子目录）上传 -> 提取 -> 下载
        log("\n=== Excel数据提取：上传文件夹 ===")
        open_tool(page, "打开Excel数据提取")
        pick_folder(page, conv)
        page.get_by_role("button", name=re.compile("开始提取")).click()
        t = wait_and_download(page, "保存文件", "提取结果.xlsx")
        check("1 提取结果可下载", bool(t), str(t))
        body = page.inner_text("body")
        check("2 页面提示不泄漏临时目录", "ka_toolbox_work" not in body)

        # 2) Excel格式互转：文件夹 + 原样打包 -> zip
        log("\n=== Excel格式互转：上传文件夹 + 原样打包 ===")
        open_tool(page, "打开Excel格式互转")
        page.get_by_role("combobox").first.click()
        time.sleep(0.5)
        page.get_by_text("新版转旧版 (.xlsx → .xls)", exact=True).click()
        time.sleep(2)
        pick_folder(page, conv)
        set_checkbox(page, "原样打包")
        page.get_by_role("button", name=re.compile("开始转换")).click()
        t = wait_and_download(page, "打包保存全部结果", "转换结果.zip")
        check("3 转换结果 zip 可下载", bool(t), str(t))
        if t:
            names = sorted(n.replace("\\", "/") for n in zipfile.ZipFile(t).namelist())
            check("4 zip 含转换结果 + 原样打包文件 + 子目录层级",
                  names == ["凭证/a.xls", "凭证/备注.txt", "凭证/子目录/b.xls"], str(names))

        # 3) PDF合并：文件夹 + 按页分拆 -> zip
        log("\n=== PDF合并：上传文件夹 + 分拆 ===")
        open_tool(page, "打开PDF合并")
        pick_folder(page, pdfs)
        set_checkbox(page, "合并后按页数分拆")
        page.locator('input[type="number"]').first.fill("1")
        page.keyboard.press("Enter")
        time.sleep(2)
        page.get_by_role("button", name=re.compile("开始合并")).click()
        t = wait_and_download(page, "打包保存全部结果", "合并结果.zip")
        check("5 分卷 zip 可下载", bool(t), str(t))
        if t:
            names = sorted(zipfile.ZipFile(t).namelist())
            check("6 3 页分成 3 卷", len(names) == 3, str(names))

        # 4) 文件批量整理：文件夹 -> 清单 -> 执行整理 -> zip
        log("\n=== 文件批量整理：上传文件夹 ===")
        open_tool(page, "打开文件批量整理")
        pick_folder(page, conv, nth=0)
        page.get_by_role("button", name=re.compile("生成文件清单")).click()
        t = wait_and_download(page, "下载文件清单", "文件清单.xlsx")
        check("7 清单可下载", bool(t), str(t))
        if t:
            try:
                import pandas as pd
                paths = [str(v) for v in pd.read_excel(t)["文件路径"] if str(v).strip()]
                check("8 清单里是相对路径（不含临时目录）",
                      all(not os.path.isabs(v) for v in paths), str(paths))
            except ImportError:
                log("  （未装 pandas，跳过清单内容校验）")
        set_checkbox(page, "复制模式")
        page.get_by_role("button", name=re.compile("执行整理")).click()
        t = wait_and_download(page, "打包保存全部结果", "整理结果.zip")
        check("9 整理结果 zip 可下载", bool(t), str(t))

        browser.close()

    print()
    print(f"汇总: FAIL={len(FAILS)}")
    for f in FAILS:
        print("  [FAIL]", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
