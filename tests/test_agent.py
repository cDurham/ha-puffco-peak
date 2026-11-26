import asyncio
from contextlib import nullcontext

import pytest
from puffco_peak import agent

HELD = ["RegisterAgent", "body", "UnregisterAgent"]


async def enter(events, error):
    async with agent.pairing_agent():
        events.append("body")
        if error:
            raise error


@pytest.mark.parametrize(
    ("fail", "error", "raises", "expected"),
    [
        pytest.param({}, None, nullcontext(), HELD, id="block"),
        pytest.param({}, ValueError(), pytest.raises(ValueError), HELD, id="block raises"),
        pytest.param({"connect": OSError()}, None, nullcontext(), ["body"], id="no bus"),
        pytest.param(
            {"UnregisterAgent": asyncio.CancelledError()},
            None,
            pytest.raises(asyncio.CancelledError),
            HELD,
            id="unregister cancelled",
        ),
    ],
)
def test_pairing_agent(events, bus, fail, error, raises, expected):
    bus.fail.update(fail)
    with raises:
        asyncio.run(enter(events, error))
    assert events == expected
    assert bus.closed


def test_dbus_is_available():
    assert agent.MessageBus is not None
