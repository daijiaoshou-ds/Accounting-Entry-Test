# -*- coding: utf-8 -*-
"""
启动 Streamlit 服务，并在浏览器关闭后自动停止。

由 start.bat 调用。行为：
1. 启动 Streamlit（前台子进程，日志直接显示在本窗口）
2. 轮询 8501 端口的 ESTABLISHED 连接数
3. 一旦见过浏览器连接、之后连续 IDLE_TIMEOUT 秒无连接，判定「浏览器已关闭」，自动停止
4. 检测失败时保持运行（fail-safe：宁可不停，也不误停），用户可用 stop.bat 兜底

停止方式汇总（任选其一）：
  - 关闭浏览器 → 约 12 秒后自动停止
  - 双击 stop.bat → 立即停止
  - 关闭本黑色窗口 / 按 Ctrl+C → 立即停止
"""
import subprocess
import sys
import time

PORT = 8501
CHECK_INTERVAL = 2.0   # 检测间隔（秒）
IDLE_TIMEOUT = 12.0    # 连续无连接多久判定为浏览器已关闭（秒）


def _fix_encoding():
    """让中文 print 在 chcp 65001 的 cmd 窗口下正常显示。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def established_count() -> int:
    """统计 8501 端口当前 ESTABLISHED 连接数。检测失败返回 -1。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=6,
        ).stdout
    except Exception:
        return -1
    n = 0
    for line in out.splitlines():
        if (":" + str(PORT)) in line and "ESTABLISHED" in line:
            n += 1
    return n


def _stop(proc):
    """终止 Streamlit 子进程（先优雅 terminate，超时再强杀）。"""
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    print("[完成] 服务已停止，可以关闭本窗口。")


def main() -> None:
    _fix_encoding()
    print("=" * 56)
    print("  会计分析工具箱 启动中 ...")
    print("  提示：关闭浏览器后约 12 秒自动停止；")
    print("        也可双击 stop.bat 或关闭本窗口立即停止。")
    print("=" * 56)

    proc = subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.address=localhost", "--server.headless=false",
    ])

    seen_connection = False
    idle = 0.0
    try:
        while proc.poll() is None:
            time.sleep(CHECK_INTERVAL)
            n = established_count()
            if n < 0:
                continue  # 检测失败：保持运行，不误停
            if n > 0:
                seen_connection = True
                idle = 0.0
            elif seen_connection:
                idle += CHECK_INTERVAL
                if idle >= IDLE_TIMEOUT:
                    print()
                    print("[自动停止] 检测到浏览器已关闭，正在停止服务 ...")
                    break
    except KeyboardInterrupt:
        print()
        print("[停止] 收到中断，正在停止服务 ...")
    finally:
        _stop(proc)


if __name__ == "__main__":
    main()

