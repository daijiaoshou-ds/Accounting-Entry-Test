# -*- coding: utf-8 -*-
"""训练 v10（22 桶）。

与页面 `pages/nn_training.py` 的调用完全等价，只是走命令行。

用法：
    # 冒烟：300 条 / frozen / 1 epoch，输出到 _storage_smoke（不碰正式交付物）
    python summary_cleaner/nn/train_v10.py --smoke

    # 正式训练
    python summary_cleaner/nn/train_v10.py

    # 自定义
    python summary_cleaner/nn/train_v10.py --epochs 8 --batch-size 4 --strategy full
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_THIS = Path(__file__).resolve()
ROOT = _THIS.parents[2]
sys.path.insert(0, str(ROOT))

from summary_cleaner.v2.config import (  # noqa: E402
    NN_MODEL_CHOICES,
    NN_STORAGE_DIR,
    NN_DEFAULT_EPOCHS_V3,
    NN_DEFAULT_BATCH_SIZE_V3,
    NN_DEFAULT_MAX_LENGTH,
    NN_ENCODER_LR,
    NN_HEAD_LR,
    NN_EARLY_STOP_PATIENCE_V3,
)
from summary_cleaner.nn.model_loader import resolve_model_dir  # noqa: E402
from summary_cleaner.nn.training_data import (  # noqa: E402
    build_bucket_index,
    build_subject_switch_index,
    load_merged_records,
    split_records,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="冒烟：300 条/frozen/1 epoch")
    ap.add_argument("--epochs", type=int, default=NN_DEFAULT_EPOCHS_V3)
    ap.add_argument("--batch-size", type=int, default=NN_DEFAULT_BATCH_SIZE_V3)
    ap.add_argument("--strategy", default="full", choices=["full", "lora", "frozen"])
    ap.add_argument("--max-length", type=int, default=NN_DEFAULT_MAX_LENGTH)
    ap.add_argument("--encoder-lr", type=float, default=NN_ENCODER_LR)
    ap.add_argument("--head-lr", type=float, default=NN_HEAD_LR)
    ap.add_argument("--patience", type=int, default=NN_EARLY_STOP_PATIENCE_V3)
    ap.add_argument("--model", default=NN_MODEL_CHOICES[0])
    ap.add_argument("--save-dir", default=None)
    ap.add_argument("--sample", type=int, default=0, help="只用前 N 条（调试）")
    ap.add_argument("--summary-dropout", type=float, default=0.0,
                    help="训练时按此概率把摘要置空，逼模型只靠科目组合（堵『背摘要』捷径）")
    args = ap.parse_args()

    if args.smoke:
        args.strategy = "frozen"
        args.epochs = 1
        args.sample = args.sample or 300
        args.save_dir = args.save_dir or str(Path(NN_STORAGE_DIR).parent / "_storage_smoke")

    t0 = time.time()
    records = load_merged_records()
    if not records:
        raise SystemExit("训练数据为空，检查 _storage/training_data.json")

    if args.sample:
        # 按桶分层抽样，避免只取到单一桶
        by_b = {}
        for r in records:
            by_b.setdefault(r["bucket"], []).append(r)
        per = max(1, args.sample // max(len(by_b), 1))
        records = [r for rs in by_b.values() for r in rs[:per]]
        print(f"[冒烟] 分层抽样 {len(records)} 条，覆盖 {len(by_b)} 个桶")

    subject_to_index = build_subject_switch_index(records)
    bucket_to_idx = build_bucket_index(records)
    train_records, val_records = split_records(records)

    print(f"记录 {len(records):,} | 桶 {len(bucket_to_idx)} | "
          f"科目开关 {len(subject_to_index)} | 训练 {len(train_records):,} / 验证 {len(val_records):,}")

    model_dir = resolve_model_dir(args.model)
    print(f"编码器 {args.model} → {model_dir}")

    from summary_cleaner.nn.trainer import FinanceClassifierTrainer

    save_dir = args.save_dir or str(NN_STORAGE_DIR)
    trainer = FinanceClassifierTrainer(
        encoder_model_name=args.model,
        model_dir=model_dir,
        records=records,
        subject_to_index=subject_to_index,
        bucket_to_idx=bucket_to_idx,
        save_dir=save_dir,
    )

    def on_progress(p):
        print(f"  [Epoch {p['epoch']}/{p['total_epochs']}] "
              f"train_loss={p.get('train_loss'):.4f} "
              f"val_loss={p.get('val_loss'):.4f} "
              f"val_acc={p.get('val_acc'):.4f} "
              f"best={p.get('best_val_acc')}")

    result = trainer.train(
        strategy=args.strategy,
        epochs=args.epochs,
        batch_size=args.batch_size,
        encoder_lr=args.encoder_lr,
        head_lr=args.head_lr,
        max_length=args.max_length,
        early_stop_patience=args.patience,
        train_records=train_records,
        val_records=val_records,
        summary_dropout=args.summary_dropout,
        progress_callback=on_progress,
    )

    print(f"\n完成，用时 {(time.time()-t0)/60:.1f} 分钟")
    print(f"best_val_acc={result.get('best_val_acc')} best_epoch={result.get('best_epoch')}")
    print(f"交付物目录：{save_dir}")

    report = Path(save_dir) / "v10_train_result.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"结果明细：{report}")

    per_bucket = result.get("per_bucket") or result.get("val_metrics", {}).get("per_bucket")
    if per_bucket:
        print("\n分桶准确率：")
        for k, v in sorted(per_bucket.items(), key=lambda x: str(x[1])):
            print(f"  {k:<24}{v}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
