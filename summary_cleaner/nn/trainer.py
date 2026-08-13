# -*- coding: utf-8 -*-
"""
微调训练器 — V3.0（BGE 中文模型微调）

- Loss: CrossEntropy（分类任务，非聚类）
- 三策略: full（全量微调）/ frozen（冻结编码器只训头）/ lora（LoRA 适配器）
- 显存三件套: fp16 AMP + gradient checkpointing + 短序列截断
- 交付物 4 件: fine_tuned/ + finance_classifier.pt + 两个索引 json

注意: 控制台 print 只能用中文/ASCII（Windows GBK），严禁 emoji。
"""

import json
import shutil
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from summary_cleaner.v2.config import (
    NN_DEFAULT_BATCH_SIZE_V3,
    NN_DEFAULT_EPOCHS_V3,
    NN_DEFAULT_MAX_LENGTH,
    NN_EARLY_STOP_PATIENCE_V3,
    NN_ENCODER_LR,
    NN_FINE_TUNED_DIR,
    NN_HEAD_LR,
    NN_HIDDEN_DIM,
    NN_STORAGE_DIR,
    NN_SUBJECT_DIM,
)

from .model import FinanceClassifierModel
from .model_loader import load_encoder_tokenizer, resolve_model_dir


# ============================================================================
# 数据集
# ============================================================================

class FinanceDataset(Dataset):
    """预 tokenize 数据集（padding 固定长度，collate 直接 stack）。

    每条样本: {input_ids, attention_mask, subject_switches, label}
    """

    def __init__(
        self,
        records: List[Dict],
        tokenizer,
        subject_to_index: Dict[str, int],
        bucket_to_idx: Dict[str, int],
        max_length: int = NN_DEFAULT_MAX_LENGTH,
    ):
        self.items: List[Dict[str, torch.Tensor]] = []
        for rec in records:
            encoded = tokenizer(
                rec["summary"],
                max_length=max_length,
                truncation=True,
                padding="max_length",
                return_tensors="pt",
            )
            switches = torch.zeros(len(subject_to_index), dtype=torch.float32)
            for subject in rec.get("subjects", []):
                idx = subject_to_index.get(subject)
                if idx is not None:
                    switches[idx] = 1.0
            self.items.append({
                "input_ids": encoded["input_ids"][0],
                "attention_mask": encoded["attention_mask"][0],
                "subject_switches": switches,
                "label": torch.tensor(bucket_to_idx[rec["bucket"]], dtype=torch.long),
            })

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        return self.items[idx]


# ============================================================================
# 训练器
# ============================================================================

