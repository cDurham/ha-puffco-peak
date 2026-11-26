"""LORAX transport for the Puffco Peak Pro over BLE.

Ported from magikh0e/puffco-lorax-decode (puffco_client.py) and the web app's
js/lorax.js, both recovered from Puffco Connect 3.13.47 and verified on a Peak
Pro (fw gith 5652c02). This layer owns the seqId transaction model and the auth
handshake; it talks to an already-connected ``BleakClient`` that Home
Assistant's Bluetooth stack hands us.

Frame (write, COMMAND char, write-without-response):
    seqId u16 LE | opcode u8 | payload
Reply (REPLY char, notify):
    seqId u16 LE | status u8 (0 = OK) | data
Event (EVENT char, notify), numbered by the device independently of seqId:
    eventSeq u16 LE | eventOpcode u8 | body
A watch event (eventOpcode 0) carries records of handle u16 LE | len u8 | value.
Only the characteristic tells an event from a reply.

The link MUST be bonded and encrypted before any of this answers: on an
unencrypted link the Peak silently drops every frame and transactions just time
out.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import struct
from collections.abc import Callable

from bleak import BleakClient
from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

# --- Confirmed GATT UUIDs ---------------------------------------------------
SERVICE_UUID = "e276967f-ea8a-478a-a92e-d78f5dd15dd5"
VERSION_UUID = "05434bca-cc7f-4ef6-bbb3-b1c520b9800c"  # read: LORAX version
COMMAND_UUID = "60133d5c-5727-4f2c-9697-d842c5292a3c"  # write without response
REPLY_UUID = "8dc5ec05-8f7d-45ad-99db-3fbde65dbd9c"  # notify: command replies
EVENT_UUID = "43312cd1-7d34-46ce-a7d3-0a98fd9b4cb8"  # notify: async events

# The app reads one of these before any LORAX traffic. They need an encrypted
# link, so reading them makes the OS bond; it is also how we confirm the bond.
OTA_SERVICE_UUID = "1d14d6ee-fd63-4fa1-bfa4-8f47b42119f0"
OTA_APP_VERSION_UUID = "0d77cc11-4ac1-49f2-bfa9-cd96ac7a92f8"

# --- Opcodes ----------------------------------------------------------------
OP_GET_ACCESS_SEED = 0x00
OP_UNLOCK = 0x01
OP_GET_LIMITS = 0x02
OP_ACK_EVENTS = 0x03
OP_READ_SHORT = 0x10
OP_WRITE_SHORT = 0x11
OP_FILE_OPEN = 0x20
OP_FILE_WATCH = 0x23
OP_FILE_CLOSE = 0x26

EVENT_WATCH = 0
EVENT_FILE_HANDLES_PRUNED = 1

WATCH_ACK_TIMEOUT = 400
WATCH_LENGTH_MAX = 255
WATCH_EVENT_OVERHEAD = 2
ACK_RETRY_DELAY = 0.1

_HEADER = struct.Struct("<HB")
_RECORD_HEADER = struct.Struct("<HB")
_HANDLE = struct.Struct("<H")

# LoraxOpenFlags: the app passes `truncate` (0x04) on every WriteShort. It is a
# flag, not a byte position.
WRITE_FLAGS_TRUNCATE = 0x04

# Auth: token = SHA-256(key ++ seed[:16])[:16], key from the app's handshake
# table (the `lorax` entry).
_LORAX_KEY = base64.b64decode("ZMZFYlbyb1scoSc3pd1x+w==")


def auth_token(seed: bytes) -> bytes:
    """Return the 16-byte UnlockAccess token for a 16-byte access seed."""
    return hashlib.sha256(_LORAX_KEY + seed[:16]).digest()[:16]


class LoraxError(Exception):
    """A LORAX frame came back with a non-zero status byte."""

    def __init__(self, code: int, label: str) -> None:
        super().__init__(f"{label} failed (device status {code})")
        self.code = code


class LoraxTimeout(Exception):
    """A transaction got no reply within the timeout (usually: not bonded)."""


TRANSACT_ERRORS = (LoraxError, LoraxTimeout, BleakError)


class LoraxClient:
    """LORAX transactions over a connected BleakClient.

    One transaction is in flight at a time: BlueZ serialises GATT operations and
    the device processes commands in order anyway.
    """

    def __init__(self, client: BleakClient, timeout: float = 5.0) -> None:
        self._client = client
        self._timeout = timeout
        self._seq = 0
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._lock = asyncio.Lock()
        self._watchers: dict[int, Callable[[bytes], None]] = {}
        self._unacked: list[tuple[int, int]] = []
        self._ack_task: asyncio.Task[None] | None = None
        self._has_events = False
        self.max_payload = 128

    def watching(self, handle: int) -> bool:
        """Whether the device is still pushing changes for an open file."""
        return handle in self._watchers

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        """Subscribe to the reply (and optional event) notify characteristics."""
        await self._client.start_notify(REPLY_UUID, self._on_reply)
        try:
            await self._client.start_notify(EVENT_UUID, self._on_event)
        except Exception as err:  # noqa: BLE001 - event channel is optional
            _LOGGER.debug("Event channel unavailable: %s", err)
        else:
            self._has_events = True

    async def auth(self) -> None:
        """Run the GetLimits / GetAccessSeed / UnlockAccess handshake."""
        limits = await self.transact(OP_GET_LIMITS)
        if len(limits) >= 2:
            self.max_payload = struct.unpack_from("<H", limits, 0)[0]
        seed = await self.transact(OP_GET_ACCESS_SEED)
        if len(seed) < 16:
            raise LoraxError(0, f"GetAccessSeed returned {len(seed)} bytes")
        await self.transact(OP_UNLOCK, auth_token(seed))
        _LOGGER.debug("LORAX unlocked (max payload %d)", self.max_payload)

    # -- notify handling ----------------------------------------------------
    def _on_reply(self, _char: object, data: bytearray) -> None:
        if len(data) < _HEADER.size:
            return
        seq, status = _HEADER.unpack_from(data)
        fut = self._pending.pop(seq, None)
        if fut is None or fut.done():
            return
        if status != 0:
            fut.set_exception(LoraxError(status, f"seq {seq}"))
        else:
            fut.set_result(bytes(data[_HEADER.size :]))

    def _on_event(self, _char: object, data: bytearray) -> None:
        if len(data) < _HEADER.size:
            return
        seq, opcode = _HEADER.unpack_from(data)
        body = bytes(data[_HEADER.size :])
        self._unacked.append((seq, opcode))
        if self._ack_task is None or self._ack_task.done():
            self._ack_task = asyncio.create_task(self._ack_events())
        if opcode == EVENT_WATCH:
            for handle, value in _watch_records(body):
                watcher = self._watchers.get(handle)
                if watcher is None:
                    continue
                try:
                    watcher(value)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Watcher for handle %d failed", handle)
        elif opcode == EVENT_FILE_HANDLES_PRUNED:
            whole = len(body) - len(body) % _HANDLE.size
            for (handle,) in _HANDLE.iter_unpack(body[:whole]):
                if self._watchers.pop(handle, None) is not None:
                    _LOGGER.debug("Device pruned watched handle %d", handle)

    async def _ack_events(self) -> None:
        while self._unacked:
            batch = self._unacked[: max(1, self.max_payload // _HEADER.size)]
            payload = b"".join(_HEADER.pack(seq, opcode) for seq, opcode in batch)
            try:
                await self.transact(OP_ACK_EVENTS, payload)
            except BleakError as err:
                _LOGGER.debug("AckEvents stopped: %s", err)
                return
            except TRANSACT_ERRORS as err:
                _LOGGER.debug("AckEvents failed, retrying: %s", err)
                await asyncio.sleep(ACK_RETRY_DELAY)
            else:
                del self._unacked[: len(batch)]

    # -- transactions -------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    async def transact(self, opcode: int, payload: bytes = b"") -> bytes:
        """Send one opcode + payload and await its reply bytes."""
        async with self._lock:
            seq = self._next_seq()
            frame = _HEADER.pack(seq, opcode) + payload
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bytes] = loop.create_future()
            self._pending[seq] = fut
            try:
                await self._client.write_gatt_char(COMMAND_UUID, frame, response=False)
                return await asyncio.wait_for(fut, self._timeout)
            except TimeoutError as err:
                raise LoraxTimeout(
                    f"opcode 0x{opcode:02x} got no reply in {self._timeout}s"
                ) from err
            finally:
                self._pending.pop(seq, None)

    async def read_short(self, path: str, size: int | None = None) -> bytes:
        """ReadShort: offset u16 | maxLen u16 | path (no terminator)."""
        if size is None:
            size = self.max_payload
        payload = struct.pack("<HH", 0, size) + path.encode("ascii")
        return await self.transact(OP_READ_SHORT, payload)

    async def write_short(self, path: str, data: bytes) -> None:
        """WriteShort: offset u16 | flags u8 | path | 0x00 | data."""
        payload = (
            struct.pack("<HB", 0, WRITE_FLAGS_TRUNCATE)
            + path.encode("ascii")
            + b"\x00"
            + bytes(data)
        )
        await self.transact(OP_WRITE_SHORT, payload)

    async def watch(
        self, path: str, period: int, on_value: Callable[[bytes], None]
    ) -> int:
        """Open ``path`` and have the device push its content on every change.

        The device sends the current content at once, then again whenever it
        changes. ``on_value`` runs in the event loop. Returns the file handle.
        """
        if not self._has_events:
            raise BleakError("event channel unavailable")
        reply = await self.transact(OP_FILE_OPEN, b"\x00" + path.encode("ascii"))
        if len(reply) != _HANDLE.size:
            raise LoraxError(0, f"FileOpen {path} returned {len(reply)} bytes")
        (handle,) = _HANDLE.unpack(reply)
        length = min(self.max_payload - WATCH_EVENT_OVERHEAD, WATCH_LENGTH_MAX)
        self._watchers[handle] = on_value
        try:
            await self.transact(
                OP_FILE_WATCH,
                struct.pack("<HHHHH", handle, period, WATCH_ACK_TIMEOUT, 0, length),
            )
        except Exception:
            self._watchers.pop(handle, None)
            if self._client.is_connected:
                try:
                    await self.transact(OP_FILE_CLOSE, _HANDLE.pack(handle))
                except TRANSACT_ERRORS as err:
                    _LOGGER.debug("Closing handle %d failed: %s", handle, err)
            raise
        return handle

    def fail_pending(self, exc: Exception) -> None:
        """Reject every in-flight transaction and drop every watch (on disconnect)."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        self._watchers.clear()
        self._unacked.clear()
        if self._ack_task is not None:
            self._ack_task.cancel()
            self._ack_task = None


def _watch_records(body: bytes) -> list[tuple[int, bytes]]:
    records: list[tuple[int, bytes]] = []
    offset = 0
    while len(body) - offset >= _RECORD_HEADER.size:
        handle, length = _RECORD_HEADER.unpack_from(body, offset)
        offset += _RECORD_HEADER.size
        value = body[offset : offset + length]
        if len(value) != length:
            break
        records.append((handle, value))
        offset += length
    return records


async def read_bond_characteristic(client: BleakClient) -> bytes:
    """Read the encrypted OTA app-version characteristic.

    This is the app's ``triggerBonding`` probe: the read needs an encrypted link,
    so it both drives the OS bond and confirms it. Raises on an unencrypted link
    (the device answers with ATT "Insufficient Encryption").
    """
    return await client.read_gatt_char(OTA_APP_VERSION_UUID)
