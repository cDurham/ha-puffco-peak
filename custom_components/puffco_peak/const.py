"""Constants for the Puffco Peak integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "puffco_peak"
MANUFACTURER: Final = "Puffco"

# Config entry data
CONF_ADDRESS: Final = "address"

# --- GATT (Peak Pro LORAX service), confirmed on hardware -------------------
# Advertised service that the config flow / manifest match on.
SERVICE_UUID: Final = "e276967f-ea8a-478a-a92e-d78f5dd15dd5"
# Puffco Bluetooth SIG company identifier (manufacturer_id in adverts).
PUFFCO_COMPANY_ID: Final = 3075

# --- Poll cadence -----------------------------------------------------------
# The heater reading is only interesting while the device is doing something, so
# poll fast when it is preheating / active and slowly the rest of the time.
POLL_IDLE: Final = timedelta(seconds=30)
POLL_ACTIVE: Final = timedelta(seconds=3)

# operatingState values (/p/app/stat/id) that mean a heat cycle is live.
ACTIVE_STATES: Final = frozenset({7, 8, 9})  # HeatCyclePreheat, HeatCycleActive, HeatCycleFade

# Fields read on every poll (name -> included only if the device has it).
POLL_FIELDS: Final = (
    "operatingState",
    "userHeaterTemp",
    "userHeaterTempCommand",
    "stateElapsedTime",
    "stateTotalTime",
    "selectedHeatCycle",
    "battSoc",
    "battChargeState",
    "battChargeSource",
    "battChargeEstTimeToFull",
    "battVolts",
    "battTemp",
    "approxDabsRemaining",
    "totalHeatCycles",
    "stealthMode",
    "ledBrightness",
    "lanternRemainingTime",
    "lanternColorApi2",
)

# Number of on-device heat profiles.
PROFILE_COUNT: Final = 4

# Fields read once at setup for the device registry / static sensors.
INFO_FIELDS: Final = (
    "serialNumber",
    "gitHash",
    "modelCode",
    "hardwareRevision",
    "swOtaVersion",
    "deviceName",
)
