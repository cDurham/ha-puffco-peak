"""A temporary BlueZ pairing agent, so the Peak can bond on Home Assistant OS.

BlueZ refuses to pair while no pairing agent is registered (bluetoothd logs
"No agent available"), and Home Assistant OS registers none. Pairing from an
interactive bluetoothctl works only because bluetoothctl registers its own.

So while the integration bonds with the Peak, it registers an agent that only
has to exist (references are to BlueZ's src/ directory):

- The first agent registered becomes the default (agent_create() in agent.c),
  and Pair() uses the default when the caller has no agent of its own
  (agent_get()). bleak calls Pair() from its own D-Bus connection, which has
  none.
- For a pairing the client asked for, BlueZ accepts a Just Works request
  itself and never asks the agent (device_confirm_passkey() in device.c).
  So the agent declines anything it is asked.

If another agent is registered already (a desktop's, or bluetoothctl's), that
one stays the default and handles pairing as before; ours waits behind it.
"""

# The agent's string annotations are D-Bus signatures, not Python names.
# ruff: noqa: F821

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from typing import Any

_LOGGER = logging.getLogger(__name__)

AGENT_PATH = "/org/homeassistant/puffco_peak/pairing_agent"
CAPABILITY = "NoInputNoOutput"
_DBUS_TIMEOUT = 5.0


def _rejecting_agent() -> Any:
    """Build the agent object (dbus_fast only loads on Linux, so import it here)."""
    from dbus_fast import DBusError
    from dbus_fast.service import ServiceInterface, method

    def declined() -> DBusError:
        return DBusError("org.bluez.Error.Rejected", "Declined by the Puffco Peak integration")

    class RejectingAgent(ServiceInterface):
        """org.bluez.Agent1 that declines every request BlueZ sends it."""

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
            raise declined()

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            raise declined()

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            raise declined()

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            raise declined()

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            raise declined()

        @method()
        def RequestAuthorization(self, device: "o"):
            raise declined()

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            raise declined()

    return RejectingAgent()


async def _register(bus: Any) -> None:
    from dbus_fast import DBusError, Message, MessageType

    reply = await asyncio.wait_for(
        bus.call(
            Message(
                destination="org.bluez",
                path="/org/bluez",
                interface="org.bluez.AgentManager1",
                member="RegisterAgent",
                signature="os",
                body=[AGENT_PATH, CAPABILITY],
            )
        ),
        _DBUS_TIMEOUT,
    )
    if reply is not None and reply.message_type == MessageType.ERROR:
        raise DBusError(reply.error_name, " ".join(map(str, reply.body)))


def _authenticator() -> Any:
    """Authenticate to the system bus the way bleak does."""
    try:
        from bleak.backends.bluezdbus.utils import get_dbus_authenticator
    except Exception:  # noqa: BLE001 - fall back to dbus_fast's default
        return None
    return get_dbus_authenticator()


@contextlib.asynccontextmanager
async def pairing_agent() -> AsyncIterator[None]:
    """Keep a pairing agent registered with BlueZ while the block runs.

    Does nothing off Linux. If the agent can't be registered, logs why and
    runs the block anyway, which then bonds as it would without the agent.
    """
    if not sys.platform.startswith("linux"):
        yield
        return

    bus: Any = None
    try:
        try:
            from dbus_fast import BusType
            from dbus_fast.aio import MessageBus

            bus = MessageBus(bus_type=BusType.SYSTEM, auth=_authenticator())
            await asyncio.wait_for(bus.connect(), _DBUS_TIMEOUT)
            bus.export(AGENT_PATH, _rejecting_agent())
            await _register(bus)
            _LOGGER.debug("Registered a temporary BlueZ pairing agent")
        except Exception as err:  # noqa: BLE001 - bond without the agent
            _LOGGER.warning(
                "Could not register a BlueZ pairing agent, so bonding may fail: %s", err
            )
        yield
    finally:
        # Closing the connection is enough to remove the agent: BlueZ watches
        # its owner and drops the agent, and its default, when that goes away
        # (agent_create() and agent_disconnect() in agent.c).
        if bus is not None:
            with contextlib.suppress(Exception):
                bus.disconnect()
