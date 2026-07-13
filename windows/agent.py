###############################################################################
# MouseBridge agent v0.1 - local PC (Moonlight side)
#
# Captures the physical mouse via Raw Input and streams HID deltas over UDP
# to the Pi gadget (usually via the relay on the remote PC) - but only while
# the configured streaming window is in the foreground. Focus changes are
# event-driven (SetWinEventHook), not polled.
###############################################################################

import ctypes
import ctypes.wintypes as wt
import os
import socket
import struct
import sys
import time

MAGIC = 0x4D42  # "MB"
PACKET_FMT = "<HHBbhhbb"  # magic, seq, buttons, flags, dx, dy, wheel, hwheel
FLAG_KEEPALIVE = 0x01
KEEPALIVE_MS = 100

# --- Config (defaults, overridden by config.txt) ---
PI_HOST = "127.0.0.1"
PI_PORT = 8800
WINDOW_TITLE = "Moonlight"
TITLE_MATCH = "exact"  # exact | contains
SCALE = 1.0  # motion multiplier: >1 = faster remote cursor, <1 = slower


BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "mousebridge-agent.log")


def log(message):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Agent] {message}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config(filename="config.txt"):
    global PI_HOST, PI_PORT, WINDOW_TITLE, TITLE_MATCH, SCALE
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        log(f"No config at '{path}', using defaults.")
        return
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            try:
                if key == "PI_HOST":
                    PI_HOST = value
                elif key == "PI_PORT":
                    PI_PORT = int(value)
                elif key == "WINDOW_TITLE":
                    WINDOW_TITLE = value
                elif key == "TITLE_MATCH":
                    TITLE_MATCH = value.lower()
                elif key == "SCALE":
                    SCALE = float(value)
                else:
                    log(f"Warning: unknown key '{key}' on line {line_num}")
            except ValueError:
                log(f"Warning: bad value for '{key}' on line {line_num}: '{value}'")
    log(f"Config: target={PI_HOST}:{PI_PORT} title='{WINDOW_TITLE}' "
        f"({TITLE_MATCH}) scale={SCALE}")


# --- Win32 setup ---
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 64-bit correctness: default ctypes restype is a 32-bit int, which truncates
# handles/LRESULTs and mangles HWND_MESSAGE (-3)
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, wt.LPVOID]
user32.GetForegroundWindow.restype = wt.HWND
user32.SetWinEventHook.restype = wt.HANDLE
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WM_INPUT = 0x00FF
WM_TIMER = 0x0113
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
HWND_MESSAGE = -3
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
MOUSE_MOVE_ABSOLUTE = 0x0001
WHEEL_DELTA = 120

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800

BUTTON_MAP = [
    (RI_MOUSE_LEFT_BUTTON_DOWN, RI_MOUSE_LEFT_BUTTON_UP, 0x01),
    (RI_MOUSE_RIGHT_BUTTON_DOWN, RI_MOUSE_RIGHT_BUTTON_UP, 0x02),
    (RI_MOUSE_MIDDLE_BUTTON_DOWN, RI_MOUSE_MIDDLE_BUTTON_UP, 0x04),
    (RI_MOUSE_BUTTON_4_DOWN, RI_MOUSE_BUTTON_4_UP, 0x08),
    (RI_MOUSE_BUTTON_5_DOWN, RI_MOUSE_BUTTON_5_UP, 0x10),
]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wt.USHORT), ("usButtonData", wt.USHORT)]


