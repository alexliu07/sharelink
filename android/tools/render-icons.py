#!/usr/bin/env python3
"""把网页端那套手绘 SVG 渲染成安卓要的 PNG（安卓不认 SVG）。

源文件只有两个，都是手绘描边路径，和 PWA 图标同源：
  ../static/favicon.svg                     → 传统启动图标（白底圆角方块 + 渐变链环）
  icons/ic_launcher_foreground.svg          → 自适应图标的前景层（透明底，链环）

依赖 cairosvg + pillow：
    uv venv /tmp/svgvenv && uv pip install --python /tmp/svgvenv/bin/python cairosvg pillow
用法：
    /tmp/svgvenv/bin/python android/tools/render-icons.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

try:
    import cairosvg
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("需要 cairosvg 与 pillow：uv pip install --python <venv>/bin/python cairosvg pillow")

ROOT = Path(__file__).resolve().parent.parent          # android/
REPO = ROOT.parent                                     # 仓库根
RES = ROOT / "app" / "src" / "main" / "res"

# 传统图标：各密度一套
LEGACY = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def render(svg: Path, size: int) -> Image.Image:
    png = cairosvg.svg2png(url=str(svg), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def write(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    print(f"  {path.relative_to(REPO)}  {image.size[0]}×{image.size[1]}")


def main() -> None:
    legacy_src = REPO / "static" / "favicon.svg"
    foreground_src = ROOT / "icons" / "ic_launcher_foreground.svg"

    for density, size in LEGACY.items():
        write(render(legacy_src, size), RES / f"mipmap-{density}" / "ic_launcher.png")

    # 自适应图标前景层：108dp 画布，xxhdpi 一档就够（系统按需缩放）
    write(render(foreground_src, 432), RES / "mipmap-xxhdpi" / "ic_launcher_foreground.png")


if __name__ == "__main__":
    main()
