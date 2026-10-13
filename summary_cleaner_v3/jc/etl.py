# -*- coding: utf-8 -*-
"""ETL —— 读序时账、认列名、规范化、抽取模型输入、写回结果。

这是「程序」在 3.0 里的第一块职责。它**不做任何业务判断**，
只负责把用户给的乱表整理成模型能吃的东西，再把模型结论写回去。

设计上刻意完整保留 1.0/2.0 时代踩过的坑（见各函数注释），
这些坑与"用不用程序打分"无关，属于数据层面的硬约束：

  - NaN 是 truthy：`x or 0` 不能把 NaN 变 0，必须 pd.isna 显式判断
  - 红字凭证：按**绝对值**判借贷方向，否则负金额行被整行跳过
  - 字符串金额："100,000.00" 要先剥千分位再 to_numeric
  - 首行摘要可能为空：拼接摘要必须 dropna
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .config import (
    COL_BUCKET_SRC, COLUMN_PATTERNS, FIELD_EXACT_ALIASES, FIELD_EXCLUDE,
    SUPPORTED_EXT,
)

_WHITESPACE_RE = re.compile(r"\s+")

# 含这些字样的列名模式只做精确匹配
_EXACT_HINT = ("明细", "二级科目")


# ============================================================================
# 读文件
# ============================================================================

def read_journal(path: str | Path) -> pd.DataFrame:
    """读序时账（xlsx / xls / xlsm / csv）。

    CSV 依次尝试 utf-8-sig / gbk / utf-8 —— 国内财务系统导出常见 GBK。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_EXT:
        raise ValueError(
            f"不支持的格式 {p.suffix}（支持 {', '.join(sorted(SUPPORTED_EXT))}）")
    if suffix == ".csv":
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                return pd.read_csv(p, encoding=enc, dtype=object)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"CSV 编码无法识别（试过 utf-8-sig / gbk / utf-8）: {p}")
    return pd.read_excel(p, dtype=object)


# ============================================================================
# 列名检测（角色化匹配）
# ============================================================================

def detect_columns(df: pd.DataFrame) -> Dict[str, str]:
    """自动检测列名 → {字段名: 实际列名}。

    角色化匹配：每个字段有「专属列名」与「绝不接受的列名」，
    按 COLUMN_PATTERNS 的键序 + 模式序，先命中者胜，且一列只能被一个字段占用。

    为什么不能"所有字段一起子串扫描"（旧实现的做法）：
      「科目名称」含子串「科目」，会把一级科目列「科目」抢走，
      导致 subject 缺失、整份结果作废。
    """
    columns = [str(c).strip() for c in df.columns]
    original = {str(c).strip(): c for c in df.columns}
    taken: set = set()
    mapping: Dict[str, str] = {}

    for key, patterns in COLUMN_PATTERNS.items():
        exclude = FIELD_EXCLUDE.get(key, set())
        exact_only: List[str] = []
        loose: List[str] = []
        for pat in patterns:
            pat = str(pat).strip()
            if not pat:
                continue
            if any(h in pat for h in _EXACT_HINT):
                exact_only.append(pat)
            else:
                loose.append(pat)

        hit = None
        # ① 该字段专属列名（精确）优先
        for alias in sorted(FIELD_EXACT_ALIASES.get(key, set())):
            if alias in columns and alias not in exclude and alias not in taken:
                hit = alias
                break
        # ② 精确匹配「明细」类模式
        if hit is None:
            for col in columns:
                if col in taken or col in exclude:
                    continue
                if col in exact_only:
                    hit = col
                    break
        # ③ 子串匹配宽松模式
        if hit is None:
            for col in columns:
                if col in taken or col in exclude:
                    continue
                for pat in loose:
                    if pat in col:
                        hit = col
                        break
                if hit:
                    break

        if hit:
            mapping[key] = original[hit]
            taken.add(hit)

    return mapping


