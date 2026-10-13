# -*- coding: utf-8 -*-
"""训练线程控制状态 — 进程级单例（跨 Streamlit rerun 稳定）。

背景：Streamlit 每次「完整 rerun」都会重新执行 pages/nn_training.py 的模块顶层，
普通模块级变量（线程句柄 / 锁 / Event / 开始时间）会被重置，导致：
  - 防重入锁失效（_TRAIN_THREAD 恒为 None → 训练中可再起线程 → 并发 OOM）
  - 停止按钮信号丢失
  - 耗时 / ETA 显示错误
  - _progress_fragment 陷入整页 rerun 紧循环

本模块是独立 import 的模块，只被 Python 导入一次（缓存在 sys.modules），
因此 _STATE 单例跨 Streamlit rerun 稳定，且任何线程（含训练 worker 线程）都能安全访问。
"""

import threading


class _TrainState:
    __slots__ = ("thread", "stop_event", "lock", "start_time")

    def __init__(self):
        self.thread = None                 # 当前训练线程
        self.stop_event = threading.Event()  # 停止信号（训练器每轮开始前检查）
        self.lock = threading.Lock()       # 防重入锁：任何时刻只允许一个训练线程
        self.start_time = 0.0              # 训练开始时刻（ETA 估算用）


_STATE = _TrainState()


def get_train_state() -> _TrainState:
    """返回跨 rerun 稳定的训练控制状态单例。"""
    return _STATE
