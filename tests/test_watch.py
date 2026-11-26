import asyncio
import struct

import pytest
from bleak.exc import BleakError
from puffco_peak import lorax
from puffco_peak.device import PuffcoDevice
from puffco_peak.lorax import LoraxClient, LoraxError

STATE_PATH = "/p/app/stat/id"
PERIOD = 2897
HANDLE = 4
IDLE = b"\x05"
PREHEAT = b"\x07"
EFBIG = 27


class Peak:
    def __init__(self):
        self.is_connected = True
        self.notify = {}
        self.commands = []
        self.event_seq = 0
        self.watch_status = 0
        self.content = IDLE
        self.hold = set()
        self.next_handle = HANDLE
        self.has_events = True

    async def start_notify(self, uuid, callback):
        if uuid == lorax.EVENT_UUID and not self.has_events:
            raise BleakError("characteristic not found")
        self.notify[uuid] = callback

    async def write_gatt_char(self, _uuid, frame, response):
        seq, opcode = struct.unpack_from("<HB", frame, 0)
        payload = bytes(frame[3:])
        self.commands.append((opcode, payload))
        if opcode in self.hold:
            return
        if opcode == lorax.OP_FILE_OPEN:
            self.reply(seq, 0, struct.pack("<H", self.next_handle))
            self.next_handle += 1
        elif opcode == lorax.OP_FILE_WATCH:
            self.reply(seq, self.watch_status)
            if not self.watch_status:
                self.push(self.content, struct.unpack_from("<H", payload)[0])
        else:
            self.reply(seq, 0)

    def reply(self, seq, status, data=b""):
        self.send(lorax.REPLY_UUID, struct.pack("<HB", seq, status) + data)

    def event(self, opcode, body, seq=None):
        if seq is None:
            seq = self.event_seq
            self.event_seq += 1
        self.send(lorax.EVENT_UUID, struct.pack("<HB", seq, opcode) + body)
        return seq

    def push(self, value, handle=HANDLE):
        return self.event(lorax.EVENT_WATCH, record(handle, value))

    def send(self, uuid, frame):
        asyncio.get_running_loop().call_soon(self.notify[uuid], None, bytearray(frame))

    def sent(self, opcode):
        return [payload for op, payload in self.commands if op == opcode]


def record(handle, value):
    return struct.pack("<HB", handle, len(value)) + value


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


async def watching(peak):
    client = LoraxClient(peak, timeout=1.0)
    client.max_payload = 241
    await client.start()
    values = []
    handle = await client.watch(STATE_PATH, PERIOD, values.append)
    assert handle == HANDLE
    await settle()
    return client, values


def acked(peak):
    records = b"".join(peak.sent(lorax.OP_ACK_EVENTS))
    return [seq for seq, _opcode in struct.iter_unpack("<HB", records)]


def test_watch_opens_the_path_and_watches_the_whole_file():
    async def scenario():
        peak = Peak()
        await watching(peak)
        return peak

    peak = asyncio.run(scenario())
    assert peak.sent(lorax.OP_FILE_OPEN) == [b"\x00" + STATE_PATH.encode()]
    assert peak.sent(lorax.OP_FILE_WATCH) == [struct.pack("<HHHHH", HANDLE, PERIOD, 400, 0, 239)]


def test_watcher_gets_the_current_value_then_each_change():
    async def scenario():
        peak = Peak()
        _client, values = await watching(peak)
        peak.push(PREHEAT)
        await settle()
        return values

    assert asyncio.run(scenario()) == [IDLE, PREHEAT]


def test_every_event_is_acknowledged_by_its_own_seq():
    async def scenario():
        peak = Peak()
        await watching(peak)
        peak.push(PREHEAT)
        await settle()
        return peak

    assert acked(asyncio.run(scenario())) == [0, 1]


