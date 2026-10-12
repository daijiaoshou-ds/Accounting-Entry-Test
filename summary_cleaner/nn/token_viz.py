# -*- coding: utf-8 -*-
"""
分词 + 向量可视化 — 纯逻辑（页面 pages/nn_training.py「Tab 5」调用）

只读已训练好的 fine_tuned/，不参与训练与推理，纯粹把模型内部的两次变换摊开给人看:
  ① tokenizer: 一句摘要 → token 序列 → 每个 token 的 token id
  ② 模型: 每个 token id → 向量
       word_embeddings 查表   → 静态词向量（同一个 id 永远是同一行，与句子无关）
       编码器 24 层之后        → 上下文向量（同一个字在不同摘要里不一样）
       位置 0 的上下文向量     → CLS 句向量（Tab4 相似度检索用的就是它）

token id 的本质: 词表是 21128 行的表，id 就是行号；
词嵌入矩阵是 21128 × 1024，第 id 行就是该 token 的 1024 维静态向量。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import html as _html
import re

import numpy as np
import pandas as pd

# 演示时标注出来的特殊符号（BGE 会额外插入，不是摘要里的字）
SPECIAL_TOKENS = ("[CLS]", "[SEP]", "[PAD]", "[UNK]", "[MASK]")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
CATEGORY_ORDER = ("中文单字", "中文词", "拉丁字母", "数字", "标点/符号",
                  "续接子词", "特殊符号")


# ============================================================================
# 加载（只读交付物①）
# ============================================================================

def load_encoder_for_viz(fine_tuned_dir, device: str = None) -> Tuple[Any, Any, str]:
    """加载分词器 + BGE 编码器（不需要分类头 .pt，单独也能用）。

    Args:
        fine_tuned_dir: 交付物① 目录（nn/_storage/fine_tuned/）
        device: "cuda"/"cpu"，None = 自动

    Returns:
        (encoder, tokenizer, device)

    Raises:
        FileNotFoundError: 模型目录不完整
    """
    import torch

    from .model_loader import is_model_complete, load_encoder_tokenizer

    fine_tuned_dir = Path(fine_tuned_dir)
    if not is_model_complete(fine_tuned_dir):
        raise FileNotFoundError(f"微调模型不完整（交付物①）: {fine_tuned_dir}")

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    encoder, tokenizer = load_encoder_tokenizer(fine_tuned_dir)
    if device == "cpu":
        # fine_tuned 以 fp16 存盘；CPU 没有 fp16 原生算子，单条 forward 也慢一个量级
        encoder = encoder.float()
    encoder.to(device).eval()
    return encoder, tokenizer, device


def describe_tokenizer(tokenizer) -> Dict[str, Any]:
    """词表规模 + 特殊符号 id（页面上做说明用）。"""
    return {
        "分词器": type(tokenizer).__name__,
        "词表大小": int(tokenizer.vocab_size),
        "[CLS]": tokenizer.cls_token_id,
        "[SEP]": tokenizer.sep_token_id,
        "[PAD]": tokenizer.pad_token_id,
        "[UNK]": tokenizer.unk_token_id,
    }


# ============================================================================
# ① 分词: 文本 → token / token id
# ============================================================================

def _tokenize(tokenizer, text: str, max_length: int, pad: bool) -> Dict[str, Any]:
    """统一编码入口（token 明细与向量计算必须用同一套参数，否则位置对不上）。"""
    kwargs = dict(max_length=max_length, truncation=True,
                  padding="max_length" if pad else False)
    try:
        enc = tokenizer(text, return_offsets_mapping=True, **kwargs)
        offsets: Optional[List[Tuple[int, int]]] = list(enc["offset_mapping"])
    except (TypeError, ValueError, NotImplementedError, KeyError):
        # 慢分词器（非 fast）不提供 offset_mapping：退化为不显示原文片段
        enc = tokenizer(text, **kwargs)
        offsets = None
    ids = list(enc["input_ids"])
    return {
        "input_ids": ids,
        "tokens": tokenizer.convert_ids_to_tokens(ids),
        "offsets": offsets,
        "attention_mask": list(enc["attention_mask"]),
    }


def classify_token(token: str, is_special: bool = False) -> str:
    """按 token 形态分类（上色 / 统计用）。

    这个分词器（BERT 中文 WordPiece）中文侧全是单字；`##` 前缀单独归一类
    （词表里每个汉字其实还带一份 `##` 变体，是这份中文词表的历史遗留）。
    "中文词"分支留给将来换成带多字词的词表（如其它 embedding 模型）。
    """
    if is_special:
        return "特殊符号"
    if token.startswith("##"):
        return "续接子词"
    if _CJK_RE.search(token):
        return "中文单字" if len(token) == 1 else "中文词"
    if any(ch.isdigit() for ch in token):
        return "数字"
    if token.isascii() and token.isalpha():
        return "拉丁字母"
    return "标点/符号"


def tokenize_detail(tokenizer, text: str, max_length: int = 64,
                    pad: bool = False) -> List[Dict[str, Any]]:
    """分词明细: 位置 / token / token_id / 对应原文片段 / 类别 / 是否特殊符号。

    Args:
        pad: True = 按推理口径 padding 到 max_length（推理时模型总是看到 64 个位置）
    """
    encoded = _tokenize(tokenizer, text, max_length, pad)
    special_ids = set(getattr(tokenizer, "all_special_ids", []) or [])
    rows: List[Dict[str, Any]] = []
    for i, (tid, tok) in enumerate(zip(encoded["input_ids"], encoded["tokens"])):
        off = encoded["offsets"][i] if encoded["offsets"] is not None else None
        if off is not None and off[1] > off[0]:
            source = text[off[0]:off[1]]
        else:
            source = ""
        is_special = int(tid) in special_ids
        rows.append({
            "位置": i,
            "token": tok,
            "token_id": int(tid),
            "原文片段": source,
            "类别": classify_token(tok, is_special),
            "特殊符号": "✓" if is_special else "",
            "被关注": "✓" if encoded["attention_mask"][i] else "",
        })
    return rows


def token_composition(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """本句的 token 构成（各类别计数，按固定顺序）。"""
    counts = {name: 0 for name in CATEGORY_ORDER}
    for r in rows:
        counts[r["类别"]] = counts.get(r["类别"], 0) + 1
    return {k: v for k, v in counts.items() if v}


def vocab_stats(tokenizer) -> Dict[str, int]:
    """整个词表的构成统计（解释"中文为什么是一字一 token"用）。

    2 万多次正则，只在加载模型后算一次（页面侧缓存，不每轮重算）。
    """
    counts = {name: 0 for name in CATEGORY_ORDER}
    for token in tokenizer.get_vocab():
        special = token.startswith("[") and token.endswith("]")
        counts[classify_token(token, special)] += 1
    return counts


# ============================================================================
# ② 向量: token id → 静态词向量 / 上下文向量 / CLS 句向量
# ============================================================================

def encode_for_model(tokenizer, text: str, max_length: int = 64,
                     pad: bool = False, device: str = "cpu"):
    """编码成模型输入张量（与 tokenize_detail 同一套参数）。"""
    import torch

    encoded = _tokenize(tokenizer, text, max_length, pad)
    input_ids = torch.tensor([encoded["input_ids"]], dtype=torch.long, device=device)
    attention_mask = torch.tensor([encoded["attention_mask"]],
                                  dtype=torch.long, device=device)
    return input_ids, attention_mask


def compute_token_vectors(encoder, input_ids, attention_mask) -> Dict[str, np.ndarray]:
    """把每个 token 在模型内部的向量取出来。

    Returns:
        static:     [T, H] 词嵌入层查表结果（与上下文无关）
        contextual: [T, H] 编码器最后一层输出（分类/检索真正用的表示）
        cls:        [H]    位置 0 的上下文向量 = CLS 句向量
        token_ids:  [T]
        hidden_size: int
        num_layers:  int
    """
    import torch

    with torch.no_grad():
        static = encoder.get_input_embeddings()(input_ids)          # [B,T,H]
        outputs = encoder(input_ids=input_ids, attention_mask=attention_mask,
                          return_dict=True)
        contextual = outputs.last_hidden_state                      # [B,T,H]

    return {
        "static": static[0].float().cpu().numpy(),
        "contextual": contextual[0].float().cpu().numpy(),
        "cls": contextual[0, 0, :].float().cpu().numpy(),
        "token_ids": input_ids[0].cpu().numpy(),
        "hidden_size": int(contextual.shape[-1]),
        "num_layers": int(getattr(encoder.config, "num_hidden_layers", 0)),
    }


def vector_preview(vectors: np.ndarray, labels: List[str],
                   dims: int = 12) -> pd.DataFrame:
    """前 dims 维 + 模长/均值/标准差 的预览表（整表 1024 维没法看）。"""
    dims = max(1, min(int(dims), vectors.shape[1]))
    head = np.round(vectors[:, :dims], 4)
    df = pd.DataFrame(head, columns=[f"d{i}" for i in range(dims)], index=labels)
    df.insert(dims, "‖v‖", np.round(np.linalg.norm(vectors, axis=1), 3))
    df.insert(dims + 1, "均值", np.round(vectors.mean(axis=1), 4))
    df.insert(dims + 2, "标准差", np.round(vectors.std(axis=1), 4))
    return df


def cosine_matrix(vectors: np.ndarray) -> np.ndarray:
    """两两余弦相似度（[N,N]，对角线恒 1）。"""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = vectors / norms
    return unit @ unit.T


def cosine_to(vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """一个向量对一组向量的余弦相似度。"""
    norm = np.linalg.norm(vec)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    return (matrix @ vec) / (norms * (norm if norm else 1.0))


def pairwise_cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """逐行配对余弦相似度（[N,H] vs [N,H] → [N]），用于看同一 token
    的静态词向量与上下文向量差多远。"""
    na = np.linalg.norm(a, axis=1)
    nb = np.linalg.norm(b, axis=1)
    denom = na * nb
    denom[denom == 0] = 1.0
    return np.sum(a * b, axis=1) / denom


# ============================================================================
# token 色块条（纯 HTML）
# ============================================================================

# 按位置轮换的马卡龙配色（bg, 文字色）
_STRIP_PALETTE = (
    ("#E8F0FE", "#1D4ED8"),   # 蓝
    ("#FEF3E2", "#B45309"),   # 琥珀
    ("#F3E8FF", "#7E22CE"),   # 紫
    ("#E6F7EE", "#15803D"),   # 绿
    ("#FDE8EF", "#BE123C"),   # 粉
    ("#E0F5F5", "#0F766E"),   # 青
)
_STRIP_SPECIAL = ("#F1F5F9", "#64748B")   # 特殊符号：灰底虚线


def token_strip_html(rows: List[Dict[str, Any]]) -> str:
    """token 色块条：每个 token 一个胶囊，token 文字 + token_id。

    - 颜色按位置轮换（同一句里相邻 token 不同色，便于数数）
    - 续接子词（##x）沿用上一个 token 的颜色并虚线相连，看得出是同一个词的碎片
    - [CLS]/[SEP]/[PAD] 等灰色虚线，一眼区分"模型自动加的"和"摘要里的字"
    - padding 补齐位半透明；悬停看位置 / 原文片段 / 类别
    """
    chips: List[str] = []
    color_i = 0
    prev_color = _STRIP_PALETTE[0]
    for r in rows:
        token = str(r["token"])
        is_special = bool(r.get("特殊符号"))
        continuation = token.startswith("##")
        if is_special:
            bg, fg = _STRIP_SPECIAL
        elif continuation:
            bg, fg = prev_color          # 同一词的碎片用同一个颜色
        else:
            bg, fg = _STRIP_PALETTE[color_i % len(_STRIP_PALETTE)]
            color_i += 1
        prev_color = (bg, fg)

        attended = bool(r.get("被关注"))
        tip = [f"位置 {r['位置']}", f"类别 {r.get('类别', '')}"]
        if r.get("原文片段"):
            tip.append(f"原文片段「{r['原文片段']}」")
        if is_special:
            tip.append("模型自动加的特殊符号")
        if continuation:
            tip.append("续接上一个 token 的子词")
        if not attended:
            tip.append("padding 补齐位，不参与计算")

        border = ("border:1px dashed #CBD5E1;" if is_special
                  else "border:1px solid transparent;")
        connect = "margin-left:-6px;border-left:1px dotted #94A3B8;" if continuation else ""
        fade = "" if attended else "opacity:.45;"
        chips.append(
            f'<span title="{_html.escape(" · ".join(tip))}" '
            f'style="display:inline-flex;align-items:center;gap:6px;'
            f'padding:5px 11px;border-radius:9px;background:{bg};{border}{connect}{fade}'
            f'white-space:nowrap;font-size:15px;line-height:1.45;">'
            f'<span style="color:{fg};font-weight:600;">{_html.escape(token)}</span>'
            f'<span style="color:#94A3B8;font-size:12px;'
            f'font-family:ui-monospace,Consolas,monospace;">{r["token_id"]}</span>'
            f'</span>'
        )
    return (
        '<div style="display:flex;flex-wrap:wrap;gap:7px;align-items:center;'
        'padding:14px 16px;background:#FCFDFF;border:1px solid #E8EDF3;'
        'border-radius:14px;">' + "".join(chips) + "</div>"
    )


# ============================================================================
# 热力图（纯 HTML）
# ============================================================================
# 部署环境只装 pandas + streamlit（无 matplotlib），pandas Styler
# .background_gradient 依赖 matplotlib 会在运行时 ImportError——这里自己
# 按数值算背景色拼 HTML 表格，零额外依赖。

_WHITE = (255, 255, 255)
_RED = (214, 39, 40)      # 正值（发散色阶）
_BLUE = (31, 119, 180)    # 负值（发散色阶）
_DEEP_BLUE = (8, 48, 107)  # 单色色阶的深端


def _lerp_rgb(c1, c2, t: float) -> str:
    t = max(0.0, min(1.0, t))
    r, g, b = (int(round(a + (b - a) * t)) for a, b in zip(c1, c2))
    return f"rgb({r},{g},{b})"


def _text_color(rgb: str) -> str:
    r, g, b = (int(x) for x in rgb[4:-1].split(","))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#fff" if luminance < 0.6 else "#333"


def heatmap_html(matrix: np.ndarray, row_labels: List[str],
                 col_labels: Optional[List[str]] = None,
                 decimals: int = 3, diverging: bool = True,
                 max_rows: int = 64) -> str:
    """把矩阵画成带数值的热力图 HTML（供 st.markdown(unsafe_allow_html=True)）。

    Args:
        diverging: True = 红白蓝发散色阶（向量数值有正有负，0 居中）；
                   False = 白→深蓝单色色阶（相似度 0~1 场景）
        max_rows: 行数上限（token 太多时只画前若干行，避免页面卡）
    """
    matrix = np.asarray(matrix, dtype=float)
    truncated = matrix.shape[0] > max_rows
    matrix = matrix[:max_rows]
    row_labels = list(row_labels)[:max_rows]
    if col_labels is None:
        col_labels = [f"d{i}" for i in range(matrix.shape[1])]
    col_labels = list(col_labels)[:matrix.shape[1]]

    if diverging:
        scale = np.abs(matrix).max() or 1.0
        def color_of(v):
            return _lerp_rgb(_WHITE, _RED if v >= 0 else _BLUE, abs(v) / scale)
    else:
        lo, hi = matrix.min(), matrix.max()
        span = (hi - lo) or 1.0
        def color_of(v):
            return _lerp_rgb(_WHITE, _DEEP_BLUE, (v - lo) / span)

    head = "".join(
        f'<th style="padding:2px 6px;font-size:11px;color:#555;'
        f'font-weight:600;">{_html.escape(str(c))}</th>'
        for c in col_labels
    )
    body = []
    for label, row in zip(row_labels, matrix):
        cells = []
        for v in row:
            bg = color_of(v)   # 算一次（几千个格子时别算两遍）
            cells.append(
                f'<td style="padding:2px 6px;font-size:11px;text-align:right;'
                f'background:{bg};color:{_text_color(bg)};'
                f'white-space:nowrap;">{v:.{decimals}f}</td>'
            )
        body.append(
            f'<tr><th style="padding:2px 8px;font-size:11px;text-align:right;'
            f'color:#333;font-weight:600;white-space:nowrap;">'
            f'{_html.escape(str(label))}</th>{"".join(cells)}</tr>'
        )
    note = (f'<div style="font-size:11px;color:#888;padding:4px 2px;">'
            f'（只画了前 {max_rows} 行）</div>' if truncated else "")
    return (
        '<div style="overflow-x:auto;max-width:100%;"><table style="'
        'border-collapse:collapse;font-family:ui-monospace,Consolas,monospace;">'
        f'<tr><th></th>{head}</tr>{"".join(body)}</table></div>{note}'
    )