def apply_mapping_override(df: pd.DataFrame, mapping: Dict[str, str],
                           override: Optional[Dict[str, str]] = None
                           ) -> Tuple[Dict[str, str], List[dict]]:
    """在自动检测结果上套用人工覆盖，返回 (mapping, 无效覆盖列表)。"""
    mapping = dict(mapping)
    bad: List[dict] = []
    for field, col in (override or {}).items():
        if col in df.columns:
            mapping[field] = col
        else:
            bad.append({"field": field, "column": col,
                        "reason": "该列名不在文件里"})
    return mapping, bad


# ============================================================================
# 规范化
# ============================================================================

def to_number(value: Any) -> float:
    """把单元格变成数字。

    必须处理："100,000.00"（千分位）、NaN、None、""、"-"。
    NaN 是 truthy，`value or 0` 这类写法**无效**，必须 pd.isna 显式判断。
    """
    if value is None:
        return 0.0
    if isinstance(value, float) and pd.isna(value):
        return 0.0
    text = str(value).replace(",", "").replace("，", "").strip()
    if text in ("", "-", "—", "nan", "None"):
        return 0.0
    num = pd.to_numeric(text, errors="coerce")
    return 0.0 if pd.isna(num) else float(num)


def subject_switches(group: pd.DataFrame, subject_col: str,
                     debit_col: str, credit_col: str) -> List[str]:
    """抽取一张凭证的「科目开关」：'科目[方向]'，按字典序排序。

    方向判定**按绝对值**（红字凭证的关键）：
      |debit| > |credit|        → 借
      否则 |credit| > 0          → 贷
      两者都是 0                 → 跳过该行

    为什么用绝对值：红字冲减分录金额为负，`debit > credit` 会把它整行跳过，
    导致科目组合只剩一半（实测用户凭证只提取到银行存款）。
    """
    switches = set()
    for _, row in group.iterrows():
        subject = row.get(subject_col)
        if subject is None or (isinstance(subject, float) and pd.isna(subject)):
            continue
        subject = str(subject).strip()
        if not subject:
            continue
        debit = to_number(row.get(debit_col))
        credit = to_number(row.get(credit_col))
        if abs(debit) > abs(credit):
            switches.add(f"{subject}[借]")
        elif abs(credit) > 0:
            switches.add(f"{subject}[贷]")
    return sorted(switches)


def group_summary(group: pd.DataFrame, summary_col: str,
                  max_chars: int = 200) -> str:
    """一张凭证的**模型输入摘要** = 组内全部非空摘要去重后空格拼接。

    与「首行摘要」的区别：多行凭证各行摘要可能不同，取首行会丢信息，
    而且首行可能为空（dropna 才安全）。

    这一步是模型的输入口径，训练与推理必须一致 —— 改这里要同时改训练侧。
    """
    if not summary_col or summary_col not in group.columns:
        return ""
    parts: List[str] = []
    for text in group[summary_col].dropna().astype(str):
        text = _WHITESPACE_RE.sub(" ", text).strip()
        if text and text not in parts:
            parts.append(text)
    return " ".join(parts)[:max_chars]


def voucher_key(subjects: List[str], summary: str) -> Tuple[Tuple[str, ...], str]:
    """模型输入的去重键 —— 必须**等于模型实际看到的输入**。

    同一键 ⇒ 输入逐字节相同 ⇒ 模型输出相同 ⇒ 可以只推理一次并共享。
    这是 3.0 提速（省 66% 模型调用）的正确性基础，不要改成"首行摘要"等粗键。

    注意：**这个键就是流程里的「单元」**。序时账是先按它切成单元、再给模型打分，
    单元才是复核和改判的单位（见 pipeline.build_units）。这里返回元组是给去重用的，
    落盘/命令行里用它的人类可读形式 unit_key_text()。
    """
    return (tuple(subjects), summary)


UNIT_SUBJECT_SEP = " + "
UNIT_KEY_SEP = " :: "


