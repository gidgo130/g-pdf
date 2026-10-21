# SPDX-License-Identifier: AGPL-3.0-or-later
"""Builds gpdf/assets/g-pdf.ico and .png from the owner's logo plus a PDF badge.

Source: tools/logo_source.ico (the owner's own 48 px logo). It is upscaled with
smoothing and re-thresholded so edges stay crisp at 256 px.

Run:  python tools/make_icon.py      (needs Pillow: pip install pillow)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "gpdf" / "assets"
S = 1024


def load_logo() -> Image.Image:
    ico = Image.open(HERE / "logo_source.ico")
    src = ico.ico.getimage(max(ico.ico.sizes())).convert("RGBA")
    # Dark ink on (semi-)transparent white: use luminance as the ink amount.
    lum = src.convert("L").resize((S, S), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6))
    ink = lum.point(lambda v: 255 if v < 100 else (0 if v > 156 else int((156 - v) * 255 / 56)))
    # Disc behind the logo = bounding box of the ink.
    bbox = ink.point(lambda v: 255 if v > 128 else 0).getbbox()
    disc = Image.new("L", (S, S), 0)
    ImageDraw.Draw(disc).ellipse(bbox, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste((255, 255, 255, 255), (0, 0), disc)
    out.paste((17, 17, 17, 255), (0, 0), ink)
    return out.crop(bbox).resize((880, 880), Image.LANCZOS)


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    logo = load_logo()
    img.alpha_composite(logo, (20, 20))

    d = ImageDraw.Draw(img)
    bx0, by0, bx1, by1 = 580, 540, 960, 1000
    fold = 110
    page = [(bx0, by0), (bx1 - fold, by0), (bx1, by0 + fold), (bx1, by1), (bx0, by1)]
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x + 8, y + 10) for x, y in page], fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    d.polygon(page, fill=(214, 48, 49, 255), outline=(255, 255, 255, 255), width=14)
    d.polygon([(bx1 - fold, by0), (bx1, by0 + fold), (bx1 - fold, by0 + fold)],
              fill=(255, 205, 205, 255))
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(name, 120)
            break
        except OSError:
            continue
    tw = d.textlength("PDF", font=font)
    d.text(((bx0 + bx1) / 2 - tw / 2, by0 + 255), "PDF", font=font, fill="white")
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    big = build()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "g-pdf.png")
    big.save(OUT / "g-pdf.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", OUT)
