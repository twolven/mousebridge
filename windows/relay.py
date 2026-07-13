###############################################################################
# MouseBridge relay v0.4 - remote PC (Sunshine host)
#
# Jobs:
#   1. Forward agent UDP packets from the LAN interface to the Pi on the
#      USB-ethernet (NCM) link (bidirectional, so echo/latency tests work).
#   2. Persistent status window (topmost, non-activating, draggable):
#      GREEN "ACTIVE" when the agent stream is flowing AND the Pi answers
#      health probes; RED with a reason otherwise (agent idle / PI DOWN).
#   3. Optional kill hotkey (from MouseMoveR): GetAsyncKeyState polling +
#      taskkill, no pip dependencies.
#
# Config: config.txt next to the exe (LISTEN / FORWARD / KILL_PROCESS /
# KILL_KEY / STATUS_WINDOW). CLI args override config.txt. Effective config
# is printed at startup and logged to mousebridge-relay.log.
###############################################################################

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import socket
import struct
import subprocess
import sys
import threading
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "mousebridge-relay.log")
CONFIG_FILE = os.path.join(BASE_DIR, "config.txt")

MAGIC = 0x4D42
PACKET_FMT = "<HHBbhhbb"
FLAG_KEEPALIVE = 0x01
FLAG_ECHO = 0x02
PROBE_SEQ = 0xFFFE          # marks the relay's own Pi health probes
PROBE_INTERVAL_S = 2.0
PI_TIMEOUT_S = 5.0          # no probe echo for this long -> Pi considered down
STREAM_IDLE_S = 0.6         # agent keepalives are 100ms; silence -> released

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


# --- Shared state (main loop writes, status window reads) ---
state = {
    "stream_active": False,
    "pi_last_echo": 0.0,
    "started": time.monotonic(),
}


def pi_ok():
    return (time.monotonic() - state["pi_last_echo"]) < PI_TIMEOUT_S


# --- Status window (persistent, topmost, non-activating, draggable) ---
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, wt.LPVOID]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, ctypes.c_uint,
                             wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


def _rgb(r, g, b):
    return r | (g << 8) | (b << 16)


GREEN = _rgb(22, 133, 48)
RED = _rgb(178, 44, 44)


def _status_text():
    """(text, color) for the current bridge state."""
    if state["stream_active"] and pi_ok():
        return "MouseBridge  ACTIVE", GREEN
    if not pi_ok():
        return "MouseBridge  PI DOWN", RED
    return "MouseBridge  idle", RED


@WNDPROC
def _status_wndproc(hwnd, msg, wparam, lparam):
    WM_PAINT, WM_TIMER, WM_DESTROY, WM_NCHITTEST = 0x000F, 0x0113, 0x0002, 0x0084
    if msg == WM_PAINT:
        text, bg = _status_text()
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        brush = gdi32.CreateSolidBrush(bg)
        user32.FillRect(hdc, ctypes.byref(ps.rcPaint), brush)
        gdi32.DeleteObject(brush)
        font = gdi32.CreateFontW(-15, 0, 0, 0, 600, 0, 0, 0, 0, 0, 0, 0, 0,
                                 "Segoe UI")
        old_font = gdi32.SelectObject(hdc, font)
        gdi32.SetTextColor(hdc, _rgb(255, 255, 255))
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
        rect = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        user32.DrawTextW(hdc, text, -1, ctypes.byref(rect),
                         0x1 | 0x4 | 0x20)  # CENTER | VCENTER | SINGLELINE
        gdi32.SelectObject(hdc, old_font)
        gdi32.DeleteObject(font)
        user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == WM_TIMER:
        user32.InvalidateRect(hwnd, None, False)
        return 0
    if msg == WM_NCHITTEST:
        return 2  # HTCAPTION: whole window drags
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def status_window_thread():
    WS_POPUP = 0x80000000
    WS_EX = 0x8 | 0x08000000 | 0x80 | 0x80000  # TOPMOST|NOACTIVATE|TOOLWINDOW|LAYERED
    SW_SHOWNOACTIVATE = 4

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = _status_wndproc
    wc.hInstance = hinst
    wc.lpszClassName = "MouseBridgeStatus"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        log("[Status] window class registration failed")
        return

    w, h = 190, 34
    x = user32.GetSystemMetrics(0) - w - 16
    y = user32.GetSystemMetrics(1) - h - 60
    hwnd = user32.CreateWindowExW(WS_EX, "MouseBridgeStatus", "MouseBridge",
                                  WS_POPUP, x, y, w, h,
                                  None, None, hinst, None)
    if not hwnd:
        log("[Status] window creation failed")
        return
    user32.SetLayeredWindowAttributes(hwnd, 0, 225, 2)  # LWA_ALPHA
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetTimer(hwnd, 1, 500, None)
    log("[Status] indicator window up (drag it anywhere)")

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# --- Kill hotkey ---
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


