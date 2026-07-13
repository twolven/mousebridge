#!/usr/bin/env python3
###############################################################################
# MouseBridge hidpump v0.1 - Pi (gadget side)
#
# Receives 12-byte UDP packets and writes 7-byte HID mouse reports to
# /dev/hidg0. Failsafe: if the stream goes silent while buttons are held
# (agent crash, link drop), releases all buttons after FAILSAFE_S.
###############################################################################

import select
import signal
import socket
import struct
import sys
import time

MAGIC = 0x4D42
PACKET_FMT = "<HHBbhhbb"
PACKET_SIZE = struct.calcsize(PACKET_FMT)
REPORT_FMT = "<Bhhbb"  # buttons, dx, dy, wheel, hwheel
FLAG_KEEPALIVE = 0x01

LISTEN_PORT = 8800
HID_DEV = "/dev/hidg0"
FAILSAFE_S = 0.3

running = True


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] [Pump] {message}", flush=True)


def on_signal(sig, frame):
    global running
    running = False


def main():
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    sock.setblocking(False)

    try:
        hid = open(HID_DEV, "wb", buffering=0)
    except OSError as e:
        log(f"FATAL: cannot open {HID_DEV}: {e} (gadget not configured?)")
        sys.exit(1)

    log(f"Listening on :{LISTEN_PORT}, writing to {HID_DEV}")
    last_buttons = 0
    last_seq = None
    lost = 0
    received = 0

    while running:
        ready, _, _ = select.select([sock], [], [], FAILSAFE_S)

        if not ready:
            if last_buttons:
                log(f"Failsafe: stream silent {FAILSAFE_S}s with buttons held -> releasing")
                hid.write(struct.pack(REPORT_FMT, 0, 0, 0, 0, 0))
                last_buttons = 0
            continue

        try:
            data, _ = sock.recvfrom(64)
        except OSError:
            continue
        if len(data) != PACKET_SIZE:
            continue
        magic, seq, buttons, flags, dx, dy, wheel, hwheel = struct.unpack(PACKET_FMT, data)
        if magic != MAGIC:
            continue

        received += 1
        if last_seq is not None:
            gap = (seq - last_seq - 1) & 0xFFFF
            if 0 < gap < 1000:
                lost += gap
        last_seq = seq
        if received % 100000 == 0:
            log(f"{received} packets, {lost} lost")

        # Keepalives only need a report if button state changed
        if (flags & FLAG_KEEPALIVE) and buttons == last_buttons and not (dx or dy or wheel or hwheel):
            continue

        try:
            hid.write(struct.pack(REPORT_FMT, buttons, dx, dy, wheel, hwheel))
            last_buttons = buttons
        except OSError as e:
            # Host asleep / not enumerated: hidg write blocks or errors. Drop and continue.
            log(f"HID write error: {e} (host asleep?)")
            time.sleep(0.5)

    log("Shutting down; releasing buttons.")
    try:
        hid.write(struct.pack(REPORT_FMT, 0, 0, 0, 0, 0))
        hid.close()
    except OSError:
        pass


if __name__ == "__main__":
    main()