def test_events_that_arrive_during_an_ack_go_out_in_the_next_one():
    async def scenario():
        peak = Peak()
        peak.hold.add(lorax.OP_ACK_EVENTS)
        client, _values = await watching(peak)
        peak.push(PREHEAT)
        peak.push(IDLE)
        await settle()
        peak.hold.clear()
        peak.reply(client._seq, 0)
        await settle()
        return peak

    peak = asyncio.run(scenario())
    assert [len(payload) // 3 for payload in peak.sent(lorax.OP_ACK_EVENTS)] == [1, 2]
    assert acked(peak) == [0, 1, 2]


def test_event_is_not_taken_for_the_reply_with_the_same_seq():
    async def scenario():
        peak = Peak()
        client, values = await watching(peak)
        peak.hold.add(lorax.OP_READ_SHORT)
        read = asyncio.create_task(client.read_short(STATE_PATH))
        await settle()
        seq = client._seq
        peak.push(PREHEAT, handle=99)
        peak.event(lorax.EVENT_WATCH, record(HANDLE, PREHEAT), seq=seq)
        await settle()
        answered_by_event = read.done()
        peak.reply(seq, 0, IDLE)
        return answered_by_event, await read, values

    answered_by_event, value, values = asyncio.run(scenario())
    assert not answered_by_event
    assert value == IDLE
    assert values == [IDLE, PREHEAT]


def test_pruned_handle_is_no_longer_watched():
    async def scenario():
        peak = Peak()
        client, _values = await watching(peak)
        before = client.watching(HANDLE)
        peak.event(lorax.EVENT_FILE_HANDLES_PRUNED, struct.pack("<H", HANDLE))
        await settle()
        return before, client.watching(HANDLE), peak

    before, after, peak = asyncio.run(scenario())
    assert (before, after) == (True, False)
    assert acked(peak) == [0, 1]


def test_refused_watch_closes_the_handle():
    async def scenario():
        peak = Peak()
        peak.watch_status = EFBIG
        client = LoraxClient(peak, timeout=1.0)
        await client.start()
        with pytest.raises(LoraxError):
            await client.watch(STATE_PATH, PERIOD, lambda value: None)
        return client, peak

    client, peak = asyncio.run(scenario())
    assert not client.watching(HANDLE)
    assert peak.sent(lorax.OP_FILE_CLOSE) == [struct.pack("<H", HANDLE)]


def test_disconnect_drops_every_watch():
    async def scenario():
        peak = Peak()
        client, _values = await watching(peak)
        client.fail_pending(BleakError("disconnected"))
        return client

    assert not asyncio.run(scenario()).watching(HANDLE)


def test_failing_watcher_does_not_cost_the_other_records():
    async def scenario():
        peak = Peak()
        client, _values = await watching(peak)
        client._watchers[HANDLE] = lambda value: 1 / 0
        others = []
        other = await client.watch("/p/bat/chg/src", PERIOD, others.append)
        await settle()
        peak.event(lorax.EVENT_WATCH, record(HANDLE, PREHEAT) + record(other, PREHEAT))
        await settle()
        return others, peak

    others, peak = asyncio.run(scenario())
    assert others == [IDLE, PREHEAT]
    assert acked(peak) == [0, 1, 2]


def test_watch_is_refused_without_an_event_channel():
    async def scenario():
        peak = Peak()
        peak.has_events = False
        client = LoraxClient(peak, timeout=1.0)
        await client.start()
        with pytest.raises(BleakError):
            await client.watch(STATE_PATH, PERIOD, lambda value: None)
        return peak

    assert asyncio.run(scenario()).sent(lorax.OP_FILE_OPEN) == []


def test_device_decodes_pushes_and_forgets_a_pruned_field():
    async def scenario():
        peak = Peak()
        client = LoraxClient(peak, timeout=1.0)
        await client.start()
        device = PuffcoDevice(client)
        values = []
        await device.watch("operatingState", PERIOD, values.append)
        await settle()
        peak.push(PREHEAT)
        await settle()
        pushed = device.pushed()
        peak.event(lorax.EVENT_FILE_HANDLES_PRUNED, struct.pack("<H", HANDLE))
        await settle()
        return peak, values, pushed, device

    peak, values, pushed, device = asyncio.run(scenario())
    assert peak.sent(lorax.OP_FILE_OPEN) == [b"\x00" + STATE_PATH.encode()]
    assert values == [5, 7]
    assert pushed == {"operatingState": 7}
    assert device.pushed() == {}
    assert not device.watching("operatingState")
