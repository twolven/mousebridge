###############################################################################
# MouseBridge relay v0.2 - remote PC (Sunshine host)
#
# Two jobs:
#   1. Forward agent UDP packets from the LAN interface to the Pi on the
#      USB-ethernet (NCM) link. Needed because the Pi hangs off this PC's
#      USB port rather than the LAN.
#   2. Optional kill hotkey (ported from MouseMoveR): polls a key via
#      GetAsyncKeyState (polling survives fullscreen games better than
#      hooks) and force-kills a configured process. No pip dependencies.
#
# Run at logon via Task Scheduler. Run elevated if the kill target runs
# elevated (anti-cheat launchers etc).
#
#   python relay.py --listen 0.0.0.0:8800 --forward 10.66.0.2:8800 ^
#                   --kill-process "League of Legends.exe" --kill-key backslash
###############################################################################

import argparse
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time

user32 = ctypes.windll.user32

BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "mousebridge-relay.log")

VK_NAMES = {
    "backslash": 0xDC,   # VK_OEM_5
    "pause": 0x13,
    "scrolllock": 0x91,
    "home": 0x24,
    "end": 0x23,
    "insert": 0x2D,
    "delete": 0x2E,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},  # f1=0x70 .. f12=0x7B
}


def log(message):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Relay] {message}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def kill_process(name):
    log(f"[Kill] Hotkey! taskkill /IM {name} /F")
    result = subprocess.run(["taskkill", "/IM", name, "/F"],
                            capture_output=True, text=True)
    out = (result.stdout + result.stderr).strip()
    log(f"[Kill] {out if out else 'no output'}")


def hotkey_poller(vk, process_name):
    log(f"[Kill] Polling VK 0x{vk:02X} to kill '{process_name}' (50ms).")
    was_down = False
    while True:
        down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        if down and not was_down:  # fire on press, not autorepeat
            kill_process(process_name)
        was_down = down
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(description="MouseBridge LAN->Pi UDP relay + kill hotkey")
    ap.add_argument("--listen", default="0.0.0.0:8800", help="LAN side (ip:port)")
    ap.add_argument("--forward", default="10.66.0.2:8800", help="Pi side (ip:port)")
    ap.add_argument("--kill-process", default="", metavar="NAME.EXE",
                    help="process to force-kill when the hotkey fires (disabled if empty)")
    ap.add_argument("--kill-key", default="backslash",
                    help=f"hotkey name ({', '.join(VK_NAMES)}) or hex VK code like 0xDC")
    args = ap.parse_args()

    if args.kill_process:
        key = args.kill_key.lower()
        vk = VK_NAMES.get(key)
        if vk is None:
            try:
                vk = int(key, 16 if key.startswith("0x") else 10)
            except ValueError:
                log(f"[Kill] Unknown key '{args.kill_key}'; hotkey disabled.")
                vk = None
        if vk is not None:
            threading.Thread(target=hotkey_poller,
                             args=(vk, args.kill_process), daemon=True).start()
    else:
        log("[Kill] Hotkey disabled (no --kill-process).")

    lhost, lport = args.listen.rsplit(":", 1)
    fhost, fport = args.forward.rsplit(":", 1)
    dst = (fhost, int(fport))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((lhost, int(lport)))
    sock.settimeout(1.0)  # so stats flush even when idle
    log(f"Relaying {args.listen} -> {args.forward}")

    # Rolling 60s stability stats
    STATS_INTERVAL = 60.0
    total = 0
    win_fwd = 0
    win_back = 0
    client = None  # last agent address, for routing Pi replies (echo/latency tests)
    next_stats = time.monotonic() + STATS_INTERVAL

    while True:
        now = time.monotonic()
        if now >= next_stats:
            if win_fwd or win_back:
                log(f"[Stats] 60s: {win_fwd} agent->pi ({win_fwd / STATS_INTERVAL:.0f}/s), "
                    f"{win_back} pi->agent, client={client[0] if client else 'none'} "
                    f"(lifetime {total})")
            win_fwd = win_back = 0
            next_stats = now + STATS_INTERVAL

        try:
            data, src = sock.recvfrom(64)
        except socket.timeout:
            continue
        except OSError as e:
            log(f"Socket error: {e}; continuing")
            time.sleep(0.1)
            continue

        try:
            if src[0] == dst[0]:
                if client:
                    sock.sendto(data, client)
                    win_back += 1
            else:
                client = src
                sock.sendto(data, dst)
                win_fwd += 1
            total += 1
        except OSError as e:
            log(f"Forward error: {e}")


if __name__ == "__main__":
    main()