class _RAWMOUSE_U(ctypes.Union):
    _anonymous_ = ("s",)
    _fields_ = [("ulButtons", wt.ULONG), ("s", _RAWMOUSE_BUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("usFlags", wt.USHORT), ("u", _RAWMOUSE_U),
                ("ulRawButtons", wt.ULONG), ("lLastX", wt.LONG),
                ("lLastY", wt.LONG), ("ulExtraInformation", wt.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, ctypes.c_uint,
                             wt.WPARAM, wt.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND,
                                  wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


# --- Bridge state ---
class Bridge:
    STATS_INTERVAL = 60.0

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.target = (PI_HOST, PI_PORT)
        self.streaming = False
        self.buttons = 0
        self.seq = 0
        self.sent = 0
        self.send_errors = 0
        self.session_start = 0.0
        self.next_stats = 0.0
        # Fractional motion carry so SCALE never loses slow precise movement
        self.acc_x = 0.0
        self.acc_y = 0.0

    def scale_motion(self, dx, dy):
        if SCALE == 1.0:
            return dx, dy
        self.acc_x += dx * SCALE
        self.acc_y += dy * SCALE
        out_x, out_y = int(self.acc_x), int(self.acc_y)
        self.acc_x -= out_x
        self.acc_y -= out_y
        return out_x, out_y

    def send(self, dx=0, dy=0, wheel=0, hwheel=0, flags=0):
        pkt = struct.pack(PACKET_FMT, MAGIC, self.seq, self.buttons, flags,
                          dx, dy, wheel, hwheel)
        self.seq = (self.seq + 1) & 0xFFFF
        self.sent += 1
        try:
            self.sock.sendto(pkt, self.target)
        except OSError as e:
            self.send_errors += 1
            log(f"UDP send error #{self.send_errors}: {e}")

    def maybe_log_stats(self):
        """Called from the keepalive timer; 60s stability line while streaming."""
        now = time.monotonic()
        if self.streaming and now >= self.next_stats:
            mins = (now - self.session_start) / 60.0
            log(f"[Stats] streaming {mins:.0f}min: {self.sent} pkts sent, "
                f"{self.send_errors} send errors")
            self.next_stats = now + self.STATS_INTERVAL

    def set_streaming(self, on):
        if on == self.streaming:
            return
        self.streaming = on
        if on:
            self.session_start = time.monotonic()
            self.next_stats = self.session_start + self.STATS_INTERVAL
            log(f"Focus GAINED -> streaming to {self.target[0]}:{self.target[1]}")
        else:
            # Release everything on the remote side before going silent
            self.buttons = 0
            self.send(flags=FLAG_KEEPALIVE)
            mins = (time.monotonic() - self.session_start) / 60.0
            log(f"Focus LOST -> stream stopped ({self.sent} packets, "
                f"{self.send_errors} send errors, {mins:.1f}min session)")
            self.sent = 0
            self.send_errors = 0


bridge = Bridge()


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def title_matches(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return False
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    if TITLE_MATCH == "contains":
        return WINDOW_TITLE in buf.value
    return buf.value == WINDOW_TITLE


def handle_raw_input(lparam):
    size = wt.UINT(0)
    header_size = ctypes.sizeof(RAWINPUTHEADER)
    user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size)
    if size.value == 0 or size.value > ctypes.sizeof(RAWINPUT):
        return
    raw = RAWINPUT()
    got = user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(raw),
                                 ctypes.byref(size), header_size)
    if got != size.value or raw.header.dwType != 0:  # RIM_TYPEMOUSE == 0
        return

    m = raw.mouse
    bf = m.usButtonFlags

    for down, up, bit in BUTTON_MAP:
        if bf & down:
            bridge.buttons |= bit
        if bf & up:
            bridge.buttons &= ~bit

    wheel = hwheel = 0
    if bf & RI_MOUSE_WHEEL:
        wheel = clamp(ctypes.c_short(m.usButtonData).value // WHEEL_DELTA, -127, 127)
    if bf & RI_MOUSE_HWHEEL:
        hwheel = clamp(ctypes.c_short(m.usButtonData).value // WHEEL_DELTA, -127, 127)

    dx = dy = 0
    if not (m.usFlags & MOUSE_MOVE_ABSOLUTE):
        dx, dy = bridge.scale_motion(m.lLastX, m.lLastY)
        dx = clamp(dx, -32767, 32767)
        dy = clamp(dy, -32767, 32767)

    if bridge.streaming and (dx or dy or wheel or hwheel or bf):
        bridge.send(dx, dy, wheel, hwheel)


@WNDPROC
def wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        handle_raw_input(lparam)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == WM_TIMER:
        if bridge.streaming:
            bridge.send(flags=FLAG_KEEPALIVE)  # keeps held buttons alive on the pump
            bridge.maybe_log_stats()
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


@WINEVENTPROC
def win_event_proc(hook, event, hwnd, id_object, id_child, thread, ms_time):
    if event == EVENT_SYSTEM_FOREGROUND and hwnd:
        bridge.set_streaming(title_matches(hwnd))


def main():
    log("Starting MouseBridge agent v1.0...")
    # Single instance only - a second agent would double-send every packet
    kernel32.CreateMutexW(None, False, "Global\\MouseBridgeAgent")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        log("Another agent instance is already running. Exiting.")
        sys.exit(0)
    load_config()
    bridge.target = (PI_HOST, PI_PORT)

    hinstance = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = wnd_proc
    wc.hInstance = hinstance
    wc.lpszClassName = "MouseBridgeAgent"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        log(f"RegisterClassW failed: {kernel32.GetLastError()}")
        sys.exit(1)

    hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "MouseBridge", 0,
                                  0, 0, 0, 0, wt.HWND(HWND_MESSAGE), None,
                                  hinstance, None)
    if not hwnd:
        log(f"CreateWindowExW failed: {kernel32.GetLastError()}")
        sys.exit(1)

    rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)  # Generic Desktop / Mouse
    if not user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
        log(f"RegisterRawInputDevices failed: {kernel32.GetLastError()}")
        sys.exit(1)

    hook = user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
                                  None, win_event_proc, 0, 0, WINEVENT_OUTOFCONTEXT)
    if not hook:
        log("SetWinEventHook failed; focus changes will not be detected.")
        sys.exit(1)

    user32.SetTimer(hwnd, 1, KEEPALIVE_MS, None)

    # Prime state from whatever is focused right now
    fg = user32.GetForegroundWindow()
    bridge.set_streaming(bool(fg) and title_matches(fg))
    log("Ready. Event-driven; idle until the streaming window takes focus.")

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnhookWinEvent(hook)
    bridge.set_streaming(False)
    log("Exiting.")


if __name__ == "__main__":
    main()
