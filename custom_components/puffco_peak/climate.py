"""Climate platform for Puffco Peak: target temperature and heat profiles."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTIVE_STATES
from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .device import PuffcoDevice, TEMP_MAX_F, TEMP_MIN_F, f_to_c
from .entity import PuffcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Puffco climate entity."""
    async_add_entities([PuffcoClimate(entry.runtime_data)])


class PuffcoClimate(PuffcoEntity, ClimateEntity):
    """The Peak's heater as a climate entity.

    The device heats to the selected profile's stored temperature; there is no
    separate live setpoint, so target temperature reads and edits that profile.
    """

    _attr_translation_key = "peak"
    _attr_name = None  # the device's own name
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_native_min_temp = round(f_to_c(TEMP_MIN_F))
    _attr_native_max_temp = round(f_to_c(TEMP_MAX_F))
    _attr_native_target_temperature_step = 1.0

    def __init__(self, coordinator: PuffcoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_climate"

    @property
    def _data(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    def _selected_index(self) -> int | None:
        idx = self._data.get("selectedHeatCycle")
        return idx if isinstance(idx, int) and 0 <= idx < len(self.coordinator.profiles) else None

    @property
    def native_current_temperature(self) -> float | None:
        return self._data.get("userHeaterTemp")

    @property
    def native_target_temperature(self) -> float | None:
        idx = self._selected_index()
        if idx is not None:
            temp = self.coordinator.profiles[idx].get("temp_c")
            if temp is not None:
                return temp
        return self._data.get("userHeaterTempCommand")

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._data.get("operatingState") in ACTIVE_STATES else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        return (
            HVACAction.HEATING
            if self._data.get("operatingState") in ACTIVE_STATES
            else HVACAction.IDLE
        )

    @property
    def preset_modes(self) -> list[str]:
        return [p["name"] for p in self.coordinator.profiles]

    @property
    def preset_mode(self) -> str | None:
        idx = self._selected_index()
        return self.coordinator.profiles[idx]["name"] if idx is not None else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.HEAT:
            idx = self._data.get("selectedHeatCycle")
            start = idx if isinstance(idx, int) and 0 <= idx <= 3 else 0
            await self.coordinator.async_command(lambda d: d.start_heat_cycle(start))
        else:
            await self.coordinator.async_command(lambda d: d.stop_heat_cycle())

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        for profile in self.coordinator.profiles:
            if profile["name"] == preset_mode:
                index = profile["index"]
                await self.coordinator.async_command(
                    lambda d: d.write("selectedHeatCycle", index)
                )
                return
        raise HomeAssistantError(f"Unknown heat profile: {preset_mode}")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        idx = self._selected_index()
        if idx is None:
            raise HomeAssistantError(
                "Select a heat profile first; the target temperature edits the "
                "selected profile."
            )
        value = max(
            self._attr_native_min_temp,
            min(self._attr_native_max_temp, float(temperature)),
        )

        async def _write(device: PuffcoDevice) -> None:
            await device.write(f"heatCycle{idx}Temp", value)

        await self.coordinator.async_command(_write)
        await self.coordinator.async_reload_profiles()
