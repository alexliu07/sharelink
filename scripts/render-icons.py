#!/usr/bin/env python3
"""把 static/icons/*.svg 渲染成 PWA 需要的 PNG（iOS 只认 PNG，安卓安装条件要 192/512）。

源文件永远是手绘 SVG，PNG 只是构建产物；改了 SVG 就跑一遍这个脚本。
依赖：cairosvg + pillow，装在临时 venv 里即可：
    uv venv /tmp/svgvenv && uv pip install --python /tmp/svgvenv/bin/python cairosvg pillow
用法：
    /tmp/svgvenv/bin/python scripts/render-icons.py            # 输出到 static/icons/
    /tmp/svgvenv/bin/python scripts/render-icons.py --sheet /tmp/icons.png   # 另出一张对比图（带裁切区参考线）
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

try:
    import cairosvg
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    sys.exit("需要 cairosvg 与 pillow：uv pip install --python <venv>/bin/python cairosvg pillow")

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "static" / "icons"

# (源 SVG, 输出 PNG, 边长)
TARGETS = [
    ("app-icon.svg", "icon-192.png", 192),
    ("app-icon.svg", "icon-512.png", 512),
    ("app-icon.svg", "apple-touch-icon.png", 180),   # iOS 主屏图标；我方给方角满幅白底，圆角由 iOS 自己裁（避免双重圆角留白边）
    ("app-icon-maskable.svg", "icon-maskable-512.png", 512),
]


def render(svg_path: Path, size: int) -> Image.Image:
    png = cairosvg.svg2png(url=str(svg_path), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def contact_sheet() -> Image.Image:
    """对比图：任何用途图标的小尺寸可读性 + maskable 的安全区与圆形/圆角裁切效果。"""
    any_icon = render(ICON_DIR / "app-icon.svg", 512)
    maskable = render(ICON_DIR / "app-icon-maskable.svg", 512)
    sheet = Image.new("RGBA", (1000, 760), (11, 16, 32, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 14), "any purpose: 192 / 96 / 48 / 32 px", fill=(147, 160, 200))

    x = 16
    for size in (192, 96, 48, 32):
        tile = any_icon.resize((size, size), Image.Resampling.LANCZOS)
        sheet.alpha_composite(tile, (x, 40 + (192 - size)))
        draw.text((x, 248), f"{size}px", fill=(110, 122, 160))
        x += size + 20

    draw.text((16, 292), "maskable: 安全区（红圈=中心 80% 直径）/ 圆形裁切 / 圆角裁切", fill=(147, 160, 200))
    box = 300
    mx, my = 16, 320
    sheet.alpha_composite(maskable.resize((box, box), Image.Resampling.LANCZOS), (mx, my))
    safe = box * 0.40
    draw.ellipse([mx + box / 2 - safe, my + box / 2 - safe, mx + box / 2 + safe, my + box / 2 + safe],
                 outline=(255, 90, 90))

    circle = maskable.copy()
    mask = Image.new("L", (512, 512), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 511, 511], fill=255)
    circle.putalpha(mask)
    sheet.alpha_composite(circle.resize((box, box), Image.Resampling.LANCZOS), (mx + box + 24, my))

    rounded = render(ICON_DIR / "app-icon.svg", 512)
    rmask = Image.new("L", (512, 512), 0)
    ImageDraw.Draw(rmask).rounded_rectangle([0, 0, 511, 511], radius=114, fill=255)
    rounded.putalpha(rmask)
    sheet.alpha_composite(rounded.resize((box, box), Image.Resampling.LANCZOS), (mx + 2 * (box + 24), my))
    draw.text((mx, my + box + 10), "带安全区参考", fill=(110, 122, 160))
    draw.text((mx + box + 24, my + box + 10), "圆形裁切（安卓自适应）", fill=(110, 122, 160))
    draw.text((mx + 2 * (box + 24), my + box + 10), "圆角裁切（iOS 会再削圆角）", fill=(110, 122, 160))

    # 浅色背景：白底图标在白色标签栏/浅色壁纸下是否还认得出（favicon 实际尺寸）
    draw.text((16, 646), "浅色背景（白色标签栏 / 浅色壁纸），favicon 64/48/32/16px", fill=(147, 160, 200))
    light = Image.new("RGBA", (600, 64), (245, 247, 252, 255))
    favicon = render(ICON_DIR.parent / "favicon.svg", 64)
    x = 16
    for size in (64, 48, 32, 16):
        light.alpha_composite(favicon.resize((size, size), Image.Resampling.LANCZOS), (x, (64 - size) // 2))
        x += size + 26
    sheet.alpha_composite(light, (16, 672))
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", help="额外输出一张对比图到这里（用于肉眼确认裁切效果）")
    args = parser.parse_args()

    for src, out, size in TARGETS:
        image = render(ICON_DIR / src, size)
        image.save(ICON_DIR / out)
        print(f"  {src} → icons/{out}  {image.size[0]}×{image.size[1]}  {image.mode}")
    if args.sheet:
        sheet = contact_sheet()
        sheet.save(args.sheet)
        print(f"对比图 → {args.sheet} {sheet.size}")


if __name__ == "__main__":
    main()
