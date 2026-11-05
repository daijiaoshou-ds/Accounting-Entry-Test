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
import os
import subprocess
import sys
import time
from pathlib import Path

PORT = 8501
CHECK_INTERVAL = 2.0   # 检测间隔（秒）
IDLE_TIMEOUT = 12.0    # 连续无连接多久判定为浏览器已关闭（秒）

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"


def _fix_encoding():
    """让中文 print 在 chcp 65001 的 cmd 窗口下正常显示。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _run_text(args: list, timeout: float = 6) -> str:
    """执行命令并按「本机编码」解码输出。

    中文 Windows 的 netstat/tasklist 输出是 GBK，直接 text=True 会让
    子进程读取线程按 UTF-8 解码并抛 UnicodeDecodeError（stdout 变成
    None）——旧实现因此永远拿不到连接数，"关浏览器自动停"实际从未生效。
    """
    try:
        raw = subprocess.run(args, capture_output=True, timeout=timeout).stdout
    except Exception:
        return ""
    if not raw:
        return ""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="ignore")


def established_count() -> int:
    """统计 8501 端口当前 ESTABLISHED 连接数。检测失败返回 -1。"""
    out = _run_text(["netstat", "-ano"])
    if not out:
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


def _listening_pids() -> list:
    """8501 端口上处于 LISTENING 状态的进程 PID 列表。"""
    out = _run_text(["netstat", "-ano"])
    if not out:
        return []
    pids = []
    for line in out.splitlines():
        if (":" + str(PORT)) in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = int(parts[-1])
                # PID 0 = 系统/已退出进程的残留 socket，不是真进程，
                # 照单全收会让 free_port 误判"被非 python 程序占用"
                if pid > 0 and pid not in pids:
                    pids.append(pid)
    return pids


def _pid_is_python(pid: int) -> bool:
    """占用端口的进程是不是 python（= 本工具上次运行的残留）。"""
    out = _run_text(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
    ).strip().lower()
    return "python" in out


def free_port() -> bool:
    """确保 8501 空闲。

    旧实现直接启动，端口被上一次运行（很可能是 start.bat 的生产模式窗口）
    占着时，新实例只是绑定失败，而浏览器仍然连到旧进程——于是「用
    start_eval.bat 启动却还是融合模式」这种鬼打墙。这里直接收掉残留的
    python 进程，非 python 占用则报错退出。
    """
    pids = _listening_pids()
    if not pids:
        return True
    for pid in pids:
        if not _pid_is_python(pid):
            print(f"[错误] 端口 {PORT} 被非 python 进程占用（PID {pid}），"
                  f"请先关闭该程序，或修改本文件的 PORT。")
            return False
        print(f"[提示] 端口 {PORT} 被上次运行的进程占用（PID {pid}），正在关闭 ...")
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=10)
        except Exception as e:
            print(f"[错误] 关闭失败：{e}")
            return False
    time.sleep(1.0)
    left = _listening_pids()
    if left:
        print(f"[错误] 端口 {PORT} 仍被占用：{left}")
        return False
    return True


def _pause(msg: str = "按 Enter 退出 ...") -> None:
    """等用户看一眼再关窗口；非交互环境（自动化调用）下直接返回。"""
    try:
        input(msg)
    except (EOFError, OSError):
        pass


def _ensure_venv_python() -> None:
    """确保跑的是项目 venv 里的 Python。

    只要有人用 `python run.py`（或在别的终端、IDE、快捷方式里点运行），
    PATH 里的系统 Python 会排在 venv 前面。系统 Python 通常没装 torch，
    页面就会显示「未装 torch，纯程序模式」，模型一点不参与、置信度全无，
    但界面看着一切正常——这种"静默降级"比报错难查得多。
    这里检测到解释器不对就直接换 venv 重跑。
    """
    if os.environ.get("_APP_REEXEC") == "1":
        return
    if not VENV_PY.exists():
        return
    try:
        same = Path(sys.executable).resolve() == VENV_PY.resolve()
    except OSError:
        same = False
    if same:
        return
    print(f"[提示] 当前解释器不是项目 venv：{sys.executable}", flush=True)
    print(f"       改用 {VENV_PY} 重新启动 ...", flush=True)
    env = dict(os.environ, _APP_REEXEC="1")
    code = subprocess.call(
        [str(VENV_PY), str(Path(__file__).resolve())] + sys.argv[1:], env=env,
    )
    sys.exit(code)


def _torch_info() -> tuple:
    """(是否可用, 版本或错误信息)。"""
    try:
        import torch
        return True, torch.__version__
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> None:
    _ensure_venv_python()
    _fix_encoding()
    eval_mode = os.environ.get("NN_PURE_MODEL_EVAL", "0") == "1"
    mode_text = ("评估（体检）模式 · 100% 模型，不融合程序分"
                 if eval_mode else "生产模式 · 程序 + 模型融合")
    has_torch, torch_msg = _torch_info()
    print("=" * 56, flush=True)
    print("  会计分析工具箱 启动中 ...", flush=True)
    print(f"  当前模式：{mode_text}", flush=True)
    print(f"  解释器：{sys.executable}", flush=True)
    print(f"  torch：{'v' + torch_msg if has_torch else '未安装 → 模型不参与'}",
          flush=True)
    print("  提示：关闭浏览器后约 12 秒自动停止；", flush=True)
    print("        也可双击 stop.bat 或关闭本窗口立即停止。", flush=True)
    if eval_mode:
        print("  跑账：浏览器 - 序时账清洗 - 上传 - 开始分类", flush=True)
        print("        页面顶部应显示「评估模式（体检）」；", flush=True)
        print("        跑完在「复核」页看置信度分布与输出文件路径。", flush=True)
    print("=" * 56, flush=True)

    # 评估模式没有模型就毫无意义：宁可停下来报错，也不要静默跑成纯程序
    if eval_mode and not has_torch:
        print()
        print("[错误] 评估模式需要 torch + 模型，但当前解释器没有 torch。", flush=True)
        print("       请双击 start_eval.bat 启动（它固定指向 venv 的 Python）；", flush=True)
        print("       或检查 venv 是否装好：venv\\Scripts\\pip install -r requirements-nn.txt",
              flush=True)
        _pause()
        return

    if not free_port():
        _pause()
        return

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

