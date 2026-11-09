# -*- coding: utf-8 -*-
"""清洗服务：把「读表 → 分类 → 落盘 → 分页 → 导出」抽成与界面无关的函数。

设计约束（这一层能被 Streamlit / FastAPI / CLI 同时调用）：

1. **不 import streamlit**，也不 import 任何 web 框架；
2. **不碰全局单例的 UI 状态**，参数一律显式传入，返回值一律是 dict / bytes；
3. **进度通过回调**（`progress_cb(step, pct, message)`）上报，不假设某个前端；
4. 大对象落盘为 parquet，接口只返回分页切片。

术语：一次清洗 = 一个 job。job 目录结构见 `service.jobs`。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from ..v2.classifier import JournalClassifier
from ..v2.config import BUCKET_REGISTRY, load_subject_list
from ..v2.io_utils import EXCEL_ROW_LIMIT, read_table, serialize_table, too_large_hint
from .errors import invalid, not_found
from .jobs import STATUS_DONE, STATUS_FAILED, STATUS_RUNNING, Job, JobStore

ProgressCb = Optional[Callable[[int, str], None]]

# 视图列（复核页/结果页共用，顺序即前端默认列序）
REVIEW_COLUMNS = ["凭证号", "摘要", "科目", "金额", "业务分类", "置信度",
                  "校准概率", "模型Top3", "判定来源", "后处理"]

_CLASSIFIER: Optional[JournalClassifier] = None
_CLASSIFIER_LOCK = threading.Lock()


def get_classifier() -> JournalClassifier:
    """进程内单例：模型加载一次（约 6s / 1.3GB），不要每个请求都重建。"""
    global _CLASSIFIER
    if _CLASSIFIER is None:
        with _CLASSIFIER_LOCK:
            if _CLASSIFIER is None:
                _CLASSIFIER = JournalClassifier(subject_list=load_subject_list())
    return _CLASSIFIER


# ============================================================================
# 元数据
# ============================================================================

def health() -> Dict[str, Any]:
    clf = get_classifier()
    nn = getattr(clf, "_nn", None)
    configured = False
    try:
        from ..v2.config import get_nn_storage_dir
        configured = (Path(get_nn_storage_dir()) / "finance_classifier.pt").exists()
    except OSError:
        pass
    return {
        "status": "ok",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "buckets": len(clf._all_bucket_names),
        "subjects": len(clf.subject_list),
        "model": {
            # configured: 交付物在盘上（≠ 已加载）；loaded 才代表这次会话真的加载过
            "configured": configured,
            "available": bool(getattr(clf, "_nn_available", False)),
            "loaded": bool(getattr(nn, "is_loaded", False)),
            "device": getattr(nn, "device", None),
        },
    }


def buckets() -> Dict[str, Any]:
    """业务桶表（含父级/定义/训练标记/打分标记），前端下拉框与图例用。"""
    import json
    from pathlib import Path as _Path

    tax_path = (_Path(__file__).resolve().parents[1]
                / "v2" / "assets" / "bucket_taxonomy_v10.json")
    taxonomy: Dict[str, Any] = {}
    try:
        taxonomy = json.loads(tax_path.read_text(encoding="utf-8")).get("buckets", {})
    except (OSError, json.JSONDecodeError):
        pass

    clf = get_classifier()
    parents = {cfg.get("parent") for cfg in taxonomy.values() if cfg.get("parent")}
    items = []
    for name, info in BUCKET_REGISTRY.items():
        cfg = taxonomy.get(name) or {}
        items.append({
            "name": name,
            "code": cfg.get("code", ""),
            "parent": cfg.get("parent") or "",
            "definition": cfg.get("definition", ""),
            "train": bool(cfg.get("train", True)),
            "is_parent": name in parents,
            "hard_rule": name in clf._hard_rule_buckets,
            "sentinel": name in clf._sentinel_buckets,
            "scored": name in clf.bucket_names,
            "anchors": list(cfg.get("anchors") or []),
            "subjects": list((info.get("subjects") or {}).keys()),
        })
    return {"count": len(items), "items": items}


# ============================================================================
# 读表 + 字段识别
# ============================================================================

def _preview(df: pd.DataFrame, n: int = 20) -> List[Dict[str, Any]]:
    head = df.head(n).copy()
    head = head.where(pd.notna(head), None)
    return json.loads(head.to_json(orient="records", force_ascii=False,
                                   date_format="iso"))


def inspect(source: Any, filename: str = "") -> Dict[str, Any]:
    """读表并给出建议的字段映射（不落盘、不分类）。"""
    df = read_table(source, filename=filename)
    if df.empty:
        raise invalid("empty_table", "表格没有数据行")

    clf = get_classifier()
    mapping = clf.auto_detect_columns(df)
    ok, missing = clf.validate_column_mapping(mapping)
    return {
        "filename": filename or getattr(source, "name", ""),
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "suggested_mapping": mapping,
        "mapping_complete": bool(ok),
        "missing_fields": missing,
        "excel_row_limit": EXCEL_ROW_LIMIT,
        "preview": _preview(df),
    }


def required_fields() -> List[str]:
    return ["voucher_no", "subject"]


def check_mapping(mapping: Dict[str, str]) -> None:
    if not mapping:
        raise invalid("mapping_required", "缺少字段映射")
    clf = get_classifier()
    ok, missing = clf.validate_column_mapping(mapping)
    if not ok:
        raise invalid("mapping_incomplete",
                      "字段映射不完整：" + "、".join(missing),
                      missing=missing)


# ============================================================================
# 建任务 / 跑任务
# ============================================================================

def create_job(store: JobStore, data: bytes, filename: str,
               mapping: Optional[Dict[str, str]] = None,
               options: Optional[Dict[str, Any]] = None) -> Job:
    """落盘原件 + 建 job。mapping 为空时自动识别。"""
    if not data:
        raise invalid("empty_file", "文件内容为空")
    options = dict(options or {})
    df_head = read_table(data, filename=filename)
    if df_head.empty:
        raise invalid("empty_table", "表格没有数据行")

    if not mapping:
        mapping = get_classifier().auto_detect_columns(df_head)
    check_mapping(mapping)

    # 映射里值为空的字段直接剔除，避免 classify 里到处判 ""
    mapping = {k: v for k, v in mapping.items() if v}

    job = store.create(filename, mapping, options)
    store.save_source(job, data, filename)
    job.update_meta(rows=int(len(df_head)), columns=[str(c) for c in df_head.columns])
    return job


def run_job(job: Job, progress_cb: ProgressCb = None) -> Dict[str, Any]:
    """执行一次完整清洗，结果写进 job 目录。

    同步执行（调用方决定放线程还是放进程）；异常会把 job 置为 failed 并抛出。
    """
    def report(pct: int, msg: str) -> None:
        job.set_status(STATUS_RUNNING, pct, msg)
        if progress_cb:
            progress_cb(pct, msg)

    meta = job.meta
    try:
        report(5, "读取原始文件")
        path = job.path / (meta.get("stored_name") or "")
        if not path.exists():
            raise not_found("原始文件", job.id)
        df = read_table(path, filename=meta.get("filename", ""))
        job.update_meta(rows=int(len(df)))

        mapping = meta["mapping"]
        options = meta.get("options") or {}
        clf = get_classifier()

        report(15, "加载模型与规则")
        clf._load_nn_if_available()

        report(30, "逐凭证分类")
        classified, detail, stats = clf.classify(
            df, mapping,
            alpha=float(options.get("alpha", 0.2)),
            bookkeeper_col=options.get("bookkeeper_col") or None,
            bookkeeper_mapping=options.get("bookkeeper_mapping") or None,
        )

        report(80, "保存结果")
        job.save_frame("result.parquet", classified)
        if detail is not None and len(detail):
            job.save_frame("detail.parquet", detail)
        vouchers = _build_vouchers(classified, mapping)
        job.save_frame("vouchers.parquet", vouchers)

        # 未审训练数据：classify 已写到 nn storage，这里只把路径抄进 meta
        nn_path = stats.get("nn_training_path")
        if nn_path and Path(nn_path).exists():
            target = job.file("unreviewed.json")
            try:
                target.write_text(Path(nn_path).read_text(encoding="utf-8"),
                                  encoding="utf-8")
                stats["unreviewed_file"] = target.name
            except OSError:
                pass

        summary = _summarize(classified, detail, stats, mapping)
        job.update_meta(stats=stats, summary=summary,
                        rows=int(len(classified)),
                        vouchers=int(len(vouchers)),
                        finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        report(100, "完成")
        job.set_status(STATUS_DONE, 100, "完成")
        return summary
    except Exception as exc:
        job.set_status(STATUS_FAILED, 100, f"失败：{type(exc).__name__}: {exc}",
                       error=str(exc))
        raise


def _build_vouchers(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """凭证级汇总表（复核页按它分页，避免每页都从行级表 groupby）。"""
    v_col = mapping["voucher_no"]
    s_col = mapping.get("subject", "")
    sum_col = mapping.get("summary", "")
    d_col = mapping.get("debit", "")

    keep = [v_col] + [c for c in ("业务分类", "置信度", "校准概率", "模型Top3",
                                  "判定来源", "后处理") if c in df.columns]
    head = df[keep].drop_duplicates(subset=[v_col], keep="first").copy()

    if sum_col and sum_col in df.columns:
        first_sum = df[[v_col, sum_col]].drop_duplicates(subset=[v_col], keep="first")
        head = head.merge(first_sum, on=v_col, how="left")
    if s_col and s_col in df.columns:
        subj = (df[[v_col, s_col]].dropna(subset=[s_col])
                .astype({s_col: str}).groupby(v_col)[s_col]
                .agg(lambda s: "、".join(pd.unique(s))))
        head = head.merge(subj.rename("科目"), on=v_col, how="left")
    if d_col and d_col in df.columns:
        amt = pd.to_numeric(df[d_col].astype(str).str.replace(",", "").str.strip(),
                            errors="coerce").abs().fillna(0.0)
        head = head.merge(amt.groupby(df[v_col]).sum().rename("金额"),
                          left_on=v_col, right_index=True, how="left")

    head = head.rename(columns={v_col: "凭证号"})
    if sum_col and sum_col in head.columns:
        head = head.rename(columns={sum_col: "摘要"})
        head["摘要"] = head["摘要"].astype(str).str.slice(0, 100)

    p = pd.to_numeric(head.get("校准概率"), errors="coerce")
    head["_p"] = p.fillna(-1.0)
    head = head.sort_values("_p").drop(columns="_p").reset_index(drop=True)

    for col in REVIEW_COLUMNS:
        if col not in head.columns:
            head[col] = ""
    return head[REVIEW_COLUMNS]


def _summarize(df: pd.DataFrame, detail: Optional[pd.DataFrame],
               stats: Dict[str, Any], mapping: Dict[str, str]) -> Dict[str, Any]:
    v_col = mapping["voucher_no"]
    bucket_counts = (df.drop_duplicates(subset=[v_col], keep="first")["业务分类"]
                     .value_counts().to_dict())
    return {
        "rows": int(len(df)),
        "vouchers": int(df[v_col].nunique()),
        "coverage": stats.get("coverage"),
        "classified_count": stats.get("classified_count"),
        "bucket_counts": bucket_counts,
        "confidence_dist": stats.get("confidence_dist") or {},
        "nn_fusion": stats.get("nn_fusion") or {},
        "eval_mode": bool(stats.get("eval_mode")),
        "detail_rows": int(len(detail)) if detail is not None else 0,
        "detail_columns": int(len(detail.columns)) if detail is not None else 0,
    }


# ============================================================================
# 查询
# ============================================================================

def job_summary(job: Job) -> Dict[str, Any]:
    meta = job.meta
    return {
        "job_id": job.id,
        "filename": meta.get("filename", ""),
        "status": job.status,
        "mapping": meta.get("mapping", {}),
        "summary": meta.get("summary"),
        "created_at": meta.get("created_at"),
        "finished_at": meta.get("finished_at"),
    }


def review_rows(job: Job, level: str = "mid_low", offset: int = 0,
                limit: int = 50, bucket: str = "") -> Dict[str, Any]:
    """复核清单分页（凭证级，按校准概率升序：最不确定的排最前）。"""
    table = job.load_frame("vouchers.parquet")
    view = table
    keep = {"low": ["低"], "mid_low": ["中", "低"], "all": None}.get(level)
    if level not in ("low", "mid_low", "all"):
        raise invalid("bad_level", f"level 只能是 low/mid_low/all，收到 {level}")
    if keep is not None:
        view = view[view["置信度"].isin(keep)]
    if bucket:
        view = view[view["业务分类"] == bucket]

    total = int(len(view))
    offset = max(0, int(offset))
    limit = max(1, min(1000, int(limit)))
    page = view.iloc[offset:offset + limit].copy()
    page = page[[c for c in REVIEW_COLUMNS if c in page.columns]]
    page = page.where(pd.notna(page), None)
    records = json.loads(page.to_json(orient="records", force_ascii=False,
                                      date_format="iso"))
    return {"total": total, "offset": offset, "limit": limit,
            "level": level, "bucket": bucket, "items": records}


def export(job: Job, kind: str = "result") -> Tuple[bytes, str, str, str]:
    """导出为可下载文件。返回 (data, filename, mime, hint)。"""
    stem = Path(job.meta.get("filename") or "结果").stem
    if kind == "result":
        df = job.load_frame("result.parquet")
        label = "分类结果"
    elif kind == "detail":
        df = job.load_frame("detail.parquet")
        label = "分数明细"
    elif kind == "vouchers":
        df = job.load_frame("vouchers.parquet")
        label = "凭证汇总"
    else:
        raise invalid("bad_kind", f"kind 只能是 result/detail/vouchers，收到 {kind}")

    data, final_name, mime, _ = serialize_table(df, f"{stem}_{label}.xlsx",
                                                sheet_name=label)
    return data, final_name, mime, too_large_hint(len(df))


def unreviewed(job: Job) -> Dict[str, Any]:
    """未审训练数据（给复核 AI / 人工看的那份）。"""
    path = job.file("unreviewed.json")
    if not path.exists():
        raise not_found("未审训练数据", job.id)
    return json.loads(path.read_text(encoding="utf-8"))
