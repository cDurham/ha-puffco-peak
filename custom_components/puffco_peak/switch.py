"""Switch platform for Puffco Peak: stealth mode."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .entity import PuffcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Puffco stealth switch."""
    async_add_entities([PuffcoStealthSwitch(entry.runtime_data)])


class PuffcoStealthSwitch(PuffcoEntity, SwitchEntity):
    """Stealth mode (dims the device's lights)."""

    _attr_translation_key = "stealth"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PuffcoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_stealth"

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("stealthMode")
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(lambda d: d.set_stealth(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(lambda d: d.set_stealth(False))
