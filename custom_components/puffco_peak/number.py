"""Number platform for Puffco Peak: profile editing and LED brightness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PROFILE_COUNT
from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .device import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    DURATION_MAX,
    DURATION_MIN,
    PuffcoDevice,
    TEMP_MAX_F,
    TEMP_MIN_F,
    f_to_c,
)
from .entity import PuffcoEntity
from .fields import FIELDS

_MIN_C = round(f_to_c(TEMP_MIN_F))
_MAX_C = round(f_to_c(TEMP_MAX_F))
# LED brightness is 38.25..255 on the wire; expose it as a friendly percentage.
_BRIGHTNESS_PCT_MIN = round(BRIGHTNESS_MIN / BRIGHTNESS_MAX * 100)


@dataclass(frozen=True, kw_only=True)
class ProfileNumberSpec:
    """A per-profile editable number."""

    suffix: str  # heatCycle{i}<suffix> field name
    key: str  # coordinator.profiles[i][key]
    label: str  # entity name tail
    native_min_value: float
    native_max_value: float
    native_step: float
    unit: str | None = None
    device_class: NumberDeviceClass | None = None
    enabled_default: bool = True


PROFILE_NUMBERS: tuple[ProfileNumberSpec, ...] = (
    ProfileNumberSpec(
        suffix="Temp",
        key="temp_c",
        label="temperature",
        native_min_value=_MIN_C,
        native_max_value=_MAX_C,
        native_step=1,
        unit=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
    ),
    ProfileNumberSpec(
        suffix="Time",
        key="time_s",
        label="duration",
        native_min_value=DURATION_MIN,
        native_max_value=DURATION_MAX,
        native_step=1,
        unit=UnitOfTime.SECONDS,
        device_class=NumberDeviceClass.DURATION,
    ),
    ProfileNumberSpec(
        suffix="BoostTempDelta",
        key="boost_temp_c",
        label="boost temperature",
        native_min_value=0,
        native_max_value=20,
        native_step=0.5,
        unit=UnitOfTemperature.CELSIUS,
        enabled_default=False,
    ),
    ProfileNumberSpec(
        suffix="BoostTimeDelta",
        key="boost_time_s",
        label="boost time",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        unit=UnitOfTime.SECONDS,
        device_class=NumberDeviceClass.DURATION,
        enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Puffco number entities."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    for i in range(PROFILE_COUNT):
        for spec in PROFILE_NUMBERS:
            if f"heatCycle{i}{spec.suffix}" in FIELDS:
                entities.append(PuffcoProfileNumber(coordinator, i, spec))
    if "ledBrightness" in FIELDS:
        entities.append(PuffcoBrightnessNumber(coordinator))
    async_add_entities(entities)


class PuffcoProfileNumber(PuffcoEntity, NumberEntity):
    """One editable field of one heat profile."""

    def __init__(
        self, coordinator: PuffcoCoordinator, index: int, spec: ProfileNumberSpec
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._spec = spec
        self._attr_name = f"Profile {index + 1} {spec.label}"
        self._attr_unique_id = f"{coordinator.address}_profile{index}_{spec.key}"
        self._attr_native_min_value = spec.native_min_value
        self._attr_native_max_value = spec.native_max_value
        self._attr_native_step = spec.native_step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_entity_registry_enabled_default = spec.enabled_default

    @property
    def native_value(self) -> float | None:
        if self._index < len(self.coordinator.profiles):
            return self.coordinator.profiles[self._index].get(self._spec.key)
        return None

    async def async_set_native_value(self, value: float) -> None:
        field = f"heatCycle{self._index}{self._spec.suffix}"
        await self.coordinator.async_command(lambda d: d.write(field, value))
        await self.coordinator.async_reload_profiles()


class PuffcoBrightnessNumber(PuffcoEntity, NumberEntity):
    """The device LED brightness, as a percentage."""

    _attr_translation_key = "brightness"
    _attr_native_min_value = _BRIGHTNESS_PCT_MIN
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PuffcoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_brightness"

    @property
    def native_value(self) -> float | None:
        raw = (self.coordinator.data or {}).get("ledBrightness")
        if isinstance(raw, (bytes, bytearray)) and raw:
            return round(raw[0] / BRIGHTNESS_MAX * 100)
        return None

    async def async_set_native_value(self, value: float) -> None:
        raw = value / 100 * BRIGHTNESS_MAX

        async def _set(device: PuffcoDevice) -> None:
            await device.set_brightness(raw)

        await self.coordinator.async_command(_set)
