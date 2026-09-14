"""性能日志工具：记录各阶段耗时，帮助定位性能瓶颈"""
import time
import os
from contextlib import contextmanager
from datetime import datetime


class PerfLogger:
    """性能日志器，单例模式，所有模块共享同一个实例"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.start_time = time.time()
        self.log_dir = None
        self.log_file = None
        self._indent = 0
        self._lines = []
        self._warnings = []

    def set_log_dir(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"perf_{timestamp}.log")

    def _write(self, msg: str):
        elapsed = time.time() - self.start_time
        prefix = "+{:>7.1f}s".format(elapsed)
        indent = "  " * self._indent
        line = f"{prefix} {indent}{msg}"
        self._lines.append(line)
        # Also write to file immediately for live tailing
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def info(self, msg: str):
        self._write(msg)

    def warn(self, msg: str):
        self._warnings.append(msg)
        self._write(f"[WARN] {msg}")

    @contextmanager
    def stage(self, name: str):
        """记录一个阶段的耗时"""
        t0 = time.time()
        self._write(f"BEGIN {name}")
        self._indent += 1
        try:
            yield
        finally:
            self._indent -= 1
            dt = time.time() - t0
            marker = " [SLOW!]" if dt > 5.0 else ""
            self._write(f"END {name} — {dt:.3f}s{marker}")
            if dt > 5.0:
                self._warnings.append(f"SLOW: {name} took {dt:.1f}s")

    @contextmanager
    def timer(self, name: str, warn_threshold: float = 2.0):
        """记录一次调用的耗时，超过阈值则警告"""
        t0 = time.time()
        yield
        dt = time.time() - t0
        marker = " [SLOW!]" if dt > warn_threshold else ""
        self._write(f"{name} — {dt:.3f}s{marker}")
        if dt > warn_threshold:
            self._warnings.append(f"SLOW: {name} took {dt:.1f}s")

    def counter(self, name: str, value):
        """记录计数"""
        self._write(f"{name} = {value}")

    def get_warnings(self):
        return list(self._warnings)

    def flush(self):
        """将所有行写入文件"""
        if self.log_file:
            try:
                with open(self.log_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(self._lines) + "\n")
            except Exception:
                pass

    def get_summary(self) -> str:
        lines = ["=" * 60, "性能日志摘要", "=" * 60]
        lines.extend(self._lines)
        if self._warnings:
            lines.append("")
            lines.append("=" * 60)
            lines.append(f"共 {len(self._warnings)} 条性能警告:")
            for w in self._warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


# 全局单例
perf_logger = PerfLogger()