class FinanceClassifierTrainer:
    """BGE 微调训练器。"""

    def __init__(
        self,
        encoder_model_name: str,
        model_dir: Path,
        records: List[Dict],
        subject_to_index: Dict[str, int],
        bucket_to_idx: Dict[str, int],
        save_dir: Path = None,
        device: str = None,
    ):
        """
        Args:
            encoder_model_name: BGE 模型名（MODEL_CHOICES 键）
            model_dir: 已解析的本地模型目录（resolve_model_dir 结果）
            records: 训练样本（split_records 输出）
            subject_to_index: 科目开关映射（build_subject_switch_index 输出）
            bucket_to_idx: 桶映射（build_bucket_index 输出）
            save_dir: 交付物输出目录（默认 nn/_storage/）
            device: 默认自动 CUDA/CPU
        """
        self.encoder_model_name = encoder_model_name
        self.save_dir = Path(save_dir) if save_dir else Path(NN_STORAGE_DIR)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.device = device or (
            "cuda" if torch.cuda.is_available() else "cpu"
        )

        # 加载 BGE 编码器 + tokenizer
        self.encoder, self.tokenizer = load_encoder_tokenizer(model_dir)

        # 构建模型
        self.subject_to_index = subject_to_index
        self.bucket_to_idx = bucket_to_idx
        self.model = FinanceClassifierModel(
            self.encoder,
            num_subjects=len(subject_to_index),
            num_buckets=len(bucket_to_idx),
            subject_dim=NN_SUBJECT_DIM,
            hidden_dim=NN_HIDDEN_DIM,
        )
        self.model.to(self.device)

        # 数据集
        self.records = records
        self.dataset = FinanceDataset(
            records, self.tokenizer, subject_to_index, bucket_to_idx,
        )

    # ── 策略 ──

    def _apply_strategy(self, strategy: str):
        """full / frozen / lora。"""
        if strategy == "frozen":
            for p in self.model.encoder.parameters():
                p.requires_grad = False
            print("[INF] 策略: 冻结编码器，只训练分类头")
        elif strategy == "lora":
            try:
                from peft import LoraConfig, get_peft_model
            except ImportError:
                raise RuntimeError(
                    "未安装 peft 库（LoRA 需要）。pip install peft 后重试，"
                    "或改用 full/frozen 策略。"
                )
            config = LoraConfig(
                r=16, lora_alpha=32,
                target_modules=["query", "value"],
                lora_dropout=0.1, bias="none",
            )
            self.model.encoder = get_peft_model(self.model.encoder, config)
            # 冻结基座 + checkpointing 时必须启用
            self.model.encoder.enable_input_require_grads()
            self.model.encoder.to(self.device)
            print("[INF] 策略: LoRA 微调（r=16, target=query/value）")
        else:  # full
            print("[INF] 策略: 全量微调（编码器 + 分类头）")

    def _build_param_groups(self, encoder_lr: float, head_lr: float):
        """按参数名分组: 含 'encoder' → encoder 组，其余 → head 组。"""
        encoder_params, head_params = [], []
        for name, param in self.model.named_parameters():
            if not param.requires_grad:
                continue
            if "encoder" in name:
                encoder_params.append(param)
            else:
                head_params.append(param)

        groups = [{"params": head_params, "lr": head_lr}]
        if encoder_params:
            groups.append({"params": encoder_params, "lr": encoder_lr})
        return groups

    def _prepare_model_for_training(self):
        """gradient checkpointing + use_cache=False（显存优化）。"""
        enc = self.model.encoder
        if hasattr(enc, "gradient_checkpointing_enable"):
            enc.gradient_checkpointing_enable()
        if hasattr(enc, "config") and enc.config is not None:
            enc.config.use_cache = False

    # ── 训练主循环 ──

    def train(
        self,
        strategy: str = "full",
        epochs: int = NN_DEFAULT_EPOCHS_V3,
        batch_size: int = NN_DEFAULT_BATCH_SIZE_V3,
        encoder_lr: float = NN_ENCODER_LR,
        head_lr: float = NN_HEAD_LR,
        max_length: int = NN_DEFAULT_MAX_LENGTH,
        early_stop_patience: int = NN_EARLY_STOP_PATIENCE_V3,
        use_amp: bool = True,
        train_records: Optional[List[Dict]] = None,
        val_records: Optional[List[Dict]] = None,
        seed: int = 42,
        progress_callback: Optional[Callable] = None,
        stop_flag: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """训练模型，best epoch 落盘 4 件交付物。

        Args:
            train_records/val_records: 页面层用 split_records 分层划分后传入；
                None 时自动 80/20 随机划分兜底
            max_length: 摘要 token 截断（压显存，页面滑块可调）
            progress_callback: 每轮结束调用 callable({epoch, total_epochs,
                train_loss, val_loss, val_acc, best_epoch, best_val_acc})，
                前端用它做实时进度显示（回调里写文件，不要跑重活）
            stop_flag: 每轮开始前调用，返回 True 则停止训练（best 已保存，
                不会再跑验证）

        Returns:
            训练结果 dict
        """
        torch.manual_seed(seed)
        if self.device == "cuda":
            torch.cuda.manual_seed_all(seed)

        if self.device == "cpu" and strategy == "full":
            print("[WARN] CPU 上全量微调 326M 参数不现实，建议改用 frozen/lora "
                  "策略或接 GPU 后训练")

        amp_on = use_amp and self.device == "cuda"

        # ── 数据划分 ──
        if train_records is None or val_records is None:
            from torch.utils.data import Subset
            # fallback 必须按传入的 max_length 重建数据集——
            # __init__ 里的 self.dataset 用默认长度 tokenize，
            # 直接用会忽略 train() 的 max_length 参数
            full_ds = FinanceDataset(
                self.records, self.tokenizer,
                self.subject_to_index, self.bucket_to_idx,
                max_length=max_length,
            )
            rng = torch.Generator().manual_seed(seed)
            perm = torch.randperm(len(full_ds), generator=rng).tolist()
            n_val = max(1, round(len(full_ds) * 0.2))
            val_idx = set(perm[:n_val])
            train_idx = [i for i in range(len(full_ds)) if i not in val_idx]
            train_ds = Subset(full_ds, train_idx)
            val_ds = Subset(full_ds, sorted(val_idx))
        else:
            train_ds = FinanceDataset(
                train_records, self.tokenizer,
                self.subject_to_index, self.bucket_to_idx,
                max_length=max_length,
            )
            val_ds = FinanceDataset(
                val_records, self.tokenizer,
                self.subject_to_index, self.bucket_to_idx,
                max_length=max_length,
            )

        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

        # ── 策略 + 优化器 ──
        self._apply_strategy(strategy)
        self._prepare_model_for_training()

        param_groups = self._build_param_groups(encoder_lr, head_lr)
        optimizer = torch.optim.AdamW(param_groups)

        # 学习率调度: warmup 10% 步数 + 线性衰减（微调标准做法，
        # 减缓预训练权重被大步长推离最优区域的速度）
        total_steps = len(train_loader) * epochs
        try:
            from transformers import get_linear_schedule_with_warmup
            scheduler = get_linear_schedule_with_warmup(
                optimizer,
                num_warmup_steps=int(0.1 * total_steps),
                num_training_steps=total_steps,
            )
            print(f"[INF] LR 调度: warmup {int(0.1 * total_steps)} 步 + 线性衰减")
        except ImportError:
            scheduler = None

        scaler = torch.amp.GradScaler("cuda", enabled=amp_on)

        # ── 训练循环 ──
        train_losses, val_losses, val_accs = [], [], []
        best_val_acc = -1.0
        best_epoch = 0
        patience_counter = 0
        early_stopped = False
        stopped_by_flag = False
        start_time = time.time()
        last_val_metrics: Optional[Dict] = None
        best_val_metrics: Optional[Dict] = None  # 落盘 best 那轮的指标快照

        for epoch in range(1, epochs + 1):
            if stop_flag and stop_flag():
                stopped_by_flag = True
                break

            self.model.train()
            total_loss, total_batches = 0.0, 0
            for batch in train_loader:
                if stop_flag and stop_flag():
                    stopped_by_flag = True
                    break

                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                switches = batch["subject_switches"].to(self.device)
                labels = batch["label"].to(self.device)

                optimizer.zero_grad()
                with torch.amp.autocast(
                    device_type="cuda", dtype=torch.float16, enabled=amp_on,
                ):
                    logits = self.model(input_ids, attention_mask, switches)
                    loss = F.cross_entropy(logits, labels)

                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                if scheduler is not None:
                    scheduler.step()

                total_loss += loss.item()
                total_batches += 1

            if stopped_by_flag:
                print(f"[INF] 收到停止请求，已保存 best（Epoch {best_epoch}）")
                break

            train_loss = total_loss / max(total_batches, 1)
            train_losses.append(train_loss)

            # 验证
            val_metrics = self._evaluate(val_loader, amp_on=amp_on)
            last_val_metrics = val_metrics
            val_losses.append(val_metrics["loss"])
            val_accs.append(val_metrics["accuracy"])

            print(f"[Epoch {epoch}/{epochs}] train_loss={train_loss:.4f} "
                  f"val_loss={val_metrics['loss']:.4f} "
                  f"val_acc={val_metrics['accuracy']:.4f}")

            # best 落盘（val_acc 提升）
            if val_metrics["accuracy"] > best_val_acc:
                best_val_acc = val_metrics["accuracy"]
                best_epoch = epoch
                patience_counter = 0
                best_val_metrics = val_metrics  # 与落盘模型同步的指标快照
                try:
                    self._save_best(epoch, val_metrics["accuracy"])
                except Exception as e:
                    print(f"[WARN] 交付物保存失败: {e}")
            else:
                patience_counter += 1

            # 实时进度回调（前端刷新用；放在早停 break 之前，
            # 早停发生的最后一轮指标也要上报）
            if progress_callback:
                try:
                    progress_callback({
                        "epoch": epoch,
                        "total_epochs": epochs,
                        "train_loss": train_loss,
                        "val_loss": val_metrics["loss"],
                        "val_acc": val_metrics["accuracy"],
                        "best_epoch": best_epoch,
                        "best_val_acc": best_val_acc,
                    })
                except Exception as e:
                    print(f"[WARN] 进度回调失败: {e}")

            if patience_counter >= early_stop_patience:
                early_stopped = True
                print(f"[INF] 早停: {patience_counter} 轮无提升")
                break

        total_time = time.time() - start_time

        # ── 最终评估（best 模型已在 _save_best 时保存）──
        if stopped_by_flag and last_val_metrics is not None:
            final_metrics = last_val_metrics  # 被手动停止: 不重复跑验证
        else:
            final_metrics = self._evaluate(val_loader, amp_on=amp_on)

        # 分桶准确率/混淆用 best 落盘那轮的快照——final_metrics 是最后一轮
        # 模型（早停时与已保存的 best 不是同一个模型），拿它做质量报告
        # 会与实际上线的模型对不上
        if best_val_metrics is not None:
            report_metrics = best_val_metrics
        else:
            report_metrics = final_metrics

        result = {
            "best_epoch": best_epoch,
            "best_val_acc": best_val_acc,
            "best_val_loss": val_losses[best_epoch - 1] if best_epoch > 0 else None,
            "final_val_acc": final_metrics["accuracy"],
            "total_epochs": epoch,
            "early_stopped": early_stopped,
            "train_losses": train_losses,
            "val_losses": val_losses,
            "val_accs": val_accs,
            "per_bucket_accuracy": report_metrics["per_bucket_accuracy"],
            "confusion_summary": report_metrics["confusion_summary"],
            "time_seconds": total_time,
            "strategy": strategy,
            "encoder_model": self.encoder_model_name,
            "total_records": len(self.records),
            "num_subjects": len(self.subject_to_index),
            "num_buckets": len(self.bucket_to_idx),
        }
        self._write_training_log(result)
        return result

    # ── 评估 ──

    def _evaluate(self, loader: DataLoader, amp_on: bool = False) -> Dict:
        """验证集: loss + 准确率 + per-bucket + 混淆 top10。"""
        self.model.eval()
        total_loss, total, correct = 0.0, 0, 0
        bucket_correct = defaultdict(int)
        bucket_total = defaultdict(int)
        confusion: Counter = Counter()

        with torch.no_grad():
            for batch in loader:
                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                switches = batch["subject_switches"].to(self.device)
                labels = batch["label"].to(self.device)

                with torch.amp.autocast(
                    device_type="cuda", dtype=torch.float16, enabled=amp_on,
                ):
                    logits = self.model(input_ids, attention_mask, switches)
                    loss = F.cross_entropy(logits, labels)

                preds = logits.argmax(dim=1)
                total_loss += loss.item() * len(labels)
                total += len(labels)
                correct += (preds == labels).sum().item()

                for p, l in zip(preds.tolist(), labels.tolist()):
                    bucket_correct[l] += int(p == l)
                    bucket_total[l] += 1
                    if p != l:
                        confusion[(l, p)] += 1

        accuracy = correct / max(total, 1)
        per_bucket = {}
        idx_to_bucket = {v: k for k, v in self.bucket_to_idx.items()}
        for b_idx in sorted(bucket_total.keys()):
            bucket_name = idx_to_bucket.get(b_idx, f"b{b_idx}")
            per_bucket[bucket_name] = round(
                bucket_correct[b_idx] / bucket_total[b_idx], 4
            )

        confusion_summary = [
            {
                "真实": idx_to_bucket.get(real, str(real)),
                "预测": idx_to_bucket.get(pred, str(pred)),
                "次数": cnt,
            }
            for (real, pred), cnt in confusion.most_common(10)
        ]

        return {
            "loss": total_loss / max(total, 1),
            "accuracy": accuracy,
            "correct": correct,
            "total_samples": total,
            "per_bucket_accuracy": per_bucket,
            "confusion_summary": confusion_summary,
        }

    # ── 交付物导出 ──

    def _save_best(self, epoch: int, val_acc: float):
        """best epoch 落盘 4 件交付物。

        ① fine_tuned/（微调 BGE，fp16） ② finance_classifier.pt
        ③ subject_to_index.json           ④ index_to_bucket.json
        """
        fine_tuned_dir = Path(NN_FINE_TUNED_DIR)
        if fine_tuned_dir.exists():
            backup = Path(NN_STORAGE_DIR) / "backups" / "fine_tuned_prev"
            if backup.exists():
                shutil.rmtree(backup)
            shutil.copytree(fine_tuned_dir, backup)

        # ① 微调后 BGE（LoRA 模式先合并权重再保存）
        # 注意: nn.Module 没有 detach()，且 .half()/.to() 是原地转换——
        # 必须 deepcopy 副本保存，避免污染正在训练的原模型
        import copy
        encoder = copy.deepcopy(self.model.encoder)
        if hasattr(encoder, "merge_and_unload"):
            encoder = encoder.merge_and_unload()
        encoder = encoder.half().to("cpu")
        encoder.save_pretrained(str(fine_tuned_dir))
        self.tokenizer.save_pretrained(str(fine_tuned_dir))
        print(f"[OK] 交付物① 微调 BGE: {fine_tuned_dir}")

        # ② 分类头（不含 encoder 权重）
        head_state = {
            k: v.to("cpu")
            for k, v in self.model.state_dict().items()
            if not k.startswith("encoder.")
        }
        idx_to_bucket = {v: k for k, v in self.bucket_to_idx.items()}
        checkpoint = {
            "format": "finance_classifier_v1",
            "state_dict": head_state,
            "num_subjects": len(self.subject_to_index),
            "num_buckets": len(self.bucket_to_idx),
            "subject_dim": NN_SUBJECT_DIM,
            "hidden_dim": NN_HIDDEN_DIM,  # 推理加载校验用（旧 checkpoint 无此键）
            "encoder_hidden_size": self.encoder_hidden_size(),
            "encoder_model": self.encoder_model_name,
            "bucket_to_idx": self.bucket_to_idx,
            "best_val_acc": val_acc,
            "best_epoch": epoch,
            "total_records": len(self.records),
            "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        pt_path = self.save_dir / "finance_classifier.pt"
        torch.save(checkpoint, pt_path)
        print(f"[OK] 交付物② 分类头: {pt_path}")

        # ③ ④ 索引（原子写，防中断留下半截 json 导致推理加载失败）
        from .training_data import atomic_write_json
        idx_path = self.save_dir / "subject_to_index.json"
        atomic_write_json(idx_path, self.subject_to_index)
        bucket_path = self.save_dir / "index_to_bucket.json"
        atomic_write_json(bucket_path, idx_to_bucket)
        print(f"[OK] 交付物③④ 索引: {idx_path}, {bucket_path}")

    def encoder_hidden_size(self) -> int:
        return self.model.encoder_hidden

    def _write_training_log(self, result: Dict):
        """写 training_log.json（追加历史）。"""
        log_path = self.save_dir / "training_log.json"
        history = []
        if log_path.exists():
            try:
                history = json.loads(log_path.read_text(encoding="utf-8"))
                if isinstance(history, dict):
                    history = history.get("runs", [])
            except (json.JSONDecodeError, KeyError):
                history = []
        if not isinstance(history, list):
            history = []

        history.append({
            "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "encoder_model": result["encoder_model"],
            "strategy": result["strategy"],
            "best_epoch": result["best_epoch"],
            "best_val_acc": result["best_val_acc"],
            "total_epochs": result["total_epochs"],
            "early_stopped": result["early_stopped"],
            "time_seconds": round(result["time_seconds"], 1),
            "total_records": result["total_records"],
            "num_subjects": result["num_subjects"],
            "num_buckets": result["num_buckets"],
        })
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump({"runs": history}, f, ensure_ascii=False, indent=2)
        print(f"[OK] 训练日志: {log_path}")
