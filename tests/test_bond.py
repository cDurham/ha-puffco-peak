import asyncio
import re

import pytest
from bleak.exc import BleakError
from puffco_peak import coordinator

HANG = object()
AUTH_FAILED = BleakError("org.bluez.Error.AuthenticationFailed")
UNENCRYPTED = BleakError("Insufficient Encryption")
ATTEMPTS = coordinator._BOND_READ_ATTEMPTS


async def play(step):
    if step is HANG:
        await asyncio.Event().wait()
    elif step is not None:
        raise step


class Client:
    def __init__(self, events, pair, reads):
        self.events = events
        self.pair_step = pair
        self.read_steps = list(reads)
        self.is_connected = True

    async def pair(self):
        self.events.append("pair")
        self.is_connected = not isinstance(self.pair_step, BleakError)
        await play(self.pair_step)

    async def read(self):
        self.events.append("read")
        await play(self.read_steps.pop(0) if self.read_steps else None)
        return bytes(4)

    async def disconnect(self):
        self.events.append("disconnect")


@pytest.fixture(autouse=True)
def harness(monkeypatch, bus):
    monkeypatch.setattr(coordinator, "read_bond_characteristic", Client.read)
    monkeypatch.setattr(coordinator, "_PAIR_TIMEOUT", 0.05)
    monkeypatch.setattr(coordinator, "_BOND_READ_TIMEOUT", 0.05)
    monkeypatch.setattr(coordinator, "_BOND_READ_DELAY", 0)


async def bond(events, pair, reads):
    client = Client(events, pair, reads)
    async with asyncio.timeout(1):
        await object.__new__(coordinator.PuffcoCoordinator)._bond(client)


def agent_held(reads):
    return ["RegisterAgent", "pair", *["read"] * reads, "UnregisterAgent"]


@pytest.mark.parametrize(
    ("pair", "reads"),
    [
        pytest.param(None, [], id="first read"),
        pytest.param(HANG, [], id="Pair reply withheld"),
        pytest.param(None, [UNENCRYPTED] * 2, id="read retried"),
    ],
)
def test_bonds(events, pair, reads):
    asyncio.run(bond(events, pair, reads))
    assert events == agent_held(len(reads) + 1)


@pytest.mark.parametrize(
    ("pair", "reads", "reason"),
    [
        pytest.param(None, [HANG], "last read: no reply within", id="stale bond"),
        pytest.param(AUTH_FAILED, [], f"{AUTH_FAILED}; last read: link dropped", id="pair refused"),
        pytest.param(None, [UNENCRYPTED] * ATTEMPTS, f"last read: {UNENCRYPTED}", id="exhausted"),
    ],
)
def test_reports_why_bonding_failed(events, pair, reads, reason):
    with pytest.raises(coordinator.PuffcoNotBondedError, match=re.escape(reason)):
        asyncio.run(bond(events, pair, reads))
    assert events == [*agent_held(len(reads)), "disconnect"]


def test_cancel_during_read_unregisters(events):
    async def cancel_mid_read():
        task = asyncio.create_task(bond(events, None, [HANG]))
        while "read" not in events:
            await asyncio.sleep(0)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancel_mid_read())
    assert events == agent_held(1)
