"""Base entity for Puffco Peak."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import PuffcoCoordinator


class PuffcoEntity(CoordinatorEntity[PuffcoCoordinator]):
    """Common device info and availability for Puffco entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PuffcoCoordinator) -> None:
        super().__init__(coordinator)
        info = coordinator.device_info
        hw = info.get("hardwareRevision")
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            identifiers={(DOMAIN, coordinator.address)},
            manufacturer=MANUFACTURER,
            model="Peak Pro",
            name=info.get("deviceName") or "Puffco Peak",
            serial_number=info.get("serialNumber"),
            sw_version=info.get("gitHash"),
            hw_version=str(hw) if hw is not None else None,
        )
