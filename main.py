"""Lenovo M210 RGB - static colour control.

All device work runs on a worker thread; the UI thread only ever touches widgets.
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import colorchooser

import customtkinter as ctk
from PIL import Image

import m210_device as device
import photo_render
from mouse_render import readable_text_colour, render_coffee_icon, render_mouse

APP_NAME = "M210 RGB"
APP_VERSION = "1.0.0"

BG = "#0E1013"
CARD = "#171A20"
INSET = "#1E222A"
BORDER = "#262B35"
TEXT = "#E8EAF0"
MUTED = "#868E9E"
OK = "#3ECF8E"
BAD = "#F2555A"
COFFEE = "#E8A33D"
COFFEE_HOVER = "#2A2318"

# A PayPal.me handle rather than the account's email address: it reaches the same
# account without publishing a personal address in a public repository.
PAYPAL_HANDLE = "rsenthamizhan"
DONATE_URL = f"https://www.paypal.me/{PAYPAL_HANDLE}"

# The drawn fallback is 2:3; a photo is fitted inside this box, whatever its aspect.
PREVIEW_SIZE = (340, 460)
PHOTO_NAME = "mouse.png"

PRESETS = (
    "#FF0000", "#FF6A00", "#FFD400", "#3EFF6A",
    "#00E5FF", "#2E7BFF", "#8000FF", "#FFFFFF",
)

HEX_PATTERN = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def resource_path(name: str) -> str:
    """Resolve a bundled file, both when frozen by PyInstaller and when run from source."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def parse_hex(value: str) -> tuple[int, int, int] | None:
    match = HEX_PATTERN.match(value.strip())
    if not match:
        return None
    raw = match.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=BG)
        self.title(f"{APP_NAME}")
        self.geometry("880x610")
        self.minsize(880, 610)
        self.resizable(False, False)
        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass  # A missing icon is never worth failing to start over.

        self.colour: tuple[int, int, int] = (0x80, 0x00, 0xFF)
        self.brightness = 4
        self.busy = False
        self._results: queue.Queue = queue.Queue()
        self._preview_job: str | None = None

        # Prefer a real product photo when one is shipped alongside the app,
        # and fall back to the drawn illustration so it always renders.
        candidate = resource_path(PHOTO_NAME)
        self._photo_path = candidate if photo_render.available(candidate) else None
        self._photo_size = (
            photo_render.fit_size(self._photo_path, PREVIEW_SIZE)
            if self._photo_path
            else PREVIEW_SIZE
        )

        self._build()
        self._render_preview()
        self.after(80, self._poll_results)
        self._run(self._read_from_device, "read")

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(24, 12))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header, text="Lenovo M210", font=ctk.CTkFont(size=25, weight="bold"), text_color=TEXT
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, text="RGB lighting", font=ctk.CTkFont(size=13), text_color=MUTED
        ).grid(row=1, column=0, sticky="w", pady=(1, 0))

        pill = ctk.CTkFrame(header, fg_color=INSET, corner_radius=14, border_width=1,
                            border_color=BORDER)
        pill.grid(row=0, column=1, rowspan=2, sticky="e")
        self.status_dot = ctk.CTkLabel(pill, text="●", font=ctk.CTkFont(size=13),
                                       text_color=MUTED, width=14)
        self.status_dot.grid(row=0, column=0, padx=(13, 5), pady=8)
        self.status_text = ctk.CTkLabel(pill, text="Checking…",
                                        font=ctk.CTkFont(size=12), text_color=MUTED)
        self.status_text.grid(row=0, column=1, padx=(0, 15), pady=8)

        # Preview
        preview_card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=18, border_width=1,
                                    border_color=BORDER)
        preview_card.grid(row=1, column=0, sticky="nsew", padx=(28, 14), pady=(0, 24))
        preview_card.grid_rowconfigure(0, weight=1)
        self.preview = ctk.CTkLabel(preview_card, text="")
        self.preview.grid(row=0, column=0, padx=34, pady=22)
        self.preview_caption = ctk.CTkLabel(
            preview_card, text="Preview", font=ctk.CTkFont(size=11), text_color=MUTED
        )
        self.preview_caption.grid(row=1, column=0, pady=(0, 18))

        # Controls
        panel = ctk.CTkFrame(self, fg_color=CARD, corner_radius=18, border_width=1,
                             border_color=BORDER)
        panel.grid(row=1, column=1, sticky="nsew", padx=(14, 28), pady=(0, 24))
        panel.grid_columnconfigure(0, weight=1)

        pad = 26
        ctk.CTkLabel(panel, text="COLOUR", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED).grid(row=0, column=0, sticky="w", padx=pad, pady=(24, 10))

        entry_row = ctk.CTkFrame(panel, fg_color="transparent")
        entry_row.grid(row=1, column=0, sticky="ew", padx=pad)
        entry_row.grid_columnconfigure(0, weight=1)

        self.hex_var = tk.StringVar(value=to_hex(self.colour))
        self.hex_entry = ctk.CTkEntry(
            entry_row, textvariable=self.hex_var, height=46, corner_radius=12,
            fg_color=INSET, border_color=BORDER, border_width=1, text_color=TEXT,
            font=ctk.CTkFont(size=17, family="Consolas"),
        )
        self.hex_entry.grid(row=0, column=0, sticky="ew")
        self.hex_var.trace_add("write", lambda *_: self._on_hex_typed())

        ctk.CTkButton(
            entry_row, text="Pick", width=84, height=46, corner_radius=12,
            fg_color=INSET, hover_color="#2A303B", border_width=1, border_color=BORDER,
            text_color=TEXT, font=ctk.CTkFont(size=13), command=self._pick_colour,
        ).grid(row=0, column=1, padx=(10, 0))

        swatches = ctk.CTkFrame(panel, fg_color="transparent")
        swatches.grid(row=2, column=0, sticky="ew", padx=pad, pady=(14, 0))
        for index, preset in enumerate(PRESETS):
            swatches.grid_columnconfigure(index, weight=1)
            ctk.CTkButton(
                swatches, text="", width=44, height=36, corner_radius=10,
                fg_color=preset, hover_color=preset, border_width=1, border_color=BORDER,
                command=lambda value=preset: self._set_colour(parse_hex(value)),
            ).grid(row=0, column=index, padx=2, sticky="ew")

        ctk.CTkLabel(panel, text="BRIGHTNESS", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED).grid(row=3, column=0, sticky="w", padx=pad, pady=(26, 8))

        slider_row = ctk.CTkFrame(panel, fg_color="transparent")
        slider_row.grid(row=4, column=0, sticky="ew", padx=pad)
        slider_row.grid_columnconfigure(0, weight=1)
        self.brightness_slider = ctk.CTkSlider(
            slider_row, from_=0, to=device.MAX_BRIGHTNESS,
            number_of_steps=device.MAX_BRIGHTNESS, height=18,
            fg_color=INSET, progress_color=to_hex(self.colour),
            button_color=TEXT, button_hover_color="#FFFFFF",
            command=self._on_brightness,
        )
        self.brightness_slider.set(self.brightness)
        self.brightness_slider.grid(row=0, column=0, sticky="ew")
        self.brightness_label = ctk.CTkLabel(slider_row, text="4 / 4", width=46,
                                             font=ctk.CTkFont(size=13), text_color=MUTED)
        self.brightness_label.grid(row=0, column=1, padx=(14, 0))

        self.apply_button = ctk.CTkButton(
            panel, text="Apply to mouse", height=52, corner_radius=13,
            font=ctk.CTkFont(size=15, weight="bold"), command=self._apply,
        )
        self.apply_button.grid(row=5, column=0, sticky="ew", padx=pad, pady=(30, 10))

        self.message = ctk.CTkLabel(panel, text="", font=ctk.CTkFont(size=12),
                                    text_color=MUTED, wraplength=380, justify="left")
        self.message.grid(row=6, column=0, sticky="w", padx=pad)

        panel.grid_rowconfigure(7, weight=1)

        support = ctk.CTkFrame(panel, fg_color="transparent")
        support.grid(row=8, column=0, sticky="ew", padx=pad, pady=(0, 2))
        support.grid_columnconfigure(1, weight=1)
        cup = render_coffee_icon(parse_hex(COFFEE), 17)
        self._coffee_icon = ctk.CTkImage(light_image=cup, dark_image=cup, size=(17, 17))
        self.coffee_button = ctk.CTkButton(
            support, text="  Buy me a coffee", height=34, corner_radius=10,
            image=self._coffee_icon, compound="left",
            fg_color="transparent", hover_color=COFFEE_HOVER,
            border_width=1, border_color=COFFEE,
            text_color=COFFEE, font=ctk.CTkFont(size=12, weight="bold"),
            command=self._open_donate,
        )
        self.coffee_button.grid(row=0, column=0, sticky="w")

        divider = ctk.CTkFrame(panel, fg_color=BORDER, height=1)
        divider.grid(row=9, column=0, sticky="ew", padx=pad, pady=(14, 0))

        footer = ctk.CTkFrame(panel, fg_color="transparent")
        footer.grid(row=10, column=0, sticky="ew", padx=pad, pady=(10, 18))
        footer.grid_columnconfigure(0, weight=1)
        self.device_label = ctk.CTkLabel(footer, text="", font=ctk.CTkFont(size=11),
                                         text_color=MUTED)
        self.device_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Re-read", width=78, height=30, corner_radius=9,
            fg_color="transparent", hover_color=INSET, border_width=1, border_color=BORDER,
            text_color=MUTED, font=ctk.CTkFont(size=12),
            command=lambda: self._run(self._read_from_device, "read"),
        ).grid(row=0, column=1, sticky="e")

        self._restyle_accent()

    # --------------------------------------------------------------- helpers

    def _restyle_accent(self) -> None:
        accent = to_hex(self.colour)
        self.apply_button.configure(
            fg_color=accent, hover_color=accent, text_color=readable_text_colour(self.colour)
        )
        self.brightness_slider.configure(progress_color=accent)

    def _render_preview(self) -> None:
        if self._photo_path is not None:
            image = photo_render.render_photo(self.colour, self._photo_path, self._photo_size)
            size = self._photo_size
        else:
            image = render_mouse(self.colour, PREVIEW_SIZE)
            size = PREVIEW_SIZE
        self._preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=size)
        self.preview.configure(image=self._preview_image)
        self.preview_caption.configure(text=f"Preview · {to_hex(self.colour)}")

    def _schedule_preview(self) -> None:
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(40, self._render_preview)

    def _set_colour(self, rgb: tuple[int, int, int] | None, *, from_entry: bool = False) -> None:
        if rgb is None:
            return
        self.colour = rgb
        if not from_entry:
            self.hex_var.set(to_hex(rgb))
        self._restyle_accent()
        self._schedule_preview()

    def _on_hex_typed(self) -> None:
        rgb = parse_hex(self.hex_var.get())
        valid = rgb is not None
        self.hex_entry.configure(border_color=BORDER if valid else BAD)
        if valid:
            self._set_colour(rgb, from_entry=True)

    def _on_brightness(self, value: float) -> None:
        self.brightness = int(round(value))
        self.brightness_label.configure(text=f"{self.brightness} / {device.MAX_BRIGHTNESS}")

    def _open_donate(self) -> None:
        try:
            webbrowser.open_new_tab(DONATE_URL)
            self._set_message("Opened PayPal in your browser. Thank you!")
        except Exception as error:
            self._set_message(f"Could not open the browser: {error}", error=True)

    def _pick_colour(self) -> None:
        chosen = colorchooser.askcolor(color=to_hex(self.colour), title="Choose a colour")
        if chosen and chosen[1]:
            self._set_colour(parse_hex(chosen[1]))

    def _apply(self) -> None:
        rgb = parse_hex(self.hex_var.get())
        if rgb is None:
            self._set_message("Enter a colour as RRGGBB or #RRGGBB.", error=True)
            return
        self.colour = rgb
        self._set_message("Applying…")
        self._run(self._write_to_device, "write")

    def _set_status(self, connected: bool, text: str) -> None:
        self.status_dot.configure(text_color=OK if connected else BAD)
        self.status_text.configure(text=text, text_color=TEXT if connected else MUTED)

    def _set_message(self, text: str, *, error: bool = False) -> None:
        self.message.configure(text=text, text_color=BAD if error else MUTED)

    # ------------------------------------------------------------ device I/O

    def _run(self, work, tag: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.apply_button.configure(state="disabled")
        threading.Thread(target=self._worker, args=(work, tag), daemon=True).start()

    def _worker(self, work, tag: str) -> None:
        try:
            self._results.put((tag, True, work()))
        except Exception as error:
            self._results.put((tag, False, error))

    def _read_from_device(self):
        return device.read_state()

    def _write_to_device(self):
        return device.apply_static(self.colour, self.brightness)

    def _poll_results(self) -> None:
        try:
            while True:
                tag, ok, payload = self._results.get_nowait()
                self.busy = False
                self.apply_button.configure(state="normal")
                if ok:
                    self._on_success(tag, payload)
                else:
                    self._on_failure(tag, payload)
        except queue.Empty:
            pass
        self.after(80, self._poll_results)

    def _on_success(self, tag: str, state: device.LightingState) -> None:
        self._set_status(True, "Connected")
        self.device_label.configure(
            text=f"On mouse: {state.hex_colour}  ·  brightness {state.brightness}"
        )
        if tag == "read":
            self._set_colour(state.rgb)
            self.brightness = state.brightness
            self.brightness_slider.set(state.brightness)
            self._on_brightness(state.brightness)
            self._set_message("Read the current colour from the mouse.")
        else:
            self._set_message(f"Applied {state.hex_colour} to the mouse.")

    def _on_failure(self, tag: str, error: Exception) -> None:
        connected = device.is_connected()
        self._set_status(connected, "Connected" if connected else "Not detected")
        if not connected:
            self.device_label.configure(text="")
            self._set_message(
                "Mouse not found. Connect the M210 directly by USB, then press Re-read.",
                error=True,
            )
        else:
            verb = "read from" if tag == "read" else "write to"
            self._set_message(f"Could not {verb} the mouse: {error}", error=True)


def main() -> int:
    ctk.set_appearance_mode("dark")
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
