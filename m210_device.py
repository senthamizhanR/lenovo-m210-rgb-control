"""Lenovo M210 RGB device layer - static colour only.

Protocol: EVision V2 transport over the vendor HID collection FF1C:0092,
report ID 0x04, 64-byte reports, additive checksum over bytes 3..63 stored
little-endian in bytes 1-2.

Only the static-colour path is implemented, and only the first nine bytes of
the active profile are ever written:

    [0] mode        0x01 = static on this mouse
    [1] brightness  0-4
    [2] speed       preserved
    [3] direction   preserved
    [4] preserved   always 0xFF on this device; meaning unknown, so untouched
    [5] red
    [6] green
    [7] blue
    [8] built-in slot

Deliberately NOT implemented, after testing on the hardware:

  * Direct / dynamic colour (0x12, 0x13).  This firmware acknowledges those
    commands with status 0x00 and discards them - the LEDs never take the
    supplied colours.  The only observable effect is the lighting engine
    glitching, and it can make the firmware emit a malformed mouse report that
    latches buttons down system-wide.
  * Profile bytes 9-18.  This is the button-function map, not lighting.
    Writing zeros there latched the middle and side buttons.

Both are excluded by construction: the command whitelist rejects the dynamic
opcodes, and the writer refuses any offset past byte 8.
"""

from __future__ import annotations

import hid


VID = 0x17EF
PID = 0x6187
USAGE_PAGE = 0xFF1C
USAGE = 0x0092
REPORT_ID = 0x04
REPORT_LENGTH = 64
MAX_PAYLOAD = REPORT_LENGTH - 8

CMD_BEGIN_CONFIGURE = 0x01
CMD_END_CONFIGURE = 0x02
CMD_READ_CAPABILITIES = 0x03
CMD_READ_CONFIG = 0x05
CMD_WRITE_CONFIG = 0x06

ALLOWED_COMMANDS = frozenset(
    {
        CMD_BEGIN_CONFIGURE,
        CMD_END_CONFIGURE,
        CMD_READ_CAPABILITIES,
        CMD_READ_CONFIG,
        CMD_WRITE_CONFIG,
    }
)

MODE_STATIC = 0x01
PREFIX_LENGTH = 9
MAX_BRIGHTNESS = 4

PRODUCT_NAME = "Lenovo M210 RGB Gaming Mouse"


class DeviceError(RuntimeError):
    """Any failure talking to the mouse."""


class LightingState:
    __slots__ = ("mode", "brightness", "rgb", "prefix")

    def __init__(self, prefix: bytes) -> None:
        self.prefix = bytes(prefix)
        self.mode = prefix[0]
        self.brightness = prefix[1]
        self.rgb = (prefix[5], prefix[6], prefix[7])

    @property
    def hex_colour(self) -> str:
        return "#{:02X}{:02X}{:02X}".format(*self.rgb)


def find_device() -> dict | None:
    try:
        matches = [
            item
            for item in hid.enumerate(VID, PID)
            if item.get("usage_page") == USAGE_PAGE and item.get("usage") == USAGE
        ]
    except Exception:
        return None
    return matches[0] if len(matches) == 1 else None


def is_connected() -> bool:
    return find_device() is not None


