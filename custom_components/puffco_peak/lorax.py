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
OP_READ_SHORT = 0x10
OP_WRITE_SHORT = 0x11

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
        self.max_payload = 128

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        """Subscribe to the reply (and optional event) notify characteristics."""
        await self._client.start_notify(REPLY_UUID, self._on_notify)
        try:
            await self._client.start_notify(EVENT_UUID, self._on_notify)
        except Exception as err:  # noqa: BLE001 - event channel is optional
            _LOGGER.debug("Event channel unavailable: %s", err)

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
    def _on_notify(self, _char: object, data: bytearray) -> None:
        if len(data) < 3:
            return
        seq = struct.unpack_from("<H", data, 0)[0]
        status = data[2]
        fut = self._pending.pop(seq, None)
        if fut is None or fut.done():
            return
        if status != 0:
            fut.set_exception(LoraxError(status, f"seq {seq}"))
        else:
            fut.set_result(bytes(data[3:]))

    # -- transactions -------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    async def transact(self, opcode: int, payload: bytes = b"") -> bytes:
        """Send one opcode + payload and await its reply bytes."""
        async with self._lock:
            seq = self._next_seq()
            frame = struct.pack("<HB", seq, opcode) + payload
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

    def fail_pending(self, exc: Exception) -> None:
        """Reject every in-flight transaction (on disconnect)."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()


async def read_bond_characteristic(client: BleakClient) -> bytes:
    """Read the encrypted OTA app-version characteristic.

    This is the app's ``triggerBonding`` probe: the read needs an encrypted link,
    so it both drives the OS bond and confirms it. Raises on an unencrypted link
    (the device answers with ATT "Insufficient Encryption").
    """
    return await client.read_gatt_char(OTA_APP_VERSION_UUID)
