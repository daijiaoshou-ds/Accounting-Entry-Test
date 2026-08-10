# -*- coding: utf-8 -*-
"""
推理预测 — V3.0（加载 4 件交付物 → 桶预测）

交付物:
  ① fine_tuned/（微调后 BGE）  ② finance_classifier.pt（分类头）
  ③ subject_to_index.json      ④ index_to_bucket.json

未知科目处理: 查不到索引的科目开关保持 0（不扩容，扩容破坏已训练权重），
收集进 unknown_subjects 返回并提示补充训练数据后重训。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import torch
import torch.nn.functional as F

from summary_cleaner.v2.config import NN_FINE_TUNED_DIR, NN_STORAGE_DIR

from .model import FinanceClassifierModel
from .model_loader import is_model_complete


class FinanceClassifierInference:
    """加载交付物做分类预测。"""

    def __init__(self, storage_dir: str = None, device: str = None):
        """
        Args:
            storage_dir: 交付物所在目录（默认 nn/_storage/）
            device: 默认自动 CUDA/CPU
        """
        self.storage_dir = Path(storage_dir) if storage_dir else Path(NN_STORAGE_DIR)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        self.model: Optional[FinanceClassifierModel] = None
        self.tokenizer = None
        self.subject_to_index: Dict[str, int] = {}
        self.index_to_bucket: Dict[str, str] = {}
        self.bucket_to_idx: Dict[str, int] = {}
        self.is_loaded = False
        self.model_info: Dict[str, Any] = {}

        self.load()

    def load(self) -> None:
        """加载 4 件交付物（缺失即报错，不静默）。"""
        # ① 微调后 BGE
        fine_tuned = self.storage_dir / "fine_tuned"
        if not is_model_complete(fine_tuned):
            raise FileNotFoundError(
                f"交付物①缺失（微调 BGE）: {fine_tuned}\n"
                f"请先在训练页面完成训练"
            )
        from transformers import AutoModel, AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(fine_tuned), local_files_only=True,
        )
        encoder = AutoModel.from_pretrained(
            str(fine_tuned), local_files_only=True,
        )
        encoder.to(self.device)

        # ② 分类头
        pt_path = self.storage_dir / "finance_classifier.pt"
        if not pt_path.exists():
            raise FileNotFoundError(f"交付物②缺失（分类头）: {pt_path}")
        checkpoint = torch.load(pt_path, map_location="cpu", weights_only=False)

        num_subjects = checkpoint["num_subjects"]
        num_buckets = checkpoint["num_buckets"]
        self.bucket_to_idx = checkpoint.get("bucket_to_idx", {})

        self.model = FinanceClassifierModel(
            encoder,
            num_subjects=num_subjects,
            num_buckets=num_buckets,
            subject_dim=checkpoint.get("subject_dim", 64),
            hidden_dim=256,
        )
        head_state = checkpoint["state_dict"]
        # 兼容: 旧 checkpoint 可能含完整 state_dict（过滤 encoder 键）
        filtered = {k: v for k, v in head_state.items() if not k.startswith("encoder.")}
        self.model.load_state_dict(filtered, strict=False)
        self.model.to(self.device)
        self.model.eval()

        # ③④ 索引（.pt 内嵌元数据为权威，json 缺失时从 .pt 补）
        idx_path = self.storage_dir / "subject_to_index.json"
        if idx_path.exists():
            with open(idx_path, "r", encoding="utf-8") as f:
                self.subject_to_index = json.load(f)
        if len(self.subject_to_index) != num_subjects:
            raise ValueError(
                f"交付物不一致: subject_to_index({len(self.subject_to_index)}) "
                f"与 finance_classifier.pt({num_subjects}) 不匹配"
            )

        bucket_path = self.storage_dir / "index_to_bucket.json"
        if bucket_path.exists():
            with open(bucket_path, "r", encoding="utf-8") as f:
                self.index_to_bucket = json.load(f)
        if len(self.index_to_bucket) != num_buckets and self.bucket_to_idx:
            self.index_to_bucket = {str(v): k for k, v in self.bucket_to_idx.items()}

        self.model_info = {
            "encoder_model": checkpoint.get("encoder_model", ""),
            "best_val_acc": checkpoint.get("best_val_acc"),
            "trained_at": checkpoint.get("trained_at", ""),
            "num_subjects": num_subjects,
            "num_buckets": num_buckets,
            "total_records": checkpoint.get("total_records"),
        }
        self.is_loaded = True
        print(f"[OK] 推理模型加载完成: {self.storage_dir}")

    # ── 预测 ──

    @staticmethod
    def _normalize_subject(subject: str) -> str:
        """归一化科目输入: 兼容 '[借方]'/'[贷方]' 与 '[借]'/'[贷]' 写法。

        训练数据索引键是短方向（应付账款[借]），用户手输可能带"方"字。
        """
        return subject.replace("[借方]", "[借]").replace("[贷方]", "[贷]")

    def predict(
        self,
        summary: str,
        subjects: List[str],
        top_k: int = 3,
    ) -> Dict[str, Any]:
        """预测一张凭证的桶。

        Args:
            summary: 摘要整句
            subjects: '科目[方向]' 列表（兼容 [借]/[借方]、[贷]/[贷方] 写法）

        Returns:
            {"bucket", "probability", "top3": [(桶, 概率)...], "unknown_subjects"}
        """
        if not self.is_loaded:
            raise RuntimeError("模型未加载")

        summary = str(summary).strip()
        if not summary:
            return {"bucket": "未分类", "probability": 0.0, "top3": [],
                    "unknown_subjects": list(subjects)}

        # 科目开关 one-hot（未知科目保持 0）
        switches = torch.zeros(len(self.subject_to_index), dtype=torch.float32)
        unknown: List[str] = []
        for subject in subjects:
            subject = self._normalize_subject(str(subject).strip())
            idx = self.subject_to_index.get(subject)
            if idx is not None:
                switches[idx] = 1.0
            else:
                unknown.append(subject)

        encoded = self.tokenizer(
            summary, max_length=64, truncation=True,
            padding="max_length", return_tensors="pt",
        )
        input_ids = encoded["input_ids"].to(self.device)
        attention_mask = encoded["attention_mask"].to(self.device)
        switches = switches.unsqueeze(0).to(self.device)

        with torch.no_grad():
            logits = self.model(input_ids, attention_mask, switches)
            probs = F.softmax(logits, dim=1)[0]

        top_indices = probs.argsort(descending=True)[:top_k]
        top3 = []
        for idx in top_indices.tolist():
            bucket = self.index_to_bucket.get(str(idx), f"b{idx}")
            top3.append((bucket, round(float(probs[idx]), 4)))

        top1 = top3[0]
        if unknown:
            print(f"[WARN] {len(unknown)} 个未知科目开关（新公司/新科目）: "
                  f"{', '.join(unknown[:5])}")

        return {
            "bucket": top1[0],
            "probability": top1[1],
            "top3": top3,
            "unknown_subjects": unknown,
        }

    def predict_batch(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量预测（逐条复用 predict）。

        Args:
            items: [{"summary": str, "subjects": [str]}]

        Returns:
            items + 预测字段
        """
        results = []
        for item in items:
            pred = self.predict(item["summary"], item.get("subjects", []))
            results.append({**item, **pred})
        return results

    # ── 摘要向量（相似度检索用）──

    def embed_summary(self, summary: str) -> np.ndarray:
        """CLS 向量（Tab4 摘要相似度检索用），返回 [hidden_size] float32。"""
        if not self.is_loaded:
            raise RuntimeError("模型未加载")
        encoded = self.tokenizer(
            str(summary), max_length=64, truncation=True,
            padding="max_length", return_tensors="pt",
        )
        with torch.no_grad():
            outputs = self.model.encoder(
                input_ids=encoded["input_ids"].to(self.device),
                attention_mask=encoded["attention_mask"].to(self.device),
            )
        cls_vec = outputs.last_hidden_state[0, 0, :].cpu().numpy().astype(np.float32)
        return cls_vec
