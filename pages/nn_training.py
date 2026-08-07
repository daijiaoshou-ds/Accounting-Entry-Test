# -*- coding: utf-8 -*-
"""
神经网络模型训练页面 — 开发者专用

自动流程:
  V2.1 跑完 → nn/_storage/training/{hash}.json 自动生成（桶聚合格式）
  → 你打开 hash 文件审核关键词 → 标记"已审核"
  → 点「合并已审核数据」→ 生成 training_data.json
  → 训练

运行: streamlit run pages/nn_training.py
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import pandas as pd
import json
from datetime import datetime

st.set_page_config(
    page_title="NN 模型训练", page_icon="🧠",
    layout="wide", initial_sidebar_state="expanded",
)

st.title("🧠 神经网络模型训练")

from summary_cleaner.v2.config import NN_STORAGE_DIR
_NN_DIR = Path(NN_STORAGE_DIR)
_TRAINING_DIR = _NN_DIR / "training"
_MERGED_PATH = _NN_DIR / "training_data.json"

# ============================================================================
# Session State
# ============================================================================
DEFAULTS = {
    "vocab": None, "train_dataset": None, "val_dataset": None,
    "bucket_to_idx": None, "model": None, "trainer": None,
    "training_result": None, "eval_result": None,
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ============================================================================
# 侧边栏
# ============================================================================
with st.sidebar:
    st.header("📁 训练数据")

    files = []
    if _TRAINING_DIR.exists():
        for f in sorted(_TRAINING_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                files.append({
                    "name": f.name,
                    "fp": d.get("fingerprint", "")[:12],
                    "reviewed": d.get("reviewed", False),
                    "buckets": len(d.get("buckets", {})),
                    "kw": sum(len(b.get("keywords", [])) for b in d.get("buckets", {}).values()),
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M"),
                })
            except Exception:
                pass

    if files:
        reviewed = sum(1 for f in files if f["reviewed"])
        st.success(f"{len(files)} 个文件 ({reviewed} 已审核)")
        for f in files:
            icon = "[V]" if f["reviewed"] else "[ ]"
            st.caption(f"{icon} {f['name']} | {f['buckets']}桶 {f['kw']}词 | {f['mtime']}")
    else:
        st.warning("暂无训练数据")

    st.divider()
    st.caption(f"文件位置: `{_TRAINING_DIR}`")

    if _MERGED_PATH.exists():
        st.success("training_data.json 已合并")

    # 已有模型
    models = sorted(_NN_DIR.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if models:
        st.divider()
        st.caption(f"已训练: {len(models)} 个模型")

    for vf in ["pattern_vectors.json", "keyword_vectors.json"]:
        if (_NN_DIR / vf).exists():
            st.caption(f"  + {vf}")

# ============================================================================
# Tabs
# ============================================================================
tab1, tab2, tab3, tab4 = st.tabs([
    "📋 1. 审核 + 合并",
    "🏋️ 2. 训练",
    "📊 3. 评估 + 推理",
    "🔬 4. 向量表",
])

# ── Tab 1: 审核 + 合并 ──
with tab1:
    st.header("审核训练数据 + 合并")

    st.markdown(f"""
    ### 流程

    1. V2.1 每次跑完 → `training/{{hash}}.json` **自动生成**（桶聚合格式）
    2. 你打开文件审核：删坏词、补缺词、确保 `"reviewed": true`
    3. 回到此页面 → 点「合并已审核数据」
    4. Tab 2 训练

    文件位置: `{_TRAINING_DIR}`
    """)

    if not files:
        st.info("还没有训练数据。跑一次 V2.1 分类即可自动生成。")
    else:
        # 显示文件列表
        st.subheader("当前训练文件")

        file_data = []
        for f in files:
            file_data.append({
                "文件名": f["name"],
                "指纹": f["fp"],
                "状态": "已审核" if f["reviewed"] else "待审核",
                "桶数": f["buckets"],
                "关键词数": f["kw"],
                "时间": f["mtime"],
            })
        st.dataframe(pd.DataFrame(file_data), use_container_width=True)

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
                    f"{len(merged['buckets'])} 桶"
                )
                if merged["skipped_unreviewed"]:
                    st.info(f"跳过 {merged['skipped_unreviewed']} 个未审核文件")
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

        # 预览合并数据
        if _MERGED_PATH.exists():
            with open(_MERGED_PATH, "r", encoding="utf-8") as f:
                merged = json.load(f)

            st.subheader("合并数据预览")
            bucket = st.selectbox("选择桶", sorted(merged["buckets"].keys()))
            if bucket:
                bd = merged["buckets"][bucket]
                st.write(f"**{bucket}**: {len(bd['patterns'])} patterns, {len(bd['keywords'])} keywords")
                st.text_area(
                    "Keywords (可直接编辑，保存后需重新合并)",
                    value=",\n".join(bd["keywords"]),
                    height=200, key=f"preview_{bucket}",
                )

# ── Tab 2: 训练 ──
with tab2:
    st.header("训练模型")

    if not _MERGED_PATH.exists():
        st.warning("请先在 Tab 1 合并训练数据")
    else:
        with open(_MERGED_PATH, "r", encoding="utf-8") as f:
            merged = json.load(f)
        total_kw = sum(len(b["keywords"]) for b in merged["buckets"].values())
        total_pat = sum(len(b["patterns"]) for b in merged["buckets"].values())
        st.success(
            f"训练数据: {merged['total_hashes']} 哈希, "
            f"{len(merged['buckets'])} 桶, {total_pat} patterns, {total_kw} keywords"
        )

        # 训练参数
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            dim = st.selectbox("向量维度", [64, 128, 256], index=1)
        with col2:
            epochs = st.number_input("最大轮数", 10, 500, 100, 10)
        with col3:
            batch_size = st.selectbox("批次大小", [16, 32, 64, 128], index=1)
        with col4:
            lr = st.selectbox("学习率", [0.01, 0.005, 0.001, 0.0005, 0.0001], index=2)

        col1, col2 = st.columns(2)
        with col1:
            margin = st.slider("Triplet Margin", 0.1, 1.0, 0.5, 0.05)
        with col2:
            patience = st.number_input("早停耐心", 5, 50, 15, 5)

        if st.button("🚀 开始训练", type="primary", use_container_width=True):
            from summary_cleaner.nn.training_data import load_merged_training_data
            from summary_cleaner.nn.model import VoucherEmbeddingModel, TripletLoss
            from summary_cleaner.nn.trainer import ModelTrainer
            from summary_cleaner.nn.vocab import VocabManager

            with st.spinner("加载数据..."):
                vocab = VocabManager()
                dataset, vocab, bucket_to_idx, meta = load_merged_training_data(
                    str(_MERGED_PATH), vocab=vocab,
                )
                train_ds, val_ds = dataset.split(train_ratio=0.8)
                st.session_state.vocab = vocab
                st.session_state.train_dataset = train_ds
                st.session_state.val_dataset = val_ds
                st.session_state.bucket_to_idx = bucket_to_idx
                st.info(f"{len(train_ds)} 训练 / {len(val_ds)} 验证 | {vocab}")

            with st.spinner("训练中..."):
                model = VoucherEmbeddingModel(
                    num_patterns=vocab.num_patterns,
                    num_keywords=vocab.num_keywords, dim=dim,
                )
                trainer = ModelTrainer(model, vocab, save_dir=str(_NN_DIR))
                trainer.criterion = TripletLoss(margin=margin)

                result = trainer.train(
                    train_dataset=train_ds, val_dataset=val_ds,
                    epochs=epochs, batch_size=batch_size, learning_rate=lr,
                    early_stop_patience=patience,
                )
                st.session_state.model = model
                st.session_state.trainer = trainer
                st.session_state.training_result = result

            st.success("训练完成！")
            st.line_chart(pd.DataFrame({
                "Epoch": range(1, len(result["train_losses"]) + 1),
                "Train": result["train_losses"],
                "Val": result.get("val_losses", []),
            }).set_index("Epoch"))

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("最佳轮次", result["best_epoch"])
            c2.metric("最佳 Val Loss", result["best_val_loss"])
            c3.metric("总轮数", result["total_epochs"])
            c4.metric("耗时", f"{result['time_seconds']:.0f}s")

        elif st.session_state.training_result is not None:
            result = st.session_state.training_result
            st.info(f"上次: epoch={result['best_epoch']}, loss={result['best_val_loss']}, {result['time_seconds']:.0f}s")
            st.line_chart(pd.DataFrame({
                "Epoch": range(1, len(result["train_losses"]) + 1),
                "Train": result["train_losses"],
                "Val": result.get("val_losses", []),
            }).set_index("Epoch"))

# ── Tab 3: 评估 + 推理 ──
with tab3:
    st.header("评估 + 推理测试")

    if st.session_state.trainer is None:
        st.warning("请先在 Tab 2 训练")
    else:
        eval_target = st.radio("数据集", ["验证集", "训练集"], horizontal=True)

        if st.button("📊 评估准确率", type="primary"):
            dataset = (st.session_state.val_dataset if eval_target == "验证集"
                       else st.session_state.train_dataset)
            with st.spinner("..."):
                er = st.session_state.trainer.evaluate_accuracy(dataset)
                st.session_state.eval_result = er
                st.metric("准确率", f"{er['overall_accuracy']:.2%}",
                          delta=f"{er['correct']}/{er['total_samples']}")
                per_bucket = er["per_bucket_accuracy"]
                st.dataframe(pd.DataFrame(per_bucket.items(), columns=["桶", "准确率"]).sort_values("准确率"),
                             use_container_width=True)
                st.bar_chart(pd.DataFrame(per_bucket.items(), columns=["桶", "准确率"]).set_index("桶"))
                if er["confusion_summary"]:
                    st.subheader("Top 混淆")
                    st.dataframe(pd.DataFrame(er["confusion_summary"], columns=["真实", "预测", "次数"]),
                                 use_container_width=True)

        # 导出向量表
        if st.button("📤 导出向量表"):
            from summary_cleaner.nn.training_data import export_vector_tables
            pp, kp = export_vector_tables(st.session_state.model, st.session_state.vocab, str(_NN_DIR))
            st.success(f"已导出: {pp}, {kp}")

        # 推理测试
        st.divider()
        st.subheader("手动推理测试")

        models = sorted(_NN_DIR.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if st.session_state.model is None and models:
            if st.button("📂 加载模型"):
                from summary_cleaner.nn.inference import ModelInference
                inference = ModelInference(str(_NN_DIR))
                st.session_state.model = inference.model
                st.session_state.vocab = inference.vocab
                st.session_state._inference = inference
                st.rerun()

        if st.session_state.model is not None:
            if "_inference" not in st.session_state:
                from summary_cleaner.nn.inference import ModelInference
                inf = ModelInference.__new__(ModelInference)
                inf.model = st.session_state.model
                inf.vocab = st.session_state.vocab
                inf._loaded = True
                inf.device = "cpu"
                if hasattr(st.session_state.model, "_bucket_names_list"):
                    inf._bucket_names = st.session_state.model._bucket_names_list
                elif st.session_state.model.bucket_centroids is not None:
                    inf._bucket_names = [f"b{i}" for i in range(len(st.session_state.model.bucket_centroids))]
                else:
                    inf._bucket_names = []
                st.session_state._inference = inf

            inf = st.session_state._inference
            col1, col2 = st.columns(2)
            with col1:
                p = st.text_input("Patterns", placeholder="制造费用|借, 银行存款|贷")
                k = st.text_input("Keywords", placeholder="办公费, 车间")
                if st.button("🔮 预测") and p:
                    r = inf.predict_single(
                        [x.strip() for x in p.split(",") if x.strip()],
                        [x.strip() for x in k.split(",") if x.strip()],
                    )
                    st.metric("分类", r["bucket"], delta=f"置信度: {r['confidence']:.2%}")
                    for b, s in r["top3"]:
                        st.text(f"  {b}: {s:.4f}")
            with col2:
                kw = st.text_input("查相似词", placeholder="办公费")
                if st.button("🔍") and kw:
                    sim = inf.find_similar_keywords(kw)
                    for w, s in sim:
                        st.text(f"  {w}: {s:.4f}")

# ── Tab 4: 向量表 ──
with tab4:
    st.header("向量表查看器")

    pj = _NN_DIR / "pattern_vectors.json"
    kj = _NN_DIR / "keyword_vectors.json"

    if not pj.exists() or not kj.exists():
        st.warning("还没有导出向量表。在 Tab 3 训练后点「导出向量表」即可生成。")
    else:
        with open(pj, "r", encoding="utf-8") as f:
            pdata = json.load(f)
        with open(kj, "r", encoding="utf-8") as f:
            kdata = json.load(f)

        st.success(f"Pattern: {pdata['count']} 个 × {pdata['dim']} 维 | "
                   f"Keyword: {kdata['count']} 个 × {kdata['dim']} 维")

        view = st.radio("查看", ["Pattern 向量", "Keyword 向量"], horizontal=True)

        if view == "Pattern 向量":
            query = st.text_input("搜索 Pattern（支持部分匹配）", key="search_p")
            pvecs = pdata["vectors"]
            if query:
                pvecs = {k: v for k, v in pvecs.items() if query in k}

            st.caption(f"显示 {len(pvecs)} / {pdata['count']} 个")

            # 表格：名称 + 范数 + 前 8 维
            rows = []
            for name, vec in sorted(pvecs.items(), key=lambda x: -sum(v*v for v in x[1])**0.5):
                norm = round(sum(v*v for v in vec)**0.5, 4)
                rows.append({
                    "Pattern": name[:100],
                    "范数": norm,
                    "D0": vec[0], "D1": vec[1], "D2": vec[2], "D3": vec[3],
                    "D4": vec[4], "D5": vec[5], "D6": vec[6], "D7": vec[7],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=400)

        else:
            query = st.text_input("搜索 Keyword（支持部分匹配）", key="search_k")
            kvecs = kdata["vectors"]
            if query:
                kvecs = {k: v for k, v in kvecs.items() if query in k}

            st.caption(f"显示 {len(kvecs)} / {kdata['count']} 个")

            rows = []
            for name, vec in sorted(kvecs.items(), key=lambda x: -sum(v*v for v in x[1])**0.5):
                norm = round(sum(v*v for v in vec)**0.5, 4)
                rows.append({
                    "Keyword": name,
                    "范数": norm,
                    "D0": vec[0], "D1": vec[1], "D2": vec[2], "D3": vec[3],
                    "D4": vec[4], "D5": vec[5], "D6": vec[6], "D7": vec[7],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=400)

        # 导出 Excel
        if st.button("📥 导出 vectors.xlsx"):
            from summary_cleaner.nn.training_data import export_vector_tables
            if st.session_state.model is not None and st.session_state.vocab is not None:
                export_vector_tables(st.session_state.model, st.session_state.vocab, str(_NN_DIR))
                st.success(f"已导出到 {_NN_DIR / 'vectors.xlsx'}")
            else:
                st.warning("请先加载模型")
