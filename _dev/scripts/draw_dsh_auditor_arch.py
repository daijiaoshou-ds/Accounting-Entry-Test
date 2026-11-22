# -*- coding: utf-8 -*-
"""画 dsh-auditor 插件的架构图：同一份几何，出 SVG（可编辑）+ PNG（能直接看）。

为什么用手写坐标而不是 graphviz：这张图想表达的是**三个进程边界**（浏览器 / DSH Host /
Python 引擎）与八条数据流，自动布局会把它排成一张网，边界反而看不见。

    python _dev/scripts/draw_dsh_auditor_arch.py
    → docs/reference/dsh-auditor-architecture.svg / .png
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT_SVG = ROOT / "docs" / "reference" / "dsh-auditor-architecture.svg"
OUT_PNG = ROOT / "docs" / "reference" / "dsh-auditor-architecture.png"

W, H = 1600, 1180
SCALE = 2

FONT_CN = r"C:\Windows\Fonts\msyh.ttc"
FONT_CN_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"

BG = "#FFFFFF"
BAND = {
    "browser": ("#F3F6FF", "#C8D5F2", "#2F4F9E"),
    "host": ("#F2F8F3", "#C4DEC8", "#256B33"),
    "engine": ("#FFF7EE", "#EED9BE", "#8A5314"),
}
CARD_FILL = "#FFFFFF"
CARD_STROKE = "#D3DAE3"
TITLE = "#16202B"
BODY = "#40505F"
MUTED = "#7A8794"
ACCENT = "#3B5BDB"
ARROW = "#55647A"

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


def measure(text: str, size: int, bold: bool = False, mono: bool = False) -> float:
    path = FONT_MONO if mono else (FONT_CN_BOLD if bold else FONT_CN)
    return _font(path, size).getlength(text)


def wrap(text: str, size: int, width: float, mono: bool = False) -> list[str]:
    """按字符贪心折行：中英混排时按实际字宽量，别按字数猜。"""
    out: list[str] = []
    line = ""
    for ch in text:
        candidate = line + ch
        if measure(candidate, size, mono=mono) > width and line:
            out.append(line)
            line = ch
        else:
            line = candidate
    if line:
        out.append(line)
    return out


# ── 图元 ────────────────────────────────────────────────────────────────

prims: list[dict] = []


def band(key: str, x: float, y: float, w: float, h: float, label: str, note: str = "") -> None:
    prims.append({"t": "band", "key": key, "x": x, "y": y, "w": w, "h": h,
                  "label": label, "note": note})


def card(x: float, y: float, w: float, badge: str, title: str, bullets: list[str],
         mono_note: str = "", min_h: float = 0) -> float:
    """画一张卡，返回它的实际高度（按折行后的行数算）。"""
    size_title, size_body = 15, 12
    pad = 16
    inner = w - pad * 2
    title_lines = wrap(title, size_title, inner - (22 if badge else 0))
    body_lines: list[tuple[str, list[str]]] = []
    for b in bullets:
        body_lines.append((b, wrap(b, size_body, inner - 12)))
    mono_lines = wrap(mono_note, 11, inner, mono=True) if mono_note else []

    h = pad + len(title_lines) * 20 + 6
    h += len(mono_lines) * 14
    for _, lines in body_lines:
        h += len(lines) * 17 + 2
    h = max(h + pad - 4, min_h)
    prims.append({"t": "card", "x": x, "y": y, "w": w, "h": h, "badge": badge,
                  "title_lines": title_lines, "body": body_lines,
                  "mono": mono_lines, "pad": pad,
                  "size_title": size_title, "size_body": size_body})
    return h


def label(x: float, y: float, text: str, size: int = 12, color: str = BODY,
          bold: bool = False, anchor: str = "start", mono: bool = False) -> None:
    prims.append({"t": "text", "x": x, "y": y, "text": text, "size": size,
                  "color": color, "bold": bold, "anchor": anchor, "mono": mono})


def arrow(x1: float, y1: float, x2: float, y2: float, text: str = "",
          dashed: bool = False, color: str = ARROW, width: float = 1.6,
          label_at: float = 0.5) -> None:
    prims.append({"t": "arrow", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                  "text": text, "dashed": dashed, "color": color, "width": width,
                  "label_at": label_at})


# ── 版面 ────────────────────────────────────────────────────────────────

BAND_A = (40, 74, 1520, 214)
BAND_B = (40, 318, 1520, 300)
BAND_C = (40, 648, 1520, 372)

# 浏览器
band("browser", *BAND_A, "浏览器 · DSH Web 界面（client.js：手写 h()，没有构建步骤）")
card(64, 132, 470, "①", "「审计工作台」页",
     ["新建任务、上传序时账、确认字段映射",
      "阶段时间线 / 复核表 / 下载产物（侧栏图标 + 对话页签，同一份组件）"], min_h=112)
card(552, 132, 470, "②", "对话（agent 会话）",
     ["agent 用 journal_* 工具推进任务",
      "复核会话按任务单独建，读同一份任务目录"], min_h=112)
card(1040, 132, 496, "③", "环境配置页",
     ["六步：解释器 → venv → 基础包 → torch → 模型 → BGE",
      "安装跑在 Host 里，离开页面不断、回来接上"], min_h=112)

# Host
band("host", *BAND_B, "DSH Host 进程 · 插件 Host 半边（index.js + src/，Node ESM）")
card(64, 376, 470, "①", "HTTP 路由  /plugin/journal/*",
     ["/tasks /jobs /job /review /decisions",
      "/download /preview",
      "/env /env/start /env/task /env/cancel",
      "每个请求带 agent → repoFor() 定位引擎目录",
      "只做转发：判定全在引擎，换功能不用改插件"], min_h=200)
card(552, 376, 470, "②", "工具表  journal_*",
     ["features · inspect · start", "next · submit · report",
      "给 agent 用；失败一律当文本返回，不抛异常",
      "start 立刻返回 job_id，真正的活在后台",
      "工具只补四件事：后台跑、排版、页面、叫醒"], min_h=200)
card(1040, 376, 232, "④", "runner 后台跑",
     ["单并发队列（GPU 不打架）", "spawn python，攒 40 行 stderr"], min_h=92)
card(1288, 376, 248, "⑦", "notify 叫醒",
     ["任务绑定自己的会话", "六段消息 + 作业书 md", "幂等键防重复叫"], min_h=92)
card(1040, 486, 496, "③", "settings + env",
     ["engineHome() → %LOCALAPPDATA%\\dsh-auditor\\engine",
      "引擎同步：.engine-version 变了才覆盖（venv / 模型不动）",
      "装 Python、建 venv、pip、下模型"], min_h=92)

# 引擎
band("engine", *BAND_C, "Python 引擎 · 随插件包复制到用户目录；venv 与模型留在那里")
card(64, 706, 470, "⑤", "journal.py  (CLI 入口)",
     ["stdout 只打一行 RESULT <json>，进度走 stderr"], min_h=64)
card(64, 786, 470, "⑥", "jobflow 阶段机",
     ["Stage(program | ai)：程序阶段照跑，AI 阶段停下待办",
      "state.json + decisions/*.json（幂等键 + 依据 + 时间）",
      "阶段哈希＝输入 + 选项 + 上游，resume 只补没跑完的"], min_h=176)
card(552, 706, 470, "", "features/  五个功能，一个目录读完",
     ["contra 对方科目分析　cleaning 序时账清洗",
      "cashflow 现金流量表　entry_test 会计分录测试",
      "cost 成本计算（未接入）"], min_h=140)
card(552, 862, 470, "", "core/ledger  四个功能共用的第一段",
     ["读表 / 接模型判桶 / 复核面 / 接上游"], min_h=88)
card(1040, 706, 232, "", "core/classify",
     ["桶注册表 24 类", "四类机械凭证硬约束", "去重 L1/L2/L3"], min_h=112)
card(1288, 706, 248, "", "core/nn",
     ["BGE 微调 + 分类头", "CPU torch，摘要向量缓存"], min_h=112)
card(1040, 830, 232, "", "journal_io + 数据域",
     ["字段契约 / 读写三档", "科目归一化 + 别名表"], min_h=120)
card(1288, 830, 248, "⑥", "_storage_jobs/<job_id>/",
     ["out/read clean judge export", "reviews/ decisions/ .lessons/"], min_h=120,
     mono_note="")

# ── 连线 ────────────────────────────────────────────────────────────────

arrow(299, 244, 299, 372, "fetch + 1 秒轮询")
arrow(787, 244, 787, 372, "工具调用")
arrow(1288, 244, 1288, 482, "POST /env/start", dashed=False)
arrow(300, 582, 300, 700, "runner spawn：python journal.py …")
arrow(1288, 582, 1288, 676, "首次运行：同步引擎 + 装环境", dashed=True)

# 图例
legend = [
    ("①", "页面与路由之间：1 秒轮询任务与进度"),
    ("②", "agent 调 journal_*：一次调用推进一个阶段"),
    ("③", "环境页让 env 干活：六步安装，进度可查"),
    ("④", "路由/工具把任务交给 runner：入队即返回 job_id"),
    ("⑤", "runner 起子进程：python journal.py 一条命令"),
    ("⑥", "引擎把状态与产物落到任务目录：state / decisions / parquet"),
    ("⑦", "停到复核位，notify 叫醒任务自己的会话"),
    ("⑧", "首次运行：复制引擎到用户目录，环境在那边装"),
]


# ── 渲染：SVG ───────────────────────────────────────────────────────────

def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_svg() -> str:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="Microsoft YaHei, Segoe UI, sans-serif">',
           "<defs>"]
    for key, color in (("solid", ARROW), ("dash", "#8A97A6")):
        out.append(
            f'<marker id="head-{key}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>')
    out.append("</defs>")
    out.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{BG}"/>')

    for p in prims:
        if p["t"] == "band":
            fill, stroke, ink = BAND[p["key"]]
            out.append(f'<rect x="{p["x"]}" y="{p["y"]}" width="{p["w"]}" height="{p["h"]}" '
                       f'rx="16" fill="{fill}" stroke="{stroke}"/>')
            out.append(f'<text x="{p["x"] + 22}" y="{p["y"] + 30}" font-size="17" '
                       f'font-weight="600" fill="{ink}">{esc(p["label"])}</text>')
        elif p["t"] == "card":
            out.append(f'<rect x="{p["x"]}" y="{p["y"]}" width="{p["w"]}" height="{p["h"]}" '
                       f'rx="11" fill="{CARD_FILL}" stroke="{CARD_STROKE}"/>')
            tx = p["x"] + p["pad"] + (22 if p["badge"] else 0)
            ty = p["y"] + p["pad"] + 4
            if p["badge"]:
                out.append(
                    f'<circle cx="{p["x"] + p["pad"] + 9}" cy="{ty + 5}" r="9" fill="#E7ECFB"/>'
                    f'<text x="{p["x"] + p["pad"] + 9}" y="{ty + 10}" font-size="12" '
                    f'text-anchor="middle" fill="{ACCENT}" font-weight="600">'
                    f'{esc(p["badge"])}</text>')
            for i, line in enumerate(p["title_lines"]):
                out.append(f'<text x="{tx}" y="{ty + 12 + i * 20}" font-size="{p["size_title"]}" '
                           f'font-weight="600" fill="{TITLE}">{esc(line)}</text>')
            cy = ty + len(p["title_lines"]) * 20 + 6
            for line in p["mono"]:
                out.append(f'<text x="{tx}" y="{cy + 10}" font-size="11" '
                           f'font-family="Consolas, monospace" fill="{MUTED}">{esc(line)}</text>')
                cy += 14
            for bullet, lines in p["body"]:
                for j, line in enumerate(lines):
                    prefix = "· " if j == 0 else "  "
                    out.append(f'<text x="{tx}" y="{cy + 12}" font-size="{p["size_body"]}" '
                               f'fill="{BODY}">{esc(prefix + line)}</text>')
                    cy += 17
                cy += 2
        elif p["t"] == "text":
            anchor = {"start": "start", "middle": "middle", "end": "end"}[p["anchor"]]
            out.append(f'<text x="{p["x"]}" y="{p["y"]}" font-size="{p["size"]}" '
                       f'text-anchor="{anchor}" fill="{p["color"]}"'
                       f'{" font-weight=\"600\"" if p["bold"] else ""}'
                       f'{" font-family=\"Consolas, monospace\"" if p["mono"] else ""}>'
                       f'{esc(p["text"])}</text>')
        elif p["t"] == "arrow":
            dash = ' stroke-dasharray="6 5"' if p["dashed"] else ""
            head = "dash" if p["dashed"] else "solid"
            color = "#8A97A6" if p["dashed"] else p["color"]
            out.append(f'<line x1="{p["x1"]}" y1="{p["y1"]}" x2="{p["x2"]}" y2="{p["y2"]}" '
                       f'stroke="{color}" stroke-width="{p["width"]}"{dash} '
                       f'marker-end="url(#head-{head})"/>')
            if p["text"]:
                mx = p["x1"] + (p["x2"] - p["x1"]) * p["label_at"]
                my = p["y1"] + (p["y2"] - p["y1"]) * p["label_at"]
                wpx = measure(p["text"], 11) + 12
                out.append(f'<rect x="{mx - wpx / 2:.0f}" y="{my - 10}" width="{wpx:.0f}" '
                           f'height="19" rx="5" fill="#FFFFFF" stroke="{CARD_STROKE}"/>')
                out.append(f'<text x="{mx:.0f}" y="{my + 4}" font-size="11" fill="{MUTED}" '
                           f'text-anchor="middle">{esc(p["text"])}</text>')

    # 图例
    lx, ly = 64, 1058
    for i, (badge, text) in enumerate(legend):
        col = i % 2
        row = i // 2
        x = lx + col * 760
        y = ly + row * 22
        out.append(f'<circle cx="{x + 9}" cy="{y - 4}" r="9" fill="#E7ECFB"/>')
        out.append(f'<text x="{x + 9}" y="{y + 1}" font-size="12" text-anchor="middle" '
                   f'fill="{ACCENT}" font-weight="600">{esc(badge)}</text>')
        out.append(f'<text x="{x + 26}" y="{y + 1}" font-size="12" fill="{BODY}">{esc(text)}</text>')

    out.append("</svg>")
    return "\n".join(out)


# ── 渲染：PNG（没有 SVG 光栅器时的兜底，也和 SVG 同一份坐标） ────────────

def render_png() -> None:
    img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    d = ImageDraw.Draw(img)

    def f(size: int, bold: bool = False, mono: bool = False):
        path = FONT_MONO if mono else (FONT_CN_BOLD if bold else FONT_CN)
        return _font(path, size * SCALE)

    def rect(x, y, w, h, fill, stroke=None, radius=0, width=1):
        box = [x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE]
        if radius:
            d.rounded_rectangle(box, radius=radius * SCALE, fill=fill,
                                outline=stroke, width=width * SCALE)
        else:
            d.rectangle(box, fill=fill, outline=stroke, width=width * SCALE)

    def text(x, y, s, size, color, bold=False, anchor="start", mono=False):
        font = f(size, bold, mono)
        width = d.textlength(s, font=font)
        px = x * SCALE - (width / 2 if anchor == "middle" else (width if anchor == "end" else 0))
        d.text((px, y * SCALE), s, font=font, fill=color)

    for p in prims:
        if p["t"] == "band":
            fill, stroke, ink = BAND[p["key"]]
            rect(p["x"], p["y"], p["w"], p["h"], fill, stroke, radius=16)
            text(p["x"] + 22, p["y"] + 12, p["label"], 17, ink, bold=True)
        elif p["t"] == "card":
            rect(p["x"], p["y"], p["w"], p["h"], CARD_FILL, CARD_STROKE, radius=11)
            tx = p["x"] + p["pad"] + (22 if p["badge"] else 0)
            ty = p["y"] + p["pad"] + 4
            if p["badge"]:
                cx, cy = p["x"] + p["pad"] + 9, ty + 5
                d.ellipse([(cx - 9) * SCALE, (cy - 9) * SCALE,
                           (cx + 9) * SCALE, (cy + 9) * SCALE], fill="#E7ECFB")
                text(cx, cy - 6, p["badge"], 12, ACCENT, bold=True, anchor="middle")
            for i, line in enumerate(p["title_lines"]):
                text(tx, ty + i * 20, line, p["size_title"], TITLE, bold=True)
            cy = ty + len(p["title_lines"]) * 20 + 6
            for line in p["mono"]:
                text(tx, cy, line, 11, MUTED, mono=True)
                cy += 14
            for bullet, lines in p["body"]:
                for j, line in enumerate(lines):
                    text(tx, cy, ("· " if j == 0 else "  ") + line, p["size_body"], BODY)
                    cy += 17
                cy += 2
        elif p["t"] == "text":
            text(p["x"], p["y"] - p["size"] + 2, p["text"], p["size"], p["color"],
                 bold=p["bold"], anchor=p["anchor"], mono=p["mono"])
        elif p["t"] == "arrow":
            color = "#8A97A6" if p["dashed"] else p["color"]
            x1, y1, x2, y2 = (v * SCALE for v in (p["x1"], p["y1"], p["x2"], p["y2"]))
            if p["dashed"]:
                total = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                step, drawn = 12 * SCALE, 0.0
                while drawn < total - 3 * SCALE:
                    a, b = drawn / total, min((drawn + step) / total, 1)
                    d.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a),
                            (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)], fill=color,
                           width=int(p["width"] * SCALE))
                    drawn += step * 2
            else:
                d.line([(x1, y1), (x2, y2)], fill=color, width=int(p["width"] * SCALE))
            ang = __import__("math").atan2(y2 - y1, x2 - x1)
            size = 9 * SCALE
            p1 = (x2, y2)
            p2 = (x2 - size * __import__("math").cos(ang - 0.45),
                  y2 - size * __import__("math").sin(ang - 0.45))
            p3 = (x2 - size * __import__("math").cos(ang + 0.45),
                  y2 - size * __import__("math").sin(ang + 0.45))
            d.polygon([p1, p2, p3], fill=color)
            if p["text"]:
                mx = p["x1"] + (p["x2"] - p["x1"]) * p["label_at"]
                my = p["y1"] + (p["y2"] - p["y1"]) * p["label_at"]
                wpx = measure(p["text"], 11) + 12
                rect(mx - wpx / 2, my - 10, wpx, 19, "#FFFFFF", CARD_STROKE, radius=5)
                text(mx, my - 6, p["text"], 11, MUTED, anchor="middle")

    lx, ly = 64, 1058
    for i, (badge, txt) in enumerate(legend):
        x = lx + (i % 2) * 760
        y = ly + (i // 2) * 22
        d.ellipse([(x) * SCALE, (y - 13) * SCALE, (x + 18) * SCALE, (y + 5) * SCALE],
                  fill="#E7ECFB")
        text(x + 9, y - 10, badge, 12, ACCENT, bold=True, anchor="middle")
        text(x + 26, y - 10, txt, 12, BODY)

    img.save(OUT_PNG)
    print("✓", OUT_PNG)


def main() -> int:
    OUT_SVG.parent.mkdir(parents=True, exist_ok=True)
    OUT_SVG.write_text(render_svg(), encoding="utf-8")
    print("✓", OUT_SVG)
    render_png()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