def unit_key_text(subjects, summary: str) -> str:
    """单元键的**规范文本**：`科目1[借] + 科目2[贷] :: 摘要`。

    这是单元的身份，口径修正表就用它做键，所以它必须**跨月可复用**：
    下个月序时账里出现同一个「科目组合 + 摘要」，同一份修正表直接命中。
    凭证号做不到这一点（凭证号是月内、文件内的编号）。
    """
    subs = UNIT_SUBJECT_SEP.join(str(s) for s in (subjects or []))
    return f"{subs}{UNIT_KEY_SEP}{summary or ''}"


# ============================================================================
# 抽取模型输入（凭证级）
# ============================================================================

def extract_items(df: pd.DataFrame, mapping: Dict[str, str]
                  ) -> Tuple[List[dict], pd.DataFrame]:
    """把序时账整理成「一凭证一条」的模型输入清单。

    Returns:
        (items, per_voucher)
        items: [{"vid", "subjects", "summary", "amount", "bookkeeper",
                 "subjects_raw", "details"}]  按凭证号顺序
        per_voucher: 同内容但以 DataFrame 形式，便于统计/导出

    金额口径 = 借方绝对值合计（一张凭证借贷平衡，用借方即可）；
    红字凭证也取绝对值，避免合计为 0 或负数。
    """
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    d_col = mapping["debit"]
    c_col = mapping["credit"]
    sum_col = mapping.get("summary", "")
    sn_col = mapping.get("subject_name", "")
    bk_col = mapping.get("bookkeeper", "")

    items: List[dict] = []
    for vid, group in df.groupby(v_col, sort=False):
        subjects = subject_switches(group, s_col, d_col, c_col)
        summary = group_summary(group, sum_col)
        amount = sum(abs(to_number(v)) for v in group[d_col]) if d_col in group else 0.0
        bookkeeper = ""
        if bk_col and bk_col in group.columns:
            vals = group[bk_col].dropna()
            if len(vals):
                bookkeeper = str(vals.iloc[0]).strip()
        details: List[str] = []
        if sn_col and sn_col in group.columns:
            details = [str(x) for x in group[sn_col].dropna().astype(str)]
        subjects_raw = [str(x) for x in group[s_col].dropna().astype(str)]

        items.append({
            "vid": str(vid),
            "subjects": subjects,          # 带方向，模型输入用
            "subjects_raw": subjects_raw,  # 裸科目名，硬规则用
            "summary": summary,            # 拼接摘要，模型输入用
            "amount": round(amount, 2),
            "bookkeeper": bookkeeper,
            "details": details,
        })

    per_voucher = pd.DataFrame(items) if items else pd.DataFrame(
        columns=["vid", "subjects", "subjects_raw", "summary", "amount",
                 "bookkeeper", "details"])
    return items, per_voucher


# ============================================================================
# 写回
# ============================================================================

