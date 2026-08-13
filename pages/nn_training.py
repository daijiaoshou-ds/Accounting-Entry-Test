# -*- coding: utf-8 -*-
"""
神经网络模型训练页面 — 开发者专用（V3.0 · BGE 中文模型微调）

自动流程:
  V2.1 跑完 → nn/_storage/training/{hash}.json 自动生成（records_v1 格式）
  → 你打开文件审核（改 bucket / 删记录）→ 标记"已审核"
  → 点「合并已审核数据」→ 生成 training_data.json
  → 训练（全量微调 / LoRA / 冻结编码器）

运行: streamlit run pages/nn_training.py
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import threading
import time
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="NN 模型训练 (V3.0)", page_icon="🧠",
    layout="wide", initial_sidebar_state="expanded",
)

st.title("🧠 神经网络模型训练 (V3.0 · BGE 微调)")

from summary_cleaner.v2.config import NN_STORAGE_DIR, NN_MODEL_CACHE_DIR
from summary_cleaner.nn.training_data import list_training_files
_NN_DIR = Path(NN_STORAGE_DIR)
_TRAINING_DIR = _NN_DIR / "training" / "unreviewed"   # 未审数据目录
_MERGED_PATH = _NN_DIR / "training_data.json"          # 金标准（唯一已审数据）

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

# ============================================================================
# 后台训练线程状态（模块级，Streamlit rerun 间保持）
# ============================================================================
_TRAIN_THREAD = None               # 当前训练线程
_TRAIN_STOP = threading.Event()    # 停止信号（训练器每轮开始前检查）
_TRAIN_START_TIME = 0.0            # 训练开始时刻（ETA 估算用）
_TRAIN_LOCK = threading.Lock()     # 防重入锁：任何时刻只允许一个训练线程
_PROGRESS_PATH = _NN_DIR / "training_progress.json"  # 训练进度文件


# ============================================================================
# 训练进度读写（模块级，供训练线程 + 前端 fragment 共用）
# ============================================================================

def _read_training_progress() -> dict:
    if _PROGRESS_PATH.exists():
        try:
            return json.loads(_PROGRESS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_training_progress(data: dict):
    try:
        _PROGRESS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


@st.fragment(run_every=2.0)
def _progress_fragment():
    """训练中实时进度（fragment 自动刷新，只刷新本区块不重跑页面）。

    fragment 必须在模块顶层定义（Streamlit 官方要求，嵌套定义行为不可靠）。
    线程结束时刷新整个页面以显示训练结果。
    """
    progress = _read_training_progress()
    if _TRAIN_THREAD is None or not _TRAIN_THREAD.is_alive():
        st.rerun(scope="app")  # 训练结束: 整页刷新显示结果
        return

    epoch_now = progress.get("epoch", 0) or 0
    total_now = progress.get("total_epochs", "?")
    elapsed = progress.get("elapsed_seconds") or (
        time.time() - _TRAIN_START_TIME
    )
    best_acc = progress.get("best_val_acc")
    losses = progress.get("train_losses", [])
    accs = progress.get("val_accs", [])

    st.subheader(f"训练进行中（第 {epoch_now} / {total_now} 轮）")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("已用时间", f"{elapsed / 60:.1f} 分钟")
    c2.metric("当前轮", f"{epoch_now} / {total_now}")
    c3.metric("最佳轮次", progress.get("best_epoch", 0))
    c4.metric("最佳验证准确率",
              f"{best_acc:.2%}" if best_acc is not None else "—")
    if epoch_now > 0:
        avg_per_epoch = elapsed / epoch_now
        eta = max(0.0, (total_now - epoch_now)) * avg_per_epoch
        st.caption(f"按当前节奏预计还需 ~{eta / 60:.0f} 分钟"
                   f"（早停触发会提前结束）")
    st.caption(progress.get("message", ""))

    if len(losses) >= 1:
        st.line_chart(pd.DataFrame({
            "Epoch": range(1, len(losses) + 1),
            "Train Loss": losses,
            "Val Loss": progress.get("val_losses", []),
        }).set_index("Epoch"))
    if len(accs) >= 1:
        st.line_chart(pd.DataFrame({
            "Epoch": range(1, len(accs) + 1),
            "Val Acc": accs,
        }).set_index("Epoch"))

    if st.button("⏹ 停止训练（保存当前 best 后退出）"):
        _TRAIN_STOP.set()
        st.info("已请求停止：当前轮结束后会保存 best 并退出")

# ============================================================================
# Session State
# ============================================================================
DEFAULTS = {
    "model": None, "tokenizer": None, "trainer": None,
    "trainer_records": None, "trainer_subject_to_index": None,
    "trainer_bucket_to_idx": None,
    "train_records": None, "val_records": None,
    "training_result": None, "eval_result": None, "inference": None,
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ============================================================================
# 侧边栏
# ============================================================================
with st.sidebar:
    st.header("📁 训练数据")

    # 用 training_data.list_training_files（正确支持 buckets_v2 统计）——
    # 旧实现按 records_v1 的 "records" 键解析，所有 buckets_v2 文件
    # 显示 0 条 0 桶且被误报为"旧格式将被跳过"
    files = list_training_files(str(_TRAINING_DIR))
    if files:
        reviewed = sum(1 for f in files if f["reviewed"])
        legacy = sum(1 for f in files if f["format"] != "buckets_v2")
        st.success(f"{len(files)} 个文件 ({reviewed} 已审核)")
        if legacy:
            st.caption(f"⚠ {legacy} 个旧格式（将被合并时跳过）")
        for f in files:
            icon = "[V]" if f["reviewed"] else "[ ]"
            st.caption(f"{icon} {f['filename']} | {f['records']}条 {f['buckets']}桶"
                       f" | {f['size_kb']}KB | {f['created_at'][:16]}")
    else:
        st.warning("暂无训练数据")

    st.divider()
    st.caption(f"文件位置: `{_TRAINING_DIR}`")

    if _MERGED_PATH.exists():
        st.success("training_data.json 已合并")

    # 已有交付物
    has_fine = (_NN_DIR / "fine_tuned").exists()
    has_pt = (_NN_DIR / "finance_classifier.pt").exists()
    if has_fine and has_pt:
        st.caption("✅ 已训练交付物（fine_tuned/ + .pt）")

    if not _HAS_TORCH:
        st.error("未检测到 PyTorch。请先安装: pip install torch --index-url "
                 "https://download.pytorch.org/whl/cu126")

# ============================================================================
# Tabs
# ============================================================================
tab1, tab2, tab3, tab4 = st.tabs([
    "📋 1. 审核 + 合并",
    "🏋️ 2. 训练",
    "📊 3. 评估 + 推理",
    "🔍 4. 摘要相似度",
])

# ── Tab 1: 审核 + 合并 ──
with tab1:
    st.header("审核训练数据 + 合并")

    st.markdown(f"""
    ### 流程
    1. V2.1 每次跑完 → `training/unreviewed/{{hash}}.json` **自动生成**（未审目录）
    2. （有金标准后）点「置信度拆分」→ 与金标准一致的记录**自动并入
       training_data.json**（AI 不读），主文件只剩需要审核的记录
    3. 打开主文件审核：一个桶一个桶看，不属于该桶的组合/摘要直接删除
       （建议用 AI 预审：新开 Claude Code 会话，把 `AI_REVIEW_GUIDE.md` + 数据文件
       一起交给 AI，让它按指南删除错误样本，审完标 `"reviewed": true`）
    4. 回到此页面 → 点「合并已审核数据」→ 并入金标准并删除已消费文件
    5. Tab 2 训练（training_data.json 是唯一金标准，持续扩大）

    审核原则：只删不改 + 拿不准就删（宁缺毋滥，保留的都是确认正确的样本）。
    未审文件位置: `{_TRAINING_DIR}`
    """)

    if not files:
        st.info("还没有训练数据。跑一次 V2.1 分类即可自动生成。")
    else:
        st.subheader("当前训练文件")
        file_data = []
        for f in files:
            file_data.append({
                "文件名": f["filename"],
                "指纹": f["fingerprint"][:12],
                "状态": "已审核" if f["reviewed"] else "待审核",
                "格式": f["format"],
                "记录数": f["records"],
                "桶数": f["buckets"],
                "时间": f["created_at"][:16],
            })
        st.dataframe(pd.DataFrame(file_data), use_container_width=True)

        # 预览单个哈希文件
        selected = st.selectbox(
            "预览文件内容（审核用）",
            [f["filename"] for f in files],
            key="preview_file",
        )
        if selected:
            with open(_TRAINING_DIR / selected, "r", encoding="utf-8") as f:
                hash_data = json.load(f)
            if "buckets" in hash_data:
                st.caption(f"stats: {hash_data.get('stats', {})}  |  "
                           f"reviewed: {hash_data.get('reviewed', False)}")
                # 桶聚合预览: 选桶 → 科目组合 → 摘要列表
                bucket_names = list(hash_data["buckets"].keys())
                pv_bucket = st.selectbox("选桶查看（审核用）", bucket_names,
                                         key=f"pv_bucket_{selected}")
                groups = hash_data["buckets"].get(pv_bucket, [])
                st.caption(f"{pv_bucket}: {len(groups)} 个科目组合")
                for g in groups:
                    with st.expander(
                        f"{', '.join(g['subjects'])}  "
                        f"({sum(r['count'] for r in g['records'])} 条)"
                    ):
                        st.dataframe(
                            pd.DataFrame([
                                {"摘要": r["summary"][:80], "count": r["count"]}
                                for r in g["records"]
                            ]),
                            use_container_width=True, height=250,
                        )
            elif "records" in hash_data:
                st.warning("旧格式文件（records_v1 扁平），合并时可读但建议重跑 V2.1 重新生成。")
            else:
                st.warning("旧格式文件（legacy 桶聚合），合并时将被跳过。可直接删除。")

        # 合并按钮
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔗 合并已审核数据", type="primary", use_container_width=True):
                from summary_cleaner.nn.training_data import merge_training_data
                merged = merge_training_data(
                    str(_TRAINING_DIR), only_reviewed=True,
                    output_path=str(_MERGED_PATH),
                )
                st.success(
                    f"金标准已更新: {merged['stats']['total_records']} 条记录, "
                    f"{merged['stats']['buckets']} 桶, {merged['total_hashes']} 份来源"
                )
                if merged["skipped_unreviewed"]:
                    st.info(f"跳过 {merged['skipped_unreviewed']} 个未审核文件")
                if merged["skipped_consumed"]:
                    st.info(f"{merged['skipped_consumed']} 个同指纹重导出文件"
                            f"已移入 consumed/（不再重复并入，保留副本供比对）")
                if merged["skipped_legacy_format"]:
                    st.warning(f"跳过 {merged['skipped_legacy_format']} 个旧格式文件")
                st.rerun()

        with col2:
            if st.button("🔗 合并全部（含未审核）", use_container_width=True):
                from summary_cleaner.nn.training_data import merge_training_data
                merged = merge_training_data(
                    str(_TRAINING_DIR), only_reviewed=False,
                    output_path=str(_MERGED_PATH),
                )
                st.success(f"已合并全部 {merged['total_hashes']} 个哈希")
                st.rerun()

        # 置信度拆分（主动学习）: 未审数据 vs 已审金标准
        st.divider()
        st.subheader("置信度拆分（AI 审核前先跑）")
        st.caption(
            "把选中的未审文件与金标准（training_data.json）比对：同科目组合 + 同桶 + "
            "摘要相似度≥75% → high。**high 记录直接并入金标准（AI 完全不读，不占上下文）**，"
            "主文件只留 low 供 AI 审核。"
            "跑之前请先合并过至少一份已审数据（第一份数据没有金标准，需要全审）。"
        )
        if _MERGED_PATH.exists():
            if st.button("🔍 置信度拆分（当前选中的文件）",
                         type="secondary", use_container_width=True):
                if selected:
                    from summary_cleaner.nn.training_data import compute_review_confidence
                    try:
                        result = compute_review_confidence(
                            str(_TRAINING_DIR / selected),
                            threshold=0.75,
                        )
                        st.success(
                            f"拆分完成: high={result['high']} ({result['high_ratio']:.1%}) "
                            f"已直接并入金标准（AI 不读），low={result['low']} 留在主文件"
                        )
                        st.info("主文件现在只剩 low 记录，Claude Code 会话只读它即可。"
                                "AI 审完标 reviewed 后点「合并已审核数据」并入金标准。")
                        st.rerun()
                    except Exception as e:
                        st.error(f"标注失败: {e}")
        else:
            st.info("还没有金标准数据（training_data.json）。先审核并合并一份数据，"
                    "之后跑新数据就能用置信度拆分了。")

        # 合并数据预览（buckets_v2 聚合）
        if _MERGED_PATH.exists():
            with open(_MERGED_PATH, "r", encoding="utf-8") as f:
                merged = json.load(f)
            st.subheader("合并数据预览")
            buckets_data = merged.get("buckets", {})
            total_recs = sum(
                len(r.get("records", []))
                for groups in buckets_data.values()
                for r in groups
            )
            st.caption(
                f"{total_recs} 条记录 | "
                f"{len(buckets_data)} 桶 | "
                f"来源 {merged.get('total_hashes', 0)} 哈希"
            )
            bucket = st.selectbox(
                "选择桶", list(buckets_data.keys()),
                key="preview_bucket",
            )
            groups = buckets_data.get(bucket, [])
            st.caption(f"{bucket}: {len(groups)} 个科目组合")
            for g in groups[:10]:
                with st.expander(
                    f"{', '.join(g['subjects'])}  "
                    f"({sum(r['count'] for r in g['records'])} 条)"
                ):
                    st.dataframe(
                        pd.DataFrame([
                            {"摘要": r["summary"][:80], "count": r["count"]}
                            for r in g["records"][:50]
                        ]),
                        use_container_width=True, height=200,
                    )

# ── Tab 2: 训练 ──
with tab2:
    st.header("训练模型 (BGE 微调)")

    if not _MERGED_PATH.exists():
        st.warning("请先在 Tab 1 合并训练数据")
    else:
        from summary_cleaner.nn.training_data import (
            load_merged_records, build_subject_switch_index,
            build_bucket_index, split_records,
        )
        merged_records = load_merged_records(str(_MERGED_PATH))
        if not merged_records:
            st.warning("合并数据为空（旧格式或没有已审核文件）")
        else:
            total_buckets = len({r["bucket"] for r in merged_records})
            st.success(
                f"训练数据: {len(merged_records)} 条记录, {total_buckets} 桶"
            )

            # ── 训练参数 ──
            col1, col2, col3 = st.columns(3)
            with col1:
                from summary_cleaner.nn.model_loader import MODEL_CHOICES
                model_name = st.selectbox(
                    "编码器模型",
                    list(MODEL_CHOICES.keys()),
                    format_func=lambda m: (
                        f"{m} ({MODEL_CHOICES[m]['hidden_size']} 维, "
                        f"{MODEL_CHOICES[m]['params']} 参数)"
                    ),
                )
            with col2:
                strategy = st.selectbox(
                    "微调策略",
                    ["full", "lora", "frozen"],
                    format_func=lambda s: {
                        "full": "全量微调（效果最好，8GB 显存需 batch≤4）",
                        "lora": "LoRA 适配器（省显存，质量接近）",
                        "frozen": "冻结编码器只训分类头（最快最省）",
                    }[s],
                )
            with col3:
                batch_size = st.selectbox("批次大小", [2, 4, 8, 16], index=1)

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                epochs = st.number_input("最大轮数", 3, 100, 8, 1)
            with col2:
                max_length = st.slider("摘要截断 (tokens)", 32, 128, 64, 16)
            with col3:
                encoder_lr = st.selectbox(
                    "编码器学习率", [1e-5, 2e-5, 5e-5], index=0,
                )
            with col4:
                head_lr = st.selectbox(
                    "分类头学习率", [1e-4, 5e-4, 1e-3, 5e-3], index=2,
                )

            col1, col2 = st.columns(2)
            with col1:
                early_stop = st.number_input("早停耐心", 3, 20, 5, 1)
            with col2:
                use_amp = st.checkbox("混合精度 (fp16)", value=True)

            # ── 显存自检 ──
            if _HAS_TORCH:
                if torch.cuda.is_available():
                    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
                    gpu_name = torch.cuda.get_device_name(0)
                    st.info(
                        f"GPU: {gpu_name} ({total:.1f}GB 显存)。"
                        + ("模型已较大：建议 batch≤4 + max_length=64，OOM 时改 LoRA/冻结。"
                           if "large" in model_name and strategy == "full" and total < 12
                           else "显存充足。")
                    )
                else:
                    st.error("未检测到 CUDA GPU。CPU 可跑 frozen 策略做冒烟验证，"
                             "全量/LoRA 微调不现实（建议接 GPU 后训练）。")

            # ── 训练控制（后台线程 + 实时进度，不再阻塞页面）──
            # 清理残留进度文件：进程重启后线程不存在，running/loading
            # 状态的进度文件是上次训练被杀的遗留，不清掉会触发无限整页刷新
            if _TRAIN_THREAD is None and _PROGRESS_PATH.exists():
                stale = _read_training_progress()
                if stale.get("status") in ("loading", "running"):
                    _PROGRESS_PATH.unlink()
                    print("[INF] 清理上次中断训练残留的进度文件")

            def _train_worker(model_name, strategy, batch_size, epochs, max_length,
                              encoder_lr, head_lr, early_stop, use_amp, records):
                """后台训练线程: 每轮进度写 training_progress.json。"""
                try:
                    _write_training_progress({
                        "status": "loading",
                        "message": "加载/下载 BGE 模型（首次约 1.3GB）...",
                    })
                    from summary_cleaner.nn.trainer import FinanceClassifierTrainer
                    from summary_cleaner.nn.model_loader import resolve_model_dir

                    # 显存自检: 按模型+策略估算需求（BGE-Large 全量需 ~6.5GB，
                    # 若 Tab3 推理模型还占着 GPU，8GB 显卡必然 OOM）
                    if torch.cuda.is_available():
                        free_mem, _ = torch.cuda.mem_get_info()
                        req_gb = {
                            "large": {"full": 5.5, "lora": 3.5, "frozen": 3.0},
                            "base": {"full": 3.5, "lora": 2.5, "frozen": 2.0},
                        }
                        model_key = "large" if "large" in model_name else "base"
                        need_gb = req_gb[model_key].get(strategy, 5.5)
                        if free_mem < need_gb * 1024**3:
                            raise RuntimeError(
                                f"可用显存仅 {free_mem / 1024**3:.1f}GB，"
                                f"当前配置（{model_name} + {strategy}）约需 "
                                f"{need_gb}GB。可能是 Tab3 的推理模型仍占用 GPU，"
                                f"请重启 Streamlit 后重试"
                            )

                    model_dir = resolve_model_dir(model_name)
                    subject_to_index = build_subject_switch_index(records)
                    bucket_to_idx = build_bucket_index(records)
                    train_records, val_records = split_records(records)
                    _write_training_progress({
                        "status": "running",
                        "message": f"初始化训练器...（{len(train_records)} 训练 / "
                                   f"{len(val_records)} 验证, "
                                   f"{len(subject_to_index)} 科目开关）",
                        "epoch": 0, "total_epochs": epochs,
                        "train_losses": [], "val_losses": [], "val_accs": [],
                        "best_epoch": 0, "best_val_acc": None,
                    })
                    trainer = FinanceClassifierTrainer(
                        encoder_model_name=model_name,
                        model_dir=model_dir,
                        records=records,
                        subject_to_index=subject_to_index,
                        bucket_to_idx=bucket_to_idx,
                        save_dir=str(_NN_DIR),
                    )
                    result = trainer.train(
                        strategy=strategy, epochs=epochs, batch_size=batch_size,
                        encoder_lr=encoder_lr, head_lr=head_lr,
                        max_length=max_length, early_stop_patience=early_stop,
                        use_amp=use_amp,
                        train_records=train_records, val_records=val_records,
                        progress_callback=_progress_cb,
                        stop_flag=lambda: _TRAIN_STOP.is_set(),
                    )
                    # 验证集样本去重后写入进度文件（展开样本是同一记录的
                    # 副本，按 (摘要,科目,桶) 去重回到记录级）——
                    # Tab3 批量评估从进度文件读取，线程内不碰 session_state
                    seen = set()
                    val_unique = []
                    for r in val_records:
                        key = (r.get("summary", ""), tuple(r.get("subjects", [])),
                               r.get("bucket", ""))
                        if key not in seen:
                            seen.add(key)
                            val_unique.append({
                                "summary": r.get("summary", ""),
                                "subjects": r.get("subjects", []),
                                "bucket": r.get("bucket", ""),
                            })
                    data = _read_training_progress()
                    data.update(status="finished", message="训练完成",
                                elapsed_seconds=time.time() - _TRAIN_START_TIME,
                                result=result, val_records=val_unique)
                    _write_training_progress(data)
                except Exception as e:
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()  # OOM 后释放显存缓存
                    data = _read_training_progress()
                    if "out of memory" in str(e).lower():
                        msg = ("显存不足 (OOM)！请改用 LoRA 或冻结策略、"
                               "减小 batch 或 max_length 后重试；"
                               "若 Tab3 推理模型仍占用 GPU，请重启 Streamlit")
                    else:
                        msg = f"训练失败: {e}"
                    data.update(status="error", message=msg)
                    _write_training_progress(data)
                    print(f"[ERROR] 训练线程: {e}")
                finally:
                    # 训练结束：清空 CUDA 缓存池，把显存还给推理融合用
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        print("[INF] 已释放训练显存缓存（empty_cache）")

            def _progress_cb(info: dict):
                """训练器每轮回调: 把指标追加进进度文件（前端轮询显示）。"""
                data = _read_training_progress()
                data["train_losses"] = data.get("train_losses", []) + [info["train_loss"]]
                data["val_losses"] = data.get("val_losses", []) + [info["val_loss"]]
                data["val_accs"] = data.get("val_accs", []) + [info["val_acc"]]
                data.update(
                    status="running", epoch=info["epoch"],
                    total_epochs=info["total_epochs"],
                    best_epoch=info["best_epoch"],
                    best_val_acc=info["best_val_acc"],
                    elapsed_seconds=time.time() - _TRAIN_START_TIME,
                )
                _write_training_progress(data)

            training_active = (
                _TRAIN_THREAD is not None and _TRAIN_THREAD.is_alive()
            )

            if st.button("🚀 开始训练", type="primary", use_container_width=True,
                         disabled=training_active):
                # 防重入：任何时刻只允许一个训练线程（多标签页/连点防护）
                with _TRAIN_LOCK:
                    if (_TRAIN_THREAD is not None and _TRAIN_THREAD.is_alive()):
                        st.warning("已有训练线程在运行，忽略本次点击")
                    else:
                        _TRAIN_STOP.clear()
                        _TRAIN_START_TIME = time.time()
                        if _PROGRESS_PATH.exists():
                            _PROGRESS_PATH.unlink()
                        _TRAIN_THREAD = threading.Thread(
                            target=_train_worker,
                            args=(model_name, strategy, int(batch_size),
                                  int(epochs), int(max_length),
                                  float(encoder_lr), float(head_lr),
                                  int(early_stop), bool(use_amp), merged_records),
                            daemon=True,
                        )
                        _TRAIN_THREAD.start()
                        st.rerun()  # 立即进入"训练进行中"分支

            progress = _read_training_progress()

            if training_active or progress.get("status") in ("loading", "running"):
                _progress_fragment()
            else:
                # ── 训练结束 / 未训练: 显示结果 ──
                if progress.get("status") == "finished" and progress.get("result"):
                    st.session_state.training_result = progress["result"]
                    st.session_state.val_records = progress.get("val_records") or []
                if progress.get("status") == "error":
                    st.error(progress.get("message", "训练失败"))

                if st.session_state.training_result is not None:
                    result = st.session_state.training_result
                    st.subheader("训练结果")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("最佳轮次", result["best_epoch"])
                    c2.metric("最佳验证准确率", f"{result['best_val_acc']:.2%}")
                    c3.metric("总轮数", result["total_epochs"])
                    c4.metric("耗时", f"{result['time_seconds']:.0f}s")

                    # loss 曲线
                    chart = pd.DataFrame({
                        "Epoch": range(1, len(result["train_losses"]) + 1),
                        "Train Loss": result["train_losses"],
                        "Val Loss": result["val_losses"],
                    }).set_index("Epoch")
                    st.line_chart(chart)

                    # 准确率曲线
                    st.line_chart(pd.DataFrame({
                        "Epoch": range(1, len(result["val_accs"]) + 1),
                        "Val Acc": result["val_accs"],
                    }).set_index("Epoch"))

                    col1, col2 = st.columns(2)
                    with col1:
                        st.subheader("分桶准确率")
                        st.dataframe(
                            pd.DataFrame(
                                result["per_bucket_accuracy"].items(),
                                columns=["桶", "准确率"],
                            ).sort_values("准确率", ascending=False),
                            use_container_width=True,
                        )
                    with col2:
                        if result["confusion_summary"]:
                            st.subheader("Top 混淆")
                            st.dataframe(
                                pd.DataFrame(result["confusion_summary"]),
                                use_container_width=True,
                            )

                    st.subheader("交付物")
                    st.code("\n".join([
                        f"① {_NN_DIR / 'fine_tuned/'}（微调 BGE）",
                        f"② {_NN_DIR / 'finance_classifier.pt'}（分类头）",
                        f"③ {_NN_DIR / 'subject_to_index.json'}（科目开关索引）",
                        f"④ {_NN_DIR / 'index_to_bucket.json'}（桶索引）",
                    ]))

# ── Tab 3: 评估 + 推理 ──
with tab3:
    st.header("评估 + 推理测试")

    if not (_NN_DIR / "finance_classifier.pt").exists():
        st.warning("还没有训练好的模型（交付物②缺失）。请先在 Tab 2 训练。")
    else:
        if st.button("📂 加载已训练模型", type="primary"):
            try:
                from summary_cleaner.nn.inference import FinanceClassifierInference
                inf = FinanceClassifierInference(str(_NN_DIR))
                st.session_state.inference = inf
                st.success(
                    f"模型加载完成: {inf.model_info.get('encoder_model', '')} "
                    f"(最佳验证准确率 {inf.model_info.get('best_val_acc', '-'):.2%})"
                    if inf.model_info.get("best_val_acc") is not None
                    else "模型加载完成"
                )
            except Exception as e:
                st.error(f"加载失败: {e}")

        inf = st.session_state.inference
        if inf is not None:
            st.subheader("手动推理测试")
            col1, col2 = st.columns(2)
            with col1:
                summary = st.text_input("摘要（整句）", placeholder="付杭州分公司货款")
                subjects_input = st.text_input(
                    "科目开关（逗号分隔）",
                    placeholder="应付账款[借], 银行存款[贷]",
                )
                if st.button("🔮 预测") and summary:
                    subjects = [s.strip() for s in subjects_input.split(",") if s.strip()]
                    r = inf.predict(summary, subjects)
                    st.metric("分类", r["bucket"], delta=f"概率: {r['probability']:.2%}")
                    for b, p in r["top3"]:
                        st.progress(p, text=f"{b}: {p:.2%}")
                    if r["unknown_subjects"]:
                        st.warning(f"未知科目开关: {', '.join(r['unknown_subjects'])}"
                                   "（建议补充训练数据后重训）")
            with col2:
                st.caption("科目开关格式: 一级科目[方向]")
                st.caption("例如: 应付账款[借] 表示应付账款借方")
                st.caption("预测时模型会同时考虑摘要语义和科目组合，"
                           "同摘要不同科目会得到不同结果。")

            # 批量评估：验证集（如果有 session 内记录）
            if st.session_state.val_records is not None:
                st.divider()
                st.subheader("批量评估（当前训练验证集）")
                if st.button("📊 评估验证集"):
                    with st.spinner("评估中..."):
                        items = [
                            {"summary": r["summary"], "subjects": r["subjects"],
                             "expected": r["bucket"]}
                            for r in st.session_state.val_records
                        ]
                        results = inf.predict_batch(items)
                        correct = sum(
                            1 for r in results if r["bucket"] == r["expected"]
                        )
                        total = len(results)
                        st.metric("验证集准确率", f"{correct/total:.2%}" if total else "-",
                                  delta=f"{correct}/{total}")
                        st.dataframe(
                            pd.DataFrame([
                                {"摘要": r["summary"][:50], "预测": r["bucket"],
                                 "期望": r["expected"],
                                 "概率": f"{r['probability']:.2%}",
                                 "正确": "✓" if r["bucket"] == r["expected"] else "✗"}
                                for r in results
                            ]).head(50),
                            use_container_width=True,
                        )

# ── Tab 4: 摘要相似度 ──
with tab4:
    st.header("摘要相似度检索")

    if not (_NN_DIR / "fine_tuned").exists():
        st.warning("还没有训练好的模型（交付物①缺失）。请先在 Tab 2 训练。")
    else:
        inf = st.session_state.inference
        if inf is None and _HAS_TORCH:
            if st.button("📂 加载模型"):
                from summary_cleaner.nn.inference import FinanceClassifierInference
                try:
                    st.session_state.inference = FinanceClassifierInference(str(_NN_DIR))
                    st.rerun()
                except Exception as e:
                    st.error(f"加载失败: {e}")
            inf = st.session_state.inference

        if inf is not None:
            st.markdown("""
            输入一段摘要，找出训练数据中最相似的样本（按 CLS 向量余弦相似度）。
            用于验证 V3.0 的核心能力：**同科目组合下，不同公司名的相似摘要
            （如"付杭州分公司货款" vs "付北京分公司货款"）应检索到同类样本**。
            """)

            query = st.text_input("查询摘要", placeholder="付杭州分公司货款", key="sim_query")
            if st.button("🔍 检索 Top10") and query:
                q_vec = inf.embed_summary(query)
                merged_records = load_merged_records(str(_MERGED_PATH))
                if not merged_records:
                    st.warning("合并数据为空，无法检索")
                else:
                    from summary_cleaner.nn.inference import FinanceClassifierInference
                    import numpy as np
                    vectors = [inf.embed_summary(r["summary"]) for r in merged_records]
                    sims = []
                    for i, v in enumerate(vectors):
                        sim = float(np.dot(q_vec, v) / (
                            np.linalg.norm(q_vec) * np.linalg.norm(v) + 1e-8
                        ))
                        sims.append((i, sim))
                    sims.sort(key=lambda x: -x[1])
                    rows = []
                    for i, sim in sims[:10]:
                        r = merged_records[i]
                        rows.append({
                            "相似度": f"{sim:.4f}",
                            "摘要": r["summary"][:60],
                            "桶": r["bucket"],
                            "count": r["count"],
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
