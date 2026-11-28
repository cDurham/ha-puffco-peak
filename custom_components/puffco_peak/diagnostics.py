"""Diagnostics for Puffco Peak."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import PuffcoConfigEntry

# The device name carries a per-device number (shown as "Peak Pro" plus digits),
# so it identifies the Peak as much as the serial does.
_REDACT = {"serialNumber", "euid", "deviceName"}


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    return {k: ("**REDACTED**" if k in _REDACT and v else v) for k, v in data.items()}


def _json_safe(value: Any) -> Any:
    """Turn raw byte values into hex strings, which JSON can hold.

    Fields of type uint8Array (such as ledBrightness) decode to bytes, and so
    can an unrecognized color, at any depth.
    """
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PuffcoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return _json_safe(
        {
            "device_info": _redact(coordinator.device_info),
            "profiles": coordinator.profiles,
            "data": coordinator.data,
            "update_interval": str(coordinator.update_interval),
            "connected": coordinator.last_update_success,
        }
    )
