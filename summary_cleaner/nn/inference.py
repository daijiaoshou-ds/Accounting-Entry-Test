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

from summary_cleaner.v2.config import (
    get_nn_storage_dir, NN_HIDDEN_DIM,
)

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
        self.storage_dir = Path(storage_dir) if storage_dir else Path(get_nn_storage_dir())
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        self.model: Optional[FinanceClassifierModel] = None
        self.tokenizer = None
        self.subject_to_index: Dict[str, int] = {}
        self.index_to_bucket: Dict[str, str] = {}
        self.bucket_to_idx: Dict[str, int] = {}
        self.is_loaded = False
        self.model_info: Dict[str, Any] = {}
        self._precision = ""   # "int8"(CPU) / "fp16"(GPU)，load() 后确定

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
        # weights_only=True：checkpoint 仅含 dict/list/str/int/float/Tensor，
        # 无需 pickle 反序列化任意对象（旧实现 False，加载来源不可信的
        # .pt 文件时有任意代码执行风险）
        checkpoint = torch.load(pt_path, map_location="cpu", weights_only=True)

        num_subjects = checkpoint["num_subjects"]
        num_buckets = checkpoint["num_buckets"]
        self.bucket_to_idx = checkpoint.get("bucket_to_idx", {})

        self.model = FinanceClassifierModel(
            encoder,
            num_subjects=num_subjects,
            num_buckets=num_buckets,
            subject_dim=checkpoint.get("subject_dim", 64),
            hidden_dim=checkpoint.get("hidden_dim", NN_HIDDEN_DIM),
        )
        head_state = checkpoint["state_dict"]
        # 兼容: 旧 checkpoint 可能含完整 state_dict（过滤 encoder 键）
        filtered = {k: v for k, v in head_state.items() if not k.startswith("encoder.")}
        # 显式校验：旧实现 strict=False 静默吞掉 shape 不匹配，
        # hidden_dim/num_subjects 不一致时分类头会带随机权重上线且无告警。
        # 注意模型自带 encoder 子模块（其权重来自 fine_tuned/），校验
        # 范围只限分类头部分，否则 encoder.* 键会恒被误报"缺失"
        model_state = self.model.state_dict()
        head_keys = {k for k in model_state if not k.startswith("encoder.")}
        filtered_keys = set(filtered.keys())
        missing = head_keys - filtered_keys
        unexpected = filtered_keys - set(model_state.keys())
        if missing or unexpected:
            raise ValueError(
                f"分类头权重与 checkpoint 不匹配: 缺 {len(missing)} 键, "
                f"多 {len(unexpected)} 键（missing={sorted(missing)[:3]}, "
                f"unexpected={sorted(unexpected)[:3]}）。"
                f"可能是 hidden_dim/num_subjects/桶数不一致，请重新训练"
            )
        self.model.load_state_dict(filtered, strict=False)
        self.model.to(self.device)

        # ── CPU 推理优化（2026-08-14 实测，见技术报告 §3.6）──
        # fine_tuned 以 fp16 存储（训练时给 GPU 省显存）。CPU 无 fp16 原生
        # 运算单元，直接跑实测 2.5 条/秒（1 万条 ≈68 分钟），不可用。
        # 动态 int8 量化（Linear 层，加载时一次性完成）实测 41.9 条/秒
        # （约 16 倍），300 条金标准精度对比 fp16/fp32/int8 三者一致
        # （97.33%），模型内存 ~1.3GB → ~350MB。GPU 路径保持 fp16 不量化。
        # 注意: 量化前必须先 .float()——fp16 张量无法直接量化。
        # torch.quantization 已标记弃用（未来迁移 torchao），当前版本可用，
        # 用 catch_warnings 压掉弃用告警避免干扰用户日志。
        if self.device == "cpu":
            import warnings as _w
            with _w.catch_warnings():
                _w.simplefilter("ignore")
                self.model = torch.quantization.quantize_dynamic(
                    self.model.float(), {torch.nn.Linear}, dtype=torch.qint8,
                )
            self._precision = "int8"
        else:
            self._precision = "fp16"
        self.model.eval()

        # ③④ 索引（.pt 内嵌元数据为权威，json 缺失时从 .pt 补）
        idx_path = self.storage_dir / "subject_to_index.json"
        json_subject_index = None
        if idx_path.exists():
            with open(idx_path, "r", encoding="utf-8") as f:
                json_subject_index = json.load(f)
        pt_subject_index = checkpoint.get("subject_to_index")
        if json_subject_index is not None and pt_subject_index is not None:
            # 逐项校验（旧实现只比数量：同数量不同名的索引会静默错位，
            # 科目开关打到错误的权重位且无任何告警）
            if dict(json_subject_index) != dict(pt_subject_index):
                raise ValueError(
                    "交付物不一致: subject_to_index.json 与 finance_classifier.pt "
                    "内嵌科目索引逐项不一致（可能改名后未重训），请重新训练"
                )
            self.subject_to_index = dict(json_subject_index)
        elif json_subject_index is not None:
            self.subject_to_index = dict(json_subject_index)
        elif pt_subject_index is not None:
            self.subject_to_index = dict(pt_subject_index)
        if len(self.subject_to_index) != num_subjects:
            raise ValueError(
                f"交付物不一致: subject_to_index({len(self.subject_to_index)}) "
                f"与 finance_classifier.pt({num_subjects}) 不匹配"
            )

        bucket_path = self.storage_dir / "index_to_bucket.json"
        if bucket_path.exists():
            with open(bucket_path, "r", encoding="utf-8") as f:
                self.index_to_bucket = json.load(f)
        # json 与 .pt 内嵌映射逐项校验：桶改名后重训但 json 残留时，
        # 旧实现按长度判断会漏掉「同数量不同名字」的静默映射错误
        if self.bucket_to_idx:
            consistent = len(self.index_to_bucket) == num_buckets
            if consistent:
                for name, idx in self.bucket_to_idx.items():
                    if self.index_to_bucket.get(str(idx)) != name:
                        consistent = False
                        break
            if not consistent:
                self.index_to_bucket = {str(v): k for k, v in self.bucket_to_idx.items()}

        self.model_info = {
            "encoder_model": checkpoint.get("encoder_model", ""),
            "best_val_acc": checkpoint.get("best_val_acc"),
            "trained_at": checkpoint.get("trained_at", ""),
            "num_subjects": num_subjects,
            "num_buckets": num_buckets,
            "total_records": checkpoint.get("total_records"),
            "precision": self._precision,
            "device": self.device,
        }
        self.is_loaded = True
        print(f"[OK] 推理模型加载完成: {self.storage_dir} "
              f"(device={self.device}, precision={self._precision})")

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

    # ── 全量概率（融合打分用，V2.1 程序 30% + 模型 70%）──

    def predict_full(self, summary: str, subjects: List[str]) -> Dict[str, Any]:
        """全桶概率预测（融合打分用，返回所有桶的概率而非 top3）。

        Returns:
            {"probs": {桶名: 概率}, "unknown_subjects": [...]}
        """
        if not self.is_loaded:
            raise RuntimeError("模型未加载")
        summary = str(summary).strip()
        if not summary:
            return {"probs": {}, "unknown_subjects": list(subjects)}

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
        with torch.no_grad():
            logits = self.model(
                encoded["input_ids"].to(self.device),
                encoded["attention_mask"].to(self.device),
                switches.unsqueeze(0).to(self.device),
            )
            probs = F.softmax(logits, dim=1)[0]

        prob_dict = {
            self.index_to_bucket.get(str(j), f"b{j}"): float(probs[j])
            for j in range(probs.size(0))
        }
        return {"probs": prob_dict, "unknown_subjects": unknown}

    def predict_batch_full(self, items: List[Dict[str, Any]],
                           chunk_size: int = 64) -> List[Dict[str, Any]]:
        """批量全概率预测（自动分批 forward，融合打分用）。

        上万条凭证不能一次 forward（激活爆炸 OOM，8GB 显存实测
        batch=全部时需 5.5GB+ 分配失败）——内部按 chunk_size 分批。

        Args:
            items: [{"summary": str, "subjects": [str]}]
            chunk_size: 单次 forward 的凭证数（64 条 × 64 tokens 激活 ~200MB）

        Returns:
            [{"probs": {桶名: 概率}, "unknown_subjects": [...]}]
        """
        if not items:
            return []
        if not self.is_loaded:
            raise RuntimeError("模型未加载")

        summaries = [str(it.get("summary", "")).strip() for it in items]
        all_probs: List[Dict[str, float]] = []
        unknown_lists: List[List[str]] = []

        for start in range(0, len(items), chunk_size):
            chunk = items[start:start + chunk_size]
            chunk_sums = summaries[start:start + chunk_size]

            encoded = self.tokenizer(
                chunk_sums, max_length=64, truncation=True,
                padding="max_length", return_tensors="pt",
            )
            switches = torch.zeros(len(chunk), len(self.subject_to_index))
            for i, it in enumerate(chunk):
                unk: List[str] = []
                for subject in it.get("subjects", []):
                    subject = self._normalize_subject(str(subject).strip())
                    idx = self.subject_to_index.get(subject)
                    if idx is not None:
                        switches[i, idx] = 1.0
                    else:
                        unk.append(subject)
                unknown_lists.append(unk)

            with torch.no_grad():
                logits = self.model(
                    encoded["input_ids"].to(self.device),
                    encoded["attention_mask"].to(self.device),
                    switches.to(self.device),
                )
                probs = F.softmax(logits, dim=1)

            for i in range(len(chunk)):
                if not chunk_sums[i]:
                    all_probs.append({})
                    continue
                p = probs[i]
                prob_dict = {
                    self.index_to_bucket.get(str(j), f"b{j}"): float(p[j])
                    for j in range(p.size(0))
                }
                all_probs.append(prob_dict)

        results = [
            {"probs": all_probs[i], "unknown_subjects": unknown_lists[i]}
            for i in range(len(items))
        ]
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

    def embed_summary_batch(self, summaries: List[str],
                            chunk_size: int = 128) -> np.ndarray:
        """批量编码摘要 CLS 向量（Tab4 相似度检索用），返回 [N, hidden_size]。

        旧实现逐条单次 forward——万级记录 = 万次 Python 循环 + GPU 调用，
        页面卡死级慢。本方法按 chunk 批处理，把开销压到 ~N/chunk 次 forward。
        """
        if not self.is_loaded:
            raise RuntimeError("模型未加载")
        if not summaries:
            return np.zeros((0, self.model.encoder_hidden), dtype=np.float32)
        chunks = []
        for start in range(0, len(summaries), chunk_size):
            chunk = [str(s) for s in summaries[start:start + chunk_size]]
            encoded = self.tokenizer(
                chunk, max_length=64, truncation=True,
                padding="max_length", return_tensors="pt",
            )
            with torch.no_grad():
                outputs = self.model.encoder(
                    input_ids=encoded["input_ids"].to(self.device),
                    attention_mask=encoded["attention_mask"].to(self.device),
                )
            chunks.append(
                outputs.last_hidden_state[:, 0, :].cpu().numpy().astype(np.float32)
            )
        return np.concatenate(chunks, axis=0)
