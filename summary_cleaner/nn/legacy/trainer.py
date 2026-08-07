# -*- coding: utf-8 -*-
"""
模型训练器 — 训练循环 + Checkpoint + 日志

训练流程:
1. 加载训练数据 (TrainingDataset)
2. 批量前向传播 → 计算凭证向量
3. Triplet Loss (Batch Hard Mining)
4. 反向传播 → 更新 Embedding 参数
5. 每 N 轮验证 → 保存最佳模型

输出:
- model.pt            : 最佳模型权重
- vocab.json          : 词表
- training_log.json   : 训练日志 (loss 曲线等)
"""

import json
import math
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader

from .model import VoucherEmbeddingModel, TripletLoss, cosine_similarity
from .data import TrainingDataset
from .vocab import VocabManager


class ModelTrainer:
    """训练管理器 — 封装完整的训练/验证/保存流程。"""

    def __init__(
        self,
        model: VoucherEmbeddingModel,
        vocab: VocabManager,
        save_dir: str = None,
        device: str = None,
    ):
        """
        Args:
            model: VoucherEmbeddingModel 实例
            vocab: VocabManager 实例
            save_dir: 模型保存目录（默认 nn/_storage/）
            device: 训练设备 ("cpu", "cuda", "cuda:0")
        """
        self.model = model
        self.vocab = vocab

        # NN 独立存储空间：nn/_storage/
        if save_dir is None:
            from summary_cleaner.v2.config import NN_STORAGE_DIR
            save_dir = str(NN_STORAGE_DIR)
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        # 设备
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.model.to(self.device)

        # 损失函数
        self.criterion = TripletLoss(margin=0.5)
        self.optimizer = None  # 在 train() 中创建

        # 训练状态
        self.current_epoch: int = 0
        self.best_val_loss: float = float("inf")
        self.train_history: List[dict] = []
        self.val_history: List[dict] = []

    # ------------------------------------------------------------------
    # 训练
    # ------------------------------------------------------------------

    def train(
        self,
        train_dataset: TrainingDataset,
        val_dataset: TrainingDataset = None,
        epochs: int = 100,
        batch_size: int = 32,
        learning_rate: float = 0.001,
        early_stop_patience: int = 15,
        log_every: int = 10,
        save_best_only: bool = True,
    ) -> dict:
        """执行完整训练流程。

        Args:
            train_dataset: 训练数据集
            val_dataset: 验证数据集（可选，但强烈建议提供）
            epochs: 最大训练轮数
            batch_size: 批次大小（小数据集用 16~32）
            learning_rate: 学习率
            early_stop_patience: 早停轮数（验证 loss 不下降 N 轮后停止）
            log_every: 每 N 轮打印/记录一次
            save_best_only: 只保存最佳模型（节省磁盘）

        Returns:
            {
                "best_epoch": int,
                "best_val_loss": float,
                "train_loss_final": float,
                "total_epochs": int,
                "early_stopped": bool,
                "train_losses": List[float],
                "val_losses": List[float],
            }
        """
        self.optimizer = torch.optim.Adam(
            self.model.parameters(), lr=learning_rate
        )

        # DataLoader
        train_loader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            shuffle=True,
            collate_fn=train_dataset.collate_fn,
        )

        val_loader = None
        if val_dataset is not None:
            val_loader = DataLoader(
                val_dataset,
                batch_size=batch_size,
                shuffle=False,
                collate_fn=val_dataset.collate_fn,
            )

        early_stop_counter = 0
        train_losses = []
        val_losses = []

        print(f"\n{'='*60}")
        print(f"开始训练")
        print(f"  设备: {self.device}")
        print(f"  训练样本: {len(train_dataset)} 张凭证, {train_dataset.num_buckets} 个桶")
        if val_dataset:
            print(f"  验证样本: {len(val_dataset)} 张凭证")
        print(f"  模型参数: {self._count_params():,}")
        print(f"  批次大小: {batch_size}, 学习率: {learning_rate}")
        print(f"  早停耐心: {early_stop_patience} 轮")
        print(f"{'='*60}\n")

        t_start = time.time()

        for epoch in range(1, epochs + 1):
            self.current_epoch = epoch

            # ── 训练阶段 ──
            self.model.train()
            epoch_train_loss = 0.0
            num_batches = 0

            for batch in train_loader:
                batch_data = batch["batch_data"]
                labels = batch["bucket_indices"].to(self.device)

                embeddings = self.model(batch_data)
                loss = self.criterion(embeddings, labels)

                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

                epoch_train_loss += loss.item()
                num_batches += 1

            avg_train_loss = epoch_train_loss / max(num_batches, 1)
            train_losses.append(avg_train_loss)

            # ── 验证阶段 ──
            avg_val_loss = None
            if val_loader is not None:
                avg_val_loss = self._validate(val_loader)
                val_losses.append(avg_val_loss)

            # ── 记录 ──
            if epoch % log_every == 0 or epoch == 1:
                self._log_epoch(epoch, avg_train_loss, avg_val_loss, epochs)

            self.train_history.append({
                "epoch": epoch,
                "train_loss": round(avg_train_loss, 6),
                "val_loss": round(avg_val_loss, 6) if avg_val_loss else None,
            })

            # ── 早停判断 ──
            monitor_loss = avg_val_loss if avg_val_loss is not None else avg_train_loss

            if monitor_loss < self.best_val_loss:
                self.best_val_loss = monitor_loss
                early_stop_counter = 0
                self._save_checkpoint("best_model.pt", epoch, monitor_loss)
            else:
                early_stop_counter += 1
                if early_stop_counter >= early_stop_patience:
                    print(f"\n[WARN] 早停: 验证 loss 连续 {early_stop_patience} 轮未下降")
                    break

        t_elapsed = time.time() - t_start

        # ── 训练后：计算桶中心 ──
        print(f"\n计算桶中心向量...")
        self.model.compute_bucket_centroids(train_dataset, train_dataset.bucket_to_idx)

        # ── 保存最终模型 ──
        if not save_best_only:
            self._save_checkpoint(
                f"model_epoch{epoch}.pt", epoch,
                avg_train_loss if val_loader is None else avg_val_loss,
            )

        # ── 保存词表 ──
        self.vocab.save(self.save_dir / "vocab.json")

        # ── 保存训练日志 ──
        self._save_training_log(t_elapsed, epoch, train_losses, val_losses)

        print(f"\n训练完成！耗时 {t_elapsed:.0f} 秒 ({t_elapsed/60:.1f} 分钟)")
        print(f"最佳模型: {self.save_dir / 'best_model.pt'}")

        return {
            "best_epoch": self._find_best_epoch(),
            "best_val_loss": round(self.best_val_loss, 6),
            "train_loss_final": round(train_losses[-1], 6),
            "total_epochs": epoch,
            "early_stopped": early_stop_counter >= early_stop_patience,
            "train_losses": train_losses,
            "val_losses": val_losses,
            "time_seconds": round(t_elapsed, 1),
        }

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------

    def _validate(self, val_loader: DataLoader) -> float:
        """计算验证集上的 Triplet Loss。"""
        self.model.eval()
        total_loss = 0.0
        num_batches = 0

        with torch.no_grad():
            for batch in val_loader:
                batch_data = batch["batch_data"]
                labels = batch["bucket_indices"].to(self.device)

                embeddings = self.model(batch_data)
                loss = self.criterion(embeddings, labels)

                total_loss += loss.item()
                num_batches += 1

        return total_loss / max(num_batches, 1)

    # ------------------------------------------------------------------
    # 评估 — 准确率
    # ------------------------------------------------------------------

    def evaluate_accuracy(
        self,
        dataset: TrainingDataset,
    ) -> dict:
        """评估模型在数据集上的分类准确率（基于桶中心最近邻）。

        Returns:
            {
                "overall_accuracy": float,
                "per_bucket_accuracy": {bucket: accuracy},
                "confusion_summary": List[dict],  # top 混淆对
            }
        """
        self.model.eval()

        # 确保桶中心已计算
        if not hasattr(self.model, "_bucket_names_list"):
            self.model.compute_bucket_centroids(
                dataset, dataset.bucket_to_idx
            )

        centroids = self.model.bucket_centroids  # [num_buckets, dim]
        bucket_names = self.model._bucket_names_list
        num_buckets = len(bucket_names)

        correct = 0
        total = 0
        per_bucket_correct = defaultdict(int)
        per_bucket_total = defaultdict(int)
        confusion_pairs = defaultdict(int)  # (true, pred) → count

        with torch.no_grad():
            for i in range(len(dataset)):
                item = dataset[i]
                true_bucket = item["bucket"]
                p_ids = torch.tensor(item["pattern_ids"], dtype=torch.long)
                k_ids = torch.tensor(item["keyword_ids"], dtype=torch.long)

                vec = self.model.compute_voucher_vector(p_ids, k_ids)
                vec = torch.nn.functional.normalize(vec, p=2, dim=0)

                # 最近邻分类
                sims = cosine_similarity(
                    vec.unsqueeze(0).expand(num_buckets, -1),
                    centroids,
                )
                pred_idx = sims.argmax().item()
                pred_bucket = bucket_names[pred_idx]

                if pred_bucket == true_bucket:
                    correct += 1
                    per_bucket_correct[true_bucket] += 1
                else:
                    confusion_pairs[(true_bucket, pred_bucket)] += 1

                per_bucket_total[true_bucket] += 1
                total += 1

        # 计算各桶准确率
        per_bucket_acc = {}
        for bucket in sorted(per_bucket_total.keys()):
            acc = per_bucket_correct[bucket] / max(per_bucket_total[bucket], 1)
            per_bucket_acc[bucket] = round(acc, 4)

        # Top 混淆对
        top_confusions = sorted(
            confusion_pairs.items(), key=lambda x: -x[1]
        )[:10]
        confusion_summary = [
            {"true": t, "pred": p, "count": c}
            for (t, p), c in top_confusions
        ]

        return {
            "overall_accuracy": round(correct / max(total, 1), 4),
            "total_samples": total,
            "correct": correct,
            "per_bucket_accuracy": per_bucket_acc,
            "confusion_summary": confusion_summary,
        }

    # ------------------------------------------------------------------
    # 保存 / 加载
    # ------------------------------------------------------------------

    def _save_checkpoint(self, filename: str, epoch: int, loss: float):
        """保存模型 checkpoint。"""
        path = self.save_dir / filename
        torch.save(
            {
                "epoch": epoch,
                "model_state_dict": self.model.state_dict(),
                "optimizer_state_dict": self.optimizer.state_dict(),
                "loss": loss,
                "dim": self.model.dim,
                "num_patterns": self.model.num_patterns,
                "num_keywords": self.model.num_keywords,
            },
            path,
        )

    def load_checkpoint(self, filename: str = "best_model.pt"):
        """加载模型 checkpoint。"""
        path = self.save_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"模型文件不存在: {path}")

        checkpoint = torch.load(path, map_location=self.device)

        # 验证维度匹配
        if checkpoint["num_patterns"] != self.model.num_patterns:
            raise ValueError(
                f"Pattern 数量不匹配: "
                f"checkpoint={checkpoint['num_patterns']}, "
                f"model={self.model.num_patterns}"
            )

        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.to(self.device)

        if self.optimizer and "optimizer_state_dict" in checkpoint:
            self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])

        print(f"[OK] 已加载模型: {path} (epoch={checkpoint['epoch']}, loss={checkpoint['loss']:.4f})")
        return checkpoint

    # ------------------------------------------------------------------
    # 训练日志
    # ------------------------------------------------------------------

    def _save_training_log(
        self, t_elapsed: float, total_epochs: int,
        train_losses: list, val_losses: list,
    ):
        """保存训练日志到 JSON。"""
        log = {
            "training_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "device": str(self.device),
            "model_config": {
                "dim": self.model.dim,
                "num_patterns": self.model.num_patterns,
                "num_keywords": self.model.num_keywords,
                "total_params": self._count_params(),
            },
            "best_epoch": self._find_best_epoch(),
            "best_val_loss": round(self.best_val_loss, 6),
            "total_epochs": total_epochs,
            "time_seconds": round(t_elapsed, 1),
            "train_losses": [round(x, 6) for x in train_losses],
            "val_losses": [round(x, 6) for x in val_losses],
        }
        path = self.save_dir / "training_log.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _count_params(self) -> int:
        """统计模型参数总数。"""
        return sum(p.numel() for p in self.model.parameters() if p.requires_grad)

    def _find_best_epoch(self) -> int:
        """从训练历史中找到最佳 epoch。"""
        if not self.train_history:
            return 0
        best = self.train_history[0]
        for h in self.train_history:
            loss = h.get("val_loss") or h["train_loss"]
            best_loss = best.get("val_loss") or best["train_loss"]
            if loss < best_loss:
                best = h
        return best["epoch"]

    def _log_epoch(self, epoch: int, train_loss: float,
                   val_loss: float, total_epochs: int):
        """打印训练进度。"""
        val_str = f", val_loss={val_loss:.6f}" if val_loss else ""
        best_str = " ★" if (
            (val_loss or train_loss) == self.best_val_loss
        ) else ""
        print(
            f"Epoch {epoch:4d}/{total_epochs} | "
            f"train_loss={train_loss:.6f}{val_str}{best_str}"
        )