class M210:
    """One short-lived session.  Always used as a context manager."""

    def __init__(self) -> None:
        info = find_device()
        if info is None:
            raise DeviceError(f"{PRODUCT_NAME} not found. Check that it is plugged in.")
        self._device = hid.device()
        try:
            self._device.open_path(info["path"])
        except Exception as error:  # pragma: no cover - depends on OS state
            raise DeviceError(f"Could not open the mouse: {error}") from error

    def __enter__(self) -> "M210":
        self._verify_protocol()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._device.close()
        except Exception:
            pass

    def _query(self, cmd: int, offset: int = 0, size: int = 0, data: bytes | None = None) -> bytes:
        if cmd not in ALLOWED_COMMANDS:
            raise DeviceError(f"Command 0x{cmd:02X} is not permitted by this build.")
        if not 0 <= size <= MAX_PAYLOAD:
            raise DeviceError(f"Payload size {size} out of range.")
        if data is not None and len(data) != size:
            raise DeviceError("Payload length does not match declared size.")

        request = bytearray(REPORT_LENGTH)
        request[0] = REPORT_ID
        request[3] = cmd
        request[4] = size
        request[5] = offset & 0xFF
        request[6] = (offset >> 8) & 0xFF
        if data:
            request[8 : 8 + size] = data
        checksum = sum(request[3:]) & 0xFFFF
        request[1] = checksum & 0xFF
        request[2] = checksum >> 8

        try:
            written = self._device.write(request)
            if written != REPORT_LENGTH:
                raise DeviceError(f"Short write ({written}/{REPORT_LENGTH}).")
            reply = bytes(self._device.read(REPORT_LENGTH, 1500))
        except DeviceError:
            raise
        except Exception as error:
            raise DeviceError(f"Communication failed: {error}") from error

        if len(reply) != REPORT_LENGTH:
            raise DeviceError("The mouse did not reply. Try reconnecting it.")
        if reply[0] != REPORT_ID or reply[1:3] != request[1:3]:
            raise DeviceError("Reply checksum mismatch.")
        if reply[3] != cmd or reply[5:7] != request[5:7]:
            raise DeviceError("Reply did not match the request.")
        if reply[7] != 0:
            raise DeviceError(f"The mouse rejected the request (status 0x{reply[7]:02X}).")
        return reply[8 : 8 + reply[4]]

    def _verify_protocol(self) -> None:
        signature = self._query(CMD_READ_CAPABILITIES, size=7)
        if signature[:2] != b"\xAA\x55":
            raise DeviceError("This device did not answer with the expected protocol signature.")

    def _profile_offset(self) -> int:
        value = self._query(CMD_READ_CONFIG, offset=0, size=1)
        if len(value) != 1 or value[0] > 2:
            raise DeviceError("Could not determine the active profile.")
        return 1 + value[0] * 0x40

    def read_state(self) -> LightingState:
        prefix = self._query(CMD_READ_CONFIG, offset=self._profile_offset(), size=PREFIX_LENGTH)
        if len(prefix) != PREFIX_LENGTH:
            raise DeviceError("Short read of the lighting configuration.")
        return LightingState(prefix)

    def apply_static(self, rgb: tuple[int, int, int], brightness: int) -> LightingState:
        if not all(0 <= channel <= 255 for channel in rgb):
            raise DeviceError("Colour channels must be 0-255.")
        if not 0 <= brightness <= MAX_BRIGHTNESS:
            raise DeviceError(f"Brightness must be 0-{MAX_BRIGHTNESS}.")

        offset = self._profile_offset()
        current = self._query(CMD_READ_CONFIG, offset=offset, size=PREFIX_LENGTH)
        if len(current) != PREFIX_LENGTH:
            raise DeviceError("Short read before writing.")

        updated = bytearray(current)
        updated[0] = MODE_STATIC
        updated[1] = brightness
        updated[5:8] = bytes(rgb)
        updated[8] = 0
        payload = bytes(updated)

        if payload == current:
            return LightingState(current)

        began = False
        try:
            self._query(CMD_BEGIN_CONFIGURE)
            began = True
            # Offset is the profile base, and size is capped at the 9-byte
            # lighting prefix, so nothing past byte 8 can ever be written.
            self._query(CMD_WRITE_CONFIG, offset=offset, size=PREFIX_LENGTH, data=payload)
        finally:
            if began:
                self._query(CMD_END_CONFIGURE)

        verified = self._query(CMD_READ_CONFIG, offset=offset, size=PREFIX_LENGTH)
        if verified != payload:
            raise DeviceError("The mouse did not store the new colour. Nothing else was changed.")
        return LightingState(verified)


def read_state() -> LightingState:
    with M210() as mouse:
        return mouse.read_state()


def apply_static(rgb: tuple[int, int, int], brightness: int) -> LightingState:
    with M210() as mouse:
        return mouse.apply_static(rgb, brightness)
