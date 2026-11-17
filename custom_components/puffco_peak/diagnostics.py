"""Diagnostics for Puffco Peak."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import PuffcoConfigEntry

_REDACT = {"serialNumber", "euid"}


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    return {k: ("**REDACTED**" if k in _REDACT and v else v) for k, v in data.items()}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PuffcoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "device_info": _redact(coordinator.device_info),
        "profiles": coordinator.profiles,
        "data": coordinator.data,
        "update_interval": str(coordinator.update_interval),
        "connected": coordinator.last_update_success,
    }
