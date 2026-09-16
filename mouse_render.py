"""Render a top-down preview of the mouse with its three lit zones tinted.

The illustration is drawn procedurally with Pillow rather than shipped as an
image file, so the executable stays self-contained and carries no third-party
product photography.  Everything is drawn at SUPERSAMPLE times the final size
and downsampled, which gives clean edges without any manual antialiasing.

The three zones match the hardware: scroll wheel, the logo panel, and the strip
across the base.  The mouse drives all three from a single stored colour, so the
preview tints them identically - it shows what the device will actually do.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter


SUPERSAMPLE = 4

BODY_TOP = (47, 51, 60)
BODY_BOTTOM = (22, 24, 29)
OUTLINE = (92, 98, 112)
SEAM = (16, 17, 21)

def _width_at(t: float) -> float:
    """Body half-width at t, nose (0) to tail (1), as a fraction of the widest point.

    Analytic rather than interpolated between key points: a superellipse gives
    smoothly rounded ends with a full midsection, and the linear term tapers the
    nose so the shape reads as a mouse rather than a capsule.  Keeping it closed
    form avoids the curvature breaks that show up as facets along the outline.
    """
    t = min(max(t, 0.0), 1.0)
    # Skewing t pushes the widest point back to roughly 55% of the body length.
    skewed = t**1.15
    superellipse = max(0.0, 1.0 - (2.0 * skewed - 1.0) ** 2) ** 0.35
    taper = 0.76 + 0.24 * t
    return superellipse * taper


def _body_polygon(width: int, height: int, margin: int) -> list[tuple[float, float]]:
    top = margin
    bottom = height - margin
    half = (width - 2 * margin) / 2.0
    centre = width / 2.0

    steps = 240
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for step in range(steps + 1):
        t = step / steps
        y = top + (bottom - top) * t
        w = _width_at(t) * half
        left.append((centre - w, y))
        right.append((centre + w, y))
    return right + left[::-1]


def _vertical_gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    width, height = size
    gradient = Image.new("RGB", (1, height))
    pixels = gradient.load()
    for y in range(height):
        blend = y / max(height - 1, 1)
        pixels[0, y] = tuple(
            int(round(top[channel] + (bottom[channel] - top[channel]) * blend))
            for channel in range(3)
        )
    return gradient.resize((width, height), Image.Resampling.BILINEAR)


def _zone_rects(width: int, height: int, margin: int) -> dict[str, tuple]:
    top = margin
    bottom = height - margin
    span = bottom - top
    centre = width / 2.0
    half = (width - 2 * margin) / 2.0

    def y_at(t: float) -> float:
        return top + span * t

    wheel_w = half * 0.135
    wheel = (centre - wheel_w, y_at(0.10), centre + wheel_w, y_at(0.265))

    logo_w = half * 0.30
    logo_h = span * 0.055
    logo_cy = y_at(0.595)
    logo = (centre - logo_w, logo_cy - logo_h, centre + logo_w, logo_cy + logo_h)

    strip_t = 0.845
    strip_w = _width_at(strip_t) * half * 0.86
    strip_h = span * 0.022
    strip_cy = y_at(strip_t)
    strip = (centre - strip_w, strip_cy - strip_h, centre + strip_w, strip_cy + strip_h)

    return {"wheel": wheel, "logo": logo, "strip": strip}


@lru_cache(maxsize=4)
def _build_layers(size: tuple[int, int]) -> tuple:
    """Build and cache the colour-independent layers for a given output size.

    Only the lit zones change when the colour changes, so the shell, gradient and
    seams are rasterised once and the zones are kept as alpha masks to be tinted.
    Rebuilding everything per colour took over half a second, which is far too
    slow to drive a live preview.
    """
    width = size[0] * SUPERSAMPLE
    height = size[1] * SUPERSAMPLE
    margin = int(min(width, height) * 0.085)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Body silhouette as a mask, filled with a vertical gradient.
    polygon = _body_polygon(width, height, margin)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).polygon(polygon, fill=255)

    body = _vertical_gradient((width, height), BODY_TOP, BODY_BOTTOM).convert("RGBA")
    canvas.paste(body, (0, 0), mask)

    outline = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(outline).polygon(polygon, outline=OUTLINE + (200,), width=SUPERSAMPLE * 2)
    canvas.alpha_composite(outline)

    # Button seams.
    seams = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pen = ImageDraw.Draw(seams)
    top = margin
    span = (height - margin) - margin
    centre = width / 2.0
    half = (width - 2 * margin) / 2.0
    split_end = top + span * 0.40
    # The wheel sits in the button split, so the seam runs to it and resumes below.
    wheel_box = _zone_rects(width, height, margin)["wheel"]
    pen.line([(centre, top + span * 0.035), (centre, wheel_box[1])], fill=SEAM + (230,),
             width=SUPERSAMPLE * 2)
    pen.line([(centre, wheel_box[3]), (centre, split_end)], fill=SEAM + (230,),
             width=SUPERSAMPLE * 2)
    arc_w = _width_at(0.40) * half * 0.96
    pen.arc(
        [centre - arc_w, split_end - span * 0.10, centre + arc_w, split_end + span * 0.10],
        start=200, end=340, fill=SEAM + (200,), width=SUPERSAMPLE * 2,
    )
    canvas.alpha_composite(seams)

    # The lit zones are kept as alpha masks so they can be tinted cheaply later.
    zones = _zone_rects(width, height, margin)
    radius = int(span * 0.02)

    glow_mask = Image.new("L", (width, height), 0)
    glow_pen = ImageDraw.Draw(glow_mask)
    for name, box in zones.items():
        pad = span * (0.030 if name != "strip" else 0.024)
        spread = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad]
        glow_pen.rounded_rectangle(spread, radius=radius * 3, fill=112)
    glow_mask = glow_mask.filter(ImageFilter.GaussianBlur(radius=span * 0.021))

    lit_mask = Image.new("L", (width, height), 0)
    lit_pen = ImageDraw.Draw(lit_mask)
    for box in zones.values():
        lit_pen.rounded_rectangle(box, radius=radius, fill=255)

    # A soft gloss along the top of each zone, blurred so it reads as a sheen
    # rather than a second rectangle stacked on the first.
    gloss_mask = Image.new("L", (width, height), 0)
    gloss_pen = ImageDraw.Draw(gloss_mask)
    for box in zones.values():
        box_w = box[2] - box[0]
        box_h = box[3] - box[1]
        # Inset from the smaller dimension: the strip is wide and shallow, so
        # scaling by width alone would invert its gloss box.
        inset = min(box_w, box_h) * 0.18
        y0 = box[1] + inset * 0.5
        y1 = max(y0 + 1.0, box[1] + box_h * 0.38)
        gloss_pen.rounded_rectangle(
            (box[0] + inset, y0, box[2] - inset, y1), radius=radius, fill=38
        )
    gloss_mask = gloss_mask.filter(ImageFilter.GaussianBlur(radius=span * 0.004))

    resample = Image.Resampling.LANCZOS
    return (
        canvas.resize(size, resample),
        glow_mask.resize(size, resample),
        lit_mask.resize(size, resample),
        gloss_mask.resize(size, resample),
        mask.resize(size, resample),
    )


def render_mouse(colour: tuple[int, int, int], size: tuple[int, int] = (360, 540)) -> Image.Image:
    """Return an RGBA preview of the mouse with all three zones lit in `colour`.

    The mouse drives all three zones from one stored colour, so tinting them
    identically is what the hardware will actually do.
    """
    body, glow_mask, lit_mask, gloss_mask, shell = _build_layers(size)

    frame = body.copy()
    for tint, alpha in ((colour, glow_mask), (colour, lit_mask), ((255, 255, 255), gloss_mask)):
        layer = Image.new("RGBA", size, tuple(tint) + (0,))
        layer.putalpha(alpha)
        frame.alpha_composite(layer)

    # Keep the glow from spilling outside the shell.
    contained = Image.new("RGBA", size, (0, 0, 0, 0))
    contained.paste(frame, (0, 0), shell)
    return contained


def render_swatch(colour: tuple[int, int, int], size: int = 34) -> Image.Image:
    edge = size * SUPERSAMPLE
    image = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        [0, 0, edge - 1, edge - 1], radius=edge * 0.28, fill=colour + (255,)
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def relative_luminance(colour: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        srgb = value / 255.0
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(c) for c in colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def readable_text_colour(colour: tuple[int, int, int]) -> str:
    return "#101114" if relative_luminance(colour) > 0.42 else "#FFFFFF"


if __name__ == "__main__":  # quick visual check
    for name, rgb in (("purple", (0x80, 0x00, 0xFF)), ("cyan", (0x00, 0xFF, 0xFF))):
        render_mouse(rgb).save(f"preview-{name}.png")
        print(f"wrote preview-{name}.png")
