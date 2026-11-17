"""High-level Puffco Peak Pro API on top of the LORAX transport.

Value codecs and command sequences mirror the official app (Puffco Connect
3.13.47), ported from puffco-peak-control js/device.js. Peak Pro family
(pikachoid) only for now; the Proxy (toad) uses int32/scale codecs and can be
added later.
"""

from __future__ import annotations

import struct
from typing import Any

from .fields import FIELDS


# Mode command values written to /p/app/mc (modeCmd).
class Mode:
    MASTER_OFF = 0
    SLEEP = 1
    IDLE = 2
    HEAT_CYCLE_START = 7
    HEAT_CYCLE_ABORT = 8


# LumaAnimation values written in byte 4 of an RGB color. 0 keeps the animation
# the device already has, which is what the app sends by default.
class Luma:
    PRESERVE = 0
    STEADY = 1
    BREATHING = 5
    RISING = 6
    CIRCLING = 7
    CIRCLING_SLOW = 21


# Limits the app enforces in its UI.
TEMP_MIN_F = 400
TEMP_MAX_F = 644
DURATION_MIN = 15
DURATION_MAX = 120
BRIGHTNESS_MIN = 38.25
BRIGHTNESS_MAX = 255
PROFILE_NAME_MAX = 31
DEVICE_NAME_MAX = 29
ADD_TEMP_C = 5.0
ADD_TIME_SEC = 10.0
LANTERN_SECONDS = 7200.0


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def decode(field: dict, data: bytes) -> Any:
    """Decode raw bytes for a field per its wire codec."""
    ftype = field["type"]
    if ftype == "float32":
        return struct.unpack_from("<f", data, 0)[0]
    if ftype == "int32":
        val = struct.unpack_from("<i", data, 0)[0]
        div = field.get("div")
        return val / div if div else val
    if ftype == "uint32":
        return struct.unpack_from("<I", data, 0)[0]
    if ftype == "uint8":
        return data[0]
    if ftype == "int8":
        return struct.unpack_from("<b", data, 0)[0]
    if ftype == "string":
        return data.split(b"\x00", 1)[0].decode("latin1")
    if ftype == "rgbtApi2":
        return decode_color(data)
    return bytes(data)


def encode(field: dict, value: Any) -> bytes:
    """Encode a value to raw bytes for a field per its wire codec."""
    ftype = field["type"]
    if ftype == "float32":
        return struct.pack("<f", float(value))
    if ftype == "int32":
        div = field.get("div")
        return struct.pack("<i", round(value * div) if div else int(value))
    if ftype == "uint32":
        return struct.pack("<I", int(value) & 0xFFFFFFFF)
    if ftype == "uint8":
        return bytes([int(value) & 0xFF])
    if ftype == "int8":
        return struct.pack("<b", int(value))
    if ftype == "string":
        return str(value).encode("latin1")
    if ftype == "rgbtApi2":
        return encode_color(value)
    return bytes(value)


def decode_color(data: bytes) -> dict:
    """RgbtColor (8 bytes). Byte 3 selects the kind: 0 RGB, 1 table, 255 none."""
    if len(data) != 8:
        return {"kind": "unsupported", "raw": bytes(data)}
    kind = {0: "rgb", 1: "table", 255: "none"}.get(data[3], "unknown")
    return {"kind": kind, "r": data[0], "g": data[1], "b": data[2], "luma_anim": data[4]}


def encode_color(value: dict) -> bytes:
    """Encode an RGB color: r, g, b, 0, lumaAnim, 0, 0, 0."""
    r = value.get("r", 0) & 0xFF
    g = value.get("g", 0) & 0xFF
    b = value.get("b", 0) & 0xFF
    luma = value.get("luma_anim", Luma.PRESERVE) & 0xFF
    return bytes([r, g, b, 0, luma, 0, 0, 0])


class PuffcoDevice:
    """Command sequences and typed reads over a LoraxClient."""

    def __init__(self, lorax: Any) -> None:
        self._lorax = lorax
        self.fields = FIELDS

    def has(self, name: str) -> bool:
        return name in self.fields

    def _field(self, name: str) -> dict:
        field = self.fields.get(name)
        if field is None:
            raise KeyError(f"{name} is not available on this device")
        return field

    async def read(self, name: str) -> Any:
        field = self._field(name)
        return decode(field, await self._lorax.read_short(field["path"]))

    async def try_read(self, name: str) -> Any:
        if not self.has(name):
            return None
        try:
            return await self.read(name)
        except Exception:  # noqa: BLE001 - a missing/unreadable field is just None
            return None

    async def write(self, name: str, value: Any) -> None:
        field = self._field(name)
        await self._lorax.write_short(field["path"], encode(field, value))

    def enum_label(self, name: str, value: Any) -> str | None:
        field = self.fields.get(name)
        if not field or "enum" not in field:
            return None
        return field["enum"].get(int(value))

    # -- heat cycle (app's startDabbing / stopDabbing) ----------------------
    async def start_heat_cycle(self, profile_index: int) -> None:
        await self.write("selectedHeatCycle", profile_index)
        await self.write("modeCmd", Mode.HEAT_CYCLE_START)

    async def stop_heat_cycle(self) -> None:
        await self.write("modeCmd", Mode.HEAT_CYCLE_ABORT)
        await self.write("modeCmd", Mode.IDLE)

    async def add_temperature_c(self, delta_c: float) -> None:
        await self.write("tempOverride", delta_c)

    async def add_time_seconds(self, seconds: float) -> None:
        await self.write("timeOverride", seconds)

    # -- lantern / power ----------------------------------------------------
    async def lantern_on(self) -> None:
        await self.write("lanternStart", 1)
        await self.write("lanternTime", LANTERN_SECONDS)

    async def lantern_off(self) -> None:
        await self.write("lanternStart", 0)

    async def power_off(self) -> None:
        await self.write("lanternStart", 0)
        await self.write("modeCmd", Mode.MASTER_OFF)

    async def set_brightness(self, value: float) -> None:
        clamped = round(min(BRIGHTNESS_MAX, max(BRIGHTNESS_MIN, value)))
        await self.write("ledBrightness", bytes([clamped, clamped, clamped, clamped]))

    async def set_stealth(self, on: bool) -> None:
        await self.write("stealthMode", 1 if on else 0)