# --- Pi health prober ---
def pi_prober(sock, dst):
    """Sends an echo probe to the pump every PROBE_INTERVAL_S; the main loop
    consumes matching echoes and refreshes state['pi_last_echo']."""
    while True:
        pkt = struct.pack(PACKET_FMT, MAGIC, PROBE_SEQ, 0,
                          FLAG_KEEPALIVE | FLAG_ECHO, 0, 0, 0, 0)
        try:
            sock.sendto(pkt, dst)
        except OSError:
            pass
        time.sleep(PROBE_INTERVAL_S)


# --- Config ---
def load_config_file():
    cfg = {}
    if not os.path.exists(CONFIG_FILE):
        return cfg
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip().upper()] = value.strip()
    return cfg


def main():
    ap = argparse.ArgumentParser(description="MouseBridge LAN->Pi UDP relay")
    ap.add_argument("--listen", default=None, help="LAN side (ip:port)")
    ap.add_argument("--forward", default=None, help="Pi side (ip:port)")
    ap.add_argument("--kill-process", default=None, metavar="NAME.EXE")
    ap.add_argument("--kill-key", default=None)
    ap.add_argument("--no-status-window", action="store_true")
    args = ap.parse_args()

    cfg = load_config_file()
    listen = args.listen or cfg.get("LISTEN", "0.0.0.0:8800")
    forward = args.forward or cfg.get("FORWARD", "10.66.0.2:8800")
    kill_proc = args.kill_process if args.kill_process is not None \
        else cfg.get("KILL_PROCESS", "")
    kill_key = args.kill_key or cfg.get("KILL_KEY", "backslash")
    status_win = (not args.no_status_window) and \
        cfg.get("STATUS_WINDOW", cfg.get("POPUPS", "on")).lower() != "off"

    log("Starting MouseBridge relay v0.4...")
    log(f"Config file: {CONFIG_FILE} ({'found' if cfg else 'not found, defaults'})")
    log("Effective configuration:")
    log(f"  LISTEN        : {listen}")
    log(f"  FORWARD       : {forward}  (Pi gadget over USB-NCM)")
    log(f"  KILL_PROCESS  : '{kill_proc or '[disabled]'}'")
    log(f"  KILL_KEY      : {kill_key}")
    log(f"  STATUS_WINDOW : {'on' if status_win else 'off'}")

    if kill_proc:
        key = kill_key.lower()
        vk = VK_NAMES.get(key)
        if vk is None:
            try:
                vk = int(key, 16 if key.startswith("0x") else 10)
            except ValueError:
                log(f"[Kill] Unknown key '{kill_key}'; hotkey disabled.")
                vk = None
        if vk is not None:
            threading.Thread(target=hotkey_poller, args=(vk, kill_proc),
                             daemon=True).start()

    lhost, lport = listen.rsplit(":", 1)
    fhost, fport = forward.rsplit(":", 1)
    dst = (fhost, int(fport))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((lhost, int(lport)))
    sock.settimeout(0.25)  # short so stream-release detection is snappy
    log(f"Relaying {listen} -> {forward}")

    if status_win:
        threading.Thread(target=status_window_thread, daemon=True).start()
    threading.Thread(target=pi_prober, args=(sock, dst), daemon=True).start()

    probe_bytes = struct.pack(PACKET_FMT, MAGIC, PROBE_SEQ, 0,
                              FLAG_KEEPALIVE | FLAG_ECHO, 0, 0, 0, 0)

    # Rolling 60s stability stats + stream state
    STATS_INTERVAL = 60.0
    total = 0
    win_fwd = 0
    win_back = 0
    client = None
    last_agent_rx = 0.0
    next_stats = time.monotonic() + STATS_INTERVAL

    while True:
        now = time.monotonic()

        if state["stream_active"] and now - last_agent_rx > STREAM_IDLE_S:
            state["stream_active"] = False
            log("[Stream] released")

        if now >= next_stats:
            if win_fwd or win_back:
                log(f"[Stats] 60s: {win_fwd} agent->pi ({win_fwd / STATS_INTERVAL:.0f}/s), "
                    f"{win_back} pi->agent, pi={'ok' if pi_ok() else 'DOWN'}, "
                    f"client={client[0] if client else 'none'} (lifetime {total})")
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
                if data == probe_bytes:
                    # our own health probe coming back from the pump
                    if not pi_ok():
                        log("[Health] Pi is answering again")
                    state["pi_last_echo"] = now
                elif client:
                    sock.sendto(data, client)
                    win_back += 1
            else:
                client = src
                sock.sendto(data, dst)
                win_fwd += 1
                last_agent_rx = now
                if not state["stream_active"]:
                    state["stream_active"] = True
                    log(f"[Stream] ACTIVE (from {src[0]})")
            total += 1
        except OSError as e:
            log(f"Forward error: {e}")


if __name__ == "__main__":
    main()
