"""Tạo sharelink.ico — chạy một lần từ install.bat"""
from PIL import Image, ImageDraw
import sys
import os

def make_icon(out_path):
    SIZE = 256
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Nền bo góc
    draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=52,
                            fill='#1a1a2e')

    # Hình tròn đỏ
    cx, cy, r = SIZE // 2, SIZE // 2, 88
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill='#e94560')

    # Tam giác play (trắng)
    ox = 14
    pts = [(cx - 36 + ox, cy - 52),
           (cx - 36 + ox, cy + 52),
           (cx + 60 + ox, cy)]
    draw.polygon(pts, fill='white')

    # Lưu ICO với nhiều kích thước
    img.save(out_path, format='ICO',
             sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print(f"[+] Icon da tao: {out_path}")

if __name__ == '__main__':
    dest = sys.argv[1] if len(sys.argv) > 1 else 'sharelink.ico'
    make_icon(dest)
