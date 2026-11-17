"""Select platform for Puffco Peak: the active heat profile."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .entity import PuffcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Puffco profile selector."""
    async_add_entities([PuffcoProfileSelect(entry.runtime_data)])


class PuffcoProfileSelect(PuffcoEntity, SelectEntity):
    """Pick which of the four heat profiles is active."""

    _attr_translation_key = "profile"

    def __init__(self, coordinator: PuffcoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_profile"

    @property
    def options(self) -> list[str]:
        return [p["name"] for p in self.coordinator.profiles]

    @property
    def current_option(self) -> str | None:
        idx = (self.coordinator.data or {}).get("selectedHeatCycle")
        if isinstance(idx, int) and 0 <= idx < len(self.coordinator.profiles):
            return self.coordinator.profiles[idx]["name"]
        return None

    async def async_select_option(self, option: str) -> None:
        for profile in self.coordinator.profiles:
            if profile["name"] == option:
                index = profile["index"]
                await self.coordinator.async_command(
                    lambda d: d.write("selectedHeatCycle", index)
                )
                return
        raise HomeAssistantError(f"Unknown heat profile: {option}")
