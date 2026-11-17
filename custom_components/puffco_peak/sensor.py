"""Sensor platform for Puffco Peak."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PuffcoConfigEntry, PuffcoCoordinator
from .entity import PuffcoEntity
from .fields import FIELDS


def _enum_options(field_name: str) -> list[str]:
    return list(FIELDS[field_name]["enum"].values())


def _enum_label(field_name: str, value: Any) -> str | None:
    if value is None:
        return None
    return FIELDS[field_name]["enum"].get(int(value))


@dataclass(frozen=True, kw_only=True)
class PuffcoSensorDescription(SensorEntityDescription):
    """Describes a Puffco sensor, with how to pull its value from poll data."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[PuffcoSensorDescription, ...] = (
    PuffcoSensorDescription(
        key="current_temperature",
        translation_key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _round(d.get("userHeaterTemp")),
    ),
    PuffcoSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=0,
        value_fn=lambda d: _round(d.get("userHeaterTempCommand")),
    ),
    PuffcoSensorDescription(
        key="operating_state",
        translation_key="operating_state",
        device_class=SensorDeviceClass.ENUM,
        options=_enum_options("operatingState"),
        value_fn=lambda d: _enum_label("operatingState", d.get("operatingState")),
    ),
    PuffcoSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _round(d.get("battSoc")),
    ),
    PuffcoSensorDescription(
        key="charge_state",
        translation_key="charge_state",
        device_class=SensorDeviceClass.ENUM,
        options=_enum_options("battChargeState"),
        value_fn=lambda d: _enum_label("battChargeState", d.get("battChargeState")),
    ),
    PuffcoSensorDescription(
        key="charge_source",
        translation_key="charge_source",
        device_class=SensorDeviceClass.ENUM,
        options=_enum_options("battChargeSource"),
        value_fn=lambda d: _enum_label("battChargeSource", d.get("battChargeSource")),
    ),
    PuffcoSensorDescription(
        key="dabs_remaining",
        translation_key="dabs_remaining",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _round(d.get("approxDabsRemaining")),
    ),
    PuffcoSensorDescription(
        key="total_heat_cycles",
        translation_key="total_heat_cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _int(d.get("totalHeatCycles")),
    ),
    PuffcoSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("battVolts"),
    ),
    PuffcoSensorDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("battTemp"),
    ),
)


def _round(value: Any) -> float | None:
    return round(value, 1) if isinstance(value, (int, float)) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PuffcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Puffco sensors."""
    coordinator = entry.runtime_data
    async_add_entities(PuffcoSensor(coordinator, desc) for desc in SENSORS)


class PuffcoSensor(PuffcoEntity, SensorEntity):
    """A single polled Puffco value."""

    entity_description: PuffcoSensorDescription

    def __init__(
        self, coordinator: PuffcoCoordinator, description: PuffcoSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
