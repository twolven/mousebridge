# MouseBridge

**Instant, hardware-genuine mouse passthrough for Moonlight/Sunshine setups —
no VirtualHere, no USB re-enumeration, no simulated input.**

A Raspberry Pi presents a *permanently enumerated, real USB mouse* to your
remote gaming PC. Your local PC captures your physical mouse with Raw Input
and streams its movements to the Pi only while your streaming window is
focused. Switching between local and remote is just "does the stream flow" —
no device attach/detach, ever.

Successor to [py-mousemove](https://github.com/twolven/py-mousemove) /
[MouseMove-R](https://github.com/twolven/MouseMove-R).

## Why

Some games (League of Legends and friends) reject the simulated input that
Moonlight/Sunshine injects and demand a real USB HID device. VirtualHere
solves that by forwarding your physical mouse over the network — but every
focus switch physically re-plugs a USB device, and the resulting enumeration
on the remote host costs 1–3 seconds. Games that enumerate input devices at
launch also never see a mouse that wasn't attached before they started.

MouseBridge inverts the design: the (gadget) mouse is **always attached**.

**Measured, full path** (agent → LAN → relay → USB network → pump → HID write
into the host's USB stack; 5,000 probes):

| Metric | VirtualHere | MouseBridge |
|---|---|---|
| Focus switch time | 1.5–3.5 s | **< 5 ms** |
| Round-trip latency p50 | — | **0.70 ms** |
| Round-trip latency p99 | — (TCP stalls on loss) | **1.00 ms** (max 4.3 ms) |
| Packet loss | n/a | **0 / 5,000** |
| Game sees device at launch | only if attached first | **always** |

## Architecture

```
 LOCAL PC (Moonlight)              REMOTE PC (Sunshine host)          PI (gadget)
┌────────────────────┐            ┌──────────────────────────┐      ┌─────────────┐
│ physical mouse     │            │ tray icon + green/red    │ USB  │ Pi Zero 2 W │
│   │ Raw Input      │            │ status overlay           │cable │             │
│   ▼                │   LAN      │                          │      │             │
│ agent ─────────────┼── UDP ────►│ relay ──► usb-NCM ───────┼──────┤ hidpump     │
│  (streams only     │  :8800     │ 10.66.0.1     10.66.0.2 ◄┼─DHCP─┤  │          │
│   while streaming  │            │                          │      │  ▼          │
│   window focused)  │            │ ◄═ real HID mouse ═══════╪══════╡ /dev/hidg0  │
└────────────────────┘            └──────────────────────────┘      └─────────────┘
```

- **Agent** (local PC, `windows/agent.py`): Raw Input mouse capture,
  event-driven focus detection (`SetWinEventHook`, zero polling). Streams
  12-byte UDP packets only while the configured window title is foreground;
  sends a release packet and goes silent on focus loss. Single-instance.
- **Relay** (remote PC, `windows/relay.py`): windowless tray app. Forwards
  agent packets from the LAN to the Pi over the USB-ethernet link, shows a
  draggable always-on-top indicator — **green "ACTIVE"** when the stream is
  flowing *and* the Pi answers health probes (echoed through the pump every
  2 s), **red** with a reason otherwise. Right-click the tray icon to exit.
  Optional hotkey that force-kills a hung game process.
- **Pi** (`pi/`): composite USB gadget — one HID mouse (PixArt `093a:2510`
  identity, 5 buttons, 16-bit deltas, wheel + horizontal wheel) plus one NCM
  ethernet function **on the same cable**, so the Pi is wired through the
  port it's plugged into (its 2.4 GHz WiFi is never on the input path).
  `hidpump.py` turns UDP packets into HID reports with a 300 ms
  release-all-buttons failsafe; dnsmasq hands the host PC its link address.

### Why it can't desync

Packets carry *relative* deltas like a real mouse — there is no absolute
position to drift. Every packet (and a 100 ms keepalive) carries the **full
button state**, so a lost button transition self-corrects on the next packet,
and the pump releases everything after 300 ms of silence. A dropped packet
costs a couple of pixels mid-swipe, not a stuck button or an offset cursor.

## Hardware

- A USB-gadget-capable Pi: Zero / Zero 2 W / 4 / 5 (Zero 2 W is perfect).
- One **data-capable** USB cable from the Pi's gadget port (Zero: the inner
  micro-USB port; Pi 4/5: the USB-C power port) into the remote PC. The Pi
  is powered by that same port — no other wiring.
- Local and remote PC on the same LAN. Remote PC needs Windows 11 for the
  inbox NCM driver (Windows 10: set `FUNC_NET="rndis"` in
  `pi/setup-gadget.sh`).

## Setup

### 1. Pi

Flash Raspberry Pi OS Lite, enable SSH/WiFi as usual, then:

```bash
git clone https://github.com/twolven/mousebridge
cd mousebridge
sudo bash pi/install.sh     # adds dwc2 if missing, installs everything
# reboot if the installer says so
```

Plug the Pi into the remote PC. Done — the PC sees a "USB Optical Mouse"
plus a network adapter (it gets `10.66.0.1` via DHCP from the Pi).

### 2. Remote PC (Sunshine host)

Grab `mousebridge-relay.exe` from
[Releases](https://github.com/twolven/mousebridge/releases) (or build it:
`pyinstaller --onefile --noconsole windows/relay.py`), put it in a folder
next to `deploy/install-relay.cmd` and `deploy/start-relay.cmd`, and
double-click `install-relay.cmd`. It installs to `%USERPROFILE%\MouseBridge`,
opens UDP 8800 in the firewall, writes a default `config.txt`, adds a logon
startup entry, and starts the relay (tray icon + status overlay).

No Python needed — the exe is self-contained. (Running from source also
works: `python relay.py`; agent and relay are stdlib-only.)

### 3. Local PC (Moonlight)

Grab `mousebridge-agent.exe` from Releases into `%USERPROFILE%\MouseBridge`
along with `deploy/start-agent.cmd`, create `config.txt` next to it:

```ini
PI_HOST = 192.168.1.x        # the REMOTE PC's LAN IP (the relay)
PI_PORT = 8800
WINDOW_TITLE = YourPC - Moonlight   # exact title of the streaming window
TITLE_MATCH = exact          # or 'contains'
SCALE = 1.0                  # see DPI matching below
```

Run `start-agent.cmd` (and copy it into `shell:startup` for autostart). The
console window shows live logs; closing it exits the agent.

Focus your Moonlight window → the remote indicator flips green → your mouse
is hardware-real on the remote PC. Alt-tab away → released.

## Config reference

Agent (`%USERPROFILE%\MouseBridge\config.txt` on the local PC):

| Key | Meaning |
|---|---|
| `PI_HOST` / `PI_PORT` | Where packets go — the remote PC's LAN IP (relay) |
| `WINDOW_TITLE` | Streaming window to watch; focus = stream on |
| `TITLE_MATCH` | `exact` or `contains` |
| `SCALE` | Motion multiplier (fractional remainders carry over) |

Relay (`%USERPROFILE%\MouseBridge\config.txt` on the remote PC):

| Key | Meaning |
|---|---|
| `LISTEN` | LAN side, default `0.0.0.0:8800` |
| `FORWARD` | Pi side, default `10.66.0.2:8800` |
| `KILL_PROCESS` / `KILL_KEY` | Hotkey force-kill for hung games (blank = off) |
| `STATUS_WINDOW` | `on`/`off` for the green/red overlay |

### DPI matching (`SCALE`)

Under VirtualHere your mouse ran its **onboard** DPI profile on the remote
PC (no vendor software there). Under MouseBridge the sensor runs whatever
your local vendor software sets, and deltas are replayed 1:1 — so the remote
feel changes if those differ. Set `SCALE = old_dpi / current_dpi` (e.g.
onboard 1200, LGHUB 3000 → `SCALE = 0.4`). Fractions accumulate, so slow
precise aim is never lost.

## Diagnostics

- Every component logs 60-second stability stats: the agent to its console +
  `mousebridge-agent.log`, the relay to `mousebridge-relay.log`, the pump to
  `journalctl -u hidpump` (rx rate, sequence-gap loss, HID drops, worst
  inter-packet gap).
- `tools/latency_test.py --target <relay-ip>:8800 --count 5000 --with-write`
  measures the real path end-to-end: probes ride the actual protocol, the
  pump echoes after performing a genuine (null) HID write.
- `tools/hidwrite_probe.py` (on the Pi) answers "is the host actually
  polling the mouse?" with 100 paced writes — a single successful write only
  proves the 1-slot queue was empty, so don't trust one.

## Troubleshooting

- **Status window red "PI DOWN"**: pump/gadget down or the USB network link
  lost its address. `systemctl status mousebridge-gadget hidpump dnsmasq` on
  the Pi; re-plugging the cable re-enumerates everything.
- **Red "idle" while Moonlight is focused**: agent side — check the agent
  console/log and that `WINDOW_TITLE` exactly matches (it's case-sensitive).
- **Enumerated but no input** (`hidwrite_probe` says NOT polling): don't set
  the gadget's VID/PID to a vendor whose software runs on the host —
  e.g. Logitech IDs with G HUB installed: the vendor driver claims the
  device, its vendor requests go unanswered, and the endpoint is never
  polled. Stick with the PixArt identity or another vendor-software-free ID.
- **Game sensitivity feels wrong**: `SCALE` (above).
- **Windows 10 host**: NCM needs Win11; set `FUNC_NET="rndis"` in
  `pi/setup-gadget.sh` and rerun `sudo bash pi/install.sh`.

## Protocol

UDP, 12 bytes little-endian (`<HHBbhhbb`): magic `0x4D42`, wrapping seq,
button bitmap (L/R/M/B4/B5), flags (bit0 keepalive, bit1 echo), dx/dy i16,
wheel/hwheel i8. HID report (7 bytes): buttons u8, dx i16, dy i16, wheel i8,
hwheel i8. LAN-only by design; there is no authentication — don't expose
port 8800 beyond your LAN.

## License

MIT
