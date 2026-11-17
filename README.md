<p align="center">
  <img src="assets/logo.png" width="320"
       alt="Badge reading HOME ASSISTANT over PUFFCO PEAK, with a Peak Pro, a Home Assistant house, a Bluetooth link and temperature controls on a circuit-board field.">
</p>

# Puffco Peak for Home Assistant

A custom integration that connects Home Assistant to a **Puffco Peak Pro** over
Bluetooth and exposes its live state and controls as entities. It speaks the
device's own LORAX protocol directly, so nothing leaves your network and no
cloud account is involved.

This is a port of the protocol work from
[puffco-peak-control](https://github.com/magikh0e/puffco-peak-control) (the
browser app) and [puffco-lorax-decode](https://github.com/magikh0e/puffco-lorax-decode)
(the Python client and protocol notes) into Home Assistant's Bluetooth stack.

> Not affiliated with Puffco. Use it only with a device you own.

## Status

Working. Connecting, bonding, live state, and the full control surface have been
confirmed on a real Peak Pro: it bonds and unlocks over a local adapter, streams
temperature and battery, and drives the heat cycle, stealth, the lantern, and
profile edits from Home Assistant.

Peak Pro only for now. The Proxy uses a different value layout and can follow.
Peak Pros on firmware older than `AB` use the legacy Bluetooth layout and are
not supported.

## Requirements

- A **local Bluetooth adapter on the Home Assistant host** (built in or a USB
  dongle) within range of the Peak. The device needs a bonded, encrypted link,
  and bonding over an ESPHome Bluetooth proxy is not supported today, so a local
  adapter is required.
- The Peak must be **paired (bonded) with the Home Assistant host**. The first
  time you connect, put the Peak in **pairing mode** (hold the button until the
  logo glows blue) so the host can bond with it.
- One connection at a time: while Home Assistant holds the Peak, the phone app
  cannot connect to it, and vice versa.

## Install

### HACS (custom repository)

1. HACS, three-dot menu, **Custom repositories**.
2. Add `https://github.com/magikh0e/ha-puffco-peak` as an **Integration**.
3. Install **Puffco Peak**, then restart Home Assistant.

### Manual

Copy `custom_components/puffco_peak` into your Home Assistant `config/custom_components`
directory and restart.

## Set up

1. Put the Peak in pairing mode (hold the button until the logo glows blue).
2. Home Assistant should discover it automatically (**Settings, Devices &
   Services**). If not, add **Puffco Peak** manually and pick it from the list.
3. On the first connect the host bonds with the device. If setup fails the first
   time, make sure the Peak is still in pairing mode and let Home Assistant
   retry.

### If bonding does not complete

Most adapters bond automatically on the first connect. If yours does not, pair
the device once from the Home Assistant host shell, then set the integration up
again:

```bash
bluetoothctl
power on
scan on          # wait for the Peak to appear, note its MAC
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
scan off
exit
```

Once the device is bonded, later reconnects do not need pairing mode.

## Entities

**Control**

| Entity | Notes |
|---|---|
| Climate | target temperature, heating/idle, the four profiles as presets, on starts the selected profile and off stops |
| Start / Stop / Add temperature / Add time / Power off | buttons |
| Heat profile | pick the active profile |
| Stealth mode | switch |
| Lantern | the mood light, on/off and color |
| Profile 1-4 temperature / duration | editable per profile |
| Profile 1-4 boost temperature / boost time | editable per profile, disabled by default |
| Profile 1-4 name | rename a profile |
| LED brightness | device light brightness |

**Read-only**

| Entity | Notes |
|---|---|
| Heater temperature | live heater reading |
| Target temperature | commanded temperature for the active cycle |
| Operating state | off, idle, preheating, ready, and so on |
| Battery | charge percentage |
| Charge state / Charge source | charging status and how it is powered |
| Dabs remaining | the device's own estimate |
| Total heat cycles | diagnostic |
| Battery voltage / temperature | diagnostic, disabled by default |

Setting a climate target temperature edits the selected profile's stored
temperature; the device heats to a profile, it has no separate live setpoint.
The integration polls quickly while the device is preheating or active and
slowly the rest of the time.

## Credits

Protocol groundwork:
[puffco-lorax-decode](https://github.com/magikh0e/puffco-lorax-decode),
[puffco-peak-control](https://github.com/magikh0e/puffco-peak-control),
[austenstone/puffco-mcp](https://github.com/austenstone/puffco-mcp), and
[Fr0st3h's write-up](https://github.com/Fr0st3h/Puffco-Reverse-Engineering-Writeup).

## License

GPL-3.0. See [LICENSE](LICENSE).
