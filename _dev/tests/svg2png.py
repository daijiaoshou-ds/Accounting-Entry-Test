"""把报告里的 SVG 用 PIL 画一遍，好让没有浏览器的时候也能看一眼版式。

    python _dev/tests/svg2png.py <报告 HTML> <输出前缀>   # 每个 SVG 出一张 PNG

只认我们自己生成的这几种元素（rect / line / text），不是通用渲染器——
通用渲染器要装的依赖，比它省下的这点时间多。
"""
import re, sys
from PIL import Image, ImageDraw, ImageFont

FONT = r"assets/fonts/simsun.ttc"

def render(svg: str, out: str, scale: int = 2):
    view = re.search(r"viewBox='0 0 (\d+) (\d+)'", svg)
    w, h = int(view.group(1)), int(view.group(2))
    img = Image.new("RGB", (w * scale, h * scale), "white")
    d = ImageDraw.Draw(img)

    def font(size):
        return ImageFont.truetype(FONT, max(8, int(float(size) * scale)), index=0)

    for m in re.finditer(r"<rect x='([-\d.]+)' y='([-\d.]+)' width='([\d.]+)' height='([\d.]+)' fill='([#\w]+)'", svg):
        x, y, ww, hh, fill = m.groups()
        x, y, ww, hh = (float(x) * scale, float(y) * scale, float(ww) * scale, float(hh) * scale)
        d.rectangle([x, y, x + ww, y + hh], fill=fill)
    for m in re.finditer(r"<line x1='([-\d.]+)' y1='([-\d.]+)' x2='([-\d.]+)' y2='([-\d.]+)' stroke='([#\w]+)'", svg):
        x1, y1, x2, y2, stroke = m.groups()
        d.line([float(x1) * scale, float(y1) * scale, float(x2) * scale, float(y2) * scale],
               fill=stroke, width=max(1, scale // 2))
    for m in re.finditer(r"<text x='([-\d.]+)' y='([-\d.]+)' font-size='([\d.]+)' fill='([#\w]+)'([^>]*)>(.*?)</text>", svg):
        x, y, size, fill, rest, text = m.groups()
        anchor = "mm" if "text-anchor='middle'" in rest else ("rm" if "text-anchor='end'" in rest else "lm")
        d.text((float(x) * scale, float(y) * scale), text, font=font(size), fill=fill, anchor=anchor)
    img.save(out)
    return out, w, h

if __name__ == "__main__":
    html = open(sys.argv[1], encoding="utf-8").read()
    svgs = re.findall(r"<svg.*?</svg>", html, re.S)
    print("找到", len(svgs), "个 SVG")
    for i, svg in enumerate(svgs):
        out = f"{sys.argv[2]}_{i}.png"
        print(render(svg, out))
