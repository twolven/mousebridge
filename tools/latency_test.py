###############################################################################
# MouseBridge latency test - run from the local (agent) PC
#
# Sends real protocol packets with FLAG_ECHO set; the pump on the Pi replies
# after processing, so round-trips traverse the exact deployed path:
#   this PC -> [relay on remote PC -> NCM] or [WiFi] -> pump -> back
#
# --with-write makes each probe a non-keepalive zero-delta packet, so the
# pump performs a real /dev/hidg0 write (a null report - no cursor movement)
# before echoing: RTT then includes gadget-side HID processing.
#
#   python latency_test.py --target 192.168.1.3:8800 --count 5000 --with-write
###############################################################################

import argparse
import socket
import statistics
import struct
import time

MAGIC = 0x4D42
PACKET_FMT = "<HHBbhhbb"
FLAG_KEEPALIVE = 0x01
FLAG_ECHO = 0x02


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    idx = min(len(sorted_vals) - 1, max(0, round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def main():
    ap = argparse.ArgumentParser(description="MouseBridge path latency test")
    ap.add_argument("--target", required=True, help="ip:port (relay or Pi directly)")
    ap.add_argument("--count", type=int, default=5000)
    ap.add_argument("--timeout", type=float, default=0.25)
    ap.add_argument("--with-write", action="store_true",
                    help="include a real HID null-report write in the measured path")
    args = ap.parse_args()

    host, port = args.target.rsplit(":", 1)
    dst = (host, int(port))
    flags = FLAG_ECHO if args.with_write else (FLAG_ECHO | FLAG_KEEPALIVE)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)

    label = "WITH hid write" if args.with_write else "network+pump only"
    print(f"Target {args.target}, {args.count} ping-pong probes ({label})...")

    rtts = []
    lost = 0
    t_start = time.perf_counter()
    for i in range(args.count):
        seq = i & 0xFFFF
        pkt = struct.pack(PACKET_FMT, MAGIC, seq, 0, flags, 0, 0, 0, 0)
        t0 = time.perf_counter()
        sock.sendto(pkt, dst)
        while True:
            try:
                data, _ = sock.recvfrom(64)
            except socket.timeout:
                lost += 1
                break
            except ConnectionResetError:
                lost += 1
                break
            t1 = time.perf_counter()
            if len(data) == struct.calcsize(PACKET_FMT):
                _, rseq, *_ = struct.unpack(PACKET_FMT, data)
                if rseq == seq:
                    rtts.append((t1 - t0) * 1000.0)
                    break
                # stale reply from a timed-out probe; keep waiting for ours
                if (t1 - t0) > args.timeout:
                    lost += 1
                    break
    elapsed = time.perf_counter() - t_start

    if not rtts:
        print("No replies at all - is the pump/relay running?")
        return

    s = sorted(rtts)
    print(f"\nReplies {len(rtts)}/{args.count}  loss {lost} "
          f"({100.0 * lost / args.count:.2f}%)  wall {elapsed:.1f}s")
    print(f"RTT ms  min {s[0]:.2f}  p50 {pct(s, 50):.2f}  mean {statistics.fmean(s):.2f}  "
          f"p95 {pct(s, 95):.2f}  p99 {pct(s, 99):.2f}  max {s[-1]:.2f}")
    print(f"one-way estimate (RTT/2): p50 {pct(s, 50) / 2:.2f} ms, p99 {pct(s, 99) / 2:.2f} ms")


if __name__ == "__main__":
    main()
