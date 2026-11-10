# -*- coding: utf-8 -*-
"""摘要编码缓存：同一句摘要只过一遍 encoder。

## 为什么能缓存

模型是两段式：

    摘要 ──encoder（24 层）──→ CLS 向量（1024 维）──┐
                                                   ├─ concat ─→ 分类头 ─→ 桶
    科目 ──查表──→ 187 维 0/1 ─→ Linear ─→ 64 维 ────┘

**encoder 只读摘要，科目根本不进 encoder**。所以同一句摘要无论配什么科目组合，
encoder 输出逐位相同。缓存的就是这个中间结果，不是最终结论：

    同一个摘要 + 不同科目  →  encoder 跑一次，分类头跑多次
                          →  同一本账内 140 组（2.5%）
    跨次运行重复出现       →  第 2 个月起命中 45–60%

注意它与 dedup 的分工：dedup 在同一份账内把"业务等价"的凭证并成一组、复用**结论**；
这里跨账/跨次复用**读题结果**，两者正交，可叠加。

## 缓存键为什么用归一化摘要

`_normalize_summary` 抹掉数字、单号、标点（"支付1月税费1000元" → "支付月税费"）。
业务等价的两句话归一化后相同，共享同一条 CLS 向量即可——实测摘要去掉数字后
模型判定 99.34% 不变，代价可接受。若用原始摘要做键，跨月命中率只有个位数。

## 失效条件

CLS 向量是权重的产物，**换模型必须整批作废**。因此表里带 `model` 列
（`finance_classifier.pt` 的 md5 前 12 位），查询与模型指纹不匹配时视为未命中。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# CLS 向量按 fp32 存（1024 × 4B = 4KB/条）。
# fp16 存过一轮：2,216 条里有 1 条 top1 翻转、概率最大差 0.16——边界样本上
# 半个精度位就能翻盘，不值得。全量 5,500 条 fp32 约 22MB，这点空间换"结果可复现"。
DTYPE = np.float32


def model_fingerprint(pt_path: Path) -> str:
    """分类头 .pt 的 md5 前 12 位——权重变了指纹就变，缓存自动失效。"""
    h = hashlib.md5()
    with open(pt_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def summary_key(normalized: str) -> bytes:
    """归一化摘要 → 16 字节键。"""
    return hashlib.sha1(normalized.encode("utf-8")).digest()[:16]


class EmbedCache:
    """SQLite 单文件缓存（线程安全：每线程一个连接）。"""

    def __init__(self, path: Path, model: str, enabled: bool = True):
        self.path = Path(path)
        self.model = model
        self.enabled = enabled
        self._local = threading.local()
        self.stats = {"hit": 0, "miss": 0, "written": 0}
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn().execute(
            """CREATE TABLE IF NOT EXISTS emb (
                   model TEXT NOT NULL,
                   key   BLOB NOT NULL,
                   dim   INTEGER NOT NULL,
                   vec   BLOB NOT NULL,
                   hits  INTEGER NOT NULL DEFAULT 0,
                   PRIMARY KEY (model, key)
               )"""
        )
        self._conn().commit()

    # ── 连接 ──────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    # ── 读写 ──────────────────────────────────────────────────────────

    def get_many(self, keys: Sequence[bytes]) -> List[Optional[np.ndarray]]:
        """批量查询，返回与 keys 等长的列表，未命中为 None。"""
        if not self.enabled or not keys:
            return [None] * len(keys)
        out: List[Optional[np.ndarray]] = []
        conn = self._conn()
        # SQLite 单次 IN 的变量上限是 999，按 800 分批
        for start in range(0, len(keys), 800):
            part = keys[start:start + 800]
            ph = ",".join("?" * len(part))
            rows = conn.execute(
                f"SELECT key, vec FROM emb WHERE model=? AND key IN ({ph})",
                [self.model, *part],
            ).fetchall()
            found = {bytes(k): v for k, v in rows}
            for k in part:
                blob = found.get(k)
                if blob is None:
                    out.append(None)
                    self.stats["miss"] += 1
                else:
                    out.append(np.frombuffer(blob, dtype=DTYPE).copy())
                    self.stats["hit"] += 1
        return out

    def put_many(self, pairs: Iterable[Tuple[bytes, np.ndarray]]) -> int:
        """写入 (key, 向量) 对，已存在则忽略（首条为准，保证可复现）。"""
        if not self.enabled:
            return 0
        rows = []
        for key, vec in pairs:
            v = np.asarray(vec, dtype=DTYPE)
            rows.append((self.model, sqlite3.Binary(key), int(v.size),
                         sqlite3.Binary(v.tobytes())))
        if not rows:
            return 0
        self._conn().executemany(
            "INSERT OR IGNORE INTO emb (model, key, dim, vec) VALUES (?,?,?,?)", rows)
        self._conn().commit()
        self.stats["written"] += len(rows)
        return len(rows)

    # ── 维护 ──────────────────────────────────────────────────────────

    def summary_stats(self) -> Dict[str, object]:
        total = self.stats["hit"] + self.stats["miss"]
        return {
            "enabled": self.enabled,
            "model": self.model,
            "hit": self.stats["hit"],
            "miss": self.stats["miss"],
            "written": self.stats["written"],
            "hit_rate": round(self.stats["hit"] / total, 4) if total else 0.0,
            "entries": self.count(),
            "size_mb": round(self.path.stat().st_size / 2 ** 20, 1)
            if self.path.exists() else 0.0,
        }

    def count(self) -> int:
        if not self.enabled or not self.path.exists():
            return 0
        row = self._conn().execute(
            "SELECT COUNT(*) FROM emb WHERE model=?", (self.model,)).fetchone()
        return int(row[0]) if row else 0

    def purge_other_models(self) -> int:
        """清掉别的模型留下的向量（换模型后手动调用，或由 CLI 调）。"""
        if not self.enabled:
            return 0
        cur = self._conn().execute("DELETE FROM emb WHERE model<>?", (self.model,))
        self._conn().commit()
        return cur.rowcount or 0
