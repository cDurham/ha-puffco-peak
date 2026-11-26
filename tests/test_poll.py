import asyncio

import pytest
from puffco_peak import coordinator
from puffco_peak.const import POLL_ACTIVE, POLL_FIELDS, POLL_IDLE, PUSHED_FIELD_PERIODS

PUSHED = set(PUSHED_FIELD_PERIODS)
from puffco_peak.lorax import LoraxError

IDLE = 5
PREHEAT = 7
READY = 8
QI = 1
EFBIG = 27


class Device:
    def __init__(self, state):
        self.state = state
        self.reads = []
        self.during_read = {}
        self.refuses = set()
        self.watchers = {}
        self.values = {}

    def has(self, name):
        return True

    def current(self, name):
        return self.state if name == "operatingState" else 0

    async def try_read(self, name):
        self.reads.append(name)
        self.during_read.pop(name, lambda: None)()
        return self.current(name)

    async def watch(self, name, _period, on_value):
        if name in self.refuses:
            raise LoraxError(EFBIG, "EFBIG")
        self.watchers[name] = on_value
        self.push(name, self.current(name))

    def watching(self, name):
        return name in self.watchers

    def pushed(self):
        return {name: value for name, value in self.values.items() if self.watching(name)}

    def push(self, name, value):
        self.values[name] = value
        self.watchers[name](value)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(coordinator, "monotonic", clock)
    return clock


def polled(device, clock, pushed=False):
    c = coordinator.PuffcoCoordinator(None, None, "5C:02:72:00:00:00")
    c._device = device

    async def connected():
        if device is None:
            raise coordinator.UpdateFailed("out of range")
        return device

    c._ensure_connected = connected
    if device is not None:
        if pushed:
            asyncio.run(c._watch_pushed_fields(device))
        asyncio.run(c.async_request_refresh())
        device.reads.clear()
    return c


def poll(c, clock, after):
    clock.now += after
    asyncio.run(c.async_request_refresh())
    return c.data


def test_idle_peak_is_checked_at_the_active_cadence(clock):
    c = polled(Device(IDLE), clock)
    assert c.update_interval == POLL_ACTIVE


def test_heat_cycle_start_is_seen_on_the_next_check(clock):
    device = Device(IDLE)
    c = polled(device, clock)
    device.state = PREHEAT
    data = poll(c, clock, POLL_ACTIVE.total_seconds())
    assert data["operatingState"] == PREHEAT
    assert set(device.reads) == set(POLL_FIELDS)


def test_idle_check_reads_only_the_state(clock):
    device = Device(IDLE)
    c = polled(device, clock)
    before = c.data
    assert poll(c, clock, POLL_ACTIVE.total_seconds()) == before
    assert device.reads == ["operatingState"]


def test_idle_peak_is_fully_read_every_idle_interval(clock):
    device = Device(IDLE)
    c = polled(device, clock)
    poll(c, clock, POLL_IDLE.total_seconds())
    assert set(device.reads) == set(POLL_FIELDS)


def test_heat_cycle_is_fully_read_on_every_check(clock):
    device = Device(PREHEAT)
    c = polled(device, clock)
    poll(c, clock, POLL_ACTIVE.total_seconds())
    assert set(device.reads) == set(POLL_FIELDS)


def test_idle_peak_that_pushes_its_state_is_checked_at_the_idle_cadence(clock):
    c = polled(Device(IDLE), clock, pushed=True)
    assert c.update_interval == POLL_IDLE


def test_heat_cycle_is_checked_at_the_active_cadence_when_pushed(clock):
    c = polled(Device(PREHEAT), clock, pushed=True)
    assert c.update_interval == POLL_ACTIVE


