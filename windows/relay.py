###############################################################################
# MouseBridge relay v0.1 - remote PC (Sunshine host)
#
# Forwards agent UDP packets from the LAN interface to the Pi on the
# USB-ethernet (NCM) link. Needed because the Pi hangs off this PC's USB
# port rather than the LAN. Run at logon via Task Scheduler.
###############################################################################

import argparse
import socket
import time


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] [Relay] {message}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="MouseBridge LAN->Pi UDP relay")
    ap.add_argument("--listen", default="0.0.0.0:8800", help="LAN side (ip:port)")
    ap.add_argument("--forward", default="10.66.0.2:8800", help="Pi side (ip:port)")
    args = ap.parse_args()

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
