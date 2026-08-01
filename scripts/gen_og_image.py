"""Generate web/og.png, the Open Graph card for the landing page.

Drawn from the same tokens as the product rather than exported from a design tool, so
it cannot drift away from the app's colours. Run after changing the brand palette:

    python scripts/gen_og_image.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "og.png"

W, H = 1200, 630
INK = (12, 13, 16)
TEXT = (232, 234, 237)
MUTED = (154, 160, 170)
MUTED_2 = (141, 149, 161)
ACCENT = (255, 122, 26)
SETTLED = (61, 220, 132)
PATIENT = (90, 169, 255)
HOSPITAL = (255, 92, 92)


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """Best available system face, falling back to Pillow's own."""
    candidates = ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def build() -> Image.Image:
    img = Image.new("RGB", (W, H), INK)

    # The app's top-left accent wash, approximated with concentric ellipses.
    glow = Image.new("RGB", (W, H), INK)
    gd = ImageDraw.Draw(glow)
    for radius in range(520, 0, -8):
        weight = (520 - radius) / 520 * 0.16
        gd.ellipse(
            [120 - radius, -180 - radius, 120 + radius, -180 + radius],
            fill=tuple(int(c + (ACCENT[i] - c) * weight) for i, c in enumerate(INK)),
        )
    img = Image.blend(img, glow, 0.9)
    d = ImageDraw.Draw(img)

    d.text((72, 66), "ClaimIQ", font=font(38), fill=ACCENT)
    d.text((72, 112), "PRE-SUBMISSION AUDIT", font=font(17), fill=MUTED_2)

    d.text((72, 190), "Catch the deduction", font=font(74), fill=TEXT)
    d.text((72, 272), "before the insurer does.", font=font(74), fill=TEXT)

    d.text((72, 384), "Audit the claim packet before it goes to the TPA.",
           font=font(28, bold=False), fill=MUTED)
    d.text((72, 424), "Every deduction cites the rule it came from.",
           font=font(28, bold=False), fill=MUTED)

    # The three buckets, in the product's own order and colours.
    x = 72
    for label, colour, width in (
        ("Insurer settles", SETTLED, 300),
        ("Patient pays", PATIENT, 210),
        ("Hospital absorbs", HOSPITAL, 130),
    ):
        d.rounded_rectangle([x, 520, x + width, 530], radius=5, fill=colour)
        d.text((x, 544), label, font=font(19, bold=False), fill=MUTED)
        x += width + 34

    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
