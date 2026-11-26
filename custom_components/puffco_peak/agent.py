"""Temporary BlueZ pairing agent: BlueZ won't pair without one, and HAOS registers none."""

# ruff: noqa: N802, F821

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

_LOGGER = logging.getLogger(__name__)

AGENT_PATH = "/org/homeassistant/puffco_peak/pairing_agent"
_DBUS_TIMEOUT = 5.0

try:
    from dbus_fast import BusType, DBusError, Message, MessageType
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method

    from bleak.backends.bluezdbus.utils import get_dbus_authenticator
except ImportError:
    MessageBus = None
else:

    def _rejected() -> DBusError:
        return DBusError("org.bluez.Error.Rejected", "Rejected by the Puffco Peak integration")

    class _RejectingAgent(ServiceInterface):
        """Only has to exist: BlueZ auto-accepts pairings the client itself requested."""

        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):
            pass

        @method()
        def Cancel(self):
            pass

        @method()
        def RequestPinCode(self, device: "o") -> "s":
            raise _rejected()

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            raise _rejected()

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            raise _rejected()

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            raise _rejected()

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            raise _rejected()

        @method()
        def RequestAuthorization(self, device: "o"):
            raise _rejected()

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            raise _rejected()


async def _agent_manager_call(bus: MessageBus, member: str, signature: str, body: list) -> None:
    reply = await asyncio.wait_for(
        bus.call(
            Message(
                destination="org.bluez",
                path="/org/bluez",
                interface="org.bluez.AgentManager1",
                member=member,
                signature=signature,
                body=body,
            )
        ),
        _DBUS_TIMEOUT,
    )
    if reply is not None and reply.message_type == MessageType.ERROR:
        raise DBusError(reply.error_name, str(reply.body))


@contextlib.asynccontextmanager
async def pairing_agent() -> AsyncIterator[None]:
    """Keep the agent registered for the duration of the block."""
    if MessageBus is None:
        yield
        return

    bus: MessageBus | None = None
    registered = False
    try:
        try:
            bus = MessageBus(bus_type=BusType.SYSTEM, auth=get_dbus_authenticator())
            await asyncio.wait_for(bus.connect(), _DBUS_TIMEOUT)
            bus.export(AGENT_PATH, _RejectingAgent())
            await _agent_manager_call(bus, "RegisterAgent", "os", [AGENT_PATH, "NoInputNoOutput"])
            registered = True
            _LOGGER.debug("Registered temporary BlueZ pairing agent")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not register a BlueZ pairing agent, so bonding may fail: %s", err)
        yield
    finally:
        if bus is not None:
            try:
                if registered:
                    await _agent_manager_call(bus, "UnregisterAgent", "o", [AGENT_PATH])
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("UnregisterAgent failed: %s", err)
            finally:
                bus.disconnect()
