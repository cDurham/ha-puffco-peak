"""Button platform for Puffco Peak: start, stop, boosts, power off."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .device import ADD_TEMP_C, ADD_TIME_SEC
from .entity import PuffcoEntity


async def _start(coordinator: PuffcoCoordinator) -> None:
    # Check the selection before connecting, so an unknown one is refused
    # without touching the device.
    index = coordinator.selected_profile()
    await coordinator.async_command(lambda d: d.start_heat_cycle(index))


@dataclass(frozen=True, kw_only=True)
class PuffcoButtonDescription(ButtonEntityDescription):
    """Describes a Puffco button and what pressing it does."""

    press_fn: Callable[[PuffcoCoordinator], Awaitable[None]]


BUTTONS: tuple[PuffcoButtonDescription, ...] = (
    PuffcoButtonDescription(
        key="start",
        translation_key="start",
        press_fn=_start,
    ),
    PuffcoButtonDescription(
        key="stop",
        translation_key="stop",
        press_fn=lambda c: c.async_command(lambda d: d.stop_heat_cycle()),
    ),
    PuffcoButtonDescription(
        key="add_temperature",
        translation_key="add_temperature",
        press_fn=lambda c: c.async_command(lambda d: d.add_temperature_c(ADD_TEMP_C)),
    ),
    PuffcoButtonDescription(
        key="add_time",
        translation_key="add_time",
        press_fn=lambda c: c.async_command(lambda d: d.add_time_seconds(ADD_TIME_SEC)),
    ),
    PuffcoButtonDescription(
        key="power_off",
        translation_key="power_off",
        press_fn=lambda c: c.async_command(lambda d: d.power_off()),
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
        await self.entity_description.press_fn(self.coordinator)
