"""Generate the application icon from the same renderer the preview uses."""

from __future__ import annotations

from PIL import Image

from mouse_render import render_mouse

ACCENT = (0x80, 0x00, 0xFF)
SIZES = (16, 24, 32, 48, 64, 128, 256)


def build(edge: int = 256) -> Image.Image:
    mouse_height = int(edge * 0.88)
    mouse_width = int(mouse_height * 2 / 3)
    mouse = render_mouse(ACCENT, (mouse_width, mouse_height))

    canvas = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    canvas.paste(mouse, ((edge - mouse_width) // 2, (edge - mouse_height) // 2), mouse)
    return canvas


if __name__ == "__main__":
    icon = build()
    icon.save("icon.ico", sizes=[(s, s) for s in SIZES])
    icon.save("icon.png")
    print(f"wrote icon.ico with sizes {SIZES}")
