"""Connection + polling coordinator for a Puffco Peak Pro."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from time import monotonic
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from habluetooth import BaseHaRemoteScanner

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTIVE_STATES,
    DOMAIN,
    INFO_FIELDS,
    POLL_ACTIVE,
    POLL_FIELDS,
    POLL_IDLE,
    PROFILE_COUNT,
    PUSHED_FIELD_PERIODS,
)
from .bluez_agent import pairing_agent
from .device import PuffcoDevice
from .lorax import TRANSACT_ERRORS, LoraxClient, read_bond_characteristic

_LOGGER = logging.getLogger(__name__)

type PuffcoConfigEntry = ConfigEntry[PuffcoCoordinator]

# How many times to re-read the encrypted characteristic while waiting for the
# OS bond to complete before giving up with a "put it in pairing mode" message.
_BOND_READ_ATTEMPTS = 6
_BOND_READ_DELAY = 1.0
# How long to wait for BlueZ to answer Pair(). Longer than its own 30 s pairing
# timeout, so its error normally comes first; this only catches a missing reply.
_PAIR_TIMEOUT = 40.0
_STATE = "operatingState"


class PuffcoNotBondedError(UpdateFailed):
    """The link is up but not bonded/encrypted, so LORAX will not answer."""


class PuffcoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds one authenticated LORAX session and polls device state."""

    def __init__(
        self, hass: HomeAssistant, entry: PuffcoConfigEntry, address: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {address}",
            update_interval=POLL_IDLE,
            config_entry=entry,
            always_update=False,
        )
        self.address = address
        self.device_info: dict[str, Any] = {}
        self.profiles: list[dict[str, Any]] = []
        self._client: BleakClientWithServiceCache | None = None
        self._lorax: LoraxClient | None = None
        self._device: PuffcoDevice | None = None
        self._connect_lock = asyncio.Lock()
        self._full_read_at = 0.0

    @property
    def puffco(self) -> PuffcoDevice:
        """The device API for entity commands (raises if not connected)."""
        if self._device is None:
            raise UpdateFailed("Not connected to the Peak")
        return self._device

    # -- connection ---------------------------------------------------------
    @callback
    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s disconnected", self.address)
        if self._lorax is not None:
            self._lorax.fail_pending(BleakError("disconnected"))
        self._client = None
        self._lorax = None
        self._device = None

    def _connectable_ble_device(self):
        """Pick a BLE device, preferring a LOCAL adapter over a proxy.

        The Peak needs a bonded, encrypted link, and bonding over an ESPHome
        Bluetooth proxy is not supported (it fails with ESP_GATT_ERROR). When a
        local adapter can see the device, use it; only fall back to a proxy if
        that is the only route.
        """
        devices = bluetooth.async_scanner_devices_by_address(
            self.hass, self.address, connectable=True
        )
        if not devices:
            return None
        local = [d for d in devices if not isinstance(d.scanner, BaseHaRemoteScanner)]
        if not local:
            # Only a proxy can see it. Bonding does not work over a proxy, and a
            # proxy connect just hangs, so refuse rather than fall back to one.
            _LOGGER.debug(
                "%s is only reachable via a Bluetooth proxy (%d remote scanner(s)); "
                "a local adapter in range is required for bonding",
                self.address,
                len(devices),
            )
            return None
        chosen = local[0]
        _LOGGER.debug(
            "Routing to %s via local scanner %s",
            self.address,
            getattr(chosen.scanner, "name", "?"),
        )
        return chosen.ble_device

    async def _ensure_connected(self) -> PuffcoDevice:
        if self._device is not None and self._client is not None and self._client.is_connected:
            return self._device

        async with self._connect_lock:
            if (
                self._device is not None
                and self._client is not None
                and self._client.is_connected
            ):
                return self._device

            ble_device = self._connectable_ble_device()
            if ble_device is None:
                raise UpdateFailed(
                    f"Peak {self.address} is not in range of a local Bluetooth "
                    "adapter (bonding is not possible over a Bluetooth proxy)"
                )

            _LOGGER.debug("Connecting to %s", self.address)
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                disconnected_callback=self._on_disconnect,
            )

            await self._bond(client)

            lorax = LoraxClient(client)
            await lorax.start()
            try:
                await lorax.auth()
            except Exception as err:
                await self._disconnect_client(client)
                raise PuffcoNotBondedError(
                    "The Peak did not answer the unlock handshake. Put it in "
                    "pairing mode (hold the button until the logo glows blue) "
                    "and try again."
                ) from err

            device = PuffcoDevice(lorax)
            self._client = client
            self._lorax = lorax
            self._device = device

            if not self.device_info:
                await self._load_device_info(device)
            if not self.profiles:
                await self._load_profiles(device)
            await self._watch_pushed_fields(device)
            return device

    async def _watch_pushed_fields(self, device: PuffcoDevice) -> None:
        for name, period in PUSHED_FIELD_PERIODS.items():
            if not device.has(name):
                continue
            try:
                await device.watch(name, period, partial(self._on_push, name))
            except TRANSACT_ERRORS as err:
                _LOGGER.debug("No push for %s, polling for it instead: %s", name, err)

    def _state_is_pushed(self) -> bool:
        return self._device is not None and self._device.watching(_STATE)

    def _full_read_is_due(self) -> bool:
        return monotonic() - self._full_read_at >= POLL_IDLE.total_seconds()

    @callback
    def _on_push(self, name: str, value: Any) -> None:
        if not self.data or value == self.data.get(name):
            return
        self._full_read_at = 0.0
        self.update_interval = POLL_ACTIVE
        self.async_set_updated_data({**self.data, name: value})

    async def _bond(self, client: BleakClientWithServiceCache) -> None:
        """Make the OS bond with the Peak, then confirm the link is encrypted."""
        last_err: Exception | None = None
        # BlueZ won't pair without an agent, and Home Assistant OS has none.
        # Keep one through the reads too: a pairing that pair() started can
        # still be finishing when they begin.
        async with pairing_agent():
            try:
                async with asyncio.timeout(_PAIR_TIMEOUT):
                    await client.pair()
            except NotImplementedError:
                _LOGGER.debug("pair() not implemented on this backend; relying on read")
            except TimeoutError:
                _LOGGER.debug("pair() got no reply within %.0f s", _PAIR_TIMEOUT)
            except BleakError as err:
                # AlreadyExists / in-progress are fine; the read below is the truth.
                _LOGGER.debug("pair() reported: %s", err)

            for attempt in range(1, _BOND_READ_ATTEMPTS + 1):
                try:
                    value = await read_bond_characteristic(client)
                    _LOGGER.debug("Bond confirmed (OTA app version %s)", value.hex())
                    return
                except BleakError as err:
                    last_err = err
                    _LOGGER.debug("Bond read %d/%d: %s", attempt, _BOND_READ_ATTEMPTS, err)
                    await asyncio.sleep(_BOND_READ_DELAY)

        await self._disconnect_client(client)
        raise PuffcoNotBondedError(
            "Could not establish an encrypted (bonded) link. Put the Peak in "
            "pairing mode and make sure it is paired with the Home Assistant "
            f"host's Bluetooth adapter. Last error: {last_err}"
        )

    async def _disconnect_client(self, client: BleakClientWithServiceCache) -> None:
        try:
            await client.disconnect()
        except BleakError as err:
            _LOGGER.debug("Error during disconnect: %s", err)

    async def _load_device_info(self, device: PuffcoDevice) -> None:
        info: dict[str, Any] = {}
        for name in INFO_FIELDS:
            info[name] = await device.try_read(name)
        self.device_info = info
        _LOGGER.debug("Device info: %s", info)

    async def _load_profiles(self, device: PuffcoDevice) -> None:
        """Read the four heat profiles (name, temp, time, boosts) for editing."""
        profiles: list[dict[str, Any]] = []
        for i in range(PROFILE_COUNT):
            name = await device.try_read(f"heatCycle{i}Name")
            profiles.append(
                {
                    "index": i,
                    "name": name or f"Profile {i + 1}",
                    "temp_c": await device.try_read(f"heatCycle{i}Temp"),
                    "time_s": await device.try_read(f"heatCycle{i}Time"),
                    "boost_temp_c": await device.try_read(f"heatCycle{i}BoostTempDelta"),
                    "boost_time_s": await device.try_read(f"heatCycle{i}BoostTimeDelta"),
                }
            )
        self.profiles = profiles
        _LOGGER.debug("Profiles: %s", profiles)

    async def async_reload_profiles(self) -> None:
        """Re-read profiles after an edit and notify entities."""
        if self._device is not None:
            await self._load_profiles(self._device)
            self.async_update_listeners()

    def selected_profile(self) -> int:
        """The heat profile the device has selected, to start a heat cycle with.

        Raises ServiceValidationError when it is unknown: the read failed, or
        the device reports -1 ("Temp"), which is none of the four profiles.
        Guessing one would overwrite the user's selection and heat to a
        profile nobody picked.
        """
        idx = (self.data or {}).get("selectedHeatCycle")
        if isinstance(idx, int) and 0 <= idx < PROFILE_COUNT:
            return idx
        raise ServiceValidationError(
            "Select a heat profile first; the device has none of its four heat "
            "profiles selected."
        )

    async def async_command(
        self, action: Callable[[PuffcoDevice], Awaitable[None]]
    ) -> None:
        """Run a device command, making sure we are connected, then refresh."""
        device = await self._ensure_connected()
        await action(device)
        self._full_read_at = 0.0
        await self.async_request_refresh()

    # -- polling ------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self._poll()
        except Exception:
            self.update_interval = POLL_IDLE
            raise
        settled = (
            data.get(_STATE) not in ACTIVE_STATES
            and self._state_is_pushed()
            and not self._full_read_is_due()
        )
        self.update_interval = POLL_IDLE if settled else POLL_ACTIVE
        return data

    async def _poll(self) -> dict[str, Any]:
        device = await self._ensure_connected()
        pushed = device.pushed()
        state = pushed[_STATE] if _STATE in pushed else await device.try_read(_STATE)
        if (
            _STATE not in pushed
            and self.data
            and state not in ACTIVE_STATES
            and state == self.data.get(_STATE)
            and not self._full_read_is_due()
        ):
            return self.data

        self._full_read_at = monotonic()
        data: dict[str, Any] = {_STATE: state, **pushed}
        for name in POLL_FIELDS:
            if name not in data and device.has(name):
                data[name] = await device.try_read(name)
        data.update(device.pushed())
        return data

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self._client is not None:
            await self._disconnect_client(self._client)
        self._client = None
        self._lorax = None
        self._device = None
