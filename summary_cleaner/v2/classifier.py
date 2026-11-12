# -*- coding: utf-8 -*-
"""序时账清洗 —— 对外入口（单一流程，不再区分"程序"与"模型"）。

历史包袱：V2.1 曾有一套「程序打分」（PMI 传播 + 科目偏好 + 关键词 + 金额 +
纠错 + 制单人）与模型概率按 0.2/0.8 加权融合。实测证明那一整套对最终桶
零影响（全开/全关差异 0 条；与纯模型比 11,456 张只差 3 张），因此 4.0
把它们整体删除，只保留：

    读表 → 科目归一化 → 模型识别 → 程序硬约束（四类机械凭证）→ 写回

具体编排见 `summary_cleaner/pipeline.py`。本类现在只负责：
① 加载/重载模型（一次 1.3GB，务必单例复用）
② 列名自动识别与映射校验
③ 把 `classify()` 转发给 pipeline，保持旧调用方不用改

保留 `JournalClassifier` 这个名字是为了不打断 UI / service / 脚本的调用；
内部已经没有"程序打分"这一层了。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .config import (
    NN_FUSION_ENABLED,
    NN_INFERENCE_DEVICE,
    load_buckets_json,
    load_subject_list,
)
from .errors import EngineNotReady

# 硬约束桶：程序直接判定，模型不参与（这些形态可枚举，模型学不会也不需要学）
HARD_RULE_BUCKETS = ("期末结转", "资金内部往来", "汇兑损益", "员工借支")
# 哨兵桶：只作为人工标记用，任何自动判定都不该输出它
SENTINEL_BUCKETS = ("待复核",)



class JournalClassifier:
    """序时账业务分类器 —— 4.0 单一流程的对外门面。

    Usage:
        clf = JournalClassifier()
        df_out, detail, stats = clf.classify(df, mapping)
    """

    def __init__(self, buckets_path: Path = None,
                 subject_list: List[str] = None):
        if subject_list is None:
            subject_list = load_subject_list()

        self.subject_list = subject_list
        self.global_counters = None  # 4.0 不再累积词频/金额统计

        self._all_bucket_names = list(load_buckets_json(buckets_path).keys())
        self._hard_rule_buckets = set(HARD_RULE_BUCKETS)
        self._sentinel_buckets = set(SENTINEL_BUCKETS)
        # 模型可输出的桶（= 注册表 - 硬约束 - 哨兵 - 被合并的子桶）
        from .config import MERGED_BUCKETS
        self.merged_buckets = set(MERGED_BUCKETS)
        self.bucket_names = [b for b in self._all_bucket_names
                             if b not in self._hard_rule_buckets
                             and b not in self._sentinel_buckets
                             and b not in self.merged_buckets]

        # 模型（懒加载，失败则整体不可用 —— 没有程序兜底了，这是设计选择）
        self._nn = None
        self._nn_available = False
        self._nn_attempted = False
        self._nn_pt_mtime = None
        self._nn_warned = False

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    def _load_nn_if_available(self) -> None:
        """懒加载模型；交付物 mtime 变化（重训落盘）后自动重载。

        4.0 起模型是唯一判定来源，加载失败就没有结果可给（旧版还能退化成
        纯程序，现在不会了）——调用方要据此给出明确报错，而不是静默输出
        "未分类"。
        """
        if not NN_FUSION_ENABLED:
            return
        from .config import get_nn_storage_dir

        pt_path = Path(get_nn_storage_dir()) / "finance_classifier.pt"
        try:
            current_mtime = pt_path.stat().st_mtime if pt_path.exists() else None
        except OSError:
            current_mtime = None

        if (self._nn_available or self._nn_attempted) and self._nn_pt_mtime == current_mtime:
            return

        self._nn_attempted = True
        self._nn_pt_mtime = current_mtime
        if current_mtime is None:
            return

        try:
            device = NN_INFERENCE_DEVICE
            if device == "auto":
                try:
                    import torch
                    if torch.cuda.is_available():
                        free_mem, _ = torch.cuda.mem_get_info()
                        device = "cuda" if free_mem >= 2.5 * 1024**3 else "cpu"
                    else:
                        device = "cpu"
                except ImportError:
                    device = "cpu"

            from summary_cleaner.nn.inference import FinanceClassifierInference
            self._nn = FinanceClassifierInference(device=device)
            self._nn_available = True
            print(f"[INF] 模型就绪: 纯模型判定（推理设备={device}）")
        except Exception as e:  # noqa: BLE001
            self._nn = None
            self._nn_available = False
            if not self._nn_warned:
                self._nn_warned = True
                print(f"[WARN] 模型不可用: {type(e).__name__}: {e}")

    def reset_nn_cache(self) -> None:
        """强制下次 classify 重新加载模型（重训完成 / 显存释放后调用）。"""
        self._nn = None
        self._nn_available = False
        self._nn_attempted = False
        self._nn_pt_mtime = None
        self._nn_warned = False

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def classify(self, df: pd.DataFrame,
                 column_mapping: Dict[str, str],
                 alpha: float = 0.2,
                 bookkeeper_col: str = None,
                 bookkeeper_mapping: Dict[str, str] = None,
                 progress=None) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
        """执行一次清洗。

        Args:
            df: 序时账原始数据
            column_mapping: {voucher_no, subject, subject_name, summary, debit, credit}
            alpha / bookkeeper_col / bookkeeper_mapping: **已废弃**，仅为兼容旧调用
                保留签名。4.0 没有程序打分，这三个参数不再起作用。

        Returns:
            (classified_df, 凭证明细, stats)
        """
        from summary_cleaner import pipeline

        ok, missing = self.validate_column_mapping(column_mapping)
        if not ok:
            raise ValueError(f"缺少必填字段映射：{', '.join(missing)}")

        self._load_nn_if_available()
        if not self._nn_available:
            raise EngineNotReady(
                "模型未就绪，无法分类。请确认 summary_cleaner/nn/_storage/"
                "finance_classifier.pt 存在（4.0 不再有纯程序兜底）。"
            )

        result = pipeline.run(df, column_mapping, self, progress=progress)
        return result.df, result.detail, result.stats

    # ------------------------------------------------------------------
    # 列名识别（对外稳定接口，API / UI 都在用）
    # ------------------------------------------------------------------

    @staticmethod
    def auto_detect_columns(df: pd.DataFrame) -> Dict[str, str]:
        """列名 → 字段映射（**返回历史键名**：voucher_no / subject / subject_name…）。

        实现在 `journal_io`（全项目唯一一份别名表）。这里保留旧键名，是因为
        pipeline / service / api / web 都还在用它们；新代码请直接用
        `journal_io.read(...)` 拿 canonical 名（voucher_id / subject_l1 …）。
        """
        from journal_io import detect, to_legacy_mapping
        return to_legacy_mapping(detect(list(df.columns)))

    @staticmethod
    def validate_column_mapping(mapping: Dict[str, str]) -> Tuple[bool, List[str]]:
        """校验必填字段。"""
        required = ["voucher_no", "subject"]
        missing = [f for f in required if not mapping.get(f)]
        return (not missing), missing

    # ------------------------------------------------------------------
    # 展示用信息
    # ------------------------------------------------------------------

    def engine_summary(self) -> Dict[str, object]:
        """当前引擎状态（替代旧的 fusion_summary）。"""
        nn = self._nn
        return {
            "mode": "纯模型（无程序打分）",
            "available": self._nn_available,
            "device": getattr(nn, "device", None),
            "hard_rule_buckets": list(HARD_RULE_BUCKETS),
            "scored_buckets": len(self.bucket_names),
        }


_CLASSIFIER: Optional[JournalClassifier] = None
_CLASSIFIER_LOCK = threading.Lock()


def get_classifier() -> JournalClassifier:
    """进程内单例：模型加载一次（约 6s / 1.3GB），不要每个请求都重建。

    以前这个单例放在 `summary_cleaner/service/cleaner.py`，那层服务在 5.0 之后
    随老 HTTP 任务一起退场了 —— 单例属于类本身，放在这里更合适。
    """
    global _CLASSIFIER
    if _CLASSIFIER is None:
        with _CLASSIFIER_LOCK:
            if _CLASSIFIER is None:
                _CLASSIFIER = JournalClassifier(subject_list=load_subject_list())
    return _CLASSIFIER


__all__ = ["JournalClassifier", "HARD_RULE_BUCKETS", "SENTINEL_BUCKETS",
           "get_classifier"]
