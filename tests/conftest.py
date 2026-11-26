import sys
from pathlib import Path
from types import ModuleType

import pytest


class UpdateFailed(Exception):
    pass


class DataUpdateCoordinator[T]:
    pass


class FakeBus:
    def __init__(self, events):
        self.events = events
        self.fail = {}
        self.closed = False

    async def connect(self):
        self._fail("connect")

    def export(self, path, interface):
        pass

    async def call(self, message):
        self.events.append(message.member)
        self._fail(message.member)

    def disconnect(self):
        self.closed = True

    def _fail(self, step):
        if step in self.fail:
            raise self.fail[step]


def stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module


stub("homeassistant.components", bluetooth=ModuleType("bluetooth"))
stub("homeassistant.config_entries", ConfigEntry=object)
stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
stub(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=UpdateFailed,
)
stub("bleak_retry_connector", BleakClientWithServiceCache=object, establish_connection=None)
stub("habluetooth", BaseHaRemoteScanner=object)
stub("puffco_peak", __path__=[str(Path(__file__).parents[1] / "custom_components" / "puffco_peak")])


@pytest.fixture
def events():
    return []


@pytest.fixture
def bus(events, monkeypatch):
    bus = FakeBus(events)
    monkeypatch.setattr("puffco_peak.agent.MessageBus", lambda **_: bus)
    return bus