def write_back(df: pd.DataFrame, mapping: Dict[str, str],
               classifications: Dict[str, str],
               confidences: Dict[str, str],
               sources: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """把结论写回原始序时账（逐行），返回新 DataFrame。

    用字符串键映射：数值型凭证号用原始类型键 map 会全部落空。

    sources：「分类判定」列（模型 / 硬规则 / 复核改判）。为什么要有它——
    桶可能是模型判的、可能是硬规则写死的、也可能是 AI 复核改判的，
    用户打开 Excel 得分得清是哪个定的，否则"可审计"就是空话。
    """
    v_col = mapping["voucher_no"]
    out = df.copy()
    keys = out[v_col].astype(str)
    out["业务分类"] = keys.map(classifications).fillna("未分类")
    if sources is not None:
        out[COL_BUCKET_SRC] = keys.map(sources).fillna("")
    out["置信度"] = keys.map(confidences).fillna("低")
    return out


# ============================================================================
# 现金流相关凭证（逐行）
# ============================================================================

def add_row_index(df: pd.DataFrame) -> pd.DataFrame:
    """给每一行标上稳定行号（内部用，导出前会丢掉）。

    为什么不用 df.index：Excel 读进来就是 RangeIndex，但一旦中间做过
    筛选/拼接就容易错位；显式一列最稳。
    """
    out = df.copy()
    out["_行号"] = range(len(out))
    return out


def build_money_rows(df: pd.DataFrame, mapping: Dict[str, str]
                     ) -> Tuple[Dict[str, List[Any]], Dict[str, bool]]:
    """按凭证分组，抽出每行 (科目, 借, 贷, 行号)，并标记哪些凭证含货币资金。

    Returns:
        (money_by_vid, has_money)
        money_by_vid: {凭证号: [MoneyRow, ...]} —— **只含现金流相关凭证**
        has_money:    {凭证号: bool} —— 全部凭证的标记，逐行写回用

    三趟，但都是向量化的，万行级序时账毫秒级：
      ① 标出哪些行是货币资金行
      ② 得到含货币资金的凭证号集合
      ③ 只对这些凭证建行对象（整张凭证都要，不是只留货币资金行）
    """
    from .cashflow import MoneyRow, is_money_fund

    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    d_col = mapping["debit"]
    c_col = mapping["credit"]

    vids = df[v_col].astype(str)
    subj_clean = (df[s_col].where(df[s_col].notna(), "")
                  .astype(str).str.strip())
    is_money = subj_clean.map(is_money_fund)

    # ② 全部凭证的标记 + 现金流相关凭证
    has_money: Dict[str, bool] = {}
    for vid, hit in zip(vids, is_money):
        if hit:
            has_money[vid] = True
        else:
            has_money.setdefault(vid, False)

    wanted = {v for v, hit in has_money.items() if hit}
    money_by_vid: Dict[str, List[Any]] = {v: [] for v in wanted}

    # ③ 只扫相关凭证
    cols = zip(df["_行号"], vids, subj_clean, df[d_col], df[c_col])
    for row_no, vid, subj, debit, credit in cols:
        if vid not in wanted:
            continue
        money_by_vid[vid].append(MoneyRow(
            subject=subj,
            debit=to_number(debit),
            credit=to_number(credit),
            row_index=int(row_no),
        ))

    return money_by_vid, has_money


def write_cashflow(df: pd.DataFrame, mapping: Dict[str, str],
                   money_flags: Dict[str, bool],
                   cf_by_row: Dict[int, dict]) -> pd.DataFrame:
    """把现金流量项目写回序时账（逐行）。

    只有货币资金那一行才有现金流量项目 —— 这是会计惯例；
    非货币资金行留空，但它所在的整张凭证会出现在「现金流相关凭证」表里。
    """
    from .cashflow import CF_NONE

    out = df.copy()
    v_col = mapping["voucher_no"]

    out["是否现金流相关凭证"] = (
        out[v_col].astype(str).map(money_flags)
        .map({True: "是", False: ""}).fillna(""))
    out["现金流量项目"] = [cf_by_row.get(i, {}).get("cf_item", "")
                          for i in out["_行号"]]
    out["现金流方向"] = [cf_by_row.get(i, {}).get("direction", CF_NONE)
                         if i in cf_by_row else ""
                         for i in out["_行号"]]
    out["现金流判定"] = [cf_by_row.get(i, {}).get("source", "")
                         for i in out["_行号"]]
    out["现金流复核"] = [("需复核" if cf_by_row.get(i, {}).get("needs_review") else "")
                         for i in out["_行号"]]
    return out


def write_excel(df: pd.DataFrame, path: str | Path, sheet: str = "Sheet1"):
    """写单 sheet 的 Excel（自动建目录）。"""
    write_excel_sheets({sheet: df}, path)


def write_excel_sheets(sheets: Dict[str, pd.DataFrame], path: str | Path) -> Path:
    """把多个 DataFrame 写成**一个 Excel 文件里的多个 sheet**。

    为什么不一个结果一个文件：用户要看的是一份交付物，
    丢过去 7 个 xlsx 只会让人不知道从哪看起。落盘由程序负责，
    版面也由程序负责——不给 AI 留"自己决定输出成什么样"的空间。

    sheet 顺序 = dict 插入顺序（第一个放"使用说明"这类导读）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name, frame in sheets.items():
            frame.to_excel(w, index=False, sheet_name=name[:31])
    return path
