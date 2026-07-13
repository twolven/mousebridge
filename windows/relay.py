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
import socket
import subprocess
import threading
import time

user32 = ctypes.windll.user32

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
    print(f"[{time.strftime('%H:%M:%S')}] [Relay] {message}", flush=True)


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
    log(f"Relaying {args.listen} -> {args.forward}")

    count = 0
    while True:
        try:
            data, _ = sock.recvfrom(64)
            sock.sendto(data, dst)
            count += 1
            if count % 100000 == 0:
                log(f"{count} packets relayed")
        except OSError as e:
            log(f"Socket error: {e}; continuing")
            time.sleep(0.1)


if __name__ == "__main__":
    main()
