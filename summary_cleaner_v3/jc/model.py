# -*- coding: utf-8 -*-
"""模型层 —— 加载 4 件交付物、批量推理、算置信度。

这是「程序」在 3.0 里的第二块职责：把模型引用起来。它本身**不做判断**。

关键设计（都是踩坑换来的，改动前先读懂）：

  ① **同一输入只推理一次（输入折叠）**
     一批凭证里"科目组合 + 摘要"完全相同的很常见（同一业务按月重复）。
     模型输出是输入的纯函数，所以按 `(subjects, summary)` 折叠后只算一次、
     组内共用 —— 恒等变换，零精度损失。实测 1 万张凭证省 66% 调用。

     它同时修掉一个隐患：分批 forward 时批的组成会影响浮点归约顺序，
     同一个输入在不同批次里会拿到**末位不同**的概率，导致同一份序时账
     重跑结果可能变。折叠后一个输入只有一个答案，重跑稳定。

  ② **CPU 用 int8、GPU 用 fp16**
     模型权重以 fp16 落盘（训练时给 GPU 省显存），但 CPU 没有 fp16 原生
     运算单元，直接跑实测 2.5 条/秒（1 万条 ≈68 分钟）。CPU 加载后做一次
     动态量化到 int8：16 倍提速、精度无损、内存 1.3GB → 350MB。
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F
import torch.nn as nn

from .config import (
    CHUNK_SIZE, CONF_HIGH, CONF_MID, CPU_MIN_FREE_MEM_GB, INFERENCE_DEVICE,
    MAX_SEQ_LEN, MODEL_ARTIFACTS, MODEL_BUCKETS, MODEL_DIR,
)


# ============================================================================
# 模型结构
# ============================================================================

class JournalClassifierModel(nn.Module):
    """BGE 编码器 + 科目开关压缩 + 分类头。

        摘要整句 ─→ BGE encoder ─→ [CLS] 向量
        科目开关 one-hot ─→ Linear(num_subjects, 64) ─→ 64 维
        concat → Linear(hidden+64, 256) → ReLU → Dropout → Linear(256, 桶数)
    """

    def __init__(self, encoder: nn.Module, num_subjects: int, num_buckets: int,
                 subject_dim: int = 64, hidden_dim: int = 256,
                 dropout: float = 0.1):
        super().__init__()
        self.encoder = encoder
        self.encoder_hidden = encoder.config.hidden_size
        self.subject_dim = subject_dim
        self.num_subjects = num_subjects
        self.num_buckets = num_buckets
        self.subject_linear = nn.Linear(num_subjects, subject_dim)
        self.classifier = nn.Sequential(
            nn.Linear(self.encoder_hidden + subject_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_buckets),
        )
        for module in (self.subject_linear,):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor,
                subject_switches: torch.Tensor) -> torch.Tensor:
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask,
                               return_dict=True)
        cls_vec = outputs.last_hidden_state[:, 0, :]
        subject_vec = self.subject_linear(subject_switches)
        return self.classifier(torch.cat([cls_vec, subject_vec], dim=1))


# ============================================================================
# 交付物检查
# ============================================================================

def artifacts_status() -> Dict[str, bool]:
    """只做文件检查（不加载 660MB 权重），用于环境自检。"""
    st = {
        "fine_tuned/": (MODEL_ARTIFACTS["fine_tuned"] / "config.json").exists()
        and any(MODEL_ARTIFACTS["fine_tuned"].glob("*.safetensors")),
        "finance_classifier.pt": MODEL_ARTIFACTS["classifier"].exists(),
        "subject_to_index.json": MODEL_ARTIFACTS["subject_index"].exists(),
        "index_to_bucket.json": MODEL_ARTIFACTS["bucket_index"].exists(),
    }
    return st


def model_ready() -> bool:
    return all(artifacts_status().values())


def pick_device(prefer: str = INFERENCE_DEVICE) -> str:
    """按可用显存选设备：空闲 ≥2.5GB 用 cuda，否则 cpu。"""
    if prefer in ("cpu", "cuda"):
        return prefer
    try:
        if torch.cuda.is_available():
            free, _ = torch.cuda.mem_get_info()
            if free >= CPU_MIN_FREE_MEM_GB * 1024 ** 3:
                return "cuda"
    except Exception:
        pass
    return "cpu"


# ============================================================================
# 推理器
# ============================================================================

class JournalClassifier:
    """加载 4 件交付物，对「科目组合 + 摘要」批量分类。

    Usage:
        clf = JournalClassifier()            # 自动选设备
        preds = clf.predict_items(items)     # items: [{"subjects", "summary"}]
        # 或带输入折叠（推荐）：
        preds, uniq, key_of = clf.predict_folded(items)
    """

    def __init__(self, model_dir: Optional[Path] = None,
                 device: Optional[str] = None):
        self.model_dir = Path(model_dir or MODEL_DIR)
        self.device = pick_device(device or INFERENCE_DEVICE)
        self._precision = ""
        self.model: Optional[JournalClassifierModel] = None
        self.tokenizer = None
        self.subject_to_index: Dict[str, int] = {}
        self.index_to_bucket: Dict[str, str] = {}
        self.meta: Dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def load(self):
        """加载 4 件交付物（缺失显式报错，不静默降级）。"""
        fine_tuned = self.model_dir / "fine_tuned"
        st = artifacts_status() if self.model_dir == MODEL_DIR else None
        missing = []
        if not ((fine_tuned / "config.json").exists()
                and any(fine_tuned.glob("*.safetensors"))):
            missing.append(f"fine_tuned/  ({fine_tuned})")
        for name in ("finance_classifier.pt", "subject_to_index.json",
                     "index_to_bucket.json"):
            if not (self.model_dir / name).exists():
                missing.append(f"{name}  ({self.model_dir / name})")
        if missing:
            raise FileNotFoundError(
                "模型交付物缺失，无法启用模型打分类：\n  - "
                + "\n  - ".join(missing)
                + "\n请按 SKILL.md 第 1 步下载 4 件交付物到 assets/model/")

        from transformers import AutoModel, AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(fine_tuned), local_files_only=True)
        encoder = AutoModel.from_pretrained(str(fine_tuned), local_files_only=True)
        encoder.to(self.device)

        ckpt_path = self.model_dir / "finance_classifier.pt"
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
        num_subjects = ckpt["num_subjects"]
        num_buckets = ckpt["num_buckets"]

        self.model = JournalClassifierModel(
            encoder,
            num_subjects=num_subjects,
            num_buckets=num_buckets,
            subject_dim=ckpt.get("subject_dim", 64),
            hidden_dim=ckpt.get("hidden_dim", 256),
        )
        head = {k: v for k, v in ckpt["state_dict"].items()
                if not k.startswith("encoder.")}
        # 显式校验键集合：hidden_dim / num_subjects 不一致时，
        # 旧实现 strict=False 会静默带着随机权重的分类头上线
        model_keys = {k for k in self.model.state_dict()
                      if not k.startswith("encoder.")}
        miss = model_keys - set(head)
        extra = set(head) - set(self.model.state_dict())
        if miss or extra:
            raise ValueError(
                f"分类头权重与 checkpoint 不匹配：缺 {len(miss)} 键、多 {len(extra)} 键"
                f"（missing={sorted(miss)[:3]}, unexpected={sorted(extra)[:3]}）。"
                f"可能是 hidden_dim/num_subjects/桶数不一致，请重新下载交付物")
        self.model.load_state_dict(head, strict=False)
        self.model.to(self.device)

        if self.device == "cpu":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.model = torch.quantization.quantize_dynamic(
                    self.model.float(), {nn.Linear}, dtype=torch.qint8)
            self._precision = "int8"
        else:
            self._precision = "fp16"
        self.model.eval()

        # 索引：.pt 内嵌为权威，json 缺失时从 .pt 补
        pt_subj = ckpt.get("subject_to_index")
        idx_path = self.model_dir / "subject_to_index.json"
        json_subj = None
        if idx_path.exists():
            json_subj = json.loads(idx_path.read_text(encoding="utf-8"))
        if json_subj is not None and pt_subj is not None:
            if dict(json_subj) != dict(pt_subj):
                raise ValueError(
                    "subject_to_index.json 与 .pt 内嵌科目索引逐项不一致，"
                    "交付物版本不匹配，请重新下载")
            self.subject_to_index = dict(json_subj)
        elif json_subj is not None:
            self.subject_to_index = dict(json_subj)
        elif pt_subj is not None:
            self.subject_to_index = dict(pt_subj)

        bucket_path = self.model_dir / "index_to_bucket.json"
        if bucket_path.exists():
            self.index_to_bucket = json.loads(bucket_path.read_text(encoding="utf-8"))
        bucket_to_idx = ckpt.get("bucket_to_idx") or {}
        if bucket_to_idx:
            ok = (len(self.index_to_bucket) == num_buckets
                  and all(self.index_to_bucket.get(str(i)) == n
                          for n, i in bucket_to_idx.items()))
            if not ok:
                self.index_to_bucket = {str(v): k for k, v in bucket_to_idx.items()}

        self.meta = {
            "encoder_model": ckpt.get("encoder_model", ""),
            "best_val_acc": ckpt.get("best_val_acc"),
            "trained_at": ckpt.get("trained_at", ""),
            "total_records": ckpt.get("total_records"),
            "num_subjects": num_subjects,
            "num_buckets": num_buckets,
            "device": self.device,
            "precision": self._precision,
        }

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_subject(s: str) -> str:
        """兼容 [借方]/[贷方] 写法（索引键是短的 [借]/[贷]）。"""
        return str(s).replace("[借方]", "[借]").replace("[贷方]", "[贷]").strip()

    def _switch_matrix(self, items: List[dict]
                       ) -> Tuple[torch.Tensor, List[List[str]]]:
        """科目开关矩阵 + 每个 item 的未知科目列表。"""
        sw = torch.zeros(len(items), len(self.subject_to_index))
        unknown: List[List[str]] = []
        for i, it in enumerate(items):
            unk: List[str] = []
            for subj in it.get("subjects", []) or []:
                key = self._norm_subject(subj)
                idx = self.subject_to_index.get(key)
                if idx is None:
                    unk.append(key)
                else:
                    sw[i, idx] = 1.0
            unknown.append(unk)
        return sw, unknown

    def _forward(self, items: List[dict], padding: str = "max_length"
                 ) -> Tuple[List[Dict[str, float]], List[List[str]]]:
        """一批 items 的完整概率分布。"""
        summaries = [str(it.get("summary", "") or "").strip() for it in items]
        encoded = self.tokenizer(
            summaries, max_length=MAX_SEQ_LEN, truncation=True,
            padding=padding, return_tensors="pt")
        sw, unknown = self._switch_matrix(items)
        with torch.no_grad():
            logits = self.model(
                encoded["input_ids"].to(self.device),
                encoded["attention_mask"].to(self.device),
                sw.to(self.device))
            probs = F.softmax(logits, dim=1)
        out: List[Dict[str, float]] = []
        for i, s in enumerate(summaries):
            if not s:
                out.append({})
                continue
            p = probs[i]
            out.append({self.index_to_bucket.get(str(j), f"b{j}"): float(p[j])
                        for j in range(p.size(0))})
        return out, unknown

    def predict_items(self, items: List[dict],
                      chunk_size: int = CHUNK_SIZE) -> List[dict]:
        """逐条推理（不去重）。一般用 predict_folded 更省。"""
        if not items:
            return []
        probs_all: List[Dict[str, float]] = []
        unknown_all: List[List[str]] = []
        for start in range(0, len(items), chunk_size):
            p, u = self._forward(items[start:start + chunk_size])
            probs_all.extend(p)
            unknown_all.extend(u)
        return [{"probs": probs_all[i], "unknown_subjects": unknown_all[i],
                 "bucket": self.top_bucket(probs_all[i]),
                 "confidence": self.confidence(probs_all[i])}
                for i in range(len(items))]

    def predict_folded(self, items: List[dict],
                       chunk_size: int = CHUNK_SIZE
                       ) -> Tuple[List[dict], int, Dict[Any, int]]:
        """**按模型输入折叠**后推理 —— 3.0 的标准路径。

        Returns:
            (results, unique_count, key_to_index)
            results[i] 对应 items[i]（已展开回原顺序）
            key_to_index: 去重键 → 唯一输入下标，便于排查
        """
        key_to_idx: Dict[Any, int] = {}
        uniq: List[dict] = []
        idx_of: List[int] = []
        for it in items:
            key = (tuple(it.get("subjects", []) or []),
                   str(it.get("summary", "") or ""))
            i = key_to_idx.get(key)
            if i is None:
                i = len(uniq)
                key_to_idx[key] = i
                uniq.append(it)
            idx_of.append(i)

        uniq_res = self.predict_items(uniq, chunk_size=chunk_size)
        results = [uniq_res[i] for i in idx_of]
        return results, len(uniq), key_to_idx

    # ------------------------------------------------------------------
    # 结果解释
    # ------------------------------------------------------------------

    def top_bucket(self, probs: Dict[str, float]) -> Optional[str]:
        if not probs:
            return None
        return max(probs.items(), key=lambda kv: kv[1])[0]

    @staticmethod
    def confidence(probs: Dict[str, float]) -> str:
        """按模型对**最终桶**的概率分档：高 ≥0.80 / 中 ≥0.50 / 低 <0.50。"""
        if not probs:
            return "低"
        top = max(probs.values())
        if top >= CONF_HIGH:
            return "高"
        if top >= CONF_MID:
            return "中"
        return "低"

    def top_k(self, probs: Dict[str, float], k: int = 3) -> List[Tuple[str, float]]:
        return sorted(probs.items(), key=lambda kv: -kv[1])[:k]

    # ------------------------------------------------------------------
    # 摘要向量（相似度检索用，可选）
    # ------------------------------------------------------------------

    def embed_batch(self, summaries: List[str],
                    chunk_size: int = 128) -> np.ndarray:
        if not summaries:
            return np.zeros((0, self.model.encoder_hidden), dtype=np.float32)
        chunks = []
        for start in range(0, len(summaries), chunk_size):
            part = [str(s) for s in summaries[start:start + chunk_size]]
            enc = self.tokenizer(part, max_length=MAX_SEQ_LEN, truncation=True,
                                 padding="max_length", return_tensors="pt")
            with torch.no_grad():
                out = self.model.encoder(
                    input_ids=enc["input_ids"].to(self.device),
                    attention_mask=enc["attention_mask"].to(self.device))
            chunks.append(out.last_hidden_state[:, 0, :].cpu().numpy()
                          .astype(np.float32))
        return np.concatenate(chunks, axis=0)
