"""Text platform for Puffco Peak: heat profile names."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PROFILE_COUNT
from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .device import PROFILE_NAME_MAX
from .entity import PuffcoEntity
from .fields import FIELDS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Puffco profile-name text entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        PuffcoProfileName(coordinator, i)
        for i in range(PROFILE_COUNT)
        if f"heatCycle{i}Name" in FIELDS
    )


class PuffcoProfileName(PuffcoEntity, TextEntity):
    """The editable name of one heat profile."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = PROFILE_NAME_MAX

    def __init__(self, coordinator: PuffcoCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_name = f"Profile {index + 1} name"
        self._attr_unique_id = f"{coordinator.address}_profile{index}_name"

    @property
    def native_value(self) -> str | None:
        if self._index < len(self.coordinator.profiles):
            return self.coordinator.profiles[self._index].get("name")
        return None

    async def async_set_value(self, value: str) -> None:
        text = value[:PROFILE_NAME_MAX]
        await self.coordinator.async_command(
            lambda d: d.write(f"heatCycle{self._index}Name", text)
        )
        await self.coordinator.async_reload_profiles()
