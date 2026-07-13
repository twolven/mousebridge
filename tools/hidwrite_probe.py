#!/usr/bin/env python3
"""Probe: is the host actually draining /dev/hidg0? Run on the Pi.

A single successful write only proves the 1-slot queue was empty; 100 paced
writes only succeed if the host is genuinely polling the interrupt endpoint.
All-zero reports: no cursor movement on the host.
"""
import errno
import os
import time

fd = os.open("/dev/hidg0", os.O_RDWR | os.O_NONBLOCK)
ok = eagain = 0
for _ in range(100):
    try:
        os.write(fd, bytes(7))
        ok += 1
    except OSError as e:
        if e.errno == errno.EAGAIN:
            eagain += 1
        else:
            print(f"error: {e}")
            break
    time.sleep(0.005)
os.close(fd)
print(f"writes ok {ok}, EAGAIN {eagain} -> host {'IS' if ok >= 95 else 'is NOT'} polling")
