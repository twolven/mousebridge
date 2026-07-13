# MouseBridge

Hardware USB HID mouse bridge for Moonlight/Sunshine setups. Replaces
VirtualHere attach/detach switching (1–3 s per focus change) with **instant
stream gating** (< 5 ms): a Raspberry Pi presents a *permanently enumerated,
genuine USB mouse* to the remote PC, and the local PC simply starts/stops
streaming HID reports to it when the streaming window gains/loses focus.

Successor to [py-mousemove](https://github.com/twolven/py-mousemove) /
[MouseMove-R](https://github.com/twolven/MouseMove-R). No VirtualHere, no USB
re-enumeration, no simulated input — the remote PC sees real hardware on its
USB bus.

## Why

The old MouseMove architecture physically re-plugged a USB device over the
network on every focus change. The `USE` / `STOP USING` round-trip itself is
fast, but the resulting USB enumeration on the remote host (driver bind, HID
stack init) costs 1–3 seconds and cannot be optimized away.

MouseBridge inverts the design: the device is **always attached**. Switching
is just "does the local agent forward packets right now or not."

| Stage                        | MouseMove (VirtualHere) | MouseBridge        |
|------------------------------|-------------------------|--------------------|
| Focus detection              | 0–250 ms (polling)      | ~0 ms (WinEvent hook) |
| Command transport            | ~1–5 ms                 | ~0.5 ms (one UDP hop) |
| Device availability          | 1–3 s (USB enumeration) | 0 ms (always attached) |
| **Total switch time**        | **~1.5–3.5 s**          | **< 5 ms**         |

## Architecture

```
 LOCAL PC (Moonlight)                REMOTE PC (Sunshine host)         PI (gadget)
┌─────────────────────┐            ┌──────────────────────────┐      ┌─────────────┐
│ physical mouse      │            │                          │ USB  │ Pi Zero 2 W │
│   │ Raw Input       │            │  usb0 (NCM) 10.66.0.1 ◄──┼──────┤ 10.66.0.2   │
│   ▼                 │   LAN      │        ▲                 │cable │    │        │
│ agent.py ───────────┼── UDP ────►│  relay.py (UDP fwd)      │      │ hidpump.py  │
│   ▲ WinEvent hook   │  :8800     │                          │      │    ▼        │
│ (focus = stream on) │            │  ◄═ HID mouse reports ═══╪══════╡ /dev/hidg0  │
└─────────────────────┘            └──────────────────────────┘      └─────────────┘
```

- **`windows/agent.py`** (local PC): captures the physical mouse via Raw
  Input, watches foreground-window changes via `SetWinEventHook` (event-driven,
  no polling), and streams 12-byte UDP packets only while the configured
  window title is focused. On focus loss it sends a release packet (all
  buttons up) and goes silent.
- **`windows/relay.py`** (remote PC): 30-line UDP forwarder from the LAN
  interface to the Pi's USB-ethernet address. Needed because the Pi hangs off
  the remote PC's USB port, not the LAN.
- **`pi/setup-gadget.sh`** (Pi): configures a composite USB gadget via
  configfs — one HID mouse function + one NCM ethernet function on the same
  cable.
- **`pi/hidpump.py`** (Pi): receives UDP packets and writes 7-byte HID
  reports to `/dev/hidg0`. Failsafe: releases all buttons if the stream goes
  silent for 300 ms.

## Transport: why not WiFi?

The Pi Zero 2 W only has 2.4 GHz WiFi — fine for bulk traffic, bad for mouse
input (interference bursts show up as 20–100 ms latency spikes, i.e. visible
cursor stutter). The composite gadget sidesteps this entirely: the **NCM
ethernet function rides the same USB cable as the mouse**, so the Pi is wired
through the port it's already plugged into. End-to-end path is
LAN + one USB hop, sub-millisecond and deterministic.

WiFi still works as a fallback (point `PI_HOST` at the Pi's WLAN IP and skip
the relay) — useful for initial testing before the relay is set up.

## Protocol

UDP, 12 bytes, little-endian (`<HHBbhhbb`):

| Field    | Type | Meaning                                    |
|----------|------|--------------------------------------------|
| magic    | u16  | `0x4D42` ("MB")                            |
| seq      | u16  | wrapping sequence number (loss stats)      |
| buttons  | u8   | bit0=L bit1=R bit2=M bit3=B4 bit4=B5       |
| flags    | i8   | bit0 = keepalive (no motion, state only)   |
| dx, dy   | i16  | relative motion                            |
| wheel    | i8   | vertical wheel detents                     |
| hwheel   | i8   | horizontal wheel (AC Pan) detents          |

HID report (7 bytes): `buttons u8, dx i16, dy i16, wheel i8, hwheel i8`.
LAN-only by design; no auth in v0.1 (see roadmap).

## Setup

### Pi (Zero 2 W or any gadget-capable Pi, plugged into remote PC USB)

```bash
sudo mkdir -p /opt/mousebridge
sudo cp pi/setup-gadget.sh pi/hidpump.py /opt/mousebridge/
sudo cp pi/mousebridge-gadget.service pi/hidpump.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mousebridge-gadget hidpump
```

Requires `dtoverlay=dwc2` in `/boot/firmware/config.txt` and the data (inner)
micro-USB port. The gadget defaults to a generic VID/PID; if the target app
fingerprints devices, set `ID_VENDOR`/`ID_PRODUCT` in `setup-gadget.sh` to
match a real mouse.

### Remote PC (Sunshine host)

The NCM interface appears automatically on Windows 11 (UsbNcm inbox driver);
give it `10.66.0.1/24` if it doesn't DHCP. Then run the relay at logon
(Task Scheduler):

```
python windows\relay.py --listen 0.0.0.0:8800 --forward 10.66.0.2:8800
```

(On Windows 10 set `FUNC_NET=rndis` in `setup-gadget.sh` instead of `ncm`.)

### Local PC (Moonlight)

Copy `config.example.txt` to `config.txt` next to `agent.py`, set:

```ini
PI_HOST = 192.168.1.xx     # remote PC LAN IP (relay), or Pi WiFi IP for testing
PI_PORT = 8800
WINDOW_TITLE = YourPC - Moonlight
TITLE_MATCH = exact        # or 'contains'
```

Run `python windows\agent.py` (pythonw for no console).

## Known considerations

- **Double input**: unlike VirtualHere, the physical mouse never leaves the
  local PC, so Moonlight still forwards its own (simulated) input to the host
  alongside the bridge's hardware input. For apps that reject simulated input
  (the whole point of this tool) that's harmless — only the bridge's input
  lands. If an app accepts both, hide the physical mouse from Moonlight with
  HidHide (whitelist `agent.py`'s python.exe).
- **Wheel resolution**: v0.1 quantizes to 120-unit detents; hi-res scroll is
  on the roadmap.

## Roadmap

- [ ] Optional HMAC packet auth
- [ ] Hi-res wheel passthrough
- [ ] Rust/C agent for guaranteed 1 kHz on low-end local PCs
- [ ] Keyboard function on the same gadget
- [ ] Tray icon + toggle hotkey

## License

MIT
