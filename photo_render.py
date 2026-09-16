"""Tint the lit zones of a product photo to the selected colour.

The photo shows the mouse with its lighting on - a cyan wheel and logo, and a
rainbow arc around the base.  Those regions are strongly saturated while the
shell, the cable and the background are not, so saturation separates them
cleanly without hand-drawn masks.

Each lit pixel is re-tinted as target * (brightness / 255), which keeps the
original falloff, the bloom around the strip and the specular highlights, so the
recoloured image still reads as a photograph rather than a flat fill.

Everything that does not depend on the colour is computed once and cached, the
same way mouse_render.py works, so live preview updates stay fast.
"""

from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# Measured on the product photo: the shell sits around saturation 4-10 and the
# cable peaks near 23, while the wheel, logo and base arc run 120-160.  That is a
# wide gap, so a soft ramp between these bounds separates them with room to spare
# and gives feathered edges instead of a hard cut.
SATURATION_LOW = 28
SATURATION_HIGH = 110

# The arc fades to roughly V=34 at its dimmest, so the brightness gate has to sit
# well below that to keep the falloff rather than clipping it flat.
BRIGHTNESS_LOW = 18
BRIGHTNESS_HIGH = 60

# The centre of a bright glow blows out to near-white, which is unsaturated and
# would punch holes in a saturation mask.  Dilating closes those holes, since
# they are always ringed by saturated pixels.
DILATE_RADIUS = 5
MASK_BLUR = 2.0

# Sentinel used to flood-fill the backdrop before turning it transparent.
BACKDROP_KEY = (255, 0, 255)
BACKDROP_TOLERANCE = 36


def _ramp(value: int, low: int, high: int) -> int:
    """Map `value` onto 0-255 across [low, high], flat outside that band."""
    if value <= low:
        return 0
    if value >= high:
        return 255
    return ((value - low) * 255) // (high - low)


def _backdrop_alpha(image: Image.Image) -> Image.Image:
    """Return an alpha channel with the flat backdrop knocked out."""
    work = image.copy()
    width, height = work.size
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        try:
            ImageDraw.floodfill(work, corner, BACKDROP_KEY, thresh=BACKDROP_TOLERANCE)
        except Exception:
            pass

    red, green, blue = work.split()
    matches = ImageChops.multiply(
        ImageChops.multiply(
            red.point(lambda p: 255 if p == BACKDROP_KEY[0] else 0),
            green.point(lambda p: 255 if p == BACKDROP_KEY[1] else 0),
        ),
        blue.point(lambda p: 255 if p == BACKDROP_KEY[2] else 0),
    )
    return ImageChops.invert(matches).filter(ImageFilter.GaussianBlur(0.6))


@lru_cache(maxsize=4)
def _prepare(path: str, size: tuple[int, int] | None) -> tuple:
    original = Image.open(path)

    if original.mode == "RGBA" and original.getchannel("A").getextrema()[0] < 250:
        alpha = original.getchannel("A")  # the file already has a cut-out
        rgb = original.convert("RGB")
    else:
        rgb = original.convert("RGB")
        alpha = _backdrop_alpha(rgb)

    if size is not None:
        resample = Image.Resampling.LANCZOS
        rgb = rgb.resize(size, resample)
        alpha = alpha.resize(size, resample)

    _hue, saturation, value = rgb.convert("HSV").split()
    lit = ImageChops.multiply(
        saturation.point(lambda p: _ramp(p, SATURATION_LOW, SATURATION_HIGH)),
        value.point(lambda p: _ramp(p, BRIGHTNESS_LOW, BRIGHTNESS_HIGH)),
    )
    lit = lit.filter(ImageFilter.MaxFilter(DILATE_RADIUS))
    lit = lit.filter(ImageFilter.GaussianBlur(MASK_BLUR))

    # Never tint outside the mouse itself.
    lit = ImageChops.multiply(lit, alpha)

    base = rgb.convert("RGBA")
    base.putalpha(alpha)
    return base, lit, value


def fit_size(path: str, box: tuple[int, int]) -> tuple[int, int]:
    """Largest size that fits inside `box` while preserving the photo's aspect."""
    with Image.open(path) as probe:
        width, height = probe.size
    scale = min(box[0] / width, box[1] / height)
    return max(1, int(width * scale)), max(1, int(height * scale))


def render_photo(colour: tuple[int, int, int], path: str,
                 size: tuple[int, int] | None = None) -> Image.Image:
    """Return the photo with every lit zone recoloured to `colour`."""
    base, lit, value = _prepare(path, size)

    channels = [
        value.point(lambda p, level=level: (p * level) // 255)
        for level in colour
    ]
    tinted = Image.merge("RGB", channels).convert("RGBA")

    frame = base.copy()
    frame.paste(tinted, (0, 0), lit)
    return frame


def available(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with Image.open(path) as probe:
            probe.verify()
        return True
    except Exception:
        return False


if __name__ == "__main__":  # visual check, plus a mask dump for tuning
    import sys

    source = sys.argv[1] if len(sys.argv) > 1 else "mouse.png"
    if not available(source):
        raise SystemExit(f"{source} not found or unreadable")

    target = fit_size(source, (420, 560))
    _base, lit, _value = _prepare(source, target)
    lit.save("mask-lit.png")
    for name, rgb in (("purple", (0x80, 0x00, 0xFF)), ("green", (0x3E, 0xFF, 0x6A))):
        render_photo(rgb, source, target).save(f"photo-{name}.png")
        print(f"wrote photo-{name}.png")
    print("wrote mask-lit.png")
