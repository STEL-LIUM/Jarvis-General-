"""Generate icon-192.png and icon-512.png for the PWA manifest.
Run once: python pwa/make_icons.py
Requires Pillow (already in requirements.txt).
"""
import os
from PIL import Image, ImageDraw, ImageFont

_DIR = os.path.dirname(os.path.abspath(__file__))

def make_icon(size: int) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    margin = size // 12
    draw.ellipse([margin, margin, size - margin, size - margin],
                 fill="#1e1e2e")

    # Accent ring
    ring = size // 20
    draw.ellipse([margin, margin, size - margin, size - margin],
                 outline="#cba6f7", width=ring)

    # "J" glyph centered
    font_size = size // 2
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    text = "J"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill="#cba6f7", font=font)

    out = os.path.join(_DIR, f"icon-{size}.png")
    img.save(out, "PNG")
    print(f"Wrote {out}")

if __name__ == "__main__":
    make_icon(192)
    make_icon(512)
