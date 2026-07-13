#!/usr/bin/env python3
###############################################################################
# MouseBridge hidpump v0.1 - Pi (gadget side)
#
# Receives 12-byte UDP packets and writes 7-byte HID mouse reports to
# /dev/hidg0. Failsafe: if the stream goes silent while buttons are held
# (agent crash, link drop), releases all buttons after FAILSAFE_S.
###############################################################################

import errno
import os
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

FLAG_ECHO = 0x02  # reply to sender after processing (latency measurement)

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
        # Non-blocking: if the host stops polling the interrupt endpoint
        # (suspend, driver issue), writes EAGAIN instead of wedging the pump
        hid = os.open(HID_DEV, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        log(f"FATAL: cannot open {HID_DEV}: {e} (gadget not configured?)")
        sys.exit(1)

    log(f"Listening on :{LISTEN_PORT}, writing to {HID_DEV}")
    last_buttons = 0
    last_seq = None
    lost = 0
    received = 0

    # Rolling 60s stability stats: rx rate, seq-gap loss, worst inter-packet
    # gap while a stream is active (gaps >500ms = focus pause, not jitter)
    STATS_INTERVAL = 60.0
    win_rx = 0
    win_lost = 0
    win_dropped = 0  # reports the host would not accept (EAGAIN)
    win_max_gap = 0.0
    last_rx_t = None
    next_stats = time.monotonic() + STATS_INTERVAL

    while running:
        ready, _, _ = select.select([sock], [], [], FAILSAFE_S)

        now = time.monotonic()
        if now >= next_stats:
            if win_rx:
                total = win_rx + win_lost
                log(f"[Stats] 60s: rx {win_rx} ({win_rx / STATS_INTERVAL:.0f}/s), "
                    f"lost {win_lost} ({100.0 * win_lost / total:.3f}%), "
                    f"hid drops {win_dropped}, "
                    f"max stream gap {win_max_gap * 1000.0:.0f}ms "
                    f"(lifetime rx {received}, lost {lost})")
            win_rx = win_lost = win_dropped = 0
            win_max_gap = 0.0
            next_stats = now + STATS_INTERVAL

        if not ready:
            if last_buttons:
                log(f"Failsafe: stream silent {FAILSAFE_S}s with buttons held -> releasing")
                try:
                    os.write(hid, struct.pack(REPORT_FMT, 0, 0, 0, 0, 0))
                except OSError:
                    pass
                last_buttons = 0
            continue

        try:
            data, sender = sock.recvfrom(64)
        except OSError:
            continue
        if len(data) != PACKET_SIZE:
            continue
        magic, seq, buttons, flags, dx, dy, wheel, hwheel = struct.unpack(PACKET_FMT, data)
        if magic != MAGIC:
            continue

        received += 1
        win_rx += 1
        # Seq gaps only count within a live stream; after >1s of silence the
        # agent may have restarted (seq reset), which is not packet loss
        if last_seq is not None and last_rx_t is not None and (now - last_rx_t) <= 1.0:
            gap = (seq - last_seq - 1) & 0xFFFF
            if 0 < gap < 1000:
                lost += gap
                win_lost += gap
        last_seq = seq
        if last_rx_t is not None:
            delta = now - last_rx_t
            if delta <= 0.5 and delta > win_max_gap:
                win_max_gap = delta
        last_rx_t = now

        # Keepalives only need a report if button state changed
        needs_write = not ((flags & FLAG_KEEPALIVE) and buttons == last_buttons
                           and not (dx or dy or wheel or hwheel))
        if needs_write:
            report = struct.pack(REPORT_FMT, buttons, dx, dy, wheel, hwheel)
            try:
                os.write(hid, report)
                last_buttons = buttons
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    # Endpoint busy: the 1-slot queue still holds the previous
                    # report (1kHz packets can outpace the host poll). Wait
                    # briefly for writability instead of dropping - a dropped
                    # button transition only self-heals on the next keepalive
                    # ~100ms later, which breaks double-clicks.
                    _, writable, _ = select.select([], [hid], [], 0.008)
                    try:
                        if writable:
                            os.write(hid, report)
                            last_buttons = buttons
                        else:
                            win_dropped += 1  # host genuinely not polling
                    except OSError:
                        win_dropped += 1
                else:
                    log(f"HID write error: {e}")
                    time.sleep(0.5)

        # Echo after processing so a round-trip measures the full program path
        if flags & FLAG_ECHO:
            try:
                sock.sendto(data, sender)
            except OSError:
                pass

    log("Shutting down; releasing buttons.")
    try:
        os.write(hid, struct.pack(REPORT_FMT, 0, 0, 0, 0, 0))
        os.close(hid)
    except OSError:
        pass


if __name__ == "__main__":
    main()