def test_pushed_heat_cycle_start_is_published_at_once(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    device.push("operatingState", PREHEAT)
    assert [data["operatingState"] for data in c.published] == [PREHEAT]
    assert device.reads == []
    assert c.update_interval == POLL_ACTIVE


def test_pushed_change_is_followed_by_a_full_read(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    device.state = PREHEAT
    device.push("operatingState", PREHEAT)
    device.push("operatingState", IDLE)
    device.state = IDLE
    poll(c, clock, POLL_ACTIVE.total_seconds())
    assert set(device.reads) == set(POLL_FIELDS) - PUSHED
    assert c.update_interval == POLL_IDLE


def test_state_pushed_during_a_read_survives_that_read(clock):
    device = Device(PREHEAT)
    c = polled(device, clock, pushed=True)
    device.during_read["battSoc"] = lambda: device.push("operatingState", READY)
    data = poll(c, clock, POLL_ACTIVE.total_seconds())
    assert data["operatingState"] == READY


def test_pushed_state_is_not_read_again(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    poll(c, clock, POLL_IDLE.total_seconds())
    assert "operatingState" not in device.reads


def test_state_is_read_again_once_the_push_stops(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    device.watchers.clear()
    device.state = PREHEAT
    data = poll(c, clock, POLL_ACTIVE.total_seconds())
    assert data["operatingState"] == PREHEAT


def test_pushed_state_that_did_not_change_is_ignored(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    device.push("operatingState", IDLE)
    assert c.published == []


def test_state_pushed_before_the_first_read_is_ignored(clock):
    c = polled(None, clock, pushed=True)
    c._on_push("operatingState", PREHEAT)
    assert c.published == []


def test_wireless_charging_is_published_when_pushed(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    device.push("battChargeSource", QI)
    assert [data["battChargeSource"] for data in c.published] == [QI]
    assert device.reads == []


def test_pushed_charging_change_is_followed_by_a_full_read(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    device.push("battChargeSource", QI)
    data = poll(c, clock, POLL_ACTIVE.total_seconds())
    assert "battSoc" in device.reads
    assert "battChargeSource" not in device.reads
    assert data["battChargeSource"] == QI
    assert c.update_interval == POLL_IDLE


def test_charging_pushed_during_a_read_survives_that_read(clock):
    device = Device(PREHEAT)
    c = polled(device, clock, pushed=True)
    device.during_read["battSoc"] = lambda: device.push("battChargeSource", QI)
    data = poll(c, clock, POLL_ACTIVE.total_seconds())
    assert data["battChargeSource"] == QI


def test_state_is_still_checked_often_when_only_charging_is_pushed(clock):
    device = Device(IDLE)
    device.refuses = {"operatingState", "battChargeState"}
    c = polled(device, clock, pushed=True)
    device.push("battChargeSource", QI)
    device.state = PREHEAT
    data = poll(c, clock, POLL_ACTIVE.total_seconds())
    assert data["operatingState"] == PREHEAT
    assert c.update_interval == POLL_ACTIVE


def test_refused_watch_leaves_the_other_fields_pushed(clock):
    device = Device(IDLE)
    device.refuses = {"battChargeSource"}
    polled(device, clock, pushed=True)
    assert set(device.watchers) == {"operatingState", "battChargeState"}


def test_unreachable_peak_is_retried_at_the_idle_cadence(clock):
    c = polled(None, clock)
    c.update_interval = POLL_ACTIVE
    with pytest.raises(coordinator.UpdateFailed):
        poll(c, clock, POLL_ACTIVE.total_seconds())
    assert c.update_interval == POLL_IDLE


def test_pushed_idle_peak_is_fully_read_on_a_refresh_that_fires_early(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    poll(c, clock, POLL_IDLE.total_seconds() - 0.5)
    assert set(device.reads) == set(POLL_FIELDS) - PUSHED


def test_push_during_a_full_read_is_followed_by_another(clock):
    device = Device(IDLE)
    c = polled(device, clock, pushed=True)
    device.during_read["battSoc"] = lambda: device.push("battChargeSource", QI)
    poll(c, clock, POLL_IDLE.total_seconds())
    assert c.update_interval == POLL_ACTIVE
    device.reads.clear()
    poll(c, clock, POLL_ACTIVE.total_seconds())
    assert "battSoc" in device.reads
    assert c.update_interval == POLL_IDLE


def test_command_is_followed_by_a_full_read(clock):
    device = Device(IDLE)
    c = polled(device, clock)

    async def command(_device):
        pass

    asyncio.run(c.async_command(command))
    assert set(device.reads) == set(POLL_FIELDS)
