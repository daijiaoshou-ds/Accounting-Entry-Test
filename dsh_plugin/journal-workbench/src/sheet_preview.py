"""把一份表读成"预览"要的 JSON（一次性读整张表，分页/搜索在 Host 侧做）。

为什么在 Python 里读，而不是 Node 里：

  - 仓库本来就靠 venv 跑，`python_calamine` 已经是依赖（64,255 行 2.5s，
    openpyxl 要 10s）；
  - Node 侧没有 xlsx 解析器，为一个**只读预览**去装 SheetJS、或者自己写一遍
    zip + sheet XML 的解析，都不值当。

契合同 `journal.py`：stdout 只有单行 `RESULT <json>`，进度/错误走 stderr。
调用方是 `src/preview.js`，参数由它给（`--max-cells` / `--max-rows` 是内存闸门，
不是业务规则：表格再大，预览也只留前若干行，剩下的靠下载看）。

    python sheet_preview.py --file <路径> [--sheet <名字>]
                            [--max-cells 1200000] [--max-rows 60000]
"""
import argparse
import csv
import datetime
import json
import os
import sys

RESULT_PREFIX = "RESULT "

# calamine 认的表；.csv 走 csv 模块（calamine 不读纯文本）
SHEET_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"}

# 这些表是"用户上传的原样数据"，不是结果。现金流的工作簿第一张就是「原始序时账」，
# 默认打开它等于让人看自己的输入——所以没指定表时跳过它们。
RAW_SHEET_NAMES = {"原始序时账", "原表", "源数据", "raw", "sheet1", "sheet"}


def pick_sheet(names, wanted):
    if wanted and wanted in names:
        return wanted
    usable = [name for name in names if str(name).strip().lower() not in RAW_SHEET_NAMES]
    return (usable or names)[0]


def fail(message):
    sys.stderr.write(str(message) + "\n")
    sys.stdout.write(RESULT_PREFIX + json.dumps(
        {"ok": False, "error": str(message)}, ensure_ascii=False) + "\n")
    return 1


def cell_text(value):
    """单元格 → 一行文字。规则只有一条：**别人看得懂**。

    - 浮点整数值不带 `.0`：科目编码 5602210501 被 Excel 存成 float，
      原样打印是 `5602210501.0`，看着像个错的科目；
    - 日期只到天，除非真带时分（导出表的制单日期全是零点）；
    - 其余保持 `str()`，不要在预览里替人做四舍五入。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        # 12 位有效数字：够放下"283043464.13"这种九位数带角分，
        # 又不会把 0.5914 这种概率写成 0.5914000000000001
        return f"{value:.12g}"
    if isinstance(value, datetime.datetime):
        if value.hour or value.minute or value.second:
            return value.strftime("%Y-%m-%d %H:%M")
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M:%S")
    return str(value)


def trim_columns(rows):
    """砍掉整列都是空白的尾部列：有些导出会带一长串空列，白白占宽度。"""
    if not rows:
        return []
    width = max(len(row) for row in rows)
    while width > 0 and all(len(row) < width or row[width - 1] in ("", None) for row in rows):
        width -= 1
    return [list(row[:width]) + [""] * (width - len(row[:width])) for row in rows]


def cap(rows, max_rows, max_cells):
    """留前若干行。返回 (rows, truncated)。

    两道闸门都看：行数（一张 30 万行的 csv）与格子总数（列很多时行数没意义）。
    闸门是为了别把 Host 的内存吃穿，不是"这张表只有这么多"。
    """
    truncated = False
    data_rows = len(rows) - 1 if rows else 0
    if data_rows > max_rows:
        rows = rows[: max_rows + 1]
        truncated = True
    width = max((len(row) for row in rows), default=0)
    if width and len(rows) * width > max_cells:
        keep = max(2, max_cells // width)
        rows = rows[:keep]
        truncated = True
    return rows, truncated


def read_sheets(path, sheet_name, max_rows, max_cells):
    try:
        from python_calamine import CalamineWorkbook
    except ImportError as exc:  # 环境配置里"基础依赖"没装完
        raise ValueError(
            "这个解释器里没有读表引擎 python_calamine（预览要用它，导出不受影响）。"
            "在工作台的「环境配置」里补一次基础依赖再试。") from exc

    workbook = CalamineWorkbook.from_path(path)
    names = list(workbook.sheet_names)
    if not names:
        raise ValueError("这个文件里没有工作表")

    # 表名 + 行列数先给全：页签要在打开前就显示"这张表多少行"
    sheets = []
    for name in names:
        item = workbook.get_sheet_by_name(name)
        sheets.append({"name": name,
                       "rows": int(getattr(item, "total_height", 0) or 0),
                       "cols": int(getattr(item, "total_width", 0) or 0)})

    target = pick_sheet(names, sheet_name)
    raw = workbook.get_sheet_by_name(target).to_python(skip_empty_area=False)
    rows = [[cell_text(value) for value in row] for row in raw]
    rows = trim_columns(rows)
    rows, truncated = cap(rows, max_rows, max_cells)

    # 页签名上的行数保持"原表多少行"，被闸门砍过也不改：那才是这张表的大小。
    # 砍到多少由 truncated 说明，页脚会写"只含前 N 行"。
    return {"sheets": sheets, "sheet": target, "rows": rows, "truncated": truncated}


def read_csv(path, max_rows, max_cells):
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=encoding, newline="") as handle:
                raw = list(csv.reader(handle))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("这个 csv 既不是 UTF-8 也不是 GBK，读不出文字")

    rows = [[cell_text(value) for value in row] for row in raw]
    rows = trim_columns(rows)
    rows, truncated = cap(rows, max_rows, max_cells)
    name = os.path.splitext(os.path.basename(path))[0]
    return {"sheets": [{"name": name, "rows": max(0, len(rows) - 1),
                        "cols": len(rows[0]) if rows else 0}],
            "sheet": name, "rows": rows, "truncated": truncated}


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--file", required=True)
    parser.add_argument("--sheet", default="")
    parser.add_argument("--max-cells", type=int, default=1_200_000)
    parser.add_argument("--max-rows", type=int, default=60_000)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return fail("参数不对：至少要给 --file")

    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        return fail(f"找不到文件：{path}")

    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".csv":
        payload = read_csv(path, args.max_rows, args.max_cells)
    elif suffix in SHEET_SUFFIXES:
        payload = read_sheets(path, args.sheet, args.max_rows, args.max_cells)
    else:
        return fail(f"{suffix or '这个文件'} 不支持预览，用下载打开")

    payload["ok"] = True
    payload["format"] = suffix.lstrip(".")
    sys.stdout.write(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
