#!/usr/bin/env python3
"""把 index.html 里的 #icon-refresh 描边路径渲染成对照图，检查小尺寸下是否还认得出。

本机没有浏览器，图标只能这样自检：按实际交付的 path 数据渲染 16 / 24 / 48px，
以及描边加粗到 2.4 的 16px（按钮里那种小尺寸最怕糊）。
    /tmp/svgvenv/bin/python scripts/render_refresh_icon.py
"""
import cairosvg
import pathlib
import re

HTML = pathlib.Path(__file__).resolve().parent.parent / "static/index.html"
SYMBOL = re.search(r'<symbol id="icon-refresh".*?</symbol>', HTML.read_text(encoding="utf-8"), re.S)
if not SYMBOL:
    raise SystemExit("❌ index.html 里找不到 #icon-refresh")
paths = re.findall(r'<path d="([^"]+)"', SYMBOL.group(0))
print("描边路径条数:", len(paths), "→", paths)

FG, BG, LABEL = "#E8ECF8", "#131A2B", "#93A0C8"
TILES = [(16, 1.8), (24, 1.8), (48, 1.8), (16, 2.4)]
TILE, GAP, PAD, CAPTION = 96, 18, 20, 30
WIDTH = PAD * 2 + TILE * len(TILES) + GAP * (len(TILES) - 1)
HEIGHT = PAD * 2 + TILE + CAPTION

parts = [f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>']
for index, (size, stroke) in enumerate(TILES):
    x = PAD + index * (TILE + GAP) + (TILE - size) / 2
    y = PAD + (TILE - size) / 2
    body = "".join(f'<path d="{d}"/>' for d in paths)
    parts.append(
        f'<svg x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{FG}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f'{body}</svg>'
    )
    parts.append(
        f'<text x="{PAD + index * (TILE + GAP) + TILE / 2:.0f}" y="{PAD + TILE + 20}" fill="{LABEL}" '
        f'font-family="sans-serif" font-size="13" text-anchor="middle">{size}px · 描边 {stroke}</text>'
    )

out = pathlib.Path("/tmp/refresh_icon_preview.png")
cairosvg.svg2png(
    bytestring=f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}">{"".join(parts)}</svg>'.encode(),
    write_to=str(out),
    output_width=WIDTH * 3,
)
print("已渲染:", out, "（深色底、3 倍缩放）")
