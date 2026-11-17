"""Light platform for Puffco Peak: the mood lantern."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_RGB_COLOR, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .device import Luma, PuffcoDevice
from .entity import PuffcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Puffco lantern light."""
    async_add_entities([PuffcoLantern(entry.runtime_data)])


class PuffcoLantern(PuffcoEntity, LightEntity):
    """The Peak's mood light (lantern)."""

    _attr_translation_key = "lantern"
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, coordinator: PuffcoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_lantern"

    @property
    def is_on(self) -> bool | None:
        remaining = (self.coordinator.data or {}).get("lanternRemainingTime")
        return remaining > 0 if isinstance(remaining, (int, float)) else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        color = (self.coordinator.data or {}).get("lanternColorApi2")
        if isinstance(color, dict) and color.get("kind") == "rgb":
            return (color["r"], color["g"], color["b"])
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        rgb = kwargs.get(ATTR_RGB_COLOR)

        async def _on(device: PuffcoDevice) -> None:
            if rgb is not None:
                await device.write(
                    "lanternColorApi2",
                    {"r": rgb[0], "g": rgb[1], "b": rgb[2], "luma_anim": Luma.STEADY},
                )
            await device.lantern_on()

        await self.coordinator.async_command(_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(lambda d: d.lantern_off())
