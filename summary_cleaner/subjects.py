# -*- coding: utf-8 -*-
"""未知科目扫描 —— 在「科目归一化」之后、模型推理之前就能给出清单。

为什么要单独一层：未知科目的根因是**别名表没维护**，跟模型能力无关。
等到跑完整本账再看"哪些凭证被判得犹豫"是被动的；这里把检查前移到归一化，
上传文件后立刻能拿到清单，维护完别名表重跑即可，不需要重训。

"已知"的口径：模型交付物里的 `subject_to_index.json`（训练数据归一化后的
科目开关全集，形如 `应交税费[借]`）。它随每次重训重新生成，所以这条判据
永远与模型看到的世界一致。

用法（命令行）：
    python -m summary_cleaner.subjects 序时账.xlsx -o unknown.json
    python -m summary_cleaner.subjects 序时账.xlsx --mapping voucher_no=凭证编号 subject=一级科目
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from .nn.data import extract_voucher_features
from .nn.subject_norm import normalize_name

_SUBJECT_CACHE: Dict[str, Any] = {"mtime": None, "switches": frozenset(), "path": None}


def _index_path() -> Path:
    from .v2.config import get_nn_storage_dir
    return Path(get_nn_storage_dir()) / "subject_to_index.json"


def load_known_switches() -> Set[str]:
    """模型认识的科目开关集合；文件变化（重训）后自动重载。"""
    path = _index_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return set()
    if _SUBJECT_CACHE["mtime"] != mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _SUBJECT_CACHE["switches"] = frozenset(data.keys())
            _SUBJECT_CACHE["mtime"] = mtime
            _SUBJECT_CACHE["path"] = str(path)
        except (OSError, json.JSONDecodeError):
            return set()
    return set(_SUBJECT_CACHE["switches"])


def load_standard_names() -> Set[str]:
    """标准一级科目表（`一级科目明细.md`），别名表的目标集合。"""
    from .v2.config import load_subject_list
    return set(load_subject_list())


def known_summary() -> Dict[str, Any]:
    switches = load_known_switches()
    model_names = {_strip_dir(s) for s in switches}
    std = load_standard_names()
    return {
        "model_switches": len(switches),
        "model_names": len(model_names),
        "standard_names": len(std),
        # 模型见过但不是标准科目：训练数据里混进来的二级科目/往来单位名
        "model_only": len(model_names - std),
        "source": str(_index_path()),
    }


def _strip_dir(switch: str) -> str:
    if switch.endswith("借]") or switch.endswith("贷]"):
        return switch.rsplit("[", 1)[0]
    return switch


def scan(df: pd.DataFrame,
         mapping: Dict[str, str],
         subjects_map: Optional[Dict[str, List[str]]] = None,
         summary_map: Optional[Dict[str, str]] = None,
         include_vouchers: bool = True,
         max_subjects: int = 500) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """扫描未知科目。

    Args:
        df: 原始序时账（行级）
        mapping: 字段映射（需要 voucher_no / subject）
        subjects_map / summary_map: 已算过的归一化结果，传进来可省一次归一化
        include_vouchers: 报告里是否带"凭证 → 未知科目"的明细
        max_subjects: 报告里最多列多少个科目（按影响凭证数降序）

    Returns:
        (report, unknown_by_voucher)
    """
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    sum_col = mapping.get("summary", "")

    if subjects_map is None:
        subjects_map, summary_map = extract_voucher_features(
            df, v_col, s_col, mapping.get("debit", ""),
            mapping.get("credit", ""), sum_col)

    known = load_known_switches()
    if not known:
        # 没有模型交付物时不做判断，避免把全部科目误报成未知
        return ({"known": known_summary(), "unknown": {"vouchers": 0, "names": 0,
                                                       "subjects": []},
                 "note": "未找到 subject_to_index.json，跳过未知科目检查"},
                {})

    std = load_standard_names()
    model_names = {_strip_dir(s) for s in known}

    # 1) 凭证 → 未知科目（去方向，按科目名去重）
    #    原因分三类，处理方式完全不同：
    #      alias_candidate —— 不在标准科目表：别名表可能能映射掉（应交税金→应交税费）
    #      need_training   —— 是标准科目但模型没见过：别名表没用，要补训练样本
    #      direction       —— 名字认识，但这个借贷方向没出现过（罕见）
    unknown_by_voucher: Dict[str, List[str]] = {}
    names_missing_switch: Dict[str, str] = {}
    for vid, switches in subjects_map.items():
        missing = {_strip_dir(s) for s in switches if s not in known}
        if missing:
            unknown_by_voucher[vid] = sorted(missing)
            for name in missing:
                if name in names_missing_switch:
                    continue
                names_missing_switch[name] = (
                    "direction" if name in model_names
                    else "need_training" if name in std
                    else "alias_candidate")
    if not unknown_by_voucher:
        report = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": int(len(df)), "vouchers": len(subjects_map),
            "known": known_summary(),
            "unknown": {"vouchers": 0, "ratio": 0.0, "names": 0, "subjects": []},
        }
        if include_vouchers:
            report["per_voucher"] = {}
        return report, {}

    # 2) 每个未知科目的影响面：涉及行数、原始写法、样例摘要
    raw = df[s_col].astype("string").str.strip()
    # 按"唯一写法"归一化再映射回去：normalize_name 要剥后缀 + 查别名表，
    # 逐行调在 6 万行上要 30 秒，按唯一值只调几百次（实测 40s → 1.5s）
    uniq = {str(x): normalize_name(str(x)) for x in pd.unique(raw.dropna())}
    norm = raw.map(lambda x: uniq.get(str(x), "") if pd.notna(x) else "")
    frame = pd.DataFrame({"v": df[v_col].astype(str), "raw": raw, "norm": norm})
    frame = frame[frame["norm"].isin(names_missing_switch.keys())]
    if sum_col and sum_col in df.columns:
        frame["sum"] = df[sum_col].astype("string").fillna("").str.slice(0, 60)
    else:
        frame["sum"] = ""

    subjects: List[Dict[str, Any]] = []
    for name, group in frame.groupby("norm", sort=False):
        vids = group["v"].unique()
        subjects.append({
            "name": name,
            "reason": names_missing_switch.get(name, "alias_candidate"),
            "in_standard": name in std,
            "vouchers": int(len(vids)),
            "rows": int(len(group)),
            "raw_names": sorted({str(x) for x in group["raw"].dropna().unique()})[:8],
            "sample_summaries": [s for s in group["sum"].dropna().unique() if s][:3],
        })
    subjects.sort(key=lambda x: (-x["vouchers"], -x["rows"], x["name"]))

    total = max(1, len(subjects_map))
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": int(len(df)),
        "vouchers": len(subjects_map),
        "known": known_summary(),
        "unknown": {
            "vouchers": len(unknown_by_voucher),
            "ratio": round(len(unknown_by_voucher) / total, 4),
            "names": len(subjects),
            "by_reason": _count_reason(subjects),
            "subjects": subjects[:max_subjects],
        },
        # 模型认识、但不是标准科目：训练数据里混入的往来单位/二级科目名，
        # 它们不会是"未知"，但会稀释科目开关的表达力，值得单独看一眼
        "model_only_subjects": sorted(model_names - std)[:100],
    }
    if include_vouchers:
        report["per_voucher"] = {v: names for v, names in sorted(unknown_by_voucher.items())}
    return report, unknown_by_voucher


def _count_reason(subjects: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in subjects:
        out[item["reason"]] = out.get(item["reason"], 0) + 1
    return out


def scan_file(path: str | Path,
              mapping: Optional[Dict[str, str]] = None,
              out: Optional[str | Path] = None) -> Dict[str, Any]:
    """读文件 → 自动识别字段（或用手工映射）→ 扫描 → 可选写 JSON。"""
    from .v2.classifier import JournalClassifier
    from .v2.io_utils import read_table

    df = read_table(Path(path))
    auto = JournalClassifier.auto_detect_columns(df)
    if mapping:
        auto.update({k: v for k, v in mapping.items() if v})
    for key in ("voucher_no", "subject"):
        if not auto.get(key):
            raise ValueError(f"缺少必填字段：{key}（可用 --mapping {key}=列名 指定）")

    report, _ = scan(df, auto)
    report["file"] = str(Path(path))
    report["mapping"] = {k: v for k, v in auto.items() if v}
    if out:
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return report


def _parse_mapping(items: Optional[List[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--mapping 需要 key=列名，收到：{item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m summary_cleaner.subjects",
        description="扫描序时账里的未知科目（归一化之后仍不在模型科目表里的）")
    ap.add_argument("file", help="序时账文件（xlsx/xls/csv）")
    ap.add_argument("-o", "--out", help="输出 JSON 路径（默认打印到标准输出）")
    ap.add_argument("--mapping", nargs="*", help="手工指定字段，如 voucher_no=凭证编号")
    args = ap.parse_args(argv)

    report = scan_file(args.file, _parse_mapping(args.mapping), args.out)
    if args.out:
        unk = report["unknown"]
        print(f"未知科目 {unk['names']} 个，涉及 {unk['vouchers']}/{report['vouchers']} 张凭证"
              f"（{unk['ratio']:.1%}）→ {args.out}")
        for item in unk["subjects"][:15]:
            print(f"  [{item['reason']}] {item['name']}：{item['vouchers']} 张 / "
                  f"{item['rows']} 行  原始写法 {item['raw_names'][:3]}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
