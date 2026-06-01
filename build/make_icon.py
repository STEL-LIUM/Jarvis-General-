"""
Generate build\\icon.ico — a purple silhouette of the feather emoji.

Renders the Unicode feather (U+1FAB6) using Segoe UI Emoji at high resolution,
extracts the alpha mask (the feather shape), and fills it with purple. Then
saves as a multi-size .ico that Inno Setup and PyInstaller both pick up.
"""
from __future__ import annotations

import os
import sys
from PIL import Image, ImageDraw, ImageFont

PURPLE  = (165, 87, 247, 255)   # close to Catppuccin Mocha mauve, vivid
FEATHER = "\U0001FAB6"          # 🪶
RENDER  = 1024
SIZES   = [16, 32, 48, 64, 128, 256]
OUT     = os.path.join(os.path.dirname(__file__), "icon.ico")

FONTS = [
    r"C:\Windows\Fonts\seguiemj.ttf",   # Segoe UI Emoji (color)
    r"C:\Windows\Fonts\seguisym.ttf",   # fallback
]

font = None
for fp in FONTS:
    if os.path.isfile(fp):
        try:
            font = ImageFont.truetype(fp, int(RENDER * 0.78))
            print(f"Using font: {fp}")
            break
        except OSError:
            continue
if font is None:
    print("No suitable emoji font found", file=sys.stderr)
    sys.exit(1)

# Pass 1 — render the color emoji onto a transparent canvas. embedded_color=True
# tells PIL to honor the COLR/CPAL glyph tables.
shape = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
sdraw = ImageDraw.Draw(shape)
bbox = sdraw.textbbox((0, 0), FEATHER, font=font, embedded_color=True)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (RENDER - tw) // 2 - bbox[0]
y = (RENDER - th) // 2 - bbox[1]
sdraw.text((x, y), FEATHER, font=font, embedded_color=True)

# Pass 2 — turn the emoji's alpha channel into a purple silhouette so the
# final icon is a clean, tonally-uniform purple feather instead of the
# emoji's native multi-color shading.
alpha = shape.split()[-1]
purple = Image.new("RGBA", (RENDER, RENDER), PURPLE)
empty  = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
final  = Image.composite(purple, empty, alpha)

# Save as multi-resolution .ico; Pillow downsizes from `final` for each size.
final.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
print(f"Wrote: {OUT}")
print(f"Sizes: {', '.join(f'{s}x{s}' for s in SIZES)}")
