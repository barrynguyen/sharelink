#!/usr/bin/env python3
"""Generate ShareLink.icns from scratch using PIL."""
import os
import subprocess
from PIL import Image, ImageDraw

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
ICONSET = os.path.join(OUT_DIR, "ShareLink.iconset")
ICNS = os.path.join(OUT_DIR, "ShareLink.icns")


def draw_master(size: int) -> Image.Image:
    """Draw a 1024px master image with rounded square + cast symbol."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Rounded square background (macOS Big Sur+ style proportions)
    radius = int(size * 0.22)
    d.rounded_rectangle(
        [(0, 0), (size - 1, size - 1)],
        radius=radius,
        fill=(0, 0, 0, 255),  # pure black
    )
    # Inner phosphor-green ring for that CRT terminal glow
    border = max(2, int(size * 0.012))
    d.rounded_rectangle(
        [(border, border), (size - 1 - border, size - 1 - border)],
        radius=radius - border,
        outline=(0, 255, 65, 255),  # Matrix green
        width=border,
    )
    # Cast symbol: outer screen rectangle (rounded, outline) + 3 wifi arcs in corner
    pad = int(size * 0.18)
    screen_box = [pad, int(size * 0.22), size - pad, int(size * 0.72)]
    line_w = max(2, int(size * 0.035))
    d.rounded_rectangle(
        screen_box,
        radius=int(size * 0.04),
        outline=(0, 255, 65, 255),
        width=line_w,
    )
    # Wifi arcs at bottom-left of the screen — three concentric squares
    origin_x = pad + line_w
    origin_y = screen_box[3] - line_w
    arc_unit = int((screen_box[2] - screen_box[0]) * 0.28)
    for i, sz in enumerate([arc_unit, int(arc_unit * 0.65), int(arc_unit * 0.32)]):
        rx = origin_x + sz
        ry = origin_y - sz
        # Square arc emulated as rounded rect outline (top-right corner facing into TV)
        if i == 2:
            # Smallest: solid dot
            d.ellipse(
                [origin_x - line_w, origin_y - line_w, origin_x + line_w * 2,
                 origin_y + line_w * 2],
                fill=(0, 255, 65, 255),
            )
        else:
            d.arc(
                [origin_x - sz, origin_y - sz, origin_x + sz, origin_y + sz],
                start=270, end=360,
                fill=(0, 255, 65, 255),
                width=line_w,
            )
    return img


def main():
    master = draw_master(1024)
    os.makedirs(ICONSET, exist_ok=True)
    # macOS .iconset required sizes
    targets = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for name, size in targets:
        master.resize((size, size), Image.LANCZOS).save(os.path.join(ICONSET, name))
    subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", ICNS], check=True)
    print(f"Generated: {ICNS}")


if __name__ == "__main__":
    main()
