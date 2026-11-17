"""Config flow for Puffco Peak."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_ADDRESS, PUFFCO_COMPANY_ID, SERVICE_UUID


def _is_puffco(info: BluetoothServiceInfoBleak) -> bool:
    """Whether an advert looks like a Puffco LORAX device."""
    return (
        SERVICE_UUID in info.service_uuids
        or PUFFCO_COMPANY_ID in info.manufacturer_data
    )


class PuffcoConfigFlow(ConfigFlow, domain="puffco_peak"):
    """Handle a config flow for Puffco Peak."""

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or "Puffco Peak"
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device."""
        assert self._discovery is not None
        if user_input is not None:
            return self._create(self._discovery.address, self._discovery.name)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._discovery.name or "Puffco Peak"},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a device from a manual scan."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self._create(address, self._discovered.get(address))

        current = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in current or not _is_puffco(info):
                continue
            self._discovered[info.address] = info.name or info.address

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{name} ({address})"
                            for address, name in self._discovered.items()
                        }
                    )
                }
            ),
        )

    def _create(self, address: str, name: str | None) -> ConfigFlowResult:
        return self.async_create_entry(
            title=name or "Puffco Peak",
            data={CONF_ADDRESS: address},
        )
