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
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="NN 模型训练 (V3.0)", page_icon="🧠",
    layout="wide", initial_sidebar_state="expanded",
)

st.title("🧠 神经网络模型训练 (V3.0 · BGE 微调)")

from summary_cleaner.v2.config import NN_STORAGE_DIR, NN_MODEL_CACHE_DIR
_NN_DIR = Path(NN_STORAGE_DIR)
_TRAINING_DIR = _NN_DIR / "training"
_MERGED_PATH = _NN_DIR / "training_data.json"

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

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

    def _list_training_files():
        files = []
        if _TRAINING_DIR.exists():
            for f in sorted(_TRAINING_DIR.glob("*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    records = d.get("records", [])
                    files.append({
                        "name": f.name,
                        "fp": d.get("fingerprint", "")[:12],
                        "reviewed": d.get("reviewed", False),
                        "fmt": d.get("format", "legacy"),
                        "records": len(records),
                        "buckets": len({r.get("bucket", "") for r in records}),
                        "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M"),
                    })
                except Exception:
                    pass
        return files

    files = _list_training_files()
    if files:
        reviewed = sum(1 for f in files if f["reviewed"])
        legacy = sum(1 for f in files if f["fmt"] != "records_v1")
        st.success(f"{len(files)} 个文件 ({reviewed} 已审核)")
        if legacy:
            st.caption(f"⚠ {legacy} 个旧格式（将被合并时跳过）")
        for f in files:
            icon = "[V]" if f["reviewed"] else "[ ]"
            st.caption(f"{icon} {f['name']} | {f['records']}条 {f['buckets']}桶 | {f['mtime']}")
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
    1. V2.1 每次跑完 → `training/{{hash}}.json` **自动生成**（buckets_v2 按桶聚合格式）
    2. 打开文件按桶审核：**一个桶一个桶看，不属于该桶的组合/摘要直接删除**
       （建议用 AI 预审：新开 Claude Code 会话，把 `AI_REVIEW_GUIDE.md` + 数据文件
       一起交给 AI，让它按指南删除错误样本）
    3. 审完确保 `"reviewed": true`
    4. 回到此页面 → 点「合并已审核数据」
    5. Tab 2 训练

    审核原则：只删不改（宁缺毋滥，保留的都是确认正确的样本）。
    文件位置: `{_TRAINING_DIR}`
    """)

    if not files:
        st.info("还没有训练数据。跑一次 V2.1 分类即可自动生成。")
    else:
        st.subheader("当前训练文件")
        file_data = []
        for f in files:
            file_data.append({
                "文件名": f["name"],
                "指纹": f["fp"],
                "状态": "已审核" if f["reviewed"] else "待审核",
                "格式": f["fmt"],
                "记录数": f["records"],
                "桶数": f["buckets"],
                "时间": f["mtime"],
            })
        st.dataframe(pd.DataFrame(file_data), use_container_width=True)

        # 预览单个哈希文件
        selected = st.selectbox(
            "预览文件内容（审核用）",
            [f["name"] for f in files],
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
                    f"已合并 {merged['total_hashes']} 个哈希, "
                    f"{len(merged['records'])} 条记录"
                )
                if merged["skipped_unreviewed"]:
                    st.info(f"跳过 {merged['skipped_unreviewed']} 个未审核文件")
                if merged["skipped_legacy_format"]:
                    st.warning(f"跳过 {merged['skipped_legacy_format']} 个旧格式文件")
                if merged["conflict_stats"]["total_conflicts"]:
                    st.info(f"跨文件冲突 {merged['conflict_stats']['total_conflicts']} 条"
                            f"（众数裁决 {merged['conflict_stats']['resolved_by_majority']} 条）")
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
            "把选中的未审文件与已审数据（training_data.json）比对：同科目组合 + 同桶 + "
            "摘要相似度≥60% → high。**high 记录自动移入 {hash}_approved.json（已审，"
            "AI 不读）**，主文件只留 low 供 AI 审核——避免 high 白占 AI 上下文。"
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
                            threshold=0.6,
                        )
                        st.success(
                            f"拆分完成: high={result['high']} ({result['high_ratio']:.1%}) "
                            f"→ {result['approved_path'] or '无'}，"
                            f"low={result['low']} 留在主文件供 AI 审核"
                        )
                        st.info("现在主文件只有 low 记录，Claude Code 会话只读它即可。"
                                "AI 审完标 reviewed 后合并（approved 文件自动并入）。")
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
                epochs = st.number_input("最大轮数", 5, 100, 20, 5)
            with col2:
                max_length = st.slider("摘要截断 (tokens)", 32, 128, 64, 16)
            with col3:
                encoder_lr = st.selectbox(
                    "编码器学习率", [1e-5, 2e-5, 5e-5], index=1,
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

            if st.button("🚀 开始训练", type="primary", use_container_width=True):
                from summary_cleaner.nn.trainer import FinanceClassifierTrainer
                from summary_cleaner.nn.model_loader import resolve_model_dir

                try:
                    with st.spinner("加载/下载 BGE 模型（首次约 1.3GB）..."):
                        model_dir = resolve_model_dir(model_name)
                        subject_to_index = build_subject_switch_index(merged_records)
                        bucket_to_idx = build_bucket_index(merged_records)
                        train_records, val_records = split_records(merged_records)

                    with st.spinner("初始化训练器..."):
                        trainer = FinanceClassifierTrainer(
                            encoder_model_name=model_name,
                            model_dir=model_dir,
                            records=merged_records,
                            subject_to_index=subject_to_index,
                            bucket_to_idx=bucket_to_idx,
                            save_dir=str(_NN_DIR),
                        )
                        st.session_state.trainer = trainer
                        st.session_state.trainer_records = merged_records
                        st.session_state.trainer_subject_to_index = subject_to_index
                        st.session_state.trainer_bucket_to_idx = bucket_to_idx
                        st.session_state.train_records = train_records
                        st.session_state.val_records = val_records
                        st.info(
                            f"{len(train_records)} 训练 / {len(val_records)} 验证 | "
                            f"{len(subject_to_index)} 科目开关, {len(bucket_to_idx)} 桶"
                        )

                    with st.spinner("训练中（GPU 几秒/轮）..."):
                        result = trainer.train(
                            strategy=strategy,
                            epochs=int(epochs),
                            batch_size=int(batch_size),
                            encoder_lr=float(encoder_lr),
                            head_lr=float(head_lr),
                            max_length=int(max_length),
                            early_stop_patience=int(early_stop),
                            use_amp=use_amp,
                            train_records=train_records,
                            val_records=val_records,
                        )
                        st.session_state.training_result = result

                    st.success("训练完成！")
                except torch.cuda.OutOfMemoryError:
                    st.error("显存不足 (OOM)！请改用 LoRA 或冻结策略、减小 batch 或 max_length 后重试。")
                except Exception as e:
                    st.error(f"训练失败: {e}")

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
