#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log-rca-timeline / 日志分析与故障根因定位 —— 生成 512x512 技能图标（Pillow，无需联网）

用法： python gen_icon.py [输出路径]   默认 ../icon-512.png

字体目录用环境变量拼接，避免脚本内出现任何盘符字面量（分发到他人机器更可靠）。
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
except Exception:
    pass

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("[FATAL] 需要 Pillow：pip install pillow")
    raise SystemExit(2)

S = 512
SS = 4                      # 超采样倍数（抗锯齿）
W = S * SS

BG_TOP = (236, 248, 250)
BG_BOT = (203, 230, 238)
TEAL = (11, 107, 120)
TEAL_D = (7, 71, 79)
WHITE = (255, 255, 255)
INK = (60, 78, 92)
GREY = (196, 208, 218)
RED = (180, 35, 24)
AMBER = (180, 83, 9)


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def load_font(px, bold=False):
    """用环境变量拼接字体目录，不写死任何盘符路径"""
    roots = [os.environ.get("SystemRoot") or os.environ.get("windir") or "",
             "/System/Library/Fonts", "/Library/Fonts", "/usr/share/fonts/truetype"]
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhbd.ttc"]
    names += ["simhei.ttf", "simsun.ttc", "PingFang.ttc", "DejaVuSans.ttf"]
    for r in roots:
        if not r:
            continue
        base = os.path.join(r, "Fonts") if os.name == "nt" else r
        for n in names:
            p = os.path.join(base, n)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, px)
                except Exception:
                    continue
    return ImageFont.load_default()


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon-512.png"
    )

    img = Image.new("RGB", (W, W), BG_TOP)
    d = ImageDraw.Draw(img, "RGBA")

    for y in range(W):
        d.line([(0, y), (W, y)], fill=lerp(BG_TOP, BG_BOT, y / (W - 1)))

    # ---- 日志卡片 ----
    cx, cy = W * 0.5, W * 0.47
    cw, ch = W * 0.62, W * 0.56
    x0, y0 = cx - cw / 2, cy - ch / 2
    x1, y1 = cx + cw / 2, cy + ch / 2
    d.rounded_rectangle([x0 + W * 0.014, y0 + W * 0.022, x1 + W * 0.014, y1 + W * 0.022],
                        radius=int(W * 0.05), fill=(16, 92, 104, 40))
    d.rounded_rectangle([x0, y0, x1, y1], radius=int(W * 0.05), fill=WHITE)
    d.rounded_rectangle([x0, y0, x1, y1], radius=int(W * 0.05), outline=TEAL, width=int(W * 0.010))

    # 卡片标题条
    d.rounded_rectangle([x0 + W * 0.045, y0 + W * 0.055, x0 + W * 0.30, y0 + W * 0.075],
                        radius=int(W * 0.012), fill=TEAL)

    # 五行日志：普通灰条，其中两行含红色异常段
    lx = x0 + W * 0.055
    lw = cw - W * 0.11
    lh = W * 0.030
    gap = W * 0.052
    top = y0 + W * 0.115
    for i in range(5):
        ly = top + i * gap
        w = lw * (0.92 - 0.09 * (i % 3))
        d.rounded_rectangle([lx, ly, lx + w, ly + lh], radius=int(lh / 2), fill=GREY)
        if i == 2:
            d.rounded_rectangle([lx, ly, lx + w * 0.55, ly + lh], radius=int(lh / 2), fill=RED)
            d.rounded_rectangle([lx + w * 0.62, ly, lx + w * 0.78, ly + lh],
                                radius=int(lh / 2), fill=AMBER)
        if i == 3:
            d.rounded_rectangle([lx + w * 0.30, ly, lx + w * 0.50, ly + lh],
                                radius=int(lh / 2), fill=RED)

    # ---- 放大镜（根因定位） ----
    gx, gy = x1 - W * 0.085, y1 - W * 0.10
    gr = W * 0.105
    d.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=(255, 255, 255, 30),
              outline=TEAL, width=int(W * 0.022))
    d.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=TEAL)
    d.ellipse([gx - gr * 0.72, gy - gr * 0.72, gx + gr * 0.72, gy + gr * 0.72],
              fill=(255, 255, 255, 40))
    hl = gr * 0.42
    d.line([(gx + hl, gy + hl), (gx + gr * 1.72, gy + gr * 1.72)],
           fill=TEAL_D, width=int(W * 0.030))
    d.ellipse([gx - hl * 0.55, gy - hl * 0.55, gx + hl * 0.55, gy + hl * 0.55], fill=RED)

    # ---- 底部时间线：三点一线，中段红色 ----
    ty = y1 + W * 0.075
    tlx0, tlx1 = W * 0.22, W * 0.78
    d.line([(tlx0, ty), (tlx1, ty)], fill=GREY, width=int(W * 0.014))
    d.line([(tlx0, ty), (W * 0.38, ty)], fill=TEAL, width=int(W * 0.014))
    d.line([(W * 0.38, ty), (W * 0.66, ty)], fill=RED, width=int(W * 0.014))
    for px, col, rr in ((tlx0, TEAL, 0.020), (W * 0.52, RED, 0.026), (tlx1, TEAL_D, 0.020)):
        r = W * rr
        d.ellipse([px - r, ty - r, px + r, ty + r], fill=col)
        d.ellipse([px - r * 0.45, ty - r * 0.45, px + r * 0.45, ty + r * 0.45], fill=WHITE)

    # ---- 文字标识 ----
    f = load_font(int(W * 0.076), bold=True)
    label = "日志 · 根因"
    try:
        bb = d.textbbox((0, 0), label, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        d.text((W / 2 - tw / 2 - bb[0], W * 0.925 - bb[1]), label, font=f, fill=INK)
    except Exception:
        d.text((W * 0.34, W * 0.925), label, font=f, fill=INK)

    img = img.resize((S, S), Image.LANCZOS)
    out_img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out_img.paste(img, (0, 0), rounded_mask(S, int(S * 0.22)))

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    out_img.convert("RGB").save(out, "PNG", optimize=True)
    print("[OK] %s  %dx%d  %.0fKB" % (out, out_img.size[0], out_img.size[1],
                                      os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
