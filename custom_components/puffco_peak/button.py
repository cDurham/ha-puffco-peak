"""Button platform for Puffco Peak: start, stop, boosts, power off."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .device import ADD_TEMP_C, ADD_TIME_SEC, PuffcoDevice
from .entity import PuffcoEntity


def _start_index(coordinator: PuffcoCoordinator) -> int:
    idx = (coordinator.data or {}).get("selectedHeatCycle")
    return idx if isinstance(idx, int) and 0 <= idx <= 3 else 0


@dataclass(frozen=True, kw_only=True)
class PuffcoButtonDescription(ButtonEntityDescription):
    """Describes a Puffco button and the command it runs."""

    press_fn: Callable[[PuffcoCoordinator, PuffcoDevice], Awaitable[None]]


BUTTONS: tuple[PuffcoButtonDescription, ...] = (
    PuffcoButtonDescription(
        key="start",
        translation_key="start",
        press_fn=lambda c, d: d.start_heat_cycle(_start_index(c)),
    ),
    PuffcoButtonDescription(
        key="stop",
        translation_key="stop",
        press_fn=lambda c, d: d.stop_heat_cycle(),
    ),
    PuffcoButtonDescription(
        key="add_temperature",
        translation_key="add_temperature",
        press_fn=lambda c, d: d.add_temperature_c(ADD_TEMP_C),
    ),
    PuffcoButtonDescription(
        key="add_time",
        translation_key="add_time",
        press_fn=lambda c, d: d.add_time_seconds(ADD_TIME_SEC),
    ),
    PuffcoButtonDescription(
        key="power_off",
        translation_key="power_off",
        press_fn=lambda c, d: d.power_off(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Puffco buttons."""
    coordinator = entry.runtime_data
    async_add_entities(PuffcoButton(coordinator, desc) for desc in BUTTONS)


class PuffcoButton(PuffcoEntity, ButtonEntity):
    """A one-shot Puffco command."""

    entity_description: PuffcoButtonDescription

    def __init__(
        self, coordinator: PuffcoCoordinator, description: PuffcoButtonDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    async def async_press(self) -> None:
        await self.coordinator.async_command(
            lambda d: self.entity_description.press_fn(self.coordinator, d)
        )
